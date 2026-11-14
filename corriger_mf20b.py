"""
MenuFlash — Correction MF-20 (version 2, d'après pip-audit) — dépendances vulnérables.

pip-audit a trouvé 19 vulnérabilités connues dans 3 paquets :
    Flask        3.0.3  -> 3.1.3
    Werkzeug     3.0.6  -> 3.1.9
    cryptography 43.0.3 -> 49.0.0
Ce script remplace corriger_mf20.py (inutile de lancer l'ancien). Il fonctionne
que l'ancien ait été lancé ou non.

Ce qu'il fait :
1. requirements.txt (le fichier utilisé par Render) : versions corrigées ci-dessus,
   Pillow et phonenumbers épinglés (sinon Render installerait Pillow 13 sans prévenir).
   Les autres paquets ne changent pas.
2. requirements-render.txt : supprimé (Render ne l'utilise pas).
3. app.py : get_client_ip() fonctionne aussi hors d'une requête, pour que la
   sauvegarde automatique écrive sa ligne « backup_auto » dans le journal.

UTILISATION (à la racine du projet) :  python corriger_mf20b.py
Copies de sécurité : *.avant-mf20b
"""
import os
import re
import shutil
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
REQ = os.path.join(ROOT, "requirements.txt")
REQ_RENDER = os.path.join(ROOT, "requirements-render.txt")
APP = os.path.join(ROOT, "app.py")

REQUIREMENTS = """Flask==3.1.3
Werkzeug==3.1.9
Flask-SQLAlchemy==3.1.1
SQLAlchemy==2.0.31
Flask-Login==0.6.3
Flask-WTF==1.3.0
Flask-Limiter==4.1.1
Flask-Mail==0.10.0
qrcode==7.4.2
Pillow==12.3.0
gunicorn==22.0.0
reportlab==4.4.6
psycopg2-binary==2.9.10
boto3==1.35.36
cryptography==49.0.0
phonenumbers==9.0.43
"""

OLD_IP = '''def get_client_ip():
    forwarded = request.headers.get("X-Forwarded-For", "")'''
NEW_IP = '''def get_client_ip():
    from flask import has_request_context
    if not has_request_context():                 # tâche de fond (ex. sauvegarde automatique)
        return "système"
    forwarded = request.headers.get("X-Forwarded-For", "")'''


def main():
    for path in (REQ, APP):
        if not os.path.exists(path):
            sys.exit(f"ARRÊT : {os.path.basename(path)} introuvable (lancez le script à la racine du projet).")
    req_text = open(REQ, encoding="utf-8").read()
    app_text = open(APP, encoding="utf-8").read()

    if not re.search(r"(?im)^\s*flask\s*==", req_text) or "cryptography" not in req_text.lower():
        sys.exit("ARRÊT : requirements.txt ne ressemble pas à celui de MenuFlash. Aucun fichier n'a été modifié.")
    ip_done = "has_request_context()" in app_text
    if not ip_done and app_text.count(OLD_IP) != 1:
        sys.exit("ARRÊT : fonction get_client_ip introuvable dans app.py. Aucun fichier n'a été modifié.")
    if req_text == REQUIREMENTS and ip_done:
        print("Déjà appliqué : rien à faire.")
        return

    shutil.copy2(REQ, REQ + ".avant-mf20b")
    with open(REQ, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(REQUIREMENTS)
    print("  modifié : requirements.txt")
    if not ip_done:
        shutil.copy2(APP, APP + ".avant-mf20b")
        with open(APP, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(app_text.replace(OLD_IP, NEW_IP))
        print("  modifié : app.py")
    if os.path.exists(REQ_RENDER):
        os.remove(REQ_RENDER)
        print("  supprimé : requirements-render.txt (inutilisé par Render)")
    print("\nTerminé. Vérifiez ensuite avec :  pip-audit -r requirements.txt")


if __name__ == "__main__":
    main()
