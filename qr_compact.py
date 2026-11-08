"""
MenuFlash Mini — QR codes PETITS (autocollants) pour les tables.

Branchement dans app.py, JUSTE AVANT « from hardening import init_hardening » :

    from qr_compact import init_qr_compact
    init_qr_compact(app, owner_required=owner_required, active_restaurant=active_restaurant)

Problème résolu
---------------
Le « Chevalet PDF » était une carte A6 (105 x 148 mm) avec un QR code de 68 mm :
trop grand pour une table de restaurant. Les boutons « PDF » des pages Codes QR
produisent maintenant un PETIT AUTOCOLLANT par défaut.

Tailles (paramètre ?taille=...)
-------------------------------
  mini       34 x 44 mm, QR 26 mm   le plus discret (à scanner de près)
  petit      40 x 50 mm, QR 30 mm   PAR DÉFAUT : petit et fiable
  moyen      55 x 62 mm, QR 42 mm   pour une table large ou une lecture à distance
  chevalet   ancien format A6 (inchangé)

Le PDF est une page A4 qui contient des étiquettes à découper (traits de coupe
fins) : une page A4 ne se redimensionne pas à l'impression, alors qu'un PDF de
la taille exacte de l'autocollant serait agrandi par « Ajuster à la page ».
Une règle de contrôle de 5 cm figure en bas de la feuille pour vérifier que
l'impression est bien à 100 %.

  - Un seul code     : /m/<slug>/qrcodes/<id>.pdf      (?copies=1 à 40, 2 par défaut)
  - Code général     : /m/<slug>/qrcode.pdf
  - TOUTES les tables: /admin/qrcodes/planche.pdf      (gestionnaire connecté)
                       ?taille=petit  ?general=1 (ajoute le QR général)

Le QR est dessiné en vectoriel (net à toute taille d'impression) avec correction
d'erreur M (résiste à une petite salissure ou une rayure). Un module ne descend
jamais sous 0,7 mm : si l'adresse est longue (donc le QR plus dense), le QR
s'agrandit automatiquement au minimum nécessaire pour rester lisible.

Aucune nouvelle dépendance (reportlab et qrcode sont déjà utilisés).
"""
import io
import logging
import re
from functools import wraps

import qrcode
from qrcode.constants import ERROR_CORRECT_M
from reportlab.lib.colors import HexColor, black, white
from reportlab.lib.pagesizes import A4, mm
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

log = logging.getLogger("menuflash.qr_compact")

# (largeur carte, hauteur carte, taille du QR) en mm
SIZES = {
    "mini": (34, 44, 26),
    "petit": (40, 50, 30),
    "moyen": (55, 62, 42),
}
SIZE_ORDER = ["mini", "petit", "moyen"]
DEFAULT_SIZE = "petit"
MIN_MODULE_MM = 0.7          # un module (petit carré du QR) ne doit pas être plus petit
PAGE_MARGIN = 10 * mm
GAP = 3 * mm
FOOTER = 14 * mm             # bande basse : règle de contrôle et conseil
MAX_COPIES = 40
MAX_CODES = 300

INK = HexColor("#111111")
GRAY = HexColor("#8A8A85")
CUT = HexColor("#B8B6B0")


def _latin(text, limit=60):
    """Texte compatible avec les polices PDF standard (Latin-1) : pas de caractère exotique."""
    text = re.sub(r"\s+", " ", str(text or "")).strip()[:limit]
    return text.encode("cp1252", "replace").decode("cp1252")


def _fit(text, font, size, max_width, min_size=4.5):
    """Réduit la taille de police jusqu'à ce que le texte tienne ; coupe avec « … » en dernier recours."""
    while size > min_size and stringWidth(text, font, size) > max_width:
        size -= 0.25
    if stringWidth(text, font, size) > max_width:
        while len(text) > 1 and stringWidth(text + "…", font, size) > max_width:
            text = text[:-1]
        text += "…"
    return text, size


def _matrix(url):
    qr = qrcode.QRCode(error_correction=ERROR_CORRECT_M, border=0, box_size=1)
    qr.add_data(url)
    qr.make(fit=True)
    return qr.get_matrix()


