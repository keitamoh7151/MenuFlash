"""
MenuFlash — Réparation des accents et caractères spéciaux abîmés (« ConfidentialitÃ© » au lieu de « Confidentialité »).

CAUSE
-----
La commande PowerShell que je vous avais donnée pour changer le numéro de version de app.js :
    (Get-Content templates\\base.html) -replace ... | Set-Content templates\\base.html -Encoding UTF8
relit le fichier avec la page de code ANSI de Windows au lieu d'UTF-8 (Windows PowerShell 5.1), puis le réécrit en
UTF-8 : chaque caractère accentué est encodé DEUX fois (é -> Ã©, · -> Â·, — -> â€”). Elle ajoute aussi un BOM (3
octets invisibles en début de fichier). Fichiers touchés : templates/base.html et templates/signup.html.
Cette commande n'est plus jamais nécessaire : les scripts de correction changent déjà les numéros de version.

CE QUE FAIT LE SCRIPT
---------------------
Il parcourt templates/, static/ (.html .js .css) et les .py du dossier, et pour chaque fichier :
  1. retire le BOM de début de fichier ;
  2. retrouve chaque séquence abîmée (Ã©, Â·, â€”, ðŸ†•…) et la remplace par le caractère d'origine, uniquement
     quand la séquence est un UTF-8 valide (un texte correct n'est jamais modifié) ;
  3. conserve les fins de ligne du fichier (CRLF ou LF) ;
  4. écrit une copie de sécurité *.avant-encodage avant toute modification.
Il peut être relancé sans risque : un fichier déjà propre n'est pas touché.

UTILISATION (à la racine du projet, là où se trouve app.py)
    python reparer_accents.py --simuler     montre ce qui serait réparé, sans rien écrire
    python reparer_accents.py               répare
    python reparer_accents.py --verifier    code de sortie 0 si tout est propre
"""
import os
import re
import shutil
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
SUFFIX = ".avant-encodage"
EXTENSIONS = (".html", ".js", ".css", ".py")
SKIP_DIRS = {".git", "venv", ".venv", "__pycache__", "node_modules", ".vscode"}

# Table « octet -> caractère » de Windows-1252, avec les 5 octets non définis (0x81 0x8D 0x8F 0x90 0x9D)
# convertis en caractère de même numéro, comme le fait Windows PowerShell.
BYTE_TO_CHAR = {}
for _b in range(256):
    try:
        BYTE_TO_CHAR[_b] = bytes([_b]).decode("cp1252")
    except UnicodeDecodeError:
        BYTE_TO_CHAR[_b] = chr(_b)
CHAR_TO_BYTE = {c: b for b, c in BYTE_TO_CHAR.items() if b >= 0x80}
HIGH = "".join(sorted(CHAR_TO_BYTE))
# Une séquence abîmée commence forcément par l'image d'un octet de tête UTF-8 (0xC2 à 0xF4)
RUN_RE = re.compile("[" + re.escape(HIGH) + "]+")


def _utf8_len(lead):
    if 0xC2 <= lead <= 0xDF:
        return 2
    if 0xE0 <= lead <= 0xEF:
        return 3
    if 0xF0 <= lead <= 0xF4:
        return 4
    return 0


def repair_run(run):
    """Décode, dans une suite de caractères « hauts », chaque séquence qui forme un UTF-8 valide."""
    out, i, changed = [], 0, 0
    while i < len(run):
        lead = CHAR_TO_BYTE[run[i]]
        n = _utf8_len(lead)
        if n and i + n <= len(run):
            chunk = bytes(CHAR_TO_BYTE[c] for c in run[i:i + n])
            try:
                decoded = chunk.decode("utf-8")
            except UnicodeDecodeError:
                decoded = None
            if decoded and decoded.isprintable() or decoded in ("\u00a0", "\u00ad"):
                out.append(decoded)
                i += n
                changed += 1
                continue
        out.append(run[i])
        i += 1
    return "".join(out), changed


def repair_text(text):
    count = [0]

    def sub(match):
        fixed, n = repair_run(match.group(0))
        count[0] += n
        return fixed
    return RUN_RE.sub(sub, text), count[0]


def candidate_files():
    for folder, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in files:
            if name.endswith(EXTENSIONS) and SUFFIX not in name and ".avant" not in name \
                    and name != os.path.basename(__file__):          # ne s'analyse pas lui-même (exemples dans sa documentation)
                yield os.path.join(folder, name)


def analyse(path):
    raw = open(path, "rb").read()
    had_bom = raw.startswith(b"\xef\xbb\xbf")
    if had_bom:
        raw = raw[3:]
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
    fixed, n = repair_text(text)
    return had_bom, n, fixed.encode("utf-8"), text


def main():
    if not os.path.exists(os.path.join(ROOT, "app.py")):
        sys.exit("ARRÊT : lancez le script à la racine du projet (là où se trouve app.py).")
    simulate = "--simuler" in sys.argv
    check = "--verifier" in sys.argv
    touched, scanned, skipped = [], 0, []
    for path in sorted(candidate_files()):
        scanned += 1
        result = analyse(path)
        rel = os.path.relpath(path, ROOT)
        if result is None:
            skipped.append(rel)
            continue
        had_bom, n, new_bytes, _ = result
        if n == 0 and not had_bom:
            continue
        touched.append((rel, n, had_bom))
        if not (simulate or check):
            shutil.copy2(path, path + SUFFIX)
            with open(path, "wb") as fh:
                fh.write(new_bytes)
    if skipped:
        print("Fichiers non lisibles en UTF-8 (laissés intacts) : " + ", ".join(skipped))
    if not touched:
        print(f"Aucun caractère abîmé ni BOM : {scanned} fichiers vérifiés, tout est propre.")
        sys.exit(0)
    verb = "à réparer" if (simulate or check) else "réparé"
    for rel, n, had_bom in touched:
        print(f"  {verb} : {rel}  ({n} caractère(s) abîmé(s)" + (", BOM retiré" if had_bom else "") + ")")
    if simulate or check:
        print(f"\n{len(touched)} fichier(s) à réparer sur {scanned}. Lancez :  python reparer_accents.py")
        sys.exit(1 if check else 0)
    print(f"\n{len(touched)} fichier(s) réparé(s). Copies : *{SUFFIX} (à ne pas pousser sur Git).")
    print("Contrôle :  python reparer_accents.py --verifier")


if __name__ == "__main__":
    main()
