"""
MenuFlash Mini — Import du menu depuis un fichier Excel (.xlsx) ou CSV.

Branchement dans app.py (voir ajouter_branchement.py) :

    from menu_import import init_menu_import
    init_menu_import(app, owner_required=owner_required, active_restaurant=active_restaurant,
                     log_menu_change=log_menu_change, log_audit=log_audit)

Pour le restaurateur : Boutique > Importer un menu.
Au lieu de saisir 60 plats à la main, il remplit un fichier (modèle fourni en Excel
et en CSV), le dépose, VÉRIFIE L'APERÇU, puis confirme. Rien n'est écrit avant la
confirmation.

Colonnes du fichier (ligne d'en-tête obligatoire, accents et majuscules sans importance)
  Catégorie    facultative ; « Menu » par défaut
  Plat         obligatoire (120 caractères maximum)
  Prix         obligatoire, en francs entiers : 2500, « 2 500 », « 2.500 », « 2500 FCFA »
  Description  facultative (300 caractères ; le surplus est coupé)
  Préparation  facultative, en minutes (1 à 180) ; 10 par défaut
  Disponible   facultative : oui / non (oui par défaut)
Des lignes de titre au-dessus de l'en-tête sont tolérées. Les lignes vides sont ignorées.

Règles
------
- Chaque ligne est contrôlée : une ligne en erreur est ignorée (et expliquée), les
  autres sont importées. Les doublons dans le fichier sont ignorés.
- Un plat qui existe déjà (même nom dans la même catégorie, sans tenir compte des
  accents ni des majuscules) est IGNORÉ par défaut ; l'option « mettre à jour » change
  son prix, et sa description / préparation / disponibilité si le fichier les renseigne.
  Les photos, les compositions et les suggestions ne sont jamais touchées.
- Les catégories absentes sont créées, à la suite des existantes.
- Les limites sont respectées : plats de la formule (ou 300), 50 catégories ; au-delà,
  les lignes en trop sont ignorées et signalées.
- Fichier de 1 Mo et 500 lignes au maximum. Excel (.xlsx) lu sans bibliothèque externe,
  avec protection contre les fichiers piégés (archives géantes, entités XML).

Sécurité : gestionnaire connecté uniquement (isolation stricte par restaurant), jeton
CSRF, l'aperçu est confirmé par un jeton signé qui expire en 30 minutes et n'est valable
que pour ce gestionnaire et ce restaurant, tout est tracé (historique du menu et journal
de sécurité). Aucune nouvelle dépendance.
"""
import csv
import io
import logging
import re
import unicodedata
import zipfile
from xml.etree import ElementTree as ET

from flask import Response, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from models import DEFAULT_PREP_MINUTES, MenuCategory, MenuItem, db

log = logging.getLogger("menuflash.menu_import")

MAX_BYTES = 1024 * 1024
MAX_ROWS = 500
MAX_NAME, MAX_CATEGORY, MAX_DESCRIPTION = 120, 80, 300
MAX_PRICE = 10_000_000
DEFAULT_CATEGORY = "Menu"
TOKEN_MAX_AGE = 30 * 60
HEADER_SEARCH_ROWS = 10
XLSX_MAX_PART = 20 * 1024 * 1024
XLSX_MAX_FILES = 200
FORBIDDEN = set("<>{}`\\")

ALIASES = {
    "category": {"categorie", "category", "rubrique", "famille", "section", "type"},
    "name": {"plat", "nom", "nom du plat", "name", "article", "produit", "dish", "item", "designation"},
    "price": {"prix", "price", "tarif", "montant", "prix fcfa", "prix xof"},
    "description": {"description", "desc", "details", "detail", "composition"},
    "prep": {"preparation", "temps de preparation", "temps", "prep", "prep minutes", "minutes", "duree",
             "preparation min", "temps de preparation min"},
    "available": {"disponible", "disponibilite", "available", "dispo", "actif", "statut"},
}
TRUE_WORDS = {"oui", "o", "yes", "y", "1", "true", "vrai", "disponible", "dispo", "x", "ok"}
FALSE_WORDS = {"non", "n", "no", "0", "false", "faux", "epuise", "indisponible", "rupture", "off"}


