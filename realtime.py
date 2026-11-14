"""
Événements temps réel par restaurant (nouvelle commande, commande modifiée ou
annulée, thème changé).

Sécurité (audit MF-02) : plus aucun flux ouvert en permanence (Server-Sent
Events). Les événements sont gardés quelques minutes en mémoire, numérotés ;
l'écran Commandes les récupère par une requête courte (events_since). Un thread
du serveur n'est donc jamais bloqué, quel que soit le nombre d'écrans ouverts.

Limite connue (identique à l'ancienne version) : mémoire du processus. Avec
plusieurs workers gunicorn, il faudra un stockage partagé (Redis).
"""
import itertools
import threading
import time
from collections import deque

MAX_EVENTS_PER_RESTAURANT = 200      # au-delà, les plus anciens sont oubliés
MAX_EVENT_AGE_SECONDS = 600          # un événement de plus de 10 min n'est plus renvoyé
MAX_EVENTS_PER_RESPONSE = 50

_lock = threading.Lock()
_counter = itertools.count(1)
_last_seq = 0
_events = {}                         # restaurant_id -> deque[(seq, horodatage, type, data)]


def publish(restaurant_id, event_type, payload):
    """Enregistre un événement pour l'équipe du restaurant. Ne bloque jamais."""
    global _last_seq
    now = time.time()
    with _lock:
        seq = next(_counter)
        _last_seq = seq
        bucket = _events.get(restaurant_id)
        if bucket is None:
            bucket = _events[restaurant_id] = deque(maxlen=MAX_EVENTS_PER_RESTAURANT)
        bucket.append((seq, now, event_type, payload))
        # Ménage : restaurants sans activité récente.
        if len(_events) > 50 and seq % 100 == 0:
            for rid in [r for r, b in _events.items() if not b or now - b[-1][1] > MAX_EVENT_AGE_SECONDS]:
                _events.pop(rid, None)
    return seq


def events_since(restaurant_id, since):
    """Renvoie (événements, curseur).

    - since None (premier appel de la page) : aucun événement, seulement le
      curseur actuel (on ne rejoue pas l'historique : pas de son à l'ouverture).
    - since plus grand que le curseur (serveur redémarré) : aucun événement,
      le navigateur reprend au nouveau curseur.
    """
    now = time.time()
    with _lock:
        cursor = _last_seq
        if since is None or since >= cursor:
            return [], cursor
        bucket = _events.get(restaurant_id) or ()
        items = [{"type": t, "data": d} for (seq, ts, t, d) in bucket
                 if seq > since and now - ts <= MAX_EVENT_AGE_SECONDS]
    return items[-MAX_EVENTS_PER_RESPONSE:], cursor
