"""
MenuFlash — Correction de sécurité MF-08 (audit du 13/11/2026).

PROBLÈME
--------
Toutes les limites anti-abus (connexion, mot de passe oublié, 2FA, inscription,
commandes, faux QR codes…) étaient comptées dans la MÉMOIRE du processus. Sur
Render, le service redémarre à chaque déploiement et s'endort sans visite (plan
gratuit) : les compteurs repartaient alors de zéro. Un attaquant pouvait donc
recommencer ses essais après chaque redémarrage.

CORRECTION
----------
1. Nouveau fichier shared_counters.py : les compteurs sont stockés dans Redis
   (Render Key Value) quand la variable RATELIMIT_STORAGE_URI est définie.
   Sans elle (en local), la mémoire est utilisée comme avant.
2. app.py : Flask-Limiter (toutes les limites @limiter.limit) utilise ce stockage.
   Si Redis devient injoignable, l'application CONTINUE de fonctionner avec des
   compteurs en mémoire, puis revient sur Redis dès qu'il répond (aucune coupure).
3. qr_security.py : la limite « 15 faux QR codes par 10 minutes et par IP »
   passe aussi par Redis.
4. requirements.txt : ajout du client redis.
5. Au démarrage sur Render : avertissement dans les logs si la variable manque,
   erreur si Redis ne répond pas.

Inchangé : les petits compteurs qui servent seulement à ne pas remplir le
journal (order_access.py, url_shield.py) restent en mémoire, volontairement.

UTILISATION (à la racine du projet) :  python corriger_mf08.py
Vérifie tout avant d'écrire ; copies : *.avant-mf08
"""
import os
import re
import shutil
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))

SHARED_COUNTERS = '''"""
Compteurs anti-abus partagés (audit MF-08).

- RATELIMIT_STORAGE_URI définie (ex. redis://red-xxxx:6379, l'« Internal URL »
  de Render Key Value) : les compteurs sont dans Redis. Ils survivent aux
  redémarrages et aux mises en veille du service web.
- Variable absente (en local) : compteurs en mémoire, comme avant.
- Redis injoignable : repli automatique en mémoire (le site ne tombe jamais à
  cause des compteurs), nouvel essai sur Redis toutes les 30 secondes.
"""
import logging
import os
import time

from limits.storage import storage_from_string

log = logging.getLogger("menuflash.compteurs")

STORAGE_URI = (os.environ.get("RATELIMIT_STORAGE_URI") or "").strip() or "memory://"
USES_REDIS = STORAGE_URI.startswith(("redis://", "rediss://"))
# Délais courts : une panne de Redis ne doit jamais ralentir le site.
STORAGE_OPTIONS = {"socket_connect_timeout": 2, "socket_timeout": 2} if USES_REDIS else {}

_PREFIX = "menuflash:compteur:"
_RETRY_SECONDS = 30
_memory = storage_from_string("memory://")
_shared = None
_failed_at = 0.0


def backend_label():
    return "Redis" if USES_REDIS else "mémoire"


def _storage():
    global _shared
    if not USES_REDIS:
        return _memory
    if _failed_at and time.monotonic() - _failed_at < _RETRY_SECONDS:
        return _memory
    if _shared is None:
        _shared = storage_from_string(STORAGE_URI, **STORAGE_OPTIONS)
    return _shared


def _mark_failed():
    global _failed_at
    if not _failed_at or time.monotonic() - _failed_at >= _RETRY_SECONDS:
        log.error("Redis injoignable : compteurs anti-abus temporairement en mémoire.")
    _failed_at = time.monotonic()


def hit(name, key, window_seconds):
    """Ajoute 1 au compteur « name / key » (fenêtre de window_seconds) et renvoie le total."""
    full = f"{_PREFIX}{name}:{key}"
    try:
        return int(_storage().incr(full, window_seconds))
    except Exception:
        _mark_failed()
        return int(_memory.incr(full, window_seconds))


def count(name, key):
    """Valeur actuelle du compteur, sans l'augmenter."""
    full = f"{_PREFIX}{name}:{key}"
    try:
        return int(_storage().get(full) or 0)
    except Exception:
        _mark_failed()
        return int(_memory.get(full) or 0)


def check_config():
    """Au démarrage : signale une configuration absente ou un Redis qui ne répond pas."""
    if not USES_REDIS:
        if os.environ.get("RENDER"):
            log.warning("RATELIMIT_STORAGE_URI n'est pas définie : les limites anti-abus repartent "
                        "de zéro à chaque redémarrage. Créez une instance Render Key Value et "
                        "copiez son Internal URL dans cette variable.")
        return False
    try:
        ok = bool(_storage().check())
    except Exception:
        ok = False
    if not ok:
        log.error("Redis (RATELIMIT_STORAGE_URI) ne répond pas : repli en mémoire. "
                  "Vérifiez l'adresse et que l'instance Key Value est dans la même région.")
    return ok
'''