class ImportFileError(ValueError):
    """Erreur de fichier expliquée au restaurateur."""


# ==========================================================================
# Outils de texte
# ==========================================================================
def norm_key(text):
    """Minuscules, sans accents, sans ponctuation : pour comparer des noms et des en-têtes."""
    s = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def clean_text(value, max_len, label, required=False, truncate=False):
    """Texte nettoyé. Renvoie (valeur, avertissement|None) ou lève ValueError."""
    text = unicodedata.normalize("NFC", str(value if value is not None else ""))
    text = "".join(ch for ch in text if unicodedata.category(ch)[0] != "C" or ch in "\n\t")
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        if required:
            raise ValueError(f"{label} manquant")
        return "", None
    if any(ch in FORBIDDEN for ch in text):
        raise ValueError(f"{label} : caractères non autorisés (< > {{ }} ` \\)")
    if label in ("Plat", "Catégorie") and text[0] in "=@":
        raise ValueError(f"{label} : ne peut pas commencer par « {text[0]} »")
    if len(text) > max_len:
        if truncate:
            return text[:max_len].rstrip(), f"{label} raccourcie à {max_len} caractères"
        raise ValueError(f"{label} trop long ({max_len} caractères maximum)")
    return text, None


def parse_price(value):
    """Prix en francs entiers : '2500', '2 500', '2.500', '2500 FCFA', '2500,00'."""
    s = str(value if value is not None else "").replace("\u00a0", " ").replace("\u202f", " ").strip()
    if not s:
        raise ValueError("prix manquant")
    s = re.sub(r"(?i)\s*(f\s*cfa|fcfa|xof|cfa|francs?|f)\s*$", "", s)
    s = re.sub(r"(?i)^\s*(f\s*cfa|fcfa|xof|cfa)\s*", "", s)
    s = s.replace(" ", "")
    if not re.fullmatch(r"\d+(?:[.,]\d+)*", s):
        raise ValueError("prix invalide (exemple : 2500)")
    thousands = r"\d{1,3}(?:[.,]\d{3})+"
    if re.fullmatch(r"\d+", s):
        digits = s
    elif re.fullmatch(thousands, s):                      # 2.500 ou 1,250,000 : séparateur de milliers
        digits = re.sub(r"[.,]", "", s)
    else:                                                 # 2500,00 ou 1.500,00 : décimales seulement si nulles
        head, _sep, frac = re.split(r"([.,])(?=\d+$)", s)
        if not (re.fullmatch(r"\d+", head) or re.fullmatch(thousands, head)) or frac.strip("0"):
            raise ValueError("prix invalide : les prix sont en francs entiers (2500, pas 2500,50)")
        digits = re.sub(r"[.,]", "", head)
    price = int(digits)
    if price > MAX_PRICE:
        raise ValueError(f"prix trop élevé (maximum {MAX_PRICE:,})".replace(",", " "))
    return price


def parse_prep(value):
    s = str(value if value is not None else "").strip().lower().replace(",", ".")
    if not s:
        return None
    s = re.sub(r"\s*(minutes?|mins?|mn|m)$", "", s).strip()
    s = re.sub(r"\.0+$", "", s)
    if not re.fullmatch(r"\d{1,4}", s):
        raise ValueError("préparation invalide (minutes, de 1 à 180)")
    minutes = int(s)
    if not 1 <= minutes <= 180:
        raise ValueError("préparation : de 1 à 180 minutes")
    return minutes


def parse_bool(value):
    s = norm_key(value)
    if not s:
        return None
    if s in TRUE_WORDS:
        return True
    if s in FALSE_WORDS:
        return False
    raise ValueError("« Disponible » : écrivez oui ou non")


