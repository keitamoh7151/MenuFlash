"""
Remplace l'ancien logo et l'ancien titre « MenuFlash Mini » par le nouveau logo MenuFlash.

Usage, dans le dossier du projet (là où se trouve app.py) :

    python ajouter_nouveau_logo.py

Le nouveau logo est le fichier static/brand/menuflash-logo-sombre.svg (celui de la page d'accueil, déjà dans votre projet).
Le script :
  - remplace « icône + MenuFlash Mini » par le logo dans : barre du super-admin, page de connexion, inscription,
    pages légales, page « abonnement expiré » (quand le restaurant n'a pas de logo) ;
  - ajoute l'icône d'onglet (favicon) sur toutes les pages ;
  - retire le mot « Mini » partout où l'application se nomme : titres d'onglet, pieds de page « Propulsé par »,
    textes, emails, PDF (reçu, chevalet de table) ;
  - ajoute les styles du logo à la fin de static/style.css et change la version de style.css (rechargement forcé).
NE TOUCHE PAS aux logos des restaurants (restaurant.logo_url) ni à leur nom dans la barre du tableau de bord.

- Sans danger : relancé, il répond « déjà fait » et ne change rien.
- Garde le style de fin de ligne de chaque fichier (Windows ou Linux).
- Copie de sécurité de chaque fichier modifié : <fichier>.avant_logo
- Si un repère est introuvable (fichier modifié à la main), il ne touche pas au fichier et vous le dit.
"""
import os
import re
import shutil
import sys

SUFFIX = ".avant_logo"
LOGO_SRC = "{{ url_for('static', filename='brand/menuflash-logo-sombre.svg') }}"
FAVICON = '<link rel="icon" type="image/svg+xml" href="{{ url_for(\'static\', filename=\'brand/favicon.svg\') }}">'
CSS_MARK = "/* ---- Logo MenuFlash (nouveau logo) ---- */"
CSS = CSS_MARK + """
.mf-brand-logo { display: block; width: auto; max-width: 100%; object-fit: contain; }
.mf-brand-logo-nav { height: 38px; }
.mf-brand-plate {
  display: flex; justify-content: center; align-items: center; margin: 0;
  background: #351B12; border-bottom: 3px solid #FFAA0A; border-radius: 12px;
  padding: 14px 16px 10px;
}
.mf-brand-plate .mf-brand-logo { height: 64px; }
.mf-brand-plate-sm { display: inline-flex; padding: 8px 14px 6px; border-radius: 10px; }
.mf-brand-plate-sm .mf-brand-logo { height: 44px; }
@media (max-width: 575.98px) {
  .mf-brand-logo-nav { height: 32px; }
  .mf-brand-plate .mf-brand-logo { height: 54px; }
}
"""
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


def edit(path, steps):
    """steps : liste de (nom, fonction texte -> texte ou None si repère introuvable, déjà fait ?)."""
    if not os.path.exists(path):
        return report(path, "INTROUVABLE")
    text, eol = read(path)
    original, missing, done = text, [], 0
    for name, fn, is_done in steps:
        if is_done(text):
            done += 1
            continue
        new = fn(text)
        if new is None:
            missing.append(name)
        else:
            text = new
    if missing:
        return report(path, "REPÈRE INTROUVABLE (" + ", ".join(missing) + ") : fichier non modifié, voir LISEZMOI")
    if text == original:
        return report(path, "déjà fait")
    write(path, text, eol)
    report(path, "modifié")


def rx(pattern, repl, flags=re.S):
    compiled = re.compile(pattern, flags)

    def fn(text):
        new, n = compiled.subn(lambda m: repl, text, count=1)
        return new if n else None
    return fn


def img(css_class, alt="MenuFlash"):
    return f'<img src="{LOGO_SRC}" alt="{alt}" class="{css_class}">'


def contains(s):
    return lambda text: s in text


def rename_text(text):
    """« MenuFlash Mini » -> « MenuFlash » (textes affichés)."""
    return text.replace("MenuFlash Mini", "MenuFlash")


CSS_VERSION = "9"
CSS_TAG = re.compile(r"(filename=['\"]style\.css['\"]\s*\)\s*\}\}\?v=)(\d+)")


def css_version(text):
    return CSS_TAG.sub(lambda m: m.group(1) + CSS_VERSION, text, count=1) if CSS_TAG.search(text) else None


def css_done(text):
    match = CSS_TAG.search(text)
    return bool(match and match.group(2) == CSS_VERSION)


def rename_python(text):
    """Dans les .py : seulement les lignes avec un texte entre guillemets (docstrings et commentaires intacts)."""
    out = []
    for line in text.split("\n"):
        if "MenuFlash Mini" in line and ('"' in line or "'" in line) and not line.lstrip().startswith("#"):
            line = line.replace("MenuFlash Mini", "MenuFlash")
        out.append(line)
    return "\n".join(out)


