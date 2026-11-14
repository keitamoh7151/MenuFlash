"""
MenuFlash — Correction de sécurité MF-19 (audit du 13/11/2026).

PROBLÈME
--------
Si DATABASE_URL est absente ou mal nommée sur Render, l'application démarrait
SANS ERREUR sur un fichier SQLite local (menuflash.db). Or le disque de Render
est effacé à chaque redéploiement : commandes, comptes et paiements créés
pendant ce temps étaient perdus, sans aucune alerte.

CORRECTION (app.py uniquement)
------------------------------
Sur Render (variable RENDER présente, définie automatiquement par Render), le
démarrage est REFUSÉ si DATABASE_URL est absente ou ne pointe pas vers
PostgreSQL. Le déploiement échoue avec un message clair dans les logs, et
l'ancienne version continue de tourner : aucune coupure, aucune perte.
En local (pas de variable RENDER), rien ne change : SQLite reste utilisé.

UTILISATION (à la racine du projet, là où se trouve app.py)
-----------------------------------------------------------
    python corriger_mf19.py

Vérifie tout avant d'écrire ; copie de sécurité : app.py.avant-mf19
"""
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(ROOT, "app.py")

OLD = '''app.config["SQLALCHEMY_DATABASE_URI"] = _db_url
'''
NEW = '''# Audit MF-19 : sur Render, jamais de repli silencieux sur SQLite (disque effacé
# à chaque redéploiement = perte des données). Démarrage refusé à la place.
if os.environ.get("RENDER") and not _db_url.startswith("postgresql+psycopg2://"):
    raise RuntimeError(
        "DATABASE_URL absente ou invalide sur Render : démarrage refusé pour ne pas "
        "enregistrer les données sur un disque temporaire. Dans Render > Environment, "
        "définissez DATABASE_URL avec l'« Internal Database URL » de votre base PostgreSQL.")
app.config["SQLALCHEMY_DATABASE_URI"] = _db_url
'''


def main():
    if not os.path.exists(PATH):
        sys.exit("ARRÊT : app.py introuvable (lancez le script à la racine du projet).")
    text = open(PATH, encoding="utf-8").read()
    if "Audit MF-19" in text:
        print("MF-19 est déjà appliquée : rien à faire.")
        return
    if text.count(OLD) != 1:
        sys.exit("ARRÊT : repère « SQLALCHEMY_DATABASE_URI » introuvable ou en double. Aucun fichier n'a été modifié.")
    text = text.replace(OLD, NEW)
    shutil.copy2(PATH, PATH + ".avant-mf19")
    with open(PATH, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    print("  modifié : app.py")
    print("\nMF-19 appliquée. Copie de sécurité : app.py.avant-mf19 (à ne pas pousser sur Git).")


if __name__ == "__main__":
    main()