# ==========================================================================
# Lecture des fichiers
# ==========================================================================
def decode_text(data):
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):                 # « Texte Unicode » d'Excel : UTF-16
        try:
            return data.decode("utf-16")
        except UnicodeDecodeError:
            raise ImportFileError("Fichier texte illisible : enregistrez-le en CSV UTF-8.")
    if b"\x00" in data:
        raise ImportFileError("Ce fichier n'est pas un fichier texte CSV.")
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")


def read_csv_rows(data):
    text = decode_text(data)
    sample = [ln for ln in text.splitlines()[:5] if ln.strip()]
    counts = {d: sum(ln.count(d) for ln in sample) for d in (";", ",", "\t")}
    delimiter = max(counts, key=counts.get) if any(counts.values()) else ";"
    try:
        return [[c.strip() for c in row] for row in csv.reader(io.StringIO(text), delimiter=delimiter)]
    except csv.Error as exc:
        raise ImportFileError(f"Fichier CSV illisible : {exc}")


_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
       "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
       "rel": "http://schemas.openxmlformats.org/package/2006/relationships"}


def _xml(zf, name):
    info = zf.getinfo(name)
    if info.file_size > XLSX_MAX_PART:
        raise ImportFileError("Fichier Excel trop volumineux.")
    with zf.open(name) as fh:                      # lecture bornée : l'en-tête de l'archive peut mentir
        raw = fh.read(XLSX_MAX_PART + 1)
    if len(raw) > XLSX_MAX_PART:
        raise ImportFileError("Fichier Excel trop volumineux.")
    head = raw[:4096].upper()
    if b"<!DOCTYPE" in head or b"<!ENTITY" in raw[:65536].upper():
        raise ImportFileError("Fichier Excel refusé (contenu XML non autorisé).")
    try:
        return ET.fromstring(raw)
    except ET.ParseError:
        raise ImportFileError("Fichier Excel illisible ou endommagé.")


def _col_index(ref):
    letters = re.match(r"[A-Za-z]+", ref or "")
    if not letters:
        return None
    n = 0
    for ch in letters.group(0).upper():
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def read_xlsx_rows(data):
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ImportFileError("Ce fichier n'est pas un fichier Excel (.xlsx) valide.")
    with zf:
        names = zf.namelist()
        if len(names) > XLSX_MAX_FILES or sum(i.file_size for i in zf.infolist()) > 5 * XLSX_MAX_PART:
            raise ImportFileError("Fichier Excel refusé (archive trop grande).")
        if "xl/workbook.xml" not in names:
            raise ImportFileError("Ce fichier n'est pas un classeur Excel (.xlsx).")
        wb = _xml(zf, "xl/workbook.xml")
        sheet = wb.find("m:sheets/m:sheet", _NS)
        if sheet is None:
            raise ImportFileError("Le classeur Excel ne contient aucune feuille.")
        rid = sheet.get(f"{{{_NS['r']}}}id")
        target = "worksheets/sheet1.xml"
        if "xl/_rels/workbook.xml.rels" in names:
            for rel in _xml(zf, "xl/_rels/workbook.xml.rels").findall("rel:Relationship", _NS):
                if rel.get("Id") == rid:
                    target = rel.get("Target", target)
        target = target.lstrip("/")
        path = target if target.startswith("xl/") else f"xl/{target}"
        if path not in names:
            raise ImportFileError("Feuille Excel introuvable dans le fichier.")
        shared = []
        if "xl/sharedStrings.xml" in names:
            for si in _xml(zf, "xl/sharedStrings.xml").findall("m:si", _NS):
                shared.append("".join(t.text or "" for t in si.iter(f"{{{_NS['m']}}}t")))
        rows = []
        for row in _xml(zf, path).iter(f"{{{_NS['m']}}}row"):
            if len(rows) > MAX_ROWS + HEADER_SEARCH_ROWS + 50:
                raise ImportFileError(f"Trop de lignes : {MAX_ROWS} plats au maximum par fichier.")
            cells, last = {}, -1
            for c in row.findall("m:c", _NS):
                idx = _col_index(c.get("r"))
                idx = last + 1 if idx is None else idx
                last = idx
                kind = c.get("t")
                if kind == "inlineStr":
                    value = "".join(t.text or "" for t in c.iter(f"{{{_NS['m']}}}t"))
                else:
                    v = c.find("m:v", _NS)
                    value = v.text if v is not None and v.text is not None else ""
                    if kind == "s" and value.isdigit():
                        value = shared[int(value)] if int(value) < len(shared) else ""
                cells[idx] = value.strip()
            width = (max(cells) + 1) if cells else 0
            rows.append([cells.get(i, "") for i in range(width)])
        return rows


