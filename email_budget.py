"""
Plafonds d'envoi d'emails (audit lot 3) : un visiteur anonyme ne doit pas pouvoir faire envoyer
des emails en rafale à une adresse (harcèlement) ni épuiser le quota quotidien du service
d'envoi (300 par jour sur l'offre gratuite de Brevo), qui sert aussi aux réinitialisations de
mot de passe et aux invitations.

allow(nom, clé, limite, fenêtre) -> True si l'envoi est permis (et le compte). Utilise Redis
(shared_counters.py, MF-08) s'il est installé, sinon la mémoire du processus.
L'adresse n'est jamais conservée en clair : seule une empreinte sert de clé.
"""
import hashlib
import threading
import time

_lock = threading.Lock()
_memory = {}


def _digest(key):
    return hashlib.sha256(str(key).strip().lower().encode("utf-8")).hexdigest()[:24]


def allow(name, key, limit, window_seconds):
    digest = _digest(key)
    try:
        import shared_counters
        return int(shared_counters.hit(f"email-{name}", digest, window_seconds)) <= limit
    except Exception:
        pass
    now = time.monotonic()
    with _lock:
        if len(_memory) > 5000:
            for k in [k for k, (start, _) in _memory.items() if now - start > 86400]:
                _memory.pop(k, None)
        start, count = _memory.get((name, digest), (now, 0))
        if now - start > window_seconds:
            start, count = now, 0
        count += 1
        _memory[(name, digest)] = (start, count)
        return count <= limit
