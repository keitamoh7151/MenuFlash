"""
Diffusion d'événements en temps réel (Server-Sent Events), par restaurant.
"""
import json
import queue
import threading

_subscribers = {}
_lock = threading.Lock()


def subscribe(restaurant_id):
    q = queue.Queue()
    with _lock:
        _subscribers.setdefault(restaurant_id, []).append(q)
    return q


def unsubscribe(restaurant_id, q):
    with _lock:
        subs = _subscribers.get(restaurant_id)
        if subs and q in subs:
            subs.remove(q)
            if not subs:
                _subscribers.pop(restaurant_id, None)


def publish(restaurant_id, event_type, payload):
    message = json.dumps({"type": event_type, "data": payload})
    with _lock:
        subs = list(_subscribers.get(restaurant_id, []))
    for q in subs:
        q.put(message)


def format_sse(message):
    return f"data: {message}\n\n"
