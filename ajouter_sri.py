"""
MenuFlash — Empreintes d'intégrité (SRI) sur les bibliothèques chargées depuis cdn.jsdelivr.net.

POURQUOI : si le CDN (ou le compte du paquet) était compromis, un script modifié s'exécuterait sur TOUTES les
pages, y compris le panel super-admin. Avec une empreinte « integrity », le navigateur REFUSE tout fichier dont
le contenu a changé. (Les versions sont déjà figées par la CSP ; il manquait l'empreinte.)

CE QUE FAIT LE SCRIPT (à lancer sur TON PC, avec internet) :
  1. cherche, dans templates/*.html, chaque <link> et <script> qui charge https://cdn.jsdelivr.net/… ;
  2. télécharge chaque fichier DEUX fois et ne continue que si les deux copies sont identiques ;
  3. ajoute integrity="sha384-…" et crossorigin="anonymous" à la balise ;
  4. n'écrit RIEN si un seul téléchargement échoue (tout ou rien) ; copies : *.avant-sri.

UTILISATION (à la racine du projet) :   python ajouter_sri.py
Contrôle :                               python ajouter_sri.py --verifier
Si tu changes un jour la version d'une bibliothèque, relance le script (il remplace l'ancienne empreinte).
"""
import base64
import hashlib
import os
import re
import shutil
import sys
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
TEMPLATES = os.path.join(ROOT, "templates")
CDN = "https://cdn.jsdelivr.net/"
TAG_RE = re.compile(r"<(?:link|script)\b[^>]*?>", re.I | re.S)
URL_RE = re.compile(r"""(?:href|src)\s*=\s*["'](https://cdn\.jsdelivr\.net/[^"']+)["']""", re.I)
INTEGRITY_RE = re.compile(r"""\sintegrity\s*=\s*["'][^"']*["']""", re.I)
CROSSORIGIN_RE = re.compile(r"""\scrossorigin(?:\s*=\s*["'][^"']*["'])?""", re.I)


def fetch(url):
    request = urllib.request.Request(url, headers={"User-Agent": "MenuFlash-SRI/1.0", "Accept-Encoding": "identity"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def sri_of(content):
    return "sha384-" + base64.b64encode(hashlib.sha384(content).digest()).decode("ascii")


def template_files():
    if not os.path.isdir(TEMPLATES):
        sys.exit("ARRÊT : dossier templates/ introuvable (lancez le script à la racine du projet).")
    return [os.path.join(TEMPLATES, n) for n in sorted(os.listdir(TEMPLATES)) if n.endswith(".html")]


def cdn_tags(text):
    """(position début, position fin, balise, adresse) pour chaque balise qui charge le CDN."""
    for m in TAG_RE.finditer(text):
        u = URL_RE.search(m.group(0))
        if u:
            yield m.start(), m.end(), m.group(0), u.group(1)


def add_integrity(tag, digest):
    tag = INTEGRITY_RE.sub("", tag)
    tag = CROSSORIGIN_RE.sub("", tag)
    end = "/>" if tag.rstrip().endswith("/>") else ">"
    body = tag.rstrip()[: -len(end)].rstrip()
    return f'{body} integrity="{digest}" crossorigin="anonymous"{end}'


def verifier():
    missing = 0
    total = 0
    for path in template_files():
        text = open(path, encoding="utf-8").read()
        for _, _, tag, url in cdn_tags(text):
            total += 1
            ok = bool(INTEGRITY_RE.search(tag))
            missing += 0 if ok else 1
            print(("  OK     " if ok else "  MANQUE ") + f"{os.path.basename(path)} : {url.replace(CDN, '')}")
    print(f"\n{total - missing}/{total} balises protégées.")
    sys.exit(0 if missing == 0 and total else 1)


def main():
    if "--verifier" in sys.argv:
        verifier()
    files = {p: open(p, encoding="utf-8").read() for p in template_files()}
    urls = sorted({url for text in files.values() for _, _, _, url in cdn_tags(text)})
    if not urls:
        print("Aucune balise cdn.jsdelivr.net trouvée : rien à faire.")
        return
    print(f"{len(urls)} fichier(s) à protéger :")
    digests = {}
    for url in urls:
        try:
            first, second = fetch(url), fetch(url)
        except Exception as exc:
            sys.exit(f"ARRÊT : téléchargement impossible ({type(exc).__name__}) : {url}\n"
                     "Vérifiez votre connexion internet. Aucun fichier n'a été modifié.")
        if first != second or not first:
            sys.exit(f"ARRÊT : le contenu de {url} change d'un téléchargement à l'autre. Aucun fichier modifié.")
        digests[url] = sri_of(first)
        print(f"  {digests[url][:22]}…  {url.replace(CDN, '')}  ({len(first) // 1024} Ko)")
    changed = {}
    for path, text in files.items():
        parts, last = [], 0
        for start, end, tag, url in cdn_tags(text):
            parts.append(text[last:start])
            parts.append(add_integrity(tag, digests[url]))
            last = end
        parts.append(text[last:])
        new = "".join(parts)
        if new != text:
            changed[path] = new
    for path, new in changed.items():
        shutil.copy2(path, path + ".avant-sri")
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(new)
        print(f"  modifié : templates/{os.path.basename(path)}")
    print(f"\n{len(changed)} gabarit(s) modifié(s). Contrôle :  python ajouter_sri.py --verifier")
    print("Teste ensuite en local (python app.py) : les pages doivent s'afficher normalement, avec leur style.")


if __name__ == "__main__":
    main()