def read_table(filename, data):
    """Lit un fichier .csv / .txt / .xlsx envoyé par le restaurateur."""
    if not data:
        raise ImportFileError("Le fichier est vide.")
    if len(data) > MAX_BYTES:
        raise ImportFileError("Fichier trop volumineux (1 Mo maximum).")
    name = (filename or "").lower()
    if name.endswith(".xlsx"):
        if not data.startswith(b"PK"):
            raise ImportFileError("Ce fichier n'est pas un fichier Excel (.xlsx) valide.")
        return read_xlsx_rows(data)
    if name.endswith((".csv", ".txt")):
        return read_csv_rows(data)
    if name.endswith(".xls"):
        raise ImportFileError("Ancien format Excel (.xls) non pris en charge : enregistrez le fichier en .xlsx "
                              "(Fichier > Enregistrer sous) ou en CSV.")
    raise ImportFileError("Format non pris en charge : envoyez un fichier .xlsx (Excel) ou .csv.")


# ==========================================================================
# Analyse (fonction pure, testée)
# ==========================================================================
def detect_columns(table):
    """Trouve la ligne d'en-tête et les colonnes. Renvoie (index_de_ligne, {champ: index})."""
    for i, row in enumerate(table[:HEADER_SEARCH_ROWS]):
        found = {}
        for j, cell in enumerate(row):
            key = norm_key(cell)
            for field, names in ALIASES.items():
                if key in names and field not in found:
                    found[field] = j
        if "name" in found and "price" in found:
            return i, found
    raise ImportFileError("Colonnes introuvables : la première ligne doit contenir au moins « Plat » et « Prix » "
                          "(et si possible « Catégorie »). Téléchargez le modèle pour partir d'un fichier correct.")


