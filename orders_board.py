"""
MenuFlash Mini — Écran des commandes (cuisine) et historique.

Branchement dans app.py, JUSTE AVANT `from backups import init_backups` :

    from orders_board import init_orders_board
    init_orders_board(app, active_restaurant=active_restaurant, log_audit=log_audit)

Ce que fait le module
---------------------
- /admin/commandes/tableau.json : commandes en cours (nouvelle, en préparation,
  prête) avec le temps d'attente, le temps de préparation prévu et l'heure de
  passage à « Prête ». Alimente l'écran cuisine (static/orders_board.js).
- /admin/commandes/historique.json : historique paginé (30 par page), les plus
  récentes en premier, filtres Aujourd'hui / Servies / Annulées / Tout.
  Accessible au gestionnaire ET aux cuisiniers.
- /admin/commandes/<id>/remettre-prete : remet en « Prête » une commande
  marquée « Servie » par erreur, pendant 30 minutes seulement.

Sécurité
--------
- Toutes les routes exigent d'être connecté (gestionnaire ou cuisinier) et ne
  renvoient QUE les commandes du restaurant actif (isolation stricte).
- Le super-admin n'a pas accès à ces routes (il ne gère pas de restaurant).
- « Remettre en Prête » : POST + jeton CSRF, limité aux commandes servies il y a
  moins de 30 minutes, tracé dans le journal de sécurité.
- Les textes (plats, notes, tables) sont affichés par textContent côté
  navigateur : aucune injection HTML possible.
"""
import os
from datetime import datetime, timedelta

from flask import jsonify, request, abort
from flask_login import login_required, current_user

from models import db, Order, DEFAULT_PREP_MINUTES

ACTIVE_STATUSES = ("nouvelle", "en_preparation", "prete")
UNDO_SERVED_MINUTES = 30
HISTORY_PAGE_SIZE = 30
HISTORY_FILTERS = ("today", "servie", "annulee", "all")


def _utc_offset():
    try:
        minutes = int(os.environ.get("MENUFLASH_UTC_OFFSET_MINUTES", "0"))
    except ValueError:
        minutes = 0
    return timedelta(minutes=max(-720, min(minutes, 840)))


def _local(dt):
    return (dt + _utc_offset()) if dt else None


def _minutes_since(dt, now):
    if not dt:
        return 0
    return max(int((now - dt).total_seconds() // 60), 0)


def _items(order):
    return [{"name": it.item_name, "quantity": it.quantity} for it in order.items]


def _prep_minutes(order):
    try:
        return int(order.estimated_prep_minutes or DEFAULT_PREP_MINUTES)
    except Exception:
        return DEFAULT_PREP_MINUTES


def board_dict(order, now):
    created = order.created_at or now
    changed = order.updated_at or created
    return {
        "id": order.id,
        "status": order.status,
        "table": order.table_label or "Sans table",
        "notes": order.notes or "",
        "total": order.total,
        "items": _items(order),
        "elapsed": _minutes_since(created, now),
        "prep": _prep_minutes(order),
        "status_minutes": _minutes_since(changed, now),
        "time": _local(created).strftime("%H:%M"),
    }


def history_dict(order, now):
    created = order.created_at or now
    changed = order.updated_at or created
    can_undo = (order.status == "servie"
                and changed >= now - timedelta(minutes=UNDO_SERVED_MINUTES))
    return {
        "id": order.id,
        "status": order.status,
        "cancelled_by": order.cancelled_by,
        "table": order.table_label or "Sans table",
        "notes": order.notes or "",
        "total": order.total,
        "items": _items(order),
        "created": _local(created).strftime("%d/%m %H:%M"),
        "created_time": _local(created).strftime("%H:%M"),
        "finished_time": _local(changed).strftime("%H:%M"),
        "today": _local(created).date() == _local(now).date(),
        "can_undo": can_undo,
    }


def init_orders_board(app, *, active_restaurant, log_audit=None):

    def _restaurant():
        if not current_user.is_authenticated or getattr(current_user, "is_super_admin", False):
            abort(403)
        r = active_restaurant()
        if r is None:
            abort(403)
        return r

    def _json(payload):
        resp = jsonify(payload)
        resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.route("/admin/commandes/tableau.json")
    @login_required
    def orders_board_feed():
        r = _restaurant()
        now = datetime.utcnow()
        orders = (Order.query
                  .filter(Order.restaurant_id == r.id, Order.status.in_(ACTIVE_STATUSES))
                  .order_by(Order.created_at.asc())
                  .limit(300).all())
        data = [board_dict(o, now) for o in orders]
        waiting = [o["elapsed"] for o in data if o["status"] != "prete"]
        oldest = max((o for o in data if o["status"] != "prete"),
                     key=lambda o: o["elapsed"], default=None)
        return _json({
            "orders": data,
            "summary": {
                "active": len(data),
                "avg_wait": round(sum(waiting) / len(waiting)) if waiting else 0,
                "oldest_table": oldest["table"] if oldest else None,
                "oldest_minutes": oldest["elapsed"] if oldest else 0,
            },
            "server_time": _local(now).strftime("%H:%M"),
        })

    @app.route("/admin/commandes/historique.json")
    @login_required
    def orders_history_feed():
        r = _restaurant()
        now = datetime.utcnow()
        flt = request.args.get("filtre", "today")
        if flt not in HISTORY_FILTERS:
            flt = "today"
        try:
            page = max(1, min(int(request.args.get("page", 1)), 10000))
        except ValueError:
            page = 1

        q = Order.query.filter(Order.restaurant_id == r.id,
                               Order.status.in_(("servie", "annulee")))
        if flt == "today":
            local_now = _local(now)
            start_local = datetime(local_now.year, local_now.month, local_now.day)
            q = q.filter(Order.created_at >= start_local - _utc_offset())
        elif flt in ("servie", "annulee"):
            q = q.filter(Order.status == flt)

        total = q.count()
        pages = max(1, (total + HISTORY_PAGE_SIZE - 1) // HISTORY_PAGE_SIZE)
        page = min(page, pages)
        rows = (q.order_by(Order.updated_at.desc(), Order.id.desc())
                .offset((page - 1) * HISTORY_PAGE_SIZE).limit(HISTORY_PAGE_SIZE).all())
        return _json({
            "orders": [history_dict(o, now) for o in rows],
            "page": page, "pages": pages, "total": total, "filter": flt,
        })

    @app.route("/admin/commandes/<int:order_id>/remettre-prete", methods=["POST"])
    @login_required
    def orders_undo_served(order_id):
        r = _restaurant()
        order = Order.query.filter_by(id=order_id, restaurant_id=r.id).first_or_404()
        now = datetime.utcnow()
        changed = order.updated_at or order.created_at or now
        if order.status != "servie" or changed < now - timedelta(minutes=UNDO_SERVED_MINUTES):
            return _json({"ok": False, "error": "Cette commande ne peut plus être remise en « Prête »."}), 409
        order.status = "prete"
        db.session.commit()
        if log_audit:
            log_audit("order_undo_served", restaurant_id=r.id, user_email=current_user.email,
                      details=f"commande #{order.id} ({order.table_label or 'sans table'}) remise en « Prête »")
        return _json({"ok": True, "status": order.status})
