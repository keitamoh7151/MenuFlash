"""
MenuFlash Mini — « Modifier la commande » : le client retire, ajoute ou change la quantité d'un plat.

Branchement dans app.py, JUSTE AVANT « from hardening import init_hardening »
(le script ajouter_modifier_commande.py le fait pour vous) :

    from order_edit import init_order_edit
    init_order_edit(app, limiter=limiter)

Pour le client
--------------
Tant que la cuisine n'a pas commencé la préparation (statut « Nouvelle »), un SEUL bouton
« Modifier la commande » (dans « Mes commandes » et sur la page de suivi) ouvre une fenêtre :
  - « − » / « + » changent la quantité d'un plat ;
  - « Retirer » enlève un plat en entier (avec confirmation) ;
  - « Ajouter d'autres plats » ouvre le menu en mode ajout (parcours « Ajouter des plats »
    qui existait déjà, inchangé) ;
  - le nouveau total s'affiche aussitôt.
Une fois la commande « En préparation », le bouton disparaît (comme « Annuler »).

Règles (les mêmes que « Annuler » et « Ajouter des plats », voir app.py)
-----------------------------------------------------------------------
- Seul le téléphone qui a passé la commande peut la modifier (cookie signé « mf_my_orders »,
  voir order_refs.py) ; les autres reçoivent « pas passée depuis cet appareil ».
- Refusé si la commande n'est plus « Nouvelle » (« trop tard »).
- On ne peut pas retirer le DERNIER plat : il faut annuler la commande.
- AUGMENTER une quantité = ajouter un plat, donc : abonnement valide, commandes non bloquées
  par le super-admin, plat toujours DISPONIBLE au menu, au même PRIX que lors de la commande
  (sinon le client ajoute le plat depuis le menu), 50 unités maximum par plat.
  DIMINUER ou RETIRER reste toujours possible tant que la commande est « Nouvelle ».
- Le serveur relit le statut APRÈS avoir verrouillé la commande : si la cuisine clique
  « Commencer la préparation » au même instant, l'un des deux passe en premier, jamais les deux.
- Anti double-clic : la requête précise la quantité que le client voyait (« expected ») ; si elle
  a changé entre-temps, rien n'est modifié et la liste à jour est renvoyée.
- La cuisine est prévenue en temps réel (événement « commande modifiée » : son et tableau).
- Chaque modification est tracée dans le journal de sécurité (« order_item_changed »),
  sans donnée personnelle.

Adresses
--------
  GET  /m/<slug>/commande/<id>/lignes.json   lignes (id, nom, quantité, prix, can_increase), total, menu_url
  POST /m/<slug>/commande/<id>/modifier      champs : line_id, quantity (NOUVELLE quantité, 0 = retirer), expected

Rien n'est modifié dans les routes existantes : si ce module ne peut pas démarrer, l'application
fonctionne comme avant, avec ses boutons « Ajouter des plats » et « Annuler ». Aucune nouvelle
table, aucune nouvelle dépendance.
"""
import inspect
import logging

from flask import jsonify, request, url_for

from models import MenuItem, Order, OrderItem, QRCode, db

log = logging.getLogger("menuflash.order_edit")

MAX_LINE_QUANTITY = 50          # comme _parse_requested_items (app.py) : 50 unités maximum par plat
RATE_LIMIT = "60 per 10 minutes"
NEEDED = ("tenant_or_404", "_order_to_dict", "_order_feed_dict", "EDITABLE_STATUS")


def _app_context(app):
    """Fonctions et constantes de app.py, lues depuis la route « add_to_order » existante."""
    view = app.view_functions.get("add_to_order")
    if view is None:
        return None
    scope = inspect.unwrap(view).__globals__
    if any(name not in scope for name in NEEDED):
        return None
    return scope


def _owned(order_id):
    """Cette commande a-t-elle été passée depuis cet appareil ? (cookie signé d'order_refs.py)"""
    try:
        from order_refs import _owned_ids
        return order_id in _owned_ids()
    except Exception:
        log.exception("Vérification du propriétaire de la commande impossible")
        return False


def _no_store(payload, status=200):
    resp = jsonify(payload)
    resp.status_code = status
    resp.headers["Cache-Control"] = "no-store"
    return resp


def _int(value):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def plan_change(lines, line_id, new_quantity, expected):
    """
    Décide ce qu'il faut faire. Fonction pure (testée).

    lines        : [{id, quantity, …}] lignes actuelles de la commande
    new_quantity : NOUVELLE quantité de la ligne (0 = retirer le plat)
    expected     : quantité que le client voyait (None = pas de contrôle)
    renvoie      : ("ok", {"action": "remove"|"decrease"|"increase", "new_quantity", "delta"})
                   ("not_found", None) | ("invalid", message) | ("stale", None) | ("last_item", None)
    """
    line = next((l for l in lines if l["id"] == line_id), None)
    if line is None:
        return "not_found", None
    if (not isinstance(new_quantity, int) or isinstance(new_quantity, bool)
            or not 0 <= new_quantity <= MAX_LINE_QUANTITY):
        return "invalid", f"Quantité invalide (de 0 à {MAX_LINE_QUANTITY})."
    if expected is not None and expected != line["quantity"]:
        return "stale", None
    old = line["quantity"]
    if new_quantity == old:
        return "invalid", "La quantité n'a pas changé."
    if new_quantity == 0:
        if len(lines) <= 1:
            return "last_item", None
        return "ok", {"action": "remove", "new_quantity": 0, "delta": -old}
    if new_quantity < old:
        return "ok", {"action": "decrease", "new_quantity": new_quantity, "delta": new_quantity - old}
    return "ok", {"action": "increase", "new_quantity": new_quantity, "delta": new_quantity - old}


