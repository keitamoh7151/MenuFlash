"""
MenuFlash Mini - Commandes à emporter (phases 1 et 2).

Mise à jour : référence à 10 caractères (order_refs.py) à la place de « A-id »,
prénom et téléphone du client visibles par l'équipe, ticket imprimable (tickets.py).

Branchement dans app.py (déjà fait dans le fichier fourni), tout en bas :

    from takeaway import init_takeaway
    init_takeaway(app, owner_required=owner_required, active_restaurant=active_restaurant,
                  order_hooks=ORDER_CREATED_HOOKS)

Principe : une commande à emporter est une commande normale (mêmes statuts,
même écran cuisine, même suivi, même annulation avant préparation). Une ligne
supplémentaire dans la table « takeaway_orders » garde le prénom, le téléphone,
l'email, l'heure de retrait et un jeton public. Les commandes sur place ne sont
pas modifiées.

Sécurité : tout est revalidé côté serveur (téléphone valide pour son pays, heure,
délai minimum, créneau complet, service activé / suspendu), CSRF conservé,
isolation par restaurant.
"""
import logging
import re
import secrets
from datetime import datetime, timedelta

from flask import flash, g, jsonify, redirect, render_template, request, url_for
from flask_login import login_required

from mailer import send_transactional_email
from models import Order, Restaurant, db
from order_refs import display_reference, ensure_reference, get_reference
from phone_utils import check_phone
from service import local_now

log = logging.getLogger("menuflash.takeaway")
MAX_ADVANCE_DAYS = 7
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")


class TakeawaySettings(db.Model):
    __tablename__ = "takeaway_settings"
    restaurant_id = db.Column(db.Integer, primary_key=True)
    enabled = db.Column(db.Boolean, default=False, nullable=False)
    paused = db.Column(db.Boolean, default=False, nullable=False)
    allow_asap = db.Column(db.Boolean, default=True, nullable=False)
    allow_scheduled = db.Column(db.Boolean, default=True, nullable=False)
    prep_minutes = db.Column(db.Integer, default=20, nullable=False)
    slot_minutes = db.Column(db.Integer, default=15, nullable=False)
    max_per_slot = db.Column(db.Integer, default=5, nullable=False)


class TakeawayOrder(db.Model):
    __tablename__ = "takeaway_orders"
    order_id = db.Column(db.Integer, primary_key=True)
    restaurant_id = db.Column(db.Integer, nullable=False, index=True)
    public_token = db.Column(db.String(64), unique=True, nullable=False, index=True)
    customer_name = db.Column(db.String(60), nullable=False)
    customer_phone = db.Column(db.String(30), nullable=False)
    customer_email = db.Column(db.String(160))
    pickup_at = db.Column(db.DateTime, nullable=False, index=True)   # heure locale du restaurant
    pickup_type = db.Column(db.String(16), default="asap", nullable=False)
    payment_method = db.Column(db.String(20), default="pickup", nullable=False)
    confirmation_sent = db.Column(db.Boolean, default=False)
    ready_sent = db.Column(db.Boolean, default=False)


def takeaway_info_map(order_ids):
    """{order_id: {name, phone, pickup, pickup_type}} pour l'écran cuisine et le ticket.
    Le téléphone n'est destiné qu'à l'équipe du restaurant (jamais au ticket ni au client)."""
    ids = [i for i in order_ids if i]
    if not ids:
        return {}
    rows = TakeawayOrder.query.filter(TakeawayOrder.order_id.in_(ids)).all()
    return {r.order_id: {"name": r.customer_name, "phone": r.customer_phone,
                         "pickup": r.pickup_at, "pickup_type": r.pickup_type} for r in rows}


def _clean(value, n):
    return re.sub(r"\s+", " ", (value or "").strip())[:n]


def _fail(message, code=400):
    if request.headers.get("X-Requested-With") == "fetch":
        return jsonify({"ok": False, "error": message}), code
    flash(message, "error")
    return redirect(request.referrer or "/")


def _slot_bounds(pickup, slot_minutes):
    start = pickup.replace(second=0, microsecond=0)
    start -= timedelta(minutes=(start.hour * 60 + start.minute) % slot_minutes)
    return start, start + timedelta(minutes=slot_minutes)