def analyze(table, existing_cats, existing_items, items_capacity, cats_capacity, mode):
    """
    existing_cats  : {nom_normalisé}
    existing_items : {(catégorie_normalisée, nom_normalisé)}
    Renvoie {'rows': [...], 'summary': {...}, 'categories_new': [...]}.
    Statuts : new, update, exists, duplicate, limit, error.
    """
    header_idx, cols = detect_columns(table)
    body = table[header_idx + 1:]
    if sum(1 for r in body if any(c.strip() for c in r)) > MAX_ROWS:
        raise ImportFileError(f"Trop de lignes : {MAX_ROWS} plats au maximum par fichier. "
                              f"Faites-le en plusieurs fichiers.")

    def cell(row, field):
        j = cols.get(field)
        return row[j] if j is not None and j < len(row) else ""

    rows, seen, new_cats, new_items = [], set(), [], 0
    for offset, row in enumerate(body):
        if not any(c.strip() for c in row):
            continue
        line = header_idx + 2 + offset
        rec = {"line": line, "category": "", "name": "", "price": None, "description": "", "prep": None,
               "available": None, "status": "new", "messages": []}
        errors = []

        def attempt(fn, *args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except ValueError as exc:
                errors.append(str(exc))
                return None

        cat = attempt(clean_text, cell(row, "category") or DEFAULT_CATEGORY, MAX_CATEGORY, "Catégorie")
        rec["category"] = cat[0] if cat else (cell(row, "category") or DEFAULT_CATEGORY)[:MAX_CATEGORY]
        name = attempt(clean_text, cell(row, "name"), MAX_NAME, "Plat", required=True)
        rec["name"] = name[0] if name else cell(row, "name")[:MAX_NAME]
        rec["price"] = attempt(parse_price, cell(row, "price"))
        desc = attempt(clean_text, cell(row, "description"), MAX_DESCRIPTION, "Description", truncate=True)
        if desc:
            rec["description"] = desc[0]
            if desc[1]:
                rec["messages"].append(desc[1])
        rec["prep"] = attempt(parse_prep, cell(row, "prep"))
        rec["available"] = attempt(parse_bool, cell(row, "available"))
        if rec["price"] == 0 and not errors:
            rec["messages"].append("prix à 0")

        if errors:
            rec["status"], rec["messages"] = "error", errors + rec["messages"]
        else:
            ckey, nkey = norm_key(rec["category"]), norm_key(rec["name"])
            if (ckey, nkey) in seen:
                rec["status"] = "duplicate"
                rec["messages"].append("déjà présent plus haut dans le fichier")
            elif (ckey, nkey) in existing_items:
                rec["status"] = "update" if mode == "update" else "exists"
            else:
                cat_is_new = ckey not in existing_cats and ckey not in {norm_key(c) for c in new_cats}
                if new_items >= items_capacity:
                    rec["status"] = "limit"
                    rec["messages"].append("limite de plats de votre formule atteinte")
                elif cat_is_new and len(new_cats) >= cats_capacity:
                    rec["status"] = "limit"
                    rec["messages"].append("limite de catégories atteinte")
                else:
                    if cat_is_new:
                        new_cats.append(rec["category"])
                    new_items += 1
            seen.add((ckey, nkey))
        rows.append(rec)

    counts = {k: 0 for k in ("new", "update", "exists", "duplicate", "limit", "error")}
    for r in rows:
        counts[r["status"]] += 1
    # Clé « updated » (et non « update ») : « update » est une méthode des dictionnaires, ce qui
    # casserait l'affichage {{ summary.update }} dans le gabarit.
    summary = {"new": counts["new"], "updated": counts["update"], "exists": counts["exists"],
               "duplicate": counts["duplicate"], "limit": counts["limit"], "error": counts["error"],
               "total": len(rows), "importable": counts["new"] + counts["update"]}
    return {"rows": rows, "categories_new": new_cats, "summary": summary}


# ==========================================================================
# Modèles à télécharger
# ==========================================================================
SAMPLE_ROWS = [
    ("Entrées", "Salade malienne", 1500, "Tomates, concombre, oignons, vinaigrette", 8, "oui"),
    ("Plats", "Riz au gras", 2500, "Riz, viande de bœuf, légumes", 20, "oui"),
    ("Plats", "Tô + sauce gombo", 2000, "", 15, "oui"),
    ("Boissons", "Bissap", 500, "Jus d'hibiscus maison", 2, "oui"),
    ("Boissons", "Gingembre", 500, "", 2, "non"),
]
HEADER = ("Catégorie", "Plat", "Prix", "Description", "Préparation (min)", "Disponible")


def csv_template():
    buf = io.StringIO()
    buf.write("\ufeff")                                  # Excel reconnaît l'UTF-8 (accents)
    writer = csv.writer(buf, delimiter=";")
    writer.writerow(HEADER)
    for row in SAMPLE_ROWS:
        writer.writerow(row)
    return buf.getvalue().encode("utf-8")


def _esc(text):
    return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;"))


def _sheet_xml(rows, widths, bold_first=True):
    out = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
           '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">', "<cols>"]
    for i, w in enumerate(widths, 1):
        out.append(f'<col min="{i}" max="{i}" width="{w}" customWidth="1"/>')
    out.append("</cols><sheetData>")
    for r, row in enumerate(rows, 1):
        out.append(f'<row r="{r}">')
        for c, value in enumerate(row):
            ref = f"{chr(65 + c)}{r}"
            style = ' s="1"' if (bold_first and r == 1) else ""
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                out.append(f'<c r="{ref}"{style}><v>{value}</v></c>')
            else:
                out.append(f'<c r="{ref}"{style} t="inlineStr"><is><t xml:space="preserve">{_esc(value)}</t></is></c>')
        out.append("</row>")
    out.append("</sheetData></worksheet>")
    return "".join(out)