def qr_min_size_mm(url):
    """Plus petite taille de QR (mm) qui respecte la taille minimale d'un module."""
    return len(_matrix(url)) * MIN_MODULE_MM


def _draw_qr(c, url, x, y, size):
    """Dessine le QR code en vectoriel : (x, y) = coin bas-gauche, size = côté, en points."""
    m = _matrix(url)
    n = len(m)
    cell = size / n
    path = c.beginPath()
    for row_i, row in enumerate(m):
        yy = y + size - (row_i + 1) * cell
        col = 0
        while col < n:
            if row[col]:
                start = col
                while col < n and row[col]:
                    col += 1
                path.rect(x + start * cell, yy, (col - start) * cell, cell)
            else:
                col += 1
    c.setFillColor(black)
    c.drawPath(path, stroke=0, fill=1)


PAD_MM = 2.0
BOTTOM_MM = 9.0              # hauteur réservée aux textes du bas (légende + libellé de table)


def _name_size(w_mm):
    return 7.5 if w_mm > 35 else 6.5


def avail_qr_mm(w_mm, h_mm):
    """Côté maximal du QR (mm) qui tient dans une carte, textes compris."""
    name_h = _name_size(w_mm) * 0.8 / 72 * 25.4
    top = PAD_MM + name_h + 1.4 + 1.2
    bottom = BOTTOM_MM + PAD_MM
    return min(w_mm - 3.0, h_mm - top - bottom)


def pick_size(requested, urls):
    """Format demandé, ou le format supérieur si un QR (adresse longue = QR plus dense) ne tient pas."""
    need = max(qr_min_size_mm(u) for u in urls) if urls else 0
    start = SIZE_ORDER.index(requested)
    for key in SIZE_ORDER[start:]:
        w, h, qr = SIZES[key]
        if max(qr, need) <= avail_qr_mm(w, h) + 1e-6:
            return key
    return SIZE_ORDER[-1]


def _card(c, x, y, w, h, qr_mm, url, name, label, caption, accent):
    """Une étiquette : (x, y) = coin bas-gauche, tailles en points."""
    # Traits de coupe : cadre fin gris, arrondi.
    c.setStrokeColor(CUT)
    c.setLineWidth(0.4)
    c.setDash(1.5, 1.5)
    c.roundRect(x, y, w, h, 2.2 * mm, stroke=1, fill=0)
    c.setDash()

    pad = PAD_MM * mm
    inner = w - 2 * pad
    qr_pt = qr_mm * mm
    cx = x + w / 2

    # Nom du restaurant (haut)
    nm, nm_size = _fit(_latin(name, 40), "Helvetica-Bold", 7.5 if w > 35 * mm else 6.5, inner)
    top = y + h - pad
    c.setFillColor(INK)
    c.setFont("Helvetica-Bold", nm_size)
    c.drawCentredString(cx, top - nm_size * 0.8, nm)

    # Filet de couleur
    line_y = top - nm_size * 0.8 - 1.4 * mm
    c.setStrokeColor(accent)
    c.setLineWidth(0.9)
    c.line(cx - 5 * mm, line_y, cx + 5 * mm, line_y)

    # QR code, centré dans l'espace entre le filet et les textes du bas
    bottom_block = (BOTTOM_MM if label else 5.4) * mm + pad
    area_top = line_y - 1.2 * mm
    area_bottom = y + bottom_block
    qr_y = area_bottom + max(0, (area_top - area_bottom - qr_pt) / 2)
    _draw_qr(c, url, cx - qr_pt / 2, qr_y, qr_pt)

    # Textes du bas : légende puis libellé de table
    cap_text = _latin(caption, 50)
    cap_start = 5.6 if w > 35 * mm else 5.0
    if stringWidth(cap_text, "Helvetica", 4.5) > inner and " · " in cap_text:
        cap_text = cap_text.split(" · ")[0]          # carte étroite : on garde la partie française
    cap, cap_size = _fit(cap_text, "Helvetica", cap_start, inner)
    c.setFillColor(GRAY)
    c.setFont("Helvetica", cap_size)
    c.drawCentredString(cx, y + pad + (4.6 * mm if label else 0.4 * mm), cap)
    if label:
        lb, lb_size = _fit(_latin(label, 40), "Helvetica-Bold", 9.5 if w > 35 * mm else 8, inner)
        c.setFillColor(INK)
        c.setFont("Helvetica-Bold", lb_size)
        c.drawCentredString(cx, y + pad + 0.3 * mm, lb)


