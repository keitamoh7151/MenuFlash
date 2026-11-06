"""
Branche le chronomètre (perf_watch.py) dans app.py et renforce les connexions PostgreSQL.

Usage, dans le dossier du projet (là où se trouve app.py) :

    python ajouter_perf_watch.py

- Sans danger : relancé, il répond « Déjà branché » et ne change rien.
- Il ne touche à rien d'autre dans app.py (vos autres modifications sont conservées)
  et garde le style de fin de ligne du fichier (Windows ou Linux).
- Copie de sécurité : app.py.avant_perf_watch

Il ajoute 2 blocs autour de « db.init_app(app) » :
  1. AVANT : réglage des connexions PostgreSQL (vérifiées avant usage, renouvelées,
     délais de connexion). Ne s'applique pas à SQLite (votre PC).
  2. APRÈS : le chronomètre des requêtes.
"""
import os
import shutil
import sys

PATH = "app.py"
ANCHOR = "db.init_app(app)"

BEFORE = [
    "# Connexions PostgreSQL : vérifiées avant chaque usage et renouvelées. Évite l'erreur 500 après un",
    "# redémarrage ou un changement d'offre de la base, et les blocages après une longue inactivité.",
    "if app.config[\"SQLALCHEMY_DATABASE_URI\"].startswith(\"postgresql\"):",
    "    app.config[\"SQLALCHEMY_ENGINE_OPTIONS\"] = {",
    "        \"pool_pre_ping\": True,",
    "        \"pool_recycle\": 1800,",
    "        \"connect_args\": {\"connect_timeout\": 10, \"keepalives\": 1, \"keepalives_idle\": 30,",
    "                         \"keepalives_interval\": 10, \"keepalives_count\": 3},",
    "    }",
]
AFTER = [
    "",
    "# Chronomètre des requêtes lentes (journal « LENTE » / « CONNEXION » et en-tête Server-Timing).",
    "from perf_watch import init_perf_watch",
    "init_perf_watch(app)",
]


def main():
    if not os.path.exists(PATH):
        print("app.py introuvable : lancez ce script dans le dossier du projet.")
        return 1
    text = open(PATH, "rb").read().decode("utf-8")
    if "init_perf_watch" in text:
        print("Déjà branché : rien à faire.")
        return 0
    eol = "\r\n" if "\r\n" in text else "\n"
    lines = text.split(eol)
    index = next((i for i, line in enumerate(lines) if line.strip() == ANCHOR), None)
    if index is None:
        print(f"Je n'ai pas trouvé « {ANCHOR} » dans app.py. Ajoutez à la main :")
        print("\n".join(BEFORE))
        print(f"{ANCHOR}")
        print("\n".join(AFTER))
        return 1
    if "SQLALCHEMY_ENGINE_OPTIONS" in text:
        before = []
        print("Réglage SQLALCHEMY_ENGINE_OPTIONS déjà présent : je ne le modifie pas.")
    else:
        before = BEFORE
    shutil.copyfile(PATH, PATH + ".avant_perf_watch")
    lines[index + 1:index + 1] = AFTER          # d'abord après (les indices avant ne bougent pas)
    lines[index:index] = before
    open(PATH, "wb").write(eol.join(lines).encode("utf-8"))
    print("Branchement ajouté dans app.py (copie de sécurité : app.py.avant_perf_watch).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
