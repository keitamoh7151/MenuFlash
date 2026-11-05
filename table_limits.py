"""
MenuFlash Mini — Limites de commandes en attente, réglables par chaque restaurant.

Branchement dans app.py, JUSTE AVANT « from hardening import init_hardening » :

    from table_limits import init_table_limits
    init_table_limits(app, owner_required=owner_required,
                      active_restaurant=active_restaurant, log_audit=log_audit)

Principe
--------
La protection anti-fausses commandes (security_plus.py) refuse une nouvelle
commande quand trop de commandes « Nouvelle » (pas encore prises en charge par
la cuisine) attendent déjà, sur les 15 dernières minutes. Deux limites sont
réglables par chaque gestionnaire, sans toucher au code ni à Render :

  1. Commandes SUR PLACE en attente, PAR TABLE       -> page Codes QR
  2. Commandes À EMPORTER en attente, pour tout le restaurant
                                                      -> page À emporter

Dès qu'une commande passe en « En préparation », elle ne compte plus.

                      Par table (sur place)     À emporter
  Valeur par défaut   10                        15
  Minimum             2                         3
  Plafond             30                        200
  Variable Render     MAX_PENDING_ORDERS_PER_TABLE   MAX_PENDING_TAKEAWAY
  Plafond (Render)    ..._PER_TABLE_CEILING          MAX_PENDING_TAKEAWAY_CEILING

Le plafond reste sous le contrôle de l'éditeur : un restaurateur ne peut jamais
le dépasser. Les autres protections ne changent pas (6 commandes par téléphone
sur 10 minutes, 80 commandes en attente pour tout le restaurant, capacité par
créneau de retrait). Chaque changement est tracé dans le journal de sécurité.

Table « restaurant_order_limits » : créée automatiquement au démarrage. Si elle
existe déjà (version précédente), la colonne manquante est ajoutée toute seule.
Incluse dans les sauvegardes et supprimée avec le restaurant.
"""
import logging
import os
from datetime import datetime

import sqlalchemy as sa
from flask import flash, redirect, request, url_for
from flask_login import current_user

from models import db

log = logging.getLogger("menuflash.table_limits")

# Paramètres de chaque limite.
KINDS = {
    "table": {
        "env": "MAX_PENDING_ORDERS_PER_TABLE", "default": 10, "low": 2, "high": 50,
        "ceiling_env": "MAX_PENDING_ORDERS_PER_TABLE_CEILING", "ceiling_default": 30,
        "ceiling_low": 5, "ceiling_high": 50,
        "column": "max_pending_per_table", "field": "max_pending_per_table",
        "audit": "table_limit_changed",
        "what": "commandes en attente par table",
    },
    "takeaway": {
        "env": "MAX_PENDING_TAKEAWAY", "default": 15, "low": 3, "high": 200,
        "ceiling_env": "MAX_PENDING_TAKEAWAY_CEILING", "ceiling_default": 200,
        "ceiling_low": 5, "ceiling_high": 500,
        "column": "max_pending_takeaway", "field": "max_pending_takeaway",
        "audit": "takeaway_limit_changed",
        "what": "commandes à emporter en attente",
    },
}


def _env_int(name, default, low, high):
    try:
        return max(low, min(int(os.environ.get(name, default)), high))
    except (TypeError, ValueError):
        return default


def min_limit(kind):
    return KINDS[kind]["low"]


def default_limit(kind):
    k = KINDS[kind]
    return _env_int(k["env"], k["default"], k["low"], k["high"])


def ceiling_limit(kind):
    k = KINDS[kind]
    # Le plafond n'est jamais inférieur à la valeur par défaut.
    return max(default_limit(kind),
               _env_int(k["ceiling_env"], k["ceiling_default"], k["ceiling_low"], k["ceiling_high"]))


class RestaurantOrderLimit(db.Model):
    __tablename__ = "restaurant_order_limits"
    restaurant_id = db.Column(db.Integer, primary_key=True)
    max_pending_per_table = db.Column(db.Integer, nullable=True)     # vide = valeur par défaut
    max_pending_takeaway = db.Column(db.Integer, nullable=True)      # vide = valeur par défaut
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    updated_by = db.Column(db.String(120))


def resolve_limit(custom, default, ceiling, low):
    """Limite effective : valeur du restaurant (bornée), sinon valeur par défaut."""
    if custom is None:
        return default
    return max(low, min(int(custom), ceiling))


def get_limit(restaurant_id, kind):
    row = db.session.get(RestaurantOrderLimit, restaurant_id) if restaurant_id else None
    custom = getattr(row, KINDS[kind]["column"], None) if row else None
    return resolve_limit(custom, default_limit(kind), ceiling_limit(kind), min_limit(kind))


def get_table_limit(restaurant_id):
    """Commandes sur place en attente, par table (utilisé par security_plus.py)."""
    return get_limit(restaurant_id, "table")


def get_takeaway_limit(restaurant_id):
    """Commandes à emporter en attente, pour tout le restaurant (utilisé par security_plus.py)."""
    return get_limit(restaurant_id, "takeaway")


