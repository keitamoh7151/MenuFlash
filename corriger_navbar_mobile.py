"""
Barre du haut sur téléphone : le bouton menu (trois traits) passe à DROITE, sur la même ligne que le logo et « Chez Awa ».

Cause : dans static/responsive.css, la règle pour la barre du cuisinier
    .mf-navbar .container-fluid > .d-flex { width: 100%; ... }
s'appliquait aussi au logo + nom du restaurant (qui est aussi un « d-flex » enfant direct de la barre). Le logo prenait toute
la largeur et poussait le bouton sur la ligne du dessous. Même défaut dans la barre du super-admin.

Usage, dans le dossier du projet (là où se trouve app.py) :

    python corriger_navbar_mobile.py

Retouches précises :
  - static/responsive.css : la règle ne vise plus le logo/nom (« :not(.navbar-brand) »), la barre du cuisinier ne change pas ;
    et le logo + nom se réduit (avec « … ») au lieu de repousser le bouton quand le nom du restaurant est très long ;
  - templates/base.html et templates/signup.html : responsive.css?v=1 devient ?v=2 (le téléphone recharge le fichier).
- Sans danger : relancé, il répond « déjà fait ». Garde le style de fin de ligne. Copie : <fichier>.avant_navbar
"""
import os
import re
import shutil
import sys

SUFFIX = ".avant_navbar"
OLD = ".mf-navbar .container-fluid > .d-flex { width: 100%; justify-content: flex-start; }"
NEW = ".mf-navbar .container-fluid > .d-flex:not(.navbar-brand) { width: 100%; justify-content: flex-start; }"
BRAND_OLD = "  max-width: 100%;\n  flex: 1 1 auto;\n  margin-right: 0;"
BRAND_NEW = "  max-width: 100%;\n  flex: 1 1 0%;\n  margin-right: 0;"
results = []


def read(path):
    raw = open(path, "rb").read().decode("utf-8")
    return raw.replace("\r\n", "\n"), ("\r\n" if "\r\n" in raw else "\n")


def write(path, text, eol):
    if not os.path.exists(path + SUFFIX):
        shutil.copyfile(path, path + SUFFIX)
    open(path, "wb").write(text.replace("\n", eol).encode("utf-8"))


def report(path, status):
    results.append((path, status))
    print(f"{path} : {status}")


def patch_css():
    path = os.path.join("static", "responsive.css")
    if not os.path.exists(path):
        return report(path, "INTROUVABLE")
    text, eol = read(path)
    original, missing = text, []
    if NEW not in text:
        if OLD in text:
            text = text.replace(OLD, NEW, 1)
        else:
            missing.append("règle « .container-fluid > .d-flex »")
    if BRAND_NEW not in text:
        if BRAND_OLD in text:
            text = text.replace(BRAND_OLD, BRAND_NEW, 1)
        else:
            missing.append("règle « .navbar-brand »")
    if missing:
        return report(path, "REPÈRE INTROUVABLE (" + ", ".join(missing) + ") : fichier non modifié")
    if text == original:
        return report(path, "déjà fait")
    write(path, text, eol)
    report(path, "modifié")


def patch_version(name):
    path = os.path.join("templates", name)
    if not os.path.exists(path):
        return report(path, "INTROUVABLE")
    text, eol = read(path)
    pattern = re.compile(r"(filename=['\"]responsive\.css['\"]\s*\)\s*\}\}\?v=)(\d+)")
    match = pattern.search(text)
    if not match:
        return report(path, "responsive.css non chargé ici : rien à faire")
    if int(match.group(2)) >= 2:
        return report(path, "déjà fait")
    write(path, pattern.sub(lambda m: m.group(1) + "2", text, count=1), eol)
    report(path, "modifié")


def main():
    if not os.path.exists("app.py"):
        print("app.py introuvable : lancez le script dans le dossier du projet.")
        return 1
    patch_css()
    patch_version("base.html")
    patch_version("signup.html")
    bad = [p for p, s in results if "INTROUVABLE" in s and "responsive.css non chargé" not in s]
    print("\nTerminé." if not bad else "\nÀ vérifier : " + ", ".join(bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