APP_OLD = '''limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    default_limits=["1000 per minute"],
    storage_uri="memory://",
)'''
APP_NEW = '''import shared_counters                     # audit MF-08 : compteurs dans Redis (Render Key Value)

limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    default_limits=["1000 per minute"],
    storage_uri=shared_counters.STORAGE_URI,
    storage_options=shared_counters.STORAGE_OPTIONS,
    in_memory_fallback_enabled=True,       # Redis en panne : le site continue, compteurs en mémoire
    key_prefix="menuflash",
)
shared_counters.check_config()'''

QR = [
    ('''_invalid_hits = SlidingWindow(INVALID_WINDOW)
''', '''_invalid_hits = SlidingWindow(INVALID_WINDOW)      # conservé pour compatibilité (plus utilisé)


def _invalid_hit(ip):
    """Audit MF-08 : faux QR codes comptés dans Redis (survit aux redémarrages)."""
    import shared_counters
    return shared_counters.hit("qr_invalide", ip, INVALID_WINDOW)


def _invalid_count(ip):
    import shared_counters
    return shared_counters.count("qr_invalide", ip)
''', "compteur des faux QR"),
    ('''        if _invalid_hits.count(ip) > AUDIT_PER_WINDOW + INVALID_LIMIT:''',
     '''        if _invalid_count(ip) > AUDIT_PER_WINDOW + INVALID_LIMIT:''', "lecture du compteur QR"),
    ('''        n = _invalid_hits.hit(ip)''', '''        n = _invalid_hit(ip)''', "ajout au compteur QR"),
]

REDIS_LINE = "redis==8.1.0"


def main():
    paths = {n: os.path.join(ROOT, n) for n in ("app.py", "qr_security.py", "requirements.txt")}
    for n, p in paths.items():
        if not os.path.exists(p):
            sys.exit(f"ARRÊT : {n} introuvable (lancez le script à la racine du projet).")
    app_t = open(paths["app.py"], encoding="utf-8").read()
    qr_t = open(paths["qr_security.py"], encoding="utf-8").read()
    req_t = open(paths["requirements.txt"], encoding="utf-8").read()
    if "import shared_counters" in app_t:
        print("MF-08 est déjà appliquée : rien à faire.")
        return
    if app_t.count(APP_OLD) != 1:
        sys.exit("ARRÊT : bloc « limiter = Limiter(...) » introuvable dans app.py. Aucun fichier n'a été modifié.")
    app_t = app_t.replace(APP_OLD, APP_NEW)
    for old, new, label in QR:
        if qr_t.count(old) != 1:
            sys.exit(f"ARRÊT : repère « {label} » introuvable dans qr_security.py. Aucun fichier n'a été modifié.")
        qr_t = qr_t.replace(old, new)
    if not re.search(r"(?im)^\s*flask-limiter\s*==", req_t):
        sys.exit("ARRÊT : requirements.txt ne contient pas Flask-Limiter. Aucun fichier n'a été modifié.")
    if not re.search(r"(?im)^\s*redis\s*==", req_t):
        req_t = req_t.rstrip("\n") + "\n" + REDIS_LINE + "\n"

    for n in ("app.py", "qr_security.py", "requirements.txt"):
        shutil.copy2(paths[n], paths[n] + ".avant-mf08")
    for n, text in (("app.py", app_t), ("qr_security.py", qr_t), ("requirements.txt", req_t)):
        with open(paths[n], "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        print(f"  modifié : {n}")
    with open(os.path.join(ROOT, "shared_counters.py"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(SHARED_COUNTERS)
    print("  créé    : shared_counters.py")
    print("\nMF-08 appliquée. Copies de sécurité : *.avant-mf08 (à ne pas pousser sur Git).")


if __name__ == "__main__":
    main()
