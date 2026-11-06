"""
Branche « Modifier la commande » (order_edit.py + static/order_edit.js) dans app.py et base.html.

Usage, dans le dossier du projet (là où se trouve app.py) :

    python ajouter_modifier_commande.py

- Sans danger : relancé, il répond « Déjà branché » et ne change rien.
- Si vous aviez installé la version précédente (« Retirer un plat »), il la met à jour : même branchement
  dans app.py, et la version du script dans base.html passe à ?v=2 pour que les navigateurs rechargent le nouveau.
- Il ne touche à rien d'autre (vos autres modifications sont conservées) et garde le style de fin de ligne
  de chaque fichier (Windows ou Linux).
- Copies de sécurité : app.py.avant_modifier_commande et templates/base.html.avant_modifier_commande
"""
import os
import re
import shutil
import sys

APP = "app.py"
BASE = os.path.join("templates", "base.html")
SUFFIX = ".avant_modifier_commande"
VERSION = "2"

APP_BLOCK = [
    "# --- Modifier la commande : le client retire, ajoute ou change un plat (order_edit.py) ---",
    "from order_edit import init_order_edit",
    "init_order_edit(app, limiter=limiter)",
    "",
]
SCRIPT_TAG = "  <script src=\"{{ url_for('static', filename='order_edit.js') }}?v=" + VERSION + "\"></script>"
TAG_VERSION = re.compile(r"(filename=['\"]order_edit\.js['\"]\s*\)\s*\}\}\?v=)\d+")


def read(path):
    text = open(path, "rb").read().decode("utf-8")
    return text, ("\r\n" if "\r\n" in text else "\n")


def patch_app():
    if not os.path.exists(APP):
        print("app.py introuvable : lancez ce script dans le dossier du projet.")
        return False
    text, eol = read(APP)
    if "init_order_edit" in text:
        print("app.py : déjà branché.")
        return True
    lines = text.split(eol)
    index = None
    for anchor in ("from hardening import init_hardening", 'if __name__ == "__main__":'):
        index = next((i for i, l in enumerate(lines) if l.strip() == anchor), None)
        if index is not None:
            break
    if index is None:
        print("app.py : je n'ai pas trouvé où insérer. Ajoutez à la main, en bas de app.py :")
        print("\n".join(APP_BLOCK))
        return False
    shutil.copyfile(APP, APP + SUFFIX)
    lines[index:index] = APP_BLOCK
    open(APP, "wb").write(eol.join(lines).encode("utf-8"))
    print("app.py : branchement ajouté.")
    return True


def patch_base():
    if not os.path.exists(BASE):
        print("templates/base.html introuvable.")
        return False
    text, eol = read(BASE)
    if "order_edit.js" in text:
        new_text, n = TAG_VERSION.subn(r"\g<1>" + VERSION, text)
        if new_text == text:
            print("base.html : déjà branché (version à jour).")
            return True
        shutil.copyfile(BASE, BASE + SUFFIX)
        open(BASE, "wb").write(new_text.encode("utf-8"))
        print("base.html : version du script passée à ?v=" + VERSION + " (mise à jour de la version précédente).")
        return True
    lines = text.split(eol)
    index = next((i for i, l in enumerate(lines) if "filename='takeaway.js'" in l or 'filename="takeaway.js"' in l), None)
    if index is not None:
        index += 1                                              # juste après takeaway.js
    else:
        index = next((i for i, l in enumerate(lines) if l.strip().lower() == "</body>"), None)
    if index is None:
        print("base.html : je n'ai pas trouvé où insérer. Ajoutez avant </body> :")
        print(SCRIPT_TAG)
        return False
    shutil.copyfile(BASE, BASE + SUFFIX)
    lines.insert(index, SCRIPT_TAG)
    open(BASE, "wb").write(eol.join(lines).encode("utf-8"))
    print("base.html : script ajouté.")
    return True


if __name__ == "__main__":
    ok = patch_app() and patch_base()
    sys.exit(0 if ok else 1)
