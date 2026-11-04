"""
MenuFlash Mini — Numéro de commande (référence) à 10 caractères.

Branchement dans app.py, JUSTE AVANT init_takeaway(...) :

    from order_refs import init_order_refs
    init_order_refs(app, order_hooks=ORDER_CREATED_HOOKS)

Principe (comme DoorDash)
-------------------------
- Chaque nouvelle commande (sur place ou à emporter) reçoit une référence de
  10 caractères tirés au hasard, par exemple « 7KM4XP92DT ».
- Les 3 derniers caractères (« 2DT ») servent de CODE DE RETRAIT : au
  comptoir, le client donne ces 3 caractères OU le numéro complet.
- Caractères sans ambiguïté : pas de 0/O, 1/I/L, Q, B/8, S/5, Z/2.
- Tirage avec le module « secrets » (impossible à deviner).
- Unicité : la référence complète est unique dans toute la base, et le code
  de retrait est unique parmi les commandes EN COURS du même restaurant
  (deux clients présents en même temps n'ont jamais le même code).

Confidentialité du code de retrait
----------------------------------
Les pages publiques de suivi sont adressées par le numéro interne de la
commande (/m/<slug>/commande/<id>), qui se suit. Pour qu'un inconnu ne puisse
pas lire le code de retrait d'un autre client, la référence n'est montrée côté
client QU'AU TÉLÉPHONE QUI A PASSÉ LA COMMANDE (cookie signé « mf_my_orders »,
valable 48 h). Elle figure aussi dans l'email de confirmation. L'équipe du
restaurant, connectée, la voit toujours.

Stockage : nouvelle table « order_references » (aucune table existante n'est
modifiée, rien à faire sur Render). Elle est incluse dans les sauvegardes.
Les anciennes commandes, sans référence, restent affichées avec « #id ».
"""
import logging
import secrets
from datetime import datetime, timedelta

from flask import g, request
from itsdangerous import URLSafeTimedSerializer, BadSignature
from sqlalchemy.exc import IntegrityError

from models import db, Order

log = logging.getLogger("menuflash.refs")

ALPHABET = "23456789ACDEFGHJKMNPRTUVWXY"      # sans 0 1 I L O Q B S Z
LENGTH = 10
CODE_LENGTH = 3
ACTIVE_STATUSES = ("nouvelle", "en_preparation", "prete")
ACTIVE_WINDOW_HOURS = 24
MAX_TRIES = 25
OWNED_COOKIE = "mf_my_orders"
OWNED_MAX_AGE = 48 * 3600
OWNED_MAX_IDS = 30

_state = {"serializer": None}


class OrderReference(db.Model):
    __tablename__ = "order_references"
    order_id = db.Column(db.Integer, primary_key=True)
    restaurant_id = db.Column(db.Integer, nullable=False, index=True)
    reference = db.Column(db.String(LENGTH), unique=True, nullable=False)
    pickup_code = db.Column(db.String(CODE_LENGTH), nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


def _draw():
    return "".join(secrets.choice(ALPHABET) for _ in range(LENGTH))


def _code_in_use(restaurant_id, code):
    since = datetime.utcnow() - timedelta(hours=ACTIVE_WINDOW_HOURS)
    return (db.session.query(OrderReference.order_id)
            .join(Order, Order.id == OrderReference.order_id)
            .filter(OrderReference.restaurant_id == restaurant_id,
                    OrderReference.pickup_code == code,
                    Order.status.in_(ACTIVE_STATUSES),
                    Order.created_at >= since)
            .first() is not None)


def get_reference(order_id):
    row = db.session.get(OrderReference, order_id)
    return row.reference if row else None


def ensure_reference(order):
    """Renvoie la référence de la commande, en la créant si besoin."""
    existing = db.session.get(OrderReference, order.id)
    if existing:
        return existing.reference
    for _ in range(MAX_TRIES):
        ref = _draw()
        code = ref[-CODE_LENGTH:]
        if _code_in_use(order.restaurant_id, code):
            continue
        if db.session.query(OrderReference.order_id).filter_by(reference=ref).first():
            continue
        db.session.add(OrderReference(order_id=order.id, restaurant_id=order.restaurant_id,
                                      reference=ref, pickup_code=code))
        try:
            db.session.commit()
            return ref
        except IntegrityError:
            db.session.rollback()            # tirage identique au même instant : on recommence
            existing = db.session.get(OrderReference, order.id)
            if existing:
                return existing.reference
    log.error("Impossible de générer une référence pour la commande %s", order.id)
    return None


def references_for(order_ids):
    """{order_id: reference} pour une liste de commandes (une seule requête)."""
    ids = [i for i in order_ids if i]
    if not ids:
        return {}
    rows = OrderReference.query.filter(OrderReference.order_id.in_(ids)).all()
    return {r.order_id: r.reference for r in rows}


def display_reference(order_id, ref=None):
    """Texte à afficher : la référence, ou « #id » pour une ancienne commande."""
    ref = ref if ref is not None else get_reference(order_id)
    return ref or f"#{order_id}"


def _owned_ids():
    """Commandes passées depuis CE téléphone (cookie signé, impossible à falsifier)."""
    cached = getattr(g, "_mf_owned", None)
    if cached is not None:
        return cached
    ids = set()
    raw = request.cookies.get(OWNED_COOKIE, "")
    if raw and _state["serializer"] is not None:
        try:
            data = _state["serializer"].loads(raw, max_age=OWNED_MAX_AGE)
            ids = {int(i) for i in data if isinstance(i, int)}
        except (BadSignature, TypeError, ValueError):
            ids = set()
    new_id = getattr(g, "_mf_owned_new", None)
    if new_id:
        ids.add(new_id)
    g._mf_owned = ids
    return ids


def client_reference(order_id):
    """Référence affichée au CLIENT : seulement s'il a passé cette commande depuis ce téléphone."""
    if order_id in _owned_ids():
        return get_reference(order_id)
    return None


def init_order_refs(app, *, order_hooks):
    _state["serializer"] = URLSafeTimedSerializer(app.config["SECRET_KEY"], salt="mf-my-orders")

    with app.app_context():
        try:
            OrderReference.__table__.create(db.engine, checkfirst=True)
        except Exception:
            log.exception("Création de la table order_references impossible")

    def _on_order_created(order):
        g._mf_owned_new = order.id
        g._mf_owned = None
        try:
            ensure_reference(order)
        except Exception:
            db.session.rollback()
            log.exception("Référence de commande impossible (commande %s)", order.id)

    # Doit passer AVANT le module « à emporter », qui utilise la référence.
    order_hooks.insert(0, _on_order_created)

    @app.after_request
    def _remember_owned_order(response):
        new_id = getattr(g, "_mf_owned_new", None)
        if new_id:
            g._mf_owned = None
            ids = sorted(_owned_ids())[-OWNED_MAX_IDS:]
            response.set_cookie(OWNED_COOKIE, _state["serializer"].dumps(ids), max_age=OWNED_MAX_AGE,
                                httponly=True, samesite="Lax",
                                secure=app.config.get("SESSION_COOKIE_SECURE", False))
        return response

    app.jinja_env.globals["order_reference"] = get_reference
    app.jinja_env.globals["client_order_reference"] = client_reference
    app.jinja_env.globals["order_reference_display"] = display_reference
