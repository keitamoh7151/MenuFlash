"""
Applique le design « Bogolan » aux pages CLIENT : menu sur place (QR de table), menu à emporter (lien général),
suivi de commande, « menu indisponible » et « abonnement expiré ».

Usage, dans le dossier du projet (là où se trouve app.py), APRÈS avoir décompressé le zip
(il apporte static/theme_mali_client.css) :

    python ajouter_theme_client.py

Prérequis : le paquet « thème Bogolan » (ajouter_theme_mali.py) doit déjà être installé.

Retouches précises, sans rien effacer :
  - templates/base.html : la classe « mf-client » est ajoutée sur <body> pour les pages /m/…, et la feuille
    static/theme_mali_client.css est chargée sur ces pages ;
  - templates/public_menu.html et templates/order_status.html : dans le pied de page, le nom « MenuFlash » est
    remplacé par le logo MenuFlash.
Ne touche à aucun code Python, ni au logo, au nom ou à la couleur du restaurant.

- Sans danger : relancé, il répond « déjà fait » et ne change rien.
- Garde le style de fin de ligne de chaque fichier (Windows ou Linux).
- Copie de sécurité : <fichier>.avant_theme_client
- Repère introuvable (fichier modifié à la main) : il ne touche pas au fichier et vous dit quoi ajouter.
"""
import os
import re
import shutil
import sys

SUFFIX = ".avant_theme_client"
CSS_FILE = os.path.join("static", "theme_mali_client.css")
BODY_NEW = ('<body class="{{ body_class|default(\'\') }} mf-mali'
            '{{ \' mf-client\' if request.path.startswith(\'/m/\') }}">')
CLIENT_LINK = ('{% if request.path.startswith(\'/m/\') %}<link rel="stylesheet" '
               'href="{{ url_for(\'static\', filename=\'theme_mali_client.css\') }}?v=1">{% endif %}')
LOGO_IMG = ('<img class="mf-client-footer-logo" src="{{ url_for(\'static\', '
            'filename=\'brand/menuflash-logo-sombre.svg\') }}" alt="MenuFlash">')
STRONG_RE = re.compile(r'<strong class="text-dark">\s*MenuFlash(?:\s+Mini)?\s*</strong>')
results = []


def read(path):
    raw = open(path, "rb").read().decode("utf-8")
    return raw.replace("\r\n", "\n"), ("\r\n" if "\r\n" in raw else "\n")


def write(path, text, eol):
    if not os.path.exists(path + SUFFIX):
        shutil.copyfile(path, path + SUFFIX)
    open(path, "wb").write(text.replace("\n", eol).encode("utf-8"))


def report(path, status, help_text=""):
    results.append((path, status))
    print(f"{path} : {status}")
    if help_text:
        print("   " + help_text.replace("\n", "\n   "))


def patch_base():
    path = os.path.join("templates", "base.html")
    if not os.path.exists(path):
        return report(path, "INTROUVABLE")
    text, eol = read(path)
    if "theme_mali.css" not in text:
        return report(path, "INTROUVABLE (thème Bogolan non installé)",
                      "Installez d'abord le paquet « thème Bogolan » (python ajouter_theme_mali.py), puis relancez ce script.")
    original = text
    if "mf-client" not in text:
        new, n = re.subn(r'<body class="[^"]*mf-mali[^"]*">', lambda m: BODY_NEW, text, count=1)
        if not n:
            return report(path, "REPÈRE INTROUVABLE (balise body) : fichier non modifié",
                          "Remplacez la balise <body ...> de base.html par :\n  " + BODY_NEW)
        text = new
    if "theme_mali_client.css" not in text:
        lines = text.split("\n")
        idx = next((i for i, l in enumerate(lines) if "theme_mali.css" in l), None)
        if idx is None:
            return report(path, "REPÈRE INTROUVABLE (feuille de style) : fichier non modifié",
                          "Ajoutez dans <head>, après la ligne theme_mali.css :\n  " + CLIENT_LINK)
        indent = re.match(r"\s*", lines[idx]).group(0)
        lines.insert(idx + 1, indent + CLIENT_LINK)
        text = "\n".join(lines)
    if text == original:
        return report(path, "déjà fait")
    write(path, text, eol)
    report(path, "modifié")


def patch_footer(name):
    path = os.path.join("templates", name)
    if not os.path.exists(path):
        return report(path, "INTROUVABLE")
    text, eol = read(path)
    if "mf-client-footer-logo" in text:
        return report(path, "déjà fait")
    new, n = STRONG_RE.subn(lambda m: LOGO_IMG, text)
    if not n:
        return report(path, "pied de page non reconnu : logo non ajouté (sans gravité, le reste du thème s'applique)")
    write(path, new, eol)
    report(path, "modifié")


def main():
    if not os.path.exists("app.py"):
        print("app.py introuvable : lancez le script dans le dossier du projet.")
        return 1
    if not os.path.exists(CSS_FILE):
        print(f"{CSS_FILE} : INTROUVABLE (décompressez d'abord le zip dans le dossier du projet).")
        return 1
    if not os.path.exists(os.path.join("static", "brand", "menuflash-logo-sombre.svg")):
        print("ATTENTION : static/brand/menuflash-logo-sombre.svg est introuvable : le logo du pied de page ne s'affichera pas.")
    patch_base()
    patch_footer("public_menu.html")
    patch_footer("order_status.html")
    bad = [p for p, s in results if "INTROUVABLE" in s and "REPÈRE" in s or s.startswith("INTROUVABLE")]
    print("\nTerminé." if not bad else "\nÀ vérifier : " + ", ".join(bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
