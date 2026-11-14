"""
MenuFlash — Correction de sécurité MF-03 (audit du 13/11/2026).

PROBLÈME
--------
Les liens envoyés par email (réinitialisation du mot de passe, vérification de
l'email, invitations, « ce n'était pas moi », suivi de commande à emporter) sont
construits à partir de l'adresse demandée par le visiteur. Avec
ProxyFix(x_host=1), un en-tête X-Forwarded-Host fourni par un attaquant pouvait
remplacer cette adresse : l'email légitime contenait alors un lien vers le site
de l'attaquant, avec un jeton valide (prise de contrôle du compte en un clic).

CORRECTION (hardening.py uniquement)
------------------------------------
1. ProxyFix n'accepte plus X-Forwarded-Host (x_host=0). L'adresse IP réelle et
   le HTTPS (x_for, x_proto) restent gérés comme avant.
2. Nouvelle variable APP_BASE_URL (ex. https://menuflash.app) : si elle est
   définie, TOUTES les adresses construites par l'application (liens d'email,
   QR codes, redirection HTTPS) utilisent ce domaine, quel que soit l'en-tête
   Host reçu. Si elle est absente (en local), rien ne change.
3. Sur Render, un avertissement est écrit dans les logs au démarrage si
   APP_BASE_URL manque ou est mal écrite.

UTILISATION (à la racine du projet, là où se trouve app.py)
-----------------------------------------------------------
    python corriger_mf03.py

Vérifie tout avant d'écrire ; copie de sécurité : hardening.py.avant-mf03
"""
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(ROOT, "hardening.py")

OLD_PROXY = '''    inner = _ForwardedForSanitizer(app.wsgi_app)
    app.wsgi_app = ProxyFix(inner, x_for=hops, x_proto=1, x_host=1) if hops else inner
'''
NEW_PROXY = '''    inner = _ForwardedForSanitizer(_CanonicalHost.wrap(app.wsgi_app))
    # Audit MF-03 : x_host=0 — l'en-tête X-Forwarded-Host (modifiable par le
    # visiteur) ne peut plus changer l'adresse utilisée dans les liens.
    app.wsgi_app = ProxyFix(inner, x_for=hops, x_proto=1, x_host=0) if hops else inner
'''

ANCHOR = '''def _install_proxy_fix(app):'''
NEW_CLASS = '''class _CanonicalHost:
    """Audit MF-03 — adresse officielle du site.

    Si APP_BASE_URL est définie (ex. https://menuflash.app), chaque requête est
    traitée comme adressée à ce domaine : url_for(..., _external=True) produit
    donc TOUJOURS des liens vers le vrai site (emails, QR codes, redirections),
    même si un attaquant envoie un en-tête Host falsifié. L'adresse reçue est
    conservée dans environ["menuflash.received_host"] pour le diagnostic.
    Le protocole (http/https) n'est pas modifié : la redirection HTTPS continue
    de fonctionner normalement.
    """

    def __init__(self, wsgi_app, host):
        self.wsgi_app = wsgi_app
        self.host = host

    @staticmethod
    def parse(raw):
        """Renvoie le domaine (avec port éventuel) d'APP_BASE_URL, ou None si invalide."""
        from urllib.parse import urlsplit
        raw = (raw or "").strip().rstrip("/")
        if not raw:
            return None
        parts = urlsplit(raw)
        if (parts.scheme not in ("http", "https") or not parts.hostname or parts.path
                or parts.query or parts.fragment or parts.username or parts.password):
            return None
        if any(ch in parts.netloc for ch in " /\\\\@"):
            return None
        return parts.netloc.lower()

    @classmethod
    def wrap(cls, wsgi_app):
        raw = os.environ.get("APP_BASE_URL", "")
        host = cls.parse(raw)
        if host:
            log.info("Adresse officielle des liens : %s (APP_BASE_URL).", host)
            return cls(wsgi_app, host)
        if raw.strip():
            log.error("APP_BASE_URL invalide (%r) : attendu par ex. https://menuflash.app, "
                      "sans chemin ni / final. Elle est ignorée.", raw)
        elif os.environ.get("RENDER"):
            log.warning("APP_BASE_URL n'est pas définie : les liens envoyés par email "
                        "utilisent l'adresse reçue. Définissez-la dans Render > Environment.")
        return wsgi_app

    def __call__(self, environ, start_response):
        environ["menuflash.received_host"] = environ.get("HTTP_HOST", "")
        environ["HTTP_HOST"] = self.host
        name, _, port = self.host.partition(":")
        environ["SERVER_NAME"] = name
        if port:
            environ["SERVER_PORT"] = port
        return self.wsgi_app(environ, start_response)


def _install_proxy_fix(app):'''


def main():
    if not os.path.exists(PATH):
        sys.exit("ARRÊT : hardening.py introuvable (lancez le script à la racine du projet).")
    text = open(PATH, encoding="utf-8").read()
    if "class _CanonicalHost" in text:
        print("MF-03 est déjà appliquée : rien à faire.")
        return
    for old, label in ((OLD_PROXY, "installation de ProxyFix"), (ANCHOR, "def _install_proxy_fix")):
        if text.count(old) != 1:
            sys.exit(f"ARRÊT : repère « {label} » introuvable ou en double. Aucun fichier n'a été modifié.")
    text = text.replace(OLD_PROXY, NEW_PROXY).replace(ANCHOR, NEW_CLASS)
    shutil.copy2(PATH, PATH + ".avant-mf03")
    with open(PATH, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    print("  modifié : hardening.py")
    print("\nMF-03 appliquée. Copie de sécurité : hardening.py.avant-mf03 (à ne pas pousser sur Git).")


if __name__ == "__main__":
    main()
