"""
Applique le design « Bogolan » de la page d'accueil (brun, crème, ocre, motifs maliens) à toutes les autres pages
de MenuFlash : connexion, inscription, mot de passe, double authentification, tableau de bord du restaurant,
commandes, panel super-admin, pages légales.

Usage, dans le dossier du projet (là où se trouve app.py), APRÈS avoir décompressé le zip (il apporte static/theme_mali.css) :

    python ajouter_theme_mali.py

Ce que fait le script (5 retouches précises, sans rien effacer) :
  - templates/base.html : polices de la page d'accueil, feuille static/theme_mali.css, classe « mf-mali » sur <body>
    (sauf pages client /m/…), pied de page MenuFlash sur les pages connectées ;
  - templates/signup.html : mêmes polices, même feuille, même classe.
NE TOUCHE PAS : la page d'accueil, le menu client, la page de suivi, la page « menu indisponible », les logos et
couleurs des restaurants, ni le code Python.

- Sans danger : relancé, il répond « déjà fait » et ne change rien.
- Garde le style de fin de ligne de chaque fichier (Windows ou Linux).
- Copie de sécurité : <fichier>.avant_theme
- Repère introuvable (fichier modifié à la main) : il ne touche pas au fichier et vous dit quoi ajouter.
"""
import os
import re
import shutil
import sys

SUFFIX = ".avant_theme"
CSS_FILE = os.path.join("static", "theme_mali.css")
FONTS = ('<link href="https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,600;12..96,700;12..96,800'
         '&family=Instrument+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">')
THEME_LINK = '<link rel="stylesheet" href="{{ url_for(\'static\', filename=\'theme_mali.css\') }}?v=1">'
BODY_OLD = '<body class="{{ body_class|default(\'\') }}">'
BODY_NEW = '<body class="{{ body_class|default(\'\') }}{{ \' mf-mali\' if not request.path.startswith(\'/m/\' ) }}">'.replace("'/m/' )", "'/m/')")
FOOTER = '''  {% if current_user.is_authenticated and not request.path.startswith('/m/') and request.endpoint != 'admin_orders' %}
    <footer class="mf-mali-footer">
      <div class="mf-mali-footer-inner">
        <a href="{{ url_for('landing') }}" aria-label="MenuFlash, accueil">
          <img class="mf-mali-footer-logo" src="{{ url_for('static', filename='brand/menuflash-logo-sombre.svg') }}" alt="MenuFlash, le menu en un scan">
        </a>
        <div class="mf-mali-footer-links">
          <a href="{{ url_for('legal_privacy') }}">Confidentialité</a>
          <a href="{{ url_for('legal_notice') }}">Mentions légales</a>
          <span>MenuFlash, pour le Mali <span class="mf-mali-flag" aria-label="drapeau du Mali"><span style="background:#14B53A"></span><span style="background:#FCD116"></span><span style="background:#CE1126"></span></span></span>
        </div>
      </div>
    </footer>
  {% endif %}
'''
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


def insert_after_line(text, pattern, new_line):
    """Insère new_line (avec la même indentation) après la première ligne qui correspond à pattern."""
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if re.search(pattern, line):
            indent = re.match(r"\s*", line).group(0)
            lines.insert(i + 1, indent + new_line)
            return "\n".join(lines)
    return None


def patch_base():
    path = os.path.join("templates", "base.html")
    if not os.path.exists(path):
        return report(path, "INTROUVABLE")
    text, eol = read(path)
    original, missing = text, []

    if "Bricolage+Grotesque" not in text:
        new = insert_after_line(text, r"fonts\.googleapis\.com/css2\?family=Poppins", FONTS)
        if new is None:
            missing.append("polices")
        else:
            text = new
    if "theme_mali.css" not in text:
        new = insert_after_line(text, r"filename=['\"]takeaway\.css['\"]", THEME_LINK)
        if new is None:
            new = insert_after_line(text, r"filename=['\"]menu_public\.css['\"]", THEME_LINK)
        if new is None:
            missing.append("feuille de style")
        else:
            text = new
    if "mf-mali" not in text:
        if BODY_OLD in text:
            text = text.replace(BODY_OLD, BODY_NEW, 1)
        else:
            missing.append("balise body")
    if "mf-mali-footer" not in text:
        marker = '<div class="modal fade" id="mf-confirm-modal"'
        if marker in text:
            text = text.replace("  " + marker, FOOTER + "  " + marker, 1) if "  " + marker in text else text.replace(marker, FOOTER + marker, 1)
        else:
            missing.append("pied de page")
    if missing:
        return report(path, "REPÈRE INTROUVABLE (" + ", ".join(missing) + ") : fichier non modifié",
                      "Ajoutez à la main dans base.html :\n"
                      "  - dans <head>, après les autres feuilles : " + THEME_LINK + "\n"
                      "  - la balise : " + BODY_NEW)
    if text == original:
        return report(path, "déjà fait")
    write(path, text, eol)
    report(path, "modifié")


def patch_signup():
    path = os.path.join("templates", "signup.html")
    if not os.path.exists(path):
        return report(path, "INTROUVABLE")
    text, eol = read(path)
    original, missing = text, []
    if "Bricolage+Grotesque" not in text:
        new = insert_after_line(text, r"fonts\.googleapis\.com/css2\?family=Poppins", FONTS)
        if new is None:
            missing.append("polices")
        else:
            text = new
    if "theme_mali.css" not in text:
        if "</head>" in text:
            text = text.replace("</head>", "  " + THEME_LINK + "\n</head>", 1)
        else:
            missing.append("</head>")
    if "mf-mali" not in text:
        new, n = re.subn(r'<body class="mf-landing-body"', '<body class="mf-landing-body mf-mali"', text, count=1)
        if n:
            text = new
        else:
            missing.append("balise body")
    if missing:
        return report(path, "REPÈRE INTROUVABLE (" + ", ".join(missing) + ") : fichier non modifié")
    if text == original:
        return report(path, "déjà fait")
    write(path, text, eol)
    report(path, "modifié")


def check_logo_step():
    path = os.path.join("templates", "admin_login.html")
    if os.path.exists(path) and "mf-brand-plate" not in read(path)[0]:
        print("\nATTENTION : la page de connexion contient encore l'ancien logo. Installez d'abord le paquet « nouveau logo »"
              " (ajouter_nouveau_logo.py), sinon le logo apparaîtra en double.")


def main():
    if not os.path.exists("app.py"):
        print("app.py introuvable : lancez le script dans le dossier du projet.")
        return 1
    if not os.path.exists(CSS_FILE):
        print(f"{CSS_FILE} : INTROUVABLE (décompressez d'abord le zip dans le dossier du projet).")
        return 1
    if not os.path.exists(os.path.join("static", "brand", "menuflash-logo-sombre.svg")):
        print("ATTENTION : static/brand/menuflash-logo-sombre.svg est introuvable : le logo ne s'affichera pas.")
    patch_base()
    patch_signup()
    check_logo_step()
    bad = [p for p, s in results if "INTROUVABLE" in s]
    print("\nTerminé." if not bad else "\nÀ vérifier : " + ", ".join(bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
