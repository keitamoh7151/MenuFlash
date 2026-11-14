"""
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