def init_takeaway(app, *, owner_required, active_restaurant, order_hooks):
    with app.app_context():
        for model in (TakeawaySettings, TakeawayOrder):
            try:
                model.__table__.create(db.engine, checkfirst=True)
            except Exception:
                log.exception("Création de la table %s impossible", model.__tablename__)

    def settings_for(restaurant_id, create=False):
        row = db.session.get(TakeawaySettings, restaurant_id)
        if row is None and create:
            row = TakeawaySettings(restaurant_id=restaurant_id)
            db.session.add(row)
        return row

    # ------------------------------------------------------------------
    # Réglages du restaurateur
    # ------------------------------------------------------------------
    def admin_takeaway_settings():
        restaurant = active_restaurant()
        s = settings_for(restaurant.id, create=True)
        if request.method == "POST":
            try:
                prep = int(request.form.get("prep_minutes", 20))
                slot = int(request.form.get("slot_minutes", 15))
                cap = int(request.form.get("max_per_slot", 5))
            except ValueError:
                flash("Les durées et la capacité doivent être des nombres.", "error")
                return redirect(url_for("admin_takeaway_settings"))
            if not (5 <= prep <= 180 and 5 <= slot <= 60 and 1 <= cap <= 50):
                flash("Valeurs autorisées : préparation 5-180 min, créneau 5-60 min, capacité 1-50.", "error")
                return redirect(url_for("admin_takeaway_settings"))
            s.enabled = request.form.get("enabled") == "on"
            s.paused = request.form.get("paused") == "on"
            s.allow_asap = request.form.get("allow_asap") == "on"
            s.allow_scheduled = request.form.get("allow_scheduled") == "on"
            s.prep_minutes, s.slot_minutes, s.max_per_slot = prep, slot, cap
            db.session.commit()
            flash("Paramètres des commandes à emporter enregistrés.", "success")
            return redirect(url_for("admin_takeaway_settings"))
        return render_template("admin_takeaway_settings.html", restaurant=restaurant, settings=s)

    app.add_url_rule("/admin/a-emporter", "admin_takeaway_settings",
                     owner_required(admin_takeaway_settings), methods=["GET", "POST"])

    # ------------------------------------------------------------------
    # Configuration publique lue par le menu client
    # ------------------------------------------------------------------
    @app.route("/m/<slug>/a-emporter/config.json")
    def takeaway_config(slug):
        restaurant = Restaurant.query.filter_by(slug=slug, is_active=True).first_or_404()
        s = settings_for(restaurant.id)
        on = bool(s and s.enabled and not s.paused)
        resp = jsonify(
            enabled=on, paused=bool(s and s.enabled and s.paused),
            allow_asap=bool(s and s.allow_asap), allow_scheduled=bool(s and s.allow_scheduled),
            prep_minutes=s.prep_minutes if s else 20,
            now=local_now().strftime("%Y-%m-%dT%H:%M"))
        resp.headers["Cache-Control"] = "no-store"
        return resp

    # ------------------------------------------------------------------
    # Détails publics d'une commande à emporter (par jeton aléatoire)
    # ------------------------------------------------------------------
    @app.route("/commande-emporter/<token>.json")
    def takeaway_public(token):
        t = TakeawayOrder.query.filter_by(public_token=token).first_or_404()
        o = db.session.get(Order, t.order_id)
        return jsonify(reference=display_reference(o.id), name=t.customer_name, status=o.status,
                       pickup_at=t.pickup_at.strftime("%Y-%m-%dT%H:%M"), total=o.total,
                       payment="Paiement au retrait")

    # ------------------------------------------------------------------
    # Passage de commande : validation AVANT la création
    # ------------------------------------------------------------------
    original_place_order = app.view_functions["place_order"]

    def place_order(slug):
        if request.form.get("fulfillment") != "takeaway":
            return original_place_order(slug)

        restaurant = Restaurant.query.filter_by(slug=slug, is_active=True).first_or_404()
        s = settings_for(restaurant.id)
        if not s or not s.enabled:
            return _fail("Les commandes à emporter ne sont pas proposées par ce restaurant.")
        if s.paused:
            return _fail("Les commandes à emporter sont suspendues pour le moment. Réessayez plus tard.")

        name = _clean(request.form.get("customer_name"), 60)
        if len(name) < 2 or not re.fullmatch(r"[^\W\d_]+(?:[ '’\-][^\W\d_]+)*", name, re.UNICODE):
            return _fail("Indiquez votre prénom (lettres uniquement).")
        phone, err = check_phone(request.form.get("customer_phone"),
                                 request.form.get("customer_phone_country"),
                                 label="Votre téléphone", required=True)
        if err:
            return _fail(err)
        email = _clean(request.form.get("customer_email"), 160).lower()
        if email and not EMAIL_RE.match(email):
            return _fail("L'adresse email n'est pas valide.")

        now = local_now()
        kind = request.form.get("pickup_type", "asap")
        earliest = now + timedelta(minutes=s.prep_minutes)
        if kind == "scheduled":
            if not s.allow_scheduled:
                return _fail("Le retrait à une heure précise n'est pas proposé.")
            try:
                pickup = datetime.strptime((request.form.get("pickup_at") or "")[:16], "%Y-%m-%dT%H:%M")
            except ValueError:
                return _fail("Choisissez une heure de retrait valide.")
            if pickup < earliest:
                return _fail(f"Choisissez une heure au moins {s.prep_minutes} minutes à l'avance.")
            if pickup > now + timedelta(days=MAX_ADVANCE_DAYS):
                return _fail(f"Vous pouvez commander au maximum {MAX_ADVANCE_DAYS} jours à l'avance.")
        else:
            kind = "asap"
            if not s.allow_asap:
                return _fail("Le retrait dès que possible n'est pas proposé. Choisissez une heure.")
            pickup = earliest

        start, end = _slot_bounds(pickup, s.slot_minutes)
        used = (db.session.query(TakeawayOrder.order_id)
                .join(Order, Order.id == TakeawayOrder.order_id)
                .filter(TakeawayOrder.restaurant_id == restaurant.id,
                        TakeawayOrder.pickup_at >= start, TakeawayOrder.pickup_at < end,
                        Order.status != "annulee").count())
        if used >= s.max_per_slot:
            return _fail("Ce créneau est complet. Choisissez une heure un peu plus tard.")

        g.takeaway = {"restaurant": restaurant, "name": name, "phone": phone, "email": email or None,
                      "pickup": pickup.replace(second=0, microsecond=0), "kind": kind}
        return original_place_order(slug)

    app.view_functions["place_order"] = place_order

    # ------------------------------------------------------------------
    # Juste après la création : marquage, jeton, email (avant la notif cuisine)
    # ------------------------------------------------------------------
    def on_order_created(order):
        data = getattr(g, "takeaway", None)
        if not data or data["restaurant"].id != order.restaurant_id:
            return
        order.table_label = f"À emporter · {data['name']}"[:50]
        row = TakeawayOrder(order_id=order.id, restaurant_id=order.restaurant_id,
                            public_token=secrets.token_urlsafe(32), customer_name=data["name"],
                            customer_phone=data["phone"], customer_email=data["email"],
                            pickup_at=data["pickup"], pickup_type=data["kind"])
        db.session.add(row)
        db.session.commit()
        ref = ensure_reference(order) or f"#{order.id}"
        if data["email"]:
            try:
                link = url_for("order_status", slug=data["restaurant"].slug, order_id=order.id, _external=True)
                quand = "dès que possible" if data["kind"] == "asap" else f"{data['pickup']:%d/%m/%Y à %H:%M}"
                row.confirmation_sent = send_transactional_email(
                    app, data["email"], f"Commande {ref} confirmée - {data['restaurant'].name}",
                    f"Bonjour {data['name']},\n\nVotre commande {ref} chez {data['restaurant'].name} "
                    f"est bien reçue.\nRetrait : {quand} (environ {data['pickup']:%H:%M}).\n"
                    + (f"Code de retrait : {ref[-3:]} (les 3 derniers caractères du numéro).\n"
                       if not ref.startswith("#") else "") +
                    f"Paiement au retrait.\n\nSuivez votre commande : {link}\n"
                    f"Vous pouvez l'annuler tant que la préparation n'a pas commencé.\n")
                db.session.commit()
            except Exception:
                db.session.rollback()
                log.exception("Email de confirmation à emporter impossible")

    order_hooks.append(on_order_created)

    # ------------------------------------------------------------------
    # Email « votre commande est prête »
    # ------------------------------------------------------------------
    original_advance = app.view_functions["admin_order_advance"]

    def admin_order_advance(order_id):
        resp = original_advance(order_id)
        try:
            t = db.session.get(TakeawayOrder, order_id)
            o = db.session.get(Order, order_id)
            if t and o and o.status == "prete" and t.customer_email and not t.ready_sent:
                ref = get_reference(o.id) or f"#{o.id}"
                t.ready_sent = send_transactional_email(
                    app, t.customer_email, f"Commande {ref} prête - {o.restaurant.name}",
                    f"Bonjour {t.customer_name},\n\nVotre commande {ref} est prête : "
                    f"vous pouvez venir la récupérer chez {o.restaurant.name}.\n"
                    + (f"Paiement au retrait. Au comptoir, donnez votre code de retrait {ref[-3:]} "
                       f"ou le numéro complet {ref}.\n" if not ref.startswith("#")
                       else f"Paiement au retrait. Donnez la référence {ref}.\n"))
                db.session.commit()
        except Exception:
            db.session.rollback()
            log.exception("Email « commande prête » impossible")
        return resp

    app.view_functions["admin_order_advance"] = admin_order_advance