def main():
    if not os.path.exists("app.py"):
        print("app.py introuvable : lancez le script dans le dossier du projet.")
        return 1
    if not os.path.exists(os.path.join("static", "brand", "menuflash-logo-sombre.svg")):
        print("ATTENTION : static/brand/menuflash-logo-sombre.svg est introuvable. Le logo ne s'affichera pas tant que ce fichier "
              "n'est pas dans le projet (c'est celui de la page d'accueil).")

    T = "templates"
    brand_in_nav = img("mf-brand-logo mf-brand-logo-nav")
    nav_done = contains("mf-brand-logo-nav")

    # ---- barre du super-admin
    edit(os.path.join(T, "_super_admin_navbar.html"), [
        ("logo barre", rx(r'<i class="bi bi-qr-code-scan"></i>\s*MenuFlash<span class="mf-brand-mini">Mini</span>', brand_in_nav), nav_done),
    ])
    # ---- connexion : bandeau sombre avec le logo (comme l'en-tête de la page d'accueil)
    edit(os.path.join(T, "admin_login.html"), [
        ("logo connexion", rx(
            r'<i class="bi bi-qr-code-scan"[^>]*></i>\s*<h1[^>]*>\s*MenuFlash<span class="mf-brand-mini">Mini</span>\s*</h1>',
            '<h1 class="mf-brand-plate">' + img("mf-brand-logo", "MenuFlash, connexion") + '</h1>'),
         contains("mf-brand-plate")),
        ("titre", rename_text, lambda t: "MenuFlash Mini" not in t),
    ])
    # ---- inscription (barre sombre)
    edit(os.path.join(T, "signup.html"), [
        ("logo inscription", rx(r'<i class="bi bi-qr-code-scan"></i>\s*MenuFlash\s*<span>Mini</span>', brand_in_nav), nav_done),
        ("favicon", lambda t: t.replace("</title>", "</title>\n  " + FAVICON, 1) if "</title>" in t else None,
         contains("brand/favicon.svg")),
        ("titre", rename_text, lambda t: "MenuFlash Mini" not in t),
    ])
    # ---- pages légales
    edit(os.path.join(T, "legal.html"), [
        ("logo légal", rx(
            r'<i class="bi bi-qr-code-scan me-1"[^>]*></i>MenuFlash <span[^>]*>Mini</span>', brand_in_nav), nav_done),
        ("textes", rename_text, lambda t: "MenuFlash Mini" not in t),
    ])
    # ---- abonnement expiré : logo de MenuFlash seulement quand le restaurant n'a pas de logo (celui du restaurant n'est pas touché)
    edit(os.path.join(T, "subscription_expired.html"), [
        ("logo expiré", rx(
            r'<i class="bi bi-qr-code-scan"[^>]*></i>\s*<span class="fw-bold"[^>]*>MenuFlash<span class="mf-brand-mini">Mini</span></span>',
            '<span class="mf-brand-plate mf-brand-plate-sm">' + img("mf-brand-logo") + '</span>'),
         contains("mf-brand-plate-sm")),
        ("textes", rename_text, lambda t: "MenuFlash Mini" not in t),
    ])
    # ---- gabarit commun : favicon + titre par défaut + rechargement du CSS
    edit(os.path.join(T, "base.html"), [
        ("favicon", lambda t: t.replace("</title>", "</title>\n  " + FAVICON, 1) if "</title>" in t else None,
         contains("brand/favicon.svg")),
        ("titre", rename_text, lambda t: "MenuFlash Mini" not in t),
        ("version css", css_version, css_done),
    ])
    # ---- toutes les autres pages : le mot « Mini » disparaît des titres, pieds de page et textes
    for name in sorted(os.listdir(T)):
        if name.endswith(".html") and name not in ("admin_login.html", "signup.html", "legal.html",
                                                    "subscription_expired.html", "base.html"):
            path = os.path.join(T, name)
            if "MenuFlash Mini" in read(path)[0]:
                edit(path, [("textes", rename_text, lambda t: "MenuFlash Mini" not in t)])
    edit(os.path.join(T, "signup.html"), [("version css", css_version, css_done)])   # signup.html charge style.css ?v=3

    # ---- styles
    css = os.path.join("static", "style.css")
    edit(css, [("styles", lambda t: t.rstrip("\n") + "\n\n" + CSS, contains(CSS_MARK))])

    # ---- textes dans le code Python (emails, PDF)
    for name in sorted(os.listdir(".")):
        if name.endswith(".py") and not name.startswith(("ajouter_", "test_")):
            edit(name, [("textes", rename_python, lambda t: not any(
                "MenuFlash Mini" in l and ('"' in l or "'" in l) and not l.lstrip().startswith("#") for l in t.split("\n")))])

    bad = [p for p, s in results if "INTROUVABLE" in s]
    print("\nTerminé." if not bad else "\nÀ vérifier : " + ", ".join(bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
