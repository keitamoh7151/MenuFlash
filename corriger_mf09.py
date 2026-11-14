"""
MenuFlash — Correction de sécurité MF-09 (audit du 13/11/2026).

PROBLÈME
--------
La protection des cookies (attribut Secure) et la redirection vers HTTPS
dépendaient de deux variables à ne pas oublier : SECURE_COOKIES et FORCE_HTTPS.
Sans elles (oubli, nouvelle instance, faute de frappe), le cookie de session
pouvait partir en clair sur une connexion http://, sans aucune alerte.

CORRECTION (app.py uniquement)
------------------------------
1. Sur Render (variable RENDER présente) : cookies Secure et HTTPS forcé PAR
   DÉFAUT, même si les variables sont absentes.
2. Désactivation possible uniquement en écrivant explicitement « false » ;
   dans ce cas, une ERREUR est écrite dans les logs à chaque démarrage.
3. En local (sans RENDER) : rien ne change (http://127.0.0.1 fonctionne).
4. Le cookie « se souvenir de moi » de Flask-Login reçoit les mêmes protections
   (Secure, HttpOnly, SameSite=Lax).

Tous les cookies de MenuFlash (session, appareil, commandes, QR, navigation)
suivent automatiquement ce réglage : ils lisent SESSION_COOKIE_SECURE.

UTILISATION (à la racine du projet) :  python corriger_mf09.py
Vérifie tout avant d'écrire ; copie : app.py.avant-mf09
"""
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(ROOT, "app.py")

OLD_COOKIE = '''app.config["SESSION_COOKIE_SECURE"] = os.environ.get("SECURE_COOKIES", "false").lower() == "true"
'''
NEW_COOKIE = '''# Audit MF-09 : sur Render, cookies Secure et HTTPS forcé PAR DÉFAUT. On ne peut
# les désactiver qu'en écrivant explicitement « false » (erreur dans les logs).
_ON_RENDER = bool(os.environ.get("RENDER"))


def _secure_flag(name):
    raw = (os.environ.get(name) or "").strip().lower()
    if raw in ("true", "1", "yes", "oui"):
        return True
    if raw in ("false", "0", "no", "non"):
        if _ON_RENDER:
            logging.getLogger("menuflash").error(
                "%s=false sur Render : protection HTTPS désactivée volontairement. "
                "Supprimez cette variable (ou mettez true).", name)
        return False
    return _ON_RENDER                     # absente : activée en production, désactivée en local


app.config["SESSION_COOKIE_SECURE"] = _secure_flag("SECURE_COOKIES")
app.config["REMEMBER_COOKIE_SECURE"] = app.config["SESSION_COOKIE_SECURE"]
app.config["REMEMBER_COOKIE_HTTPONLY"] = True
app.config["REMEMBER_COOKIE_SAMESITE"] = "Lax"
'''
OLD_HTTPS = '''FORCE_HTTPS = os.environ.get("FORCE_HTTPS", "false").lower() == "true"
'''
NEW_HTTPS = '''FORCE_HTTPS = _secure_flag("FORCE_HTTPS")          # audit MF-09
'''


def main():
    if not os.path.exists(PATH):
        sys.exit("ARRÊT : app.py introuvable (lancez le script à la racine du projet).")
    text = open(PATH, encoding="utf-8").read()
    if "def _secure_flag(name)" in text:
        print("MF-09 est déjà appliquée : rien à faire.")
        return
    if "import logging" not in text:
        sys.exit("ARRÊT : « import logging » absent de app.py. Aucun fichier n'a été modifié.")
    for old, label in ((OLD_COOKIE, "SESSION_COOKIE_SECURE"), (OLD_HTTPS, "FORCE_HTTPS")):
        if text.count(old) != 1:
            sys.exit(f"ARRÊT : repère « {label} » introuvable dans app.py. Aucun fichier n'a été modifié.")
    if text.index(OLD_COOKIE) > text.index(OLD_HTTPS):
        sys.exit("ARRÊT : ordre inattendu des réglages dans app.py. Aucun fichier n'a été modifié.")
    text = text.replace(OLD_COOKIE, NEW_COOKIE).replace(OLD_HTTPS, NEW_HTTPS)
    shutil.copy2(PATH, PATH + ".avant-mf09")
    with open(PATH, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    print("  modifié : app.py")
    print("\nMF-09 appliquée. Copie de sécurité : app.py.avant-mf09 (à ne pas pousser sur Git).")


if __name__ == "__main__":
    main()
