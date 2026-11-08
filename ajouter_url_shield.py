"""
Branche le bouclier d'adresses (url_shield.py) dans votre projet.

Usage, dans le dossier du projet (là où se trouve app.py) :

    python ajouter_url_shield.py

Il ajoute 3 lignes dans app.py, juste avant « from hardening import init_hardening », et ne touche à rien d'autre.
- Sans danger : relancé, il répond « déjà fait » et ne change rien.
- Garde le style de fin de ligne du fichier (Windows ou Linux).
- Copie de sécurité : app.py.avant_url_shield
- Si le repère est introuvable (app.py modifié à la main), il ne touche pas au fichier et vous dit quoi ajouter.
"""
import os
import shutil
import sys

SUFFIX = ".avant_url_shield"
APP_BLOCK = [
    "# --- Bouclier d'adresses : une adresse modifiée ramène sur une page valide, sans erreur (url_shield.py) ---",
    "from url_shield import init_url_shield",
    "init_url_shield(app, log_audit=log_audit)",
    "",
]


def main():
    path = "app.py"
    if not os.path.exists(path):
        print("app.py : INTROUVABLE (lancez le script dans le dossier du projet)")
        return 1
    if not os.path.exists("url_shield.py"):
        print("url_shield.py : INTROUVABLE (décompressez d'abord le zip dans le dossier du projet)")
        return 1
    raw = open(path, "rb").read().decode("utf-8")
    eol = "\r\n" if "\r\n" in raw else "\n"
    text = raw.replace("\r\n", "\n")
    if "init_url_shield" in text:
        print("app.py : déjà fait")
        return 0
    lines = text.split("\n")
    index = None
    for anchor in ("from hardening import init_hardening", 'if __name__ == "__main__":'):
        index = next((i for i, l in enumerate(lines) if l.strip() == anchor), None)
        if index is not None:
            break
    if index is None:
        print("app.py : REPÈRE INTROUVABLE. Ajoutez à la main, en bas de app.py (avant « if __name__ »), :")
        print("\n".join(APP_BLOCK))
        return 1
    lines[index:index] = APP_BLOCK
    if not os.path.exists(path + SUFFIX):
        shutil.copyfile(path, path + SUFFIX)
    open(path, "wb").write("\n".join(lines).replace("\n", eol).encode("utf-8"))
    print("app.py : branchement ajouté")
    return 0


if __name__ == "__main__":
    sys.exit(main())
