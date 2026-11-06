"""
Ajoute dans app.py les 2 branchements nécessaires (sa_suite et menu_import).

Usage, dans le dossier du projet (là où se trouve app.py) :

    python ajouter_branchement.py

- Sans danger : relancez-le autant que vous voulez, il n'ajoute rien s'il est déjà fait.
- Il ne touche à rien d'autre dans app.py (vos autres modifications sont conservées) et
  garde le style de fin de ligne du fichier (Windows ou Linux).
- Il fait une copie de sécurité : app.py.avant_sa_suite
"""
import os
import shutil
import sys

PATH = "app.py"
MARK = "init_sa_suite"
BLOCK = [
    "# --- Outils d'exploitation du super-admin et import de menu (sa_suite.py, menu_import.py) ---",
    "from sa_suite import init_sa_suite",
    "init_sa_suite(app, super_admin_required=super_admin_required, log_audit=log_audit, limiter=limiter)",
    "",
    "from menu_import import init_menu_import",
    "init_menu_import(app, owner_required=owner_required, active_restaurant=active_restaurant,",
    "                 log_menu_change=log_menu_change, log_audit=log_audit)",
    "",
]


def main():
    if not os.path.exists(PATH):
        print("app.py introuvable : lancez ce script dans le dossier du projet.")
        return 1
    raw = open(PATH, "rb").read()
    text = raw.decode("utf-8")
    if MARK in text:
        print("Déjà branché : rien à faire.")
        return 0
    eol = "\r\n" if "\r\n" in text else "\n"
    lines = text.split(eol)
    index = None
    for anchor in ("from hardening import init_hardening", 'if __name__ == "__main__":'):
        for i, line in enumerate(lines):
            if line.strip() == anchor:
                index = i
                break
        if index is not None:
            break
    if index is None:
        print("Je n'ai pas trouvé où insérer le branchement. Ajoutez à la main, en bas de app.py :")
        print("\n".join(BLOCK))
        return 1
    shutil.copyfile(PATH, PATH + ".avant_sa_suite")
    lines[index:index] = BLOCK
    open(PATH, "wb").write(eol.join(lines).encode("utf-8"))
    print("Branchement ajouté dans app.py (copie de sécurité : app.py.avant_sa_suite).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
