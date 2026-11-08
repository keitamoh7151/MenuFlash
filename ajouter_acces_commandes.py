"""
Branche l'accès sécurisé aux commandes (order_access.py) dans votre projet.

Usage, dans le dossier du projet (là où se trouve app.py) :

    python ajouter_acces_commandes.py

Il fait 3 retouches précises et ne touche à rien d'autre :
  1. app.py                  2 lignes de branchement, avant « from hardening import init_hardening »
  2. templates/base.html     la version du script order_edit.js passe à ?v=3 (les navigateurs rechargent le nouveau)
  3. qr_security.py          (si installé) correction d'un défaut : une table nommée « 1 » ou « 2 » donnait le lien
                             d'une AUTRE table ; voir LISEZMOI

- Sans danger : relancé, il répond « déjà fait » et ne change rien.
- Garde le style de fin de ligne de chaque fichier (Windows ou Linux).
- Copie de sécurité de chaque fichier modifié : <fichier>.avant_acces_commandes
- Si un repère est introuvable (fichier modifié à la main), il ne touche pas au fichier et vous dit quoi ajouter.

Il faut aussi remplacer static/order_edit.js par celui du zip (déjà fait si vous avez décompressé le zip dans le projet).
"""
import os
import re
import shutil
import sys

SUFFIX = ".avant_acces_commandes"
APP_BLOCK = [
    "# --- Accès aux commandes : une adresse modifiée ramène le client sur sa page, sans message (order_access.py) ---",
    "from order_access import init_order_access",
    "init_order_access(app, log_audit=log_audit)",
    "",
]
TAG_VERSION = re.compile(r"(filename=['\"]order_edit\.js['\"]\s*\)\s*\}\}\?v=)(\d+)")
QR_OLD = "        if isinstance(value, int) or (isinstance(value, str) and value.isdigit()):"
QR_NEW = "        if isinstance(value, int):"
VERSION = 3

results = []


def read(path):
    raw = open(path, "rb").read().decode("utf-8")
    return raw.replace("\r\n", "\n"), ("\r\n" if "\r\n" in raw else "\n")


def write(path, text, eol):
    if not os.path.exists(path + SUFFIX):
        shutil.copyfile(path, path + SUFFIX)
    open(path, "wb").write(text.replace("\n", eol).encode("utf-8"))


def report(name, status, manual=None):
    results.append((name, status))
    print(f"{name} : {status}")
    if manual:
        print(manual)


def patch_app():
    path = "app.py"
    if not os.path.exists(path):
        return report(path, "INTROUVABLE (lancez le script dans le dossier du projet)")
    text, eol = read(path)
    if "init_order_access" in text:
        return report(path, "déjà fait")
    lines = text.split("\n")
    index = None
    for anchor in ("from hardening import init_hardening", 'if __name__ == "__main__":'):
        index = next((i for i, l in enumerate(lines) if l.strip() == anchor), None)
        if index is not None:
            break
    if index is None:
        return report(path, "REPÈRE INTROUVABLE", "Ajoutez à la main, en bas de app.py :\n" + "\n".join(APP_BLOCK))
    lines[index:index] = APP_BLOCK
    write(path, "\n".join(lines), eol)
    report(path, "branchement ajouté")


def patch_base():
    path = os.path.join("templates", "base.html")
    if not os.path.exists(path):
        return report(path, "INTROUVABLE")
    text, eol = read(path)
    match = TAG_VERSION.search(text)
    if not match:
        return report(path, "script order_edit.js absent (« Modifier la commande » non installé) : rien à faire")
    if int(match.group(2)) >= VERSION:
        return report(path, "déjà fait")
    write(path, TAG_VERSION.sub(lambda m: m.group(1) + str(VERSION), text, count=1), eol)
    report(path, f"version du script passée à ?v={VERSION}")


def patch_qr_security():
    path = "qr_security.py"
    if not os.path.exists(path):
        return report(path, "absent (QR sécurisés non installés) : rien à faire")
    text, eol = read(path)
    if QR_OLD not in text and QR_NEW in text:
        return report(path, "déjà fait")
    if QR_OLD not in text:
        return report(path, "REPÈRE INTROUVABLE",
                      "Dans _qr_links_carry_the_code, remplacez la condition par :  if isinstance(value, int):\n"
                      "(une table nommée « 1 » ne doit pas être prise pour un numéro)")
    write(path, text.replace(QR_OLD, QR_NEW, 1), eol)
    report(path, "tables nommées par des chiffres corrigées")


if __name__ == "__main__":
    for step in (patch_app, patch_base, patch_qr_security):
        step()
    sys.exit(1 if any("INTROUVABLE" in s and "absent" not in s and "app.py" not in n for n, s in results) else 0)