def xlsx_template():
    instructions = [
        ("Comment remplir le modèle",),
        ("1. Dans la feuille « Menu », remplacez les exemples par vos plats (une ligne par plat).",),
        ("2. Catégorie : Entrées, Plats, Boissons… (une catégorie absente est créée automatiquement).",),
        ("3. Prix : en francs entiers, par exemple 2500 (pas de centimes).",),
        ("4. Préparation : minutes (1 à 180). Disponible : oui ou non. Ces deux colonnes sont facultatives.",),
        ("5. Enregistrez le fichier, puis envoyez-le dans MenuFlash : Boutique > Importer un menu.",),
        ("6. Vous verrez un aperçu : rien n'est enregistré tant que vous n'avez pas confirmé.",),
        ("Maximum : 500 lignes. Les photos et les allergènes s'ajoutent ensuite, plat par plat.",),
    ]
    files = {
        "[Content_Types].xml": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            '<Override PartName="/xl/worksheets/sheet2.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
            "</Types>",
        "_rels/.rels": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            "</Relationships>",
        "xl/workbook.xml": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
            '<sheet name="Menu" sheetId="1" r:id="rId1"/><sheet name="Mode d\'emploi" sheetId="2" r:id="rId2"/>'
            "</sheets></workbook>",
        "xl/_rels/workbook.xml.rels": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet2.xml"/>'
            '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
            "</Relationships>",
        "xl/styles.xml": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font>'
            '<font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
            '<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>'
            '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
            '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
            '<cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
            '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/></cellXfs>'
            "</styleSheet>",
        "xl/worksheets/sheet1.xml": _sheet_xml([HEADER] + [list(r) for r in SAMPLE_ROWS], [18, 28, 10, 44, 18, 12]),
        "xl/worksheets/sheet2.xml": _sheet_xml([list(r) for r in instructions], [110]),
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


# ==========================================================================
# Branchement
# ==========================================================================
def init_menu_import(app, *, owner_required, active_restaurant, log_menu_change, log_audit):
    serializer = URLSafeTimedSerializer(app.config["SECRET_KEY"], salt="mf-menu-import")
    app.jinja_env.globals["mf_menu_import"] = True          # affiche les liens « Importer » (navbar, tableau de bord)

    def capacities(restaurant):
        """Nombre de plats et de catégories encore ajoutables."""
        max_items, max_cats = 300, 50
        try:
            from security_plus import MAX_CATEGORIES_PER_RESTAURANT, MAX_ITEMS_PER_RESTAURANT
            max_items, max_cats = MAX_ITEMS_PER_RESTAURANT, MAX_CATEGORIES_PER_RESTAURANT
        except Exception:
            pass
        try:
            from plans import plan_limit
            plan_items = plan_limit(restaurant, "max_menu_items")
            if plan_items:
                max_items = min(max_items, plan_items)
        except Exception:
            db.session.rollback()
        n_items = MenuItem.query.filter_by(restaurant_id=restaurant.id).count()
        n_cats = MenuCategory.query.filter_by(restaurant_id=restaurant.id).count()
        return max(0, max_items - n_items), max(0, max_cats - n_cats), max_items

    def existing_sets(restaurant):
        cats = MenuCategory.query.filter_by(restaurant_id=restaurant.id).all()
        cat_norm = {c.id: norm_key(c.name) for c in cats}
        items = MenuItem.query.filter_by(restaurant_id=restaurant.id).all()
        return ({norm_key(c.name) for c in cats},
                {(cat_norm.get(i.category_id, ""), norm_key(i.name)) for i in items})

    def back(endpoint="admin_menu_import"):
        return redirect(url_for(endpoint))

    # ------------------------------------------------------------------ pages
    def admin_menu_import():
        restaurant = active_restaurant()
        free_items, free_cats, max_items = capacities(restaurant)
        return render_template("menu_import.html", restaurant=restaurant, free_items=free_items,
                               max_items=max_items, max_rows=MAX_ROWS)

    def template_csv():
        return Response(csv_template(), mimetype="text/csv; charset=utf-8",
                        headers={"Content-Disposition": 'attachment; filename="modele_menu_menuflash.csv"',
                                 "Cache-Control": "no-store"})

    def template_xlsx():
        return Response(xlsx_template(),
                        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": 'attachment; filename="modele_menu_menuflash.xlsx"',
                                 "Cache-Control": "no-store"})

    def admin_menu_import_preview():
        restaurant = active_restaurant()
        mode = "update" if request.form.get("mode") == "update" else "skip"
        f = request.files.get("file")
        if not f or not f.filename:
            flash("Choisissez un fichier à importer.", "error")
            return back()
        try:
            data = f.read(MAX_BYTES + 1)
            table = read_table(f.filename, data)
            free_items, free_cats, max_items = capacities(restaurant)
            cats, items = existing_sets(restaurant)
            result = analyze(table, cats, items, free_items, free_cats, mode)
        except ImportFileError as exc:
            flash(str(exc), "error")
            return back()
        except Exception:
            log.exception("Import de menu : lecture impossible")
            flash("Ce fichier n'a pas pu être lu. Utilisez le modèle fourni.", "error")
            return back()
        summary = result["summary"]
        if not summary["total"]:
            flash("Aucune ligne de plat trouvée sous l'en-tête.", "error")
            return back()
        payload = {"r": restaurant.id, "u": current_user.id, "mode": mode,
                   "rows": [[r["category"], r["name"], r["price"], r["description"], r["prep"], r["available"]]
                            for r in result["rows"] if r["status"] in ("new", "update")]}
        token = serializer.dumps(payload) if payload["rows"] else None
        log_audit("menu_import_preview", success=True, restaurant_id=restaurant.id,
                  user_email=current_user.email,
                  details=f"{summary['total']} ligne(s) : {summary['importable']} importable(s), "
                          f"{summary['error']} erreur(s)")
        return render_template("menu_import_preview.html", restaurant=restaurant, result=result, mode=mode,
                               token=token, filename=f.filename[:80], free_items=free_items, max_items=max_items)

    def admin_menu_import_confirm():
        restaurant = active_restaurant()
        try:
            data = serializer.loads(request.form.get("token", ""), max_age=TOKEN_MAX_AGE)
        except SignatureExpired:
            flash("L'aperçu a expiré (30 minutes). Recommencez l'import.", "error")
            return back()
        except BadSignature:
            flash("Aperçu invalide. Recommencez l'import.", "error")
            return back()
        if data.get("r") != restaurant.id or data.get("u") != current_user.id:
            abort(403)
        mode = data.get("mode") if data.get("mode") in ("skip", "update") else "skip"
        free_items, free_cats, _ = capacities(restaurant)

        cats = {norm_key(c.name): c for c in MenuCategory.query.filter_by(restaurant_id=restaurant.id).all()}
        cat_by_id = {c.id: k for k, c in cats.items()}
        items = {(cat_by_id.get(i.category_id, ""), norm_key(i.name)): i
                 for i in MenuItem.query.filter_by(restaurant_id=restaurant.id).all()}
        next_cat_pos = (db.session.query(db.func.max(MenuCategory.position))
                        .filter_by(restaurant_id=restaurant.id).scalar() or 0)
        positions = {}
        counters = {"items": 0, "updated": 0, "categories": 0, "exists": 0, "limit": 0}
        try:
            for category, name, price, description, prep, available in data.get("rows", []):
                ckey, nkey = norm_key(category), norm_key(name)
                item = items.get((ckey, nkey))
                if item is not None:
                    if mode != "update":
                        counters["exists"] += 1
                        continue
                    item.price = int(price)
                    if description:
                        item.description = description
                    if prep:
                        item.prep_minutes = int(prep)
                    if available is not None:
                        item.is_available = bool(available)
                    counters["updated"] += 1
                    continue
                if counters["items"] >= free_items:
                    counters["limit"] += 1
                    continue
                cat = cats.get(ckey)
                if cat is None:
                    if counters["categories"] >= free_cats:
                        counters["limit"] += 1
                        continue
                    next_cat_pos += 1
                    cat = MenuCategory(restaurant_id=restaurant.id, name=category, position=next_cat_pos)
                    db.session.add(cat)
                    db.session.flush()
                    cats[ckey] = cat
                    cat_by_id[cat.id] = ckey
                    counters["categories"] += 1
                if cat.id not in positions:
                    positions[cat.id] = (db.session.query(db.func.max(MenuItem.position))
                                         .filter_by(category_id=cat.id).scalar() or 0)
                positions[cat.id] += 1
                item = MenuItem(restaurant_id=restaurant.id, category_id=cat.id, name=name,
                                description=description or None, price=int(price),
                                prep_minutes=int(prep) if prep else DEFAULT_PREP_MINUTES,
                                is_available=True if available is None else bool(available),
                                position=positions[cat.id])
                db.session.add(item)
                items[(ckey, nkey)] = item
                counters["items"] += 1
            parts = []
            if counters["items"]:
                parts.append(f"{counters['items']} plat(s) ajouté(s)")
            if counters["updated"]:
                parts.append(f"{counters['updated']} mis à jour")
            if counters["categories"]:
                parts.append(f"{counters['categories']} catégorie(s) créée(s)")
            summary = ("Import de menu depuis un fichier : " + ", ".join(parts) + ".") if parts else None
            if summary:
                log_menu_change(restaurant.id, current_user, "menu_imported", summary[:300])
            db.session.commit()
        except Exception:
            db.session.rollback()
            log.exception("Import de menu impossible (restaurant %s)", restaurant.id)
            flash("L'import a échoué : aucune modification n'a été faite.", "error")
            return back()
        log_audit("menu_import", success=True, restaurant_id=restaurant.id, user_email=current_user.email,
                  details=f"{counters['items']} ajouté(s), {counters['updated']} mis à jour, "
                          f"{counters['categories']} catégorie(s), {counters['exists']} déjà présent(s), "
                          f"{counters['limit']} refusé(s) (limite)")
        if not (counters["items"] or counters["updated"]):
            flash("Rien à importer : tous les plats existent déjà.", "success")
        else:
            flash("Menu importé : " + ", ".join(parts) + ". Ajoutez maintenant les photos de vos plats.", "success")
        if counters["limit"]:
            flash(f"{counters['limit']} ligne(s) ignorée(s) : limite de votre formule atteinte.", "error")
        return redirect(url_for("admin_dashboard"))

    app.add_url_rule("/admin/menu/import", "admin_menu_import", owner_required(admin_menu_import))
    app.add_url_rule("/admin/menu/import/modele.csv", "admin_menu_import_template_csv",
                     owner_required(template_csv))
    app.add_url_rule("/admin/menu/import/modele.xlsx", "admin_menu_import_template_xlsx",
                     owner_required(template_xlsx))
    app.add_url_rule("/admin/menu/import/apercu", "admin_menu_import_preview",
                     owner_required(admin_menu_import_preview), methods=["POST"])
    app.add_url_rule("/admin/menu/import/confirmer", "admin_menu_import_confirm",
                     owner_required(admin_menu_import_confirm), methods=["POST"])