def _footer(c, taille_txt):
    """Règle de contrôle de 5 cm + consigne d'impression."""
    x0, y0 = PAGE_MARGIN, 7 * mm
    c.setStrokeColor(black)
    c.setLineWidth(0.6)
    c.line(x0, y0, x0 + 50 * mm, y0)
    for i in range(0, 51, 10):
        c.line(x0 + i * mm, y0 - 1.2 * mm, x0 + i * mm, y0 + 1.2 * mm)
    c.setFillColor(GRAY)
    c.setFont("Helvetica", 6.5)
    c.drawString(x0 + 53 * mm, y0 - 0.8 * mm,
                 "Cette ligne doit mesurer 5 cm. Sinon, imprimez à 100 % (taille réelle), "
                 "sans « Ajuster à la page ».")
    c.drawString(x0, y0 + 3.2 * mm, f"MenuFlash · autocollants {taille_txt} · à découper sur les traits.")


def make_sheet_pdf(items, size_key=DEFAULT_SIZE, restaurant_name="", accent_hex="#D98E1F"):
    """
    items : liste de dicts {url, label, caption}. Renvoie un PDF A4 (BytesIO) avec autant de
    pages que nécessaire.
    """
    size_key = pick_size(size_key, [i["url"] for i in items])
    w_mm, h_mm, qr_mm = SIZES[size_key]
    accent = HexColor(accent_hex)
    page_w, page_h = A4
    card_w, card_h = w_mm * mm, h_mm * mm

    cols = max(1, int((page_w - 2 * PAGE_MARGIN + GAP) // (card_w + GAP)))
    rows = max(1, int((page_h - 2 * PAGE_MARGIN - FOOTER + GAP) // (card_h + GAP)))
    per_page = cols * rows
    grid_w = cols * card_w + (cols - 1) * GAP
    left = (page_w - grid_w) / 2
    top = page_h - PAGE_MARGIN

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setTitle(f"QR codes — {_latin(restaurant_name, 60)}")
    c.setAuthor("MenuFlash")

    for i, item in enumerate(items):
        if i and i % per_page == 0:
            _footer(c, size_key)
            c.showPage()
        k = i % per_page
        r, col = divmod(k, cols)
        x = left + col * (card_w + GAP)
        y = top - (r + 1) * card_h - r * GAP
        # Le QR ne descend jamais sous la taille minimale lisible.
        qr_size = max(qr_mm, qr_min_size_mm(item["url"]))
        qr_size = min(qr_size, avail_qr_mm(w_mm, h_mm))          # et reste dans la carte
        _card(c, x, y, card_w, card_h, qr_size, item["url"], restaurant_name,
              item.get("label"), item.get("caption", "Scannez pour commander · Scan to order"), accent)
    _footer(c, size_key)
    c.showPage()
    c.save()
    buf.seek(0)
    return buf


# ==========================================================================
# Branchement Flask
# ==========================================================================
def _safe_name(text):
    return re.sub(r"[^A-Za-z0-9_-]+", "-", text or "").strip("-")[:40] or "qr"


def _accent(restaurant):
    colors = getattr(restaurant, "theme_colors", None)
    try:
        value = colors["accent"] if isinstance(colors, dict) else getattr(colors, "accent", None)
    except Exception:
        value = None
    return value if isinstance(value, str) and re.fullmatch(r"#[0-9A-Fa-f]{6}", value) else "#D98E1F"


def init_qr_compact(app, *, owner_required, active_restaurant):
    from flask import abort, request, send_file, url_for
    from models import QRCode, Restaurant

    def size_from_request():
        key = (request.args.get("taille") or DEFAULT_SIZE).lower()
        return key if key in SIZES else DEFAULT_SIZE

    def copies_from_request(default=2):
        try:
            return max(1, min(int(request.args.get("copies", default)), MAX_COPIES))
        except (TypeError, ValueError):
            return default

    def restaurant_or_404(slug):
        r = Restaurant.query.filter_by(slug=slug, is_active=True).first()
        if r is None:
            abort(404)
        return r

    def pdf_response(buf, filename):
        resp = send_file(buf, mimetype="application/pdf", download_name=filename, as_attachment=False)
        resp.headers["Cache-Control"] = "no-store"
        return resp

    def general_url(restaurant, table_label=None):
        url = url_for("public_menu", slug=restaurant.slug, _external=True)
        return f"{url}?t={table_label}" if table_label else url

    # ---------- QR code d'une table : remplace le « Chevalet PDF » par un petit autocollant ----------
    original_table = app.view_functions.get("table_qrcode_pdf")
    if original_table:
        @wraps(original_table)
        def table_qrcode_pdf(slug, qr_id):
            if (request.args.get("taille") or "").lower() == "chevalet":
                return original_table(slug, qr_id)
            r = restaurant_or_404(slug)
            qr = QRCode.query.filter_by(id=qr_id, restaurant_id=r.id).first_or_404()
            url = url_for("public_menu", slug=r.slug, qr=qr.id, _external=True)
            item = {"url": url, "label": qr.label, "caption": "Scannez pour commander · Scan to order"}
            buf = make_sheet_pdf([item] * copies_from_request(), size_from_request(), r.name, _accent(r))
            return pdf_response(buf, f"qr-{_safe_name(r.slug)}-{_safe_name(qr.label)}.pdf")
        app.view_functions["table_qrcode_pdf"] = table_qrcode_pdf

    # ---------- QR code général (à emporter) ----------
    original_menu = app.view_functions.get("menu_qrcode_pdf")
    if original_menu:
        @wraps(original_menu)
        def menu_qrcode_pdf(slug):
            if (request.args.get("taille") or "").lower() == "chevalet":
                return original_menu(slug)
            r = restaurant_or_404(slug)
            item = {"url": general_url(r, request.args.get("t")), "label": "À emporter",
                    "caption": "Scannez pour commander · Order takeaway"}
            buf = make_sheet_pdf([item] * copies_from_request(), size_from_request(), r.name, _accent(r))
            return pdf_response(buf, f"qr-{_safe_name(r.slug)}-general.pdf")
        app.view_functions["menu_qrcode_pdf"] = menu_qrcode_pdf

    # ---------- Planche de TOUS les QR codes de table ----------
    def qrcodes_sheet():
        r = active_restaurant()
        if r is None:
            abort(403)
        codes = (QRCode.query.filter_by(restaurant_id=r.id).order_by(QRCode.created_at).limit(MAX_CODES).all())
        items = []
        if request.args.get("general") == "1":
            items.append({"url": general_url(r), "label": "À emporter",
                          "caption": "Scannez pour commander · Order takeaway"})
        for qr in codes:
            items.append({"url": url_for("public_menu", slug=r.slug, qr=qr.id, _external=True),
                          "label": qr.label, "caption": "Scannez pour commander · Scan to order"})
        if not items:
            abort(404)
        buf = make_sheet_pdf(items, size_from_request(), r.name, _accent(r))
        return pdf_response(buf, f"qr-{_safe_name(r.slug)}-toutes-les-tables.pdf")

    app.add_url_rule("/admin/qrcodes/planche.pdf", "admin_qrcodes_sheet",
                     owner_required(qrcodes_sheet), methods=["GET"])

    @app.context_processor
    def _inject_qr_compact():
        # Le template Codes QR affiche les boutons « petit autocollant » seulement si ce module est actif.
        return {"mf_qr_compact": True} if request.endpoint == "admin_qrcodes" else {}