def init_order_edit(app, *, limiter=None):
    if "order_modify_item" in app.view_functions:
        return                                                  # déjà branché
    scope = _app_context(app)
    if scope is None:
        log.error("order_edit : route « add_to_order » introuvable ou incomplète : modification non activée.")
        return

    tenant_or_404 = scope["tenant_or_404"]
    to_dict, feed_dict = scope["_order_to_dict"], scope["_order_feed_dict"]
    editable = scope["EDITABLE_STATUS"]
    labels = scope.get("ORDER_STATUS_LABELS", {})
    realtime = scope.get("realtime")
    log_audit = scope.get("log_audit")

    LOCKED_MSG = ("Cette commande est déjà en préparation et ne peut plus être modifiée. "
                  "Pour ajouter des plats, passez une nouvelle commande.")
    NOT_MINE = "Cette commande n'a pas été passée depuis cet appareil."

    # ------------------------------------------------------------------ utilitaires
    def get_order(slug, order_id, lock=False):
        restaurant = tenant_or_404(slug)
        query = Order.query.filter_by(id=order_id, restaurant_id=restaurant.id)
        if lock:
            # of=Order : PostgreSQL refuse FOR UPDATE sur une jointure externe ; on ne verrouille que la commande.
            # populate_existing : sans lui, SQLAlchemy garde l'ancien statut déjà chargé par la
            # première lecture, et ne verrait pas que la cuisine vient de commencer la préparation.
            query = query.with_for_update(of=Order).populate_existing()
        return restaurant, query.first()

    def menu_items_for(restaurant, order):
        ids = [i.menu_item_id for i in order.items if i.menu_item_id]
        if not ids:
            return {}
        rows = MenuItem.query.filter(MenuItem.restaurant_id == restaurant.id, MenuItem.id.in_(ids)).all()
        return {m.id: m for m in rows}

    def increase_problem(restaurant, line, menu_item):
        """Pourquoi on ne peut pas ajouter une unité de cette ligne (None = on peut)."""
        if not getattr(restaurant, "is_subscription_valid", True):
            return "Ce restaurant n'accepte plus de commandes."
        try:
            from sa_controls import control_for
            control = control_for(restaurant.id)
            if control is not None and control.orders_blocked:
                return "Ce restaurant n'accepte pas de commandes en ligne pour le moment."
        except Exception:
            db.session.rollback()
        if menu_item is None or not menu_item.is_available:
            return "Ce plat n'est plus disponible : vous ne pouvez plus en ajouter."
        if int(menu_item.price or 0) != int(line.item_price or 0):
            return ("Le prix de ce plat a changé : ajoutez-le depuis le menu "
                    "(bouton « Ajouter d'autres plats »).")
        if int(line.quantity or 0) >= MAX_LINE_QUANTITY:
            return f"Maximum {MAX_LINE_QUANTITY} unités par plat."
        return None

    def lines_payload(restaurant, order):
        menu = menu_items_for(restaurant, order)
        out = []
        for it in sorted(order.items, key=lambda i: i.id):
            problem = increase_problem(restaurant, it, menu.get(it.menu_item_id))
            out.append({"id": it.id, "name": it.item_name, "quantity": int(it.quantity or 0),
                        "price": int(it.item_price or 0), "can_increase": problem is None})
        return out

    def is_takeaway(order_id):
        try:
            from takeaway import TakeawayOrder
            return db.session.get(TakeawayOrder, order_id) is not None
        except Exception:
            db.session.rollback()
            return False

    def menu_url(restaurant, order):
        """Menu à ouvrir pour « Ajouter d'autres plats » (commande à emporter : lien général ; sinon sa table)."""
        params = {"slug": restaurant.slug, "ajouter": order.id}
        if not is_takeaway(order.id):
            # Lien destiné au CLIENT : la table n'y figure que par son NOM (qr_security.py), jamais par un code.
            # Le droit de commander à cette table vient de son QR scanné. Sans QR (ancienne commande) : menu général.
            qr_id = getattr(order, "qr_code_id", None)
            if not qr_id and order.table_label:
                row = QRCode.query.filter_by(restaurant_id=restaurant.id, label=order.table_label).first()
                qr_id = row.id if row else None
            if qr_id:
                try:
                    from qr_security import qr_name_for
                    params["qr"] = qr_name_for(qr_id)
                except Exception:
                    pass
        return url_for("public_menu", **params)

    # ------------------------------------------------------------------ lecture
    def order_lines_json(slug, order_id):
        restaurant, order = get_order(slug, order_id)
        if order is None or not _owned(order_id):
            return _no_store({"ok": False, "error": NOT_MINE}, 404)
        return _no_store({"ok": True, "editable": order.status == editable, "status": order.status,
                          "status_label": labels.get(order.status, order.status),
                          "lines": lines_payload(restaurant, order), "total": int(order.total or 0),
                          "menu_url": menu_url(restaurant, order)})

    # ------------------------------------------------------------------ modification
    def order_modify_item(slug, order_id):
        # Première lecture sans verrou : réponse « introuvable » identique pour toute commande
        # qui n'est pas la vôtre (on ne révèle pas son existence).
        restaurant, order = get_order(slug, order_id)
        if order is None or not _owned(order_id):
            return _no_store({"ok": False, "error": NOT_MINE}, 404)

        line_id, new_quantity = _int(request.form.get("line_id")), _int(request.form.get("quantity"))
        raw_expected = request.form.get("expected")
        expected = _int(raw_expected) if raw_expected not in (None, "") else None
        if line_id is None or new_quantity is None:
            return _no_store({"ok": False, "error": "Demande invalide."}, 400)

        try:
            # On verrouille la commande puis on RELIT son statut : la cuisine peut avoir avancé entre-temps.
            restaurant, order = get_order(slug, order_id, lock=True)
            if order is None:
                db.session.rollback()
                return _no_store({"ok": False, "error": NOT_MINE}, 404)
            if order.status != editable:
                db.session.rollback()
                return _no_store({"ok": False, "locked": True, "error": LOCKED_MSG, "status": order.status,
                                  "status_label": labels.get(order.status, order.status)}, 409)

            snapshot = [{"id": i.id, "quantity": int(i.quantity or 0)} for i in order.items]
            verdict, plan = plan_change(snapshot, line_id, new_quantity, expected)

            def refusal(payload, status):
                payload.update(lines=lines_payload(restaurant, order), total=int(order.total or 0))
                db.session.rollback()
                return _no_store(payload, status)

            if verdict == "not_found":
                return refusal({"ok": False, "stale": True, "error": "Ce plat n'est plus dans la commande."}, 409)
            if verdict == "invalid":
                db.session.rollback()
                return _no_store({"ok": False, "error": plan}, 400)
            if verdict == "stale":
                return refusal({"ok": False, "stale": True,
                                "error": "La commande a changé : vérifiez la liste et recommencez."}, 409)
            if verdict == "last_item":
                return refusal({"ok": False, "last_item": True,
                                "error": "C'est le dernier plat de la commande. Pour le retirer, annulez la commande."}, 409)

            item = next(i for i in order.items if i.id == line_id)
            if plan["action"] == "increase":
                menu_item = db.session.get(MenuItem, item.menu_item_id) if item.menu_item_id else None
                if menu_item is not None and menu_item.restaurant_id != restaurant.id:
                    menu_item = None
                problem = increase_problem(restaurant, item, menu_item)
                if problem:
                    return refusal({"ok": False, "refused": True, "error": problem}, 409)

            name = item.item_name
            if plan["action"] == "remove":
                db.session.delete(item)
            else:
                item.quantity = plan["new_quantity"]
            db.session.flush()
            db.session.expire(order, ["items"])
            # Si le temps de préparation est stocké sur la commande (colonne), on le recalcule.
            table = getattr(Order, "__table__", None)
            if table is not None and "estimated_prep_minutes" in table.c and order.items:
                order.estimated_prep_minutes = (max((i.prep_minutes or 0) for i in order.items)
                                                or order.estimated_prep_minutes)
            db.session.commit()
        except Exception:
            db.session.rollback()
            log.exception("Modification d'un plat impossible (commande %s)", order_id)
            return _no_store({"ok": False, "error": "Impossible de modifier la commande pour le moment."}, 500)

        order = db.session.get(Order, order_id)
        try:
            if realtime is not None:
                realtime.publish(restaurant.id, "order_updated", feed_dict(order))
        except Exception:
            log.exception("Notification temps réel impossible (commande %s)", order_id)
        try:
            if log_audit is not None:
                sign = "+" if plan["delta"] > 0 else "-"
                log_audit("order_item_changed", success=True, restaurant_id=restaurant.id, user_email=None,
                          details=f"commande #{order_id} : {sign}{abs(plan['delta'])} × {name}"[:300])
        except Exception:
            log.exception("Trace de la modification impossible")

        data = to_dict(order)
        data.update(ok=True, changed={"name": name, "delta": plan["delta"], "action": plan["action"]},
                    lines=lines_payload(restaurant, order), total=int(order.total or 0))
        return _no_store(data)

    app.add_url_rule("/m/<slug>/commande/<int:order_id>/lignes.json", "order_lines_json", order_lines_json)
    view = order_modify_item
    if limiter is not None:
        view = limiter.limit(RATE_LIMIT, methods=["POST"])(order_modify_item)
    app.add_url_rule("/m/<slug>/commande/<int:order_id>/modifier", "order_modify_item", view, methods=["POST"])