def restaurant_wide_limit(restaurant_id, base):
    """Limite globale de commandes en attente pour tout le restaurant (sur place + emporter).
    Elle ne doit jamais être plus basse que la limite à emporter du restaurant : sinon un
    fast-food réglé à 200 serait bloqué à 80."""
    try:
        return max(base, get_takeaway_limit(restaurant_id))
    except Exception:
        db.session.rollback()
        return base


def pending_takeaway_count(restaurant_id, since):
    """Commandes à emporter « Nouvelle » créées depuis `since`."""
    from models import Order
    from takeaway import TakeawayOrder
    return (db.session.query(Order.id)
            .join(TakeawayOrder, TakeawayOrder.order_id == Order.id)
            .filter(Order.restaurant_id == restaurant_id, Order.status == "nouvelle",
                    Order.created_at >= since)
            .count())


def _ensure_columns(app):
    """Ajoute les colonnes manquantes si la table existe déjà (version précédente)."""
    with app.app_context():
        try:
            RestaurantOrderLimit.__table__.create(db.engine, checkfirst=True)
            insp = sa.inspect(db.engine)
            existing = {c["name"] for c in insp.get_columns("restaurant_order_limits")}
            for kind in KINDS.values():
                if kind["column"] not in existing:
                    with db.engine.begin() as conn:
                        conn.execute(sa.text(
                            f'ALTER TABLE restaurant_order_limits ADD COLUMN {kind["column"]} INTEGER'))
                    log.warning("Colonne ajoutée : restaurant_order_limits.%s", kind["column"])
        except Exception:
            db.session.rollback()
            log.exception("Préparation de la table restaurant_order_limits impossible")


def init_table_limits(app, *, owner_required, active_restaurant, log_audit):
    _ensure_columns(app)

    def make_save(kind, back_endpoint, feature=None):
        spec = KINDS[kind]
        field = spec["field"]

        def save():
            restaurant = active_restaurant()
            back = redirect(url_for(back_endpoint))
            if restaurant is None:
                return back
            if feature:
                try:
                    from plans import has_feature
                    if not has_feature(restaurant, feature):
                        flash("Cette fonctionnalité n'est pas incluse dans votre formule.", "error")
                        return back
                except Exception:
                    db.session.rollback()
            ceiling, low = ceiling_limit(kind), min_limit(kind)
            row = db.session.get(RestaurantOrderLimit, restaurant.id)
            before = get_limit(restaurant.id, kind)

            if request.form.get("action") == "reset":
                if row:
                    setattr(row, spec["column"], None)
                    row.updated_by = current_user.email
                db.session.commit()
                new = get_limit(restaurant.id, kind)
                flash(f"Limite rétablie à la valeur par défaut : {new} {spec['what']}.", "success")
            else:
                raw = (request.form.get(field) or "").strip()
                if not raw.isdigit():
                    flash("Indiquez un nombre entier.", "error")
                    return back
                value = int(raw)
                if not (low <= value <= ceiling):
                    flash(f"La limite doit être comprise entre {low} et {ceiling}.", "error")
                    return back
                if row is None:
                    row = RestaurantOrderLimit(restaurant_id=restaurant.id)
                    db.session.add(row)
                setattr(row, spec["column"], None if value == default_limit(kind) else value)
                row.updated_by = current_user.email
                db.session.commit()
                new = get_limit(restaurant.id, kind)
                flash(f"Limite enregistrée : {new} {spec['what']}.", "success")

            if new != before:
                try:
                    log_audit(spec["audit"], restaurant_id=restaurant.id, user_email=current_user.email,
                              details=f"{spec['what']} : {before} -> {new}")
                except Exception:
                    log.exception("Trace du changement de limite impossible")
            return back

        save.__name__ = f"save_{kind}_limit"
        return save

    app.add_url_rule("/admin/qrcodes/limite", "admin_table_limit",
                     owner_required(make_save("table", "admin_qrcodes")), methods=["POST"])
    app.add_url_rule("/admin/a-emporter/limite", "admin_takeaway_limit",
                     owner_required(make_save("takeaway", "admin_takeaway_settings", feature="takeaway")),
                     methods=["POST"])

    def info(restaurant_id, kind):
        row = db.session.get(RestaurantOrderLimit, restaurant_id)
        return {
            "value": get_limit(restaurant_id, kind),
            "default": default_limit(kind),
            "ceiling": ceiling_limit(kind),
            "min": min_limit(kind),
            "custom": bool(row and getattr(row, KINDS[kind]["column"]) is not None),
        }

    @app.context_processor
    def _inject_limits():
        endpoint = request.endpoint
        if endpoint not in ("admin_qrcodes", "admin_takeaway_settings"):
            return {}
        try:
            restaurant = active_restaurant()
            if restaurant is None:
                return {}
            if endpoint == "admin_qrcodes":
                return {"mf_table_limit": info(restaurant.id, "table")}
            return {"mf_takeaway_limit": info(restaurant.id, "takeaway")}
        except Exception:
            db.session.rollback()
            return {}
