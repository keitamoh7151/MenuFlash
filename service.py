"""
MenuFlash Mini — Service en salle : horaires d'ouverture + appel du serveur.

Branchement dans app.py, JUSTE AVANT `from backups import init_backups` :

    from service import init_service
    init_service(app, owner_required=owner_required,
                 active_restaurant=active_restaurant,
                 limiter=limiter, log_menu_change=log_menu_change)

1. HORAIRES D'OUVERTURE (page Boutique > Horaires d'ouverture)
   - Jusqu'à 2 créneaux par jour (ex : 11:00-15:00 et 18:00-23:30), avec les
     créneaux qui passent minuit (ex : 19:00-02:00).
   - Bouton « Suspendre les commandes maintenant » (cuisine débordée, panne…),
     avec un message optionnel pour les clients.
   - En dehors des horaires : le menu reste consultable, mais affiche
     « Fermé actuellement · Ouvre demain à 11:00 » et refuse les commandes.
     Le refus est appliqué CÔTÉ SERVEUR (pas seulement masqué à l'écran).
   - Tant que les horaires ne sont pas activés, rien ne change : le
     restaurant est considéré comme toujours ouvert.

2. APPEL DU SERVEUR / DEMANDE DE L'ADDITION
   - Deux boutons sur le menu du client, visibles seulement si sa table est
     connue (QR code de table) et si le restaurant est ouvert.
   - L'équipe (gestionnaire ET cuisiniers) voit l'appel sur toutes les pages
     du back-office, avec un son, en moins de 5 secondes, et le marque
     « Traité » d'un clic.

Sécurité
--------
- Appels clients : jeton CSRF obligatoire, limite de 6 appels / 10 min par
  appareil, un même appel répété pour la même table n'est enregistré qu'une
  fois, 40 appels en attente maximum par restaurant, et si le restaurant a
  des QR codes de table, seules ces tables sont acceptées (impossible
  d'inventer une table).
- Horaires : modifiables uniquement par le gestionnaire, valeurs strictement
  contrôlées (format HH:MM, 2 créneaux max, message limité et filtré).
- Appels : chaque compte ne voit et ne traite que ceux de son restaurant.
- Affichage : textes insérés par textContent (aucune injection HTML).
- Les données sont incluses dans les sauvegardes, et supprimées avec le
  restaurant (y compris la composition des plats, voir _cleanup_restaurant).

Heure : le Mali est à UTC+0 toute l'année. Pour un autre pays, définir
MENUFLASH_UTC_OFFSET_MINUTES dans Render (ex : 60 pour UTC+1).
"""
import json
import os
import re
from datetime import datetime, timedelta, time as dtime

from flask import (render_template, request, redirect, url_for, flash, jsonify, abort)
from flask_login import current_user, login_required
from sqlalchemy import event

from models import db, Restaurant, QRCode

DAYS_FR = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
DAYS_EN = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
MAX_SLOTS_PER_DAY = 2
CALL_KINDS = {"serveur": ("Appel serveur", "Waiter call"),
              "addition": ("Demande d'addition", "Bill request")}
CALL_VISIBLE_MINUTES = 30
CALL_DEDUP_MINUTES = 3
MAX_PENDING_CALLS = 40
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
_MESSAGE_RE = re.compile(r"^[\w\s'’\-.,!?:()/%&]*$", re.UNICODE)


def utc_offset():
    try:
        return timedelta(minutes=max(-720, min(int(os.environ.get("MENUFLASH_UTC_OFFSET_MINUTES", "0")), 840)))
    except ValueError:
        return timedelta(0)


def local_now():
    return datetime.utcnow() + utc_offset()


# ==========================================================================
# Modèles (nouvelles tables uniquement, sans clé étrangère bloquante)
# ==========================================================================
class RestaurantHours(db.Model):
    __tablename__ = "restaurant_hours"
    restaurant_id = db.Column(db.Integer, primary_key=True)
    enabled = db.Column(db.Boolean, nullable=False, default=False)
    week = db.Column(db.Text, nullable=False, default="{}")    # {"0": [["11:00","15:00"], ...], ...}
    suspended = db.Column(db.Boolean, nullable=False, default=False)
    suspended_message = db.Column(db.String(200))
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def slots(self):
        try:
            data = json.loads(self.week or "{}")
        except ValueError:
            data = {}
        out = {}
        for d in range(7):
            out[d] = [tuple(s) for s in data.get(str(d), [])
                      if isinstance(s, list) and len(s) == 2
                      and all(isinstance(x, str) and _TIME_RE.match(x) for x in s)][:MAX_SLOTS_PER_DAY]
        return out


class ServiceCall(db.Model):
    __tablename__ = "service_calls"
    id = db.Column(db.Integer, primary_key=True)
    restaurant_id = db.Column(db.Integer, nullable=False, index=True)
    table_label = db.Column(db.String(50), nullable=False)
    kind = db.Column(db.String(20), nullable=False)
    status = db.Column(db.String(20), nullable=False, default="en_attente")
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True, nullable=False)
    handled_at = db.Column(db.DateTime)
    handled_by = db.Column(db.String(120))


# ==========================================================================
# Calcul ouvert / fermé
# ==========================================================================
def _t(hhmm):
    h, m = hhmm.split(":")
    return dtime(int(h), int(m))


def _intervals_around(slots, now):
    """Créneaux concrets (début, fin) de la veille, du jour et des 7 jours suivants."""
    base = now.date()
    for offset in range(-1, 8):
        day = base + timedelta(days=offset)
        for start, end in slots.get(day.weekday(), []):
            s = datetime.combine(day, _t(start))
            e = datetime.combine(day, _t(end))
            if e <= s:                      # passe minuit
                e += timedelta(days=1)
            yield s, e


def opening_status(restaurant_id, now=None):
    """Renvoie {"open": bool, "reason": ..., "next": datetime|None, "message": ...}."""
    rec = RestaurantHours.query.get(restaurant_id)
    if rec is None:
        return {"open": True, "reason": None, "next": None, "message": None}
    if rec.suspended:
        return {"open": False, "reason": "suspended", "next": None, "message": rec.suspended_message}
    if not rec.enabled:
        return {"open": True, "reason": None, "next": None, "message": None}
    now = now or local_now()
    intervals = sorted(_intervals_around(rec.slots(), now))
    for s, e in intervals:
        if s <= now < e:
            return {"open": True, "reason": None, "next": None, "closes": e, "message": None}
    upcoming = [s for s, e in intervals if s > now]
    return {"open": False, "reason": "hours", "next": upcoming[0] if upcoming else None, "message": None}


def next_opening_text(nxt, now=None, lang="fr"):
    if nxt is None:
        return ""
    now = now or local_now()
    delta_days = (nxt.date() - now.date()).days
    hhmm = nxt.strftime("%H:%M")
    if lang == "en":
        when = "today" if delta_days == 0 else "tomorrow" if delta_days == 1 else DAYS_EN[nxt.weekday()]
        return f"Opens {when} at {hhmm}"
    when = "aujourd'hui" if delta_days == 0 else "demain" if delta_days == 1 else DAYS_FR[nxt.weekday()].lower()
    return f"Ouvre {when} à {hhmm}"


# ==========================================================================
# Validation du formulaire d'horaires
# ==========================================================================
class HoursError(ValueError):
    pass


def parse_week(form):
    week = {}
    for d in range(7):
        slots = []
        if form.get(f"closed_{d}"):
            week[str(d)] = []
            continue
        for n in range(1, MAX_SLOTS_PER_DAY + 1):
            start = (form.get(f"open_{d}_{n}") or "").strip()
            end = (form.get(f"close_{d}_{n}") or "").strip()
            if not start and not end:
                continue
            if not (_TIME_RE.match(start) and _TIME_RE.match(end)):
                raise HoursError(f"{DAYS_FR[d]} : heure invalide (format HH:MM, ex : 11:30).")
            if start == end:
                raise HoursError(f"{DAYS_FR[d]} : l'heure d'ouverture et de fermeture sont identiques.")
            slots.append([start, end])
        if len(slots) == 2:
            a, b = sorted(slots)
            a_end = datetime.combine(datetime.min.date(), _t(a[1]))
            if a[1] <= a[0]:
                raise HoursError(f"{DAYS_FR[d]} : seul le dernier créneau peut passer minuit.")
            if datetime.combine(datetime.min.date(), _t(b[0])) < a_end:
                raise HoursError(f"{DAYS_FR[d]} : les deux créneaux se chevauchent.")
            slots = [a, b]
        week[str(d)] = slots
    return week


def clean_message(raw):
    msg = re.sub(r"\s+", " ", (raw or "")).strip()[:200]
    if msg and not _MESSAGE_RE.match(msg):
        raise HoursError("Le message contient des caractères non autorisés.")
    return msg or None


# ==========================================================================
# Branchement
# ==========================================================================
def init_service(app, *, owner_required, active_restaurant, limiter=None, log_menu_change=None):

    with app.app_context():
        for model in (RestaurantHours, ServiceCall):
            model.__table__.create(db.engine, checkfirst=True)

    # ---------------- Nettoyage à la suppression d'un restaurant ----------------
    def _cleanup_restaurant(rid):
        db.session.execute(RestaurantHours.__table__.delete().where(RestaurantHours.restaurant_id == rid))
        db.session.execute(ServiceCall.__table__.delete().where(ServiceCall.restaurant_id == rid))
        comp = db.metadata.tables.get("menu_item_compositions")
        if comp is not None:   # composition des plats : clé étrangère vers les plats
            db.session.execute(comp.delete().where(comp.c.restaurant_id == rid))

    original_delete = app.view_functions.get("super_admin_restaurant_delete")
    if original_delete is not None:
        def _delete_with_cleanup(restaurant_id):
            r = Restaurant.query.get(restaurant_id)
            if r is not None and request.method == "POST" and \
                    (request.form.get("confirm_slug") or "").strip() == r.slug:
                try:
                    from super_admin import RestaurantAdminMeta
                    meta = RestaurantAdminMeta.query.get(r.id)
                    if meta and meta.archived_at:
                        _cleanup_restaurant(r.id)
                except Exception:
                    db.session.rollback()
            return original_delete(restaurant_id=restaurant_id)
        app.view_functions["super_admin_restaurant_delete"] = _delete_with_cleanup

    # ---------------- Refus des commandes quand le restaurant est fermé ----------------
    ORDER_ENDPOINTS = {"place_order", "add_to_order"}

    @app.before_request
    def _enforce_opening_hours():
        if request.endpoint not in ORDER_ENDPOINTS or request.method != "POST":
            return None
        slug = (request.view_args or {}).get("slug")
        r = Restaurant.query.filter_by(slug=slug, is_active=True).first() if slug else None
        if r is None:
            return None
        st = opening_status(r.id)
        if st["open"]:
            return None
        msg = st["message"] or "Le restaurant ne prend pas de commandes pour le moment."
        if st["reason"] == "hours":
            msg = "Le restaurant est fermé actuellement." + (
                f" {next_opening_text(st['next'])}." if st["next"] else "")
        if request.headers.get("X-Requested-With") == "fetch" or request.is_json:
            return jsonify({"ok": False, "error": msg, "closed": True}), 409
        flash(msg, "error")
        return redirect(url_for("public_menu", slug=slug))

    # ---------------- Table du client (même logique que le menu public) ----------------
    def _resolve_table(restaurant):
        qr_id = request.values.get("qr", type=int) or request.values.get("qr_id", type=int)
        if qr_id:
            qr = QRCode.query.filter_by(id=qr_id, restaurant_id=restaurant.id).first()
            if qr:
                return qr.label
        label = re.sub(r"\s+", " ", (request.values.get("t") or request.values.get("table_label") or "")).strip()[:50]
        if not label:
            return None
        if QRCode.query.filter_by(restaurant_id=restaurant.id).count():
            # Le restaurant utilise des QR de table : seules ses tables existent.
            if not QRCode.query.filter_by(restaurant_id=restaurant.id, label=label).first():
                return None
        return label

    # ---------------- Appel du serveur (client) ----------------
    def service_call(slug):
        r = Restaurant.query.filter_by(slug=slug, is_active=True).first_or_404()
        if not r.is_subscription_valid:
            abort(404)
        kind = request.form.get("kind", "")
        if kind not in CALL_KINDS:
            return jsonify({"ok": False, "error": "Demande invalide."}), 400
        if not opening_status(r.id)["open"]:
            return jsonify({"ok": False, "error": "Le restaurant est fermé actuellement."}), 409
        table = _resolve_table(r)
        if not table:
            return jsonify({"ok": False, "error": "Table inconnue : scannez le QR code de votre table."}), 400
        now = datetime.utcnow()
        recent = ServiceCall.query.filter(
            ServiceCall.restaurant_id == r.id, ServiceCall.table_label == table,
            ServiceCall.kind == kind, ServiceCall.status == "en_attente",
            ServiceCall.created_at >= now - timedelta(minutes=CALL_DEDUP_MINUTES)).first()
        if recent:
            return jsonify({"ok": True, "duplicate": True})
        pending = ServiceCall.query.filter(
            ServiceCall.restaurant_id == r.id, ServiceCall.status == "en_attente",
            ServiceCall.created_at >= now - timedelta(minutes=CALL_VISIBLE_MINUTES)).count()
        if pending >= MAX_PENDING_CALLS:
            return jsonify({"ok": False, "error": "Trop de demandes en cours, merci de patienter."}), 429
        db.session.add(ServiceCall(restaurant_id=r.id, table_label=table, kind=kind))
        db.session.commit()
        return jsonify({"ok": True})

    view = service_call
    if limiter is not None:
        view = limiter.limit("6 per 10 minutes", methods=["POST"])(view)
    app.add_url_rule("/m/<slug>/appel", "service_call", view, methods=["POST"])

    # ---------------- Appels (équipe du restaurant) ----------------
    def _staff_restaurant():
        if not current_user.is_authenticated or getattr(current_user, "is_super_admin", False):
            abort(403)
        r = active_restaurant()
        if r is None:
            abort(403)
        return r

    @app.route("/admin/appels.json")
    @login_required
    def service_calls_feed():
        r = _staff_restaurant()
        now = datetime.utcnow()
        calls = (ServiceCall.query.filter(
            ServiceCall.restaurant_id == r.id, ServiceCall.status == "en_attente",
            ServiceCall.created_at >= now - timedelta(minutes=CALL_VISIBLE_MINUTES))
            .order_by(ServiceCall.created_at.asc()).limit(MAX_PENDING_CALLS).all())
        latest = db.session.query(db.func.max(ServiceCall.id)).filter(
            ServiceCall.restaurant_id == r.id).scalar() or 0
        resp = jsonify({"latest_id": latest, "calls": [{
            "id": c.id, "table": c.table_label, "kind": c.kind,
            "label": CALL_KINDS[c.kind][0] if c.kind in CALL_KINDS else c.kind,
            "minutes": max(int((now - c.created_at).total_seconds() // 60), 0),
        } for c in calls]})
        resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.route("/admin/appels/<int:call_id>/traiter", methods=["POST"])
    @login_required
    def service_call_handle(call_id):
        r = _staff_restaurant()
        c = ServiceCall.query.filter_by(id=call_id, restaurant_id=r.id).first_or_404()
        if c.status == "en_attente":
            c.status = "traite"
            c.handled_at = datetime.utcnow()
            c.handled_by = current_user.email[:120]
            db.session.commit()
        return jsonify({"ok": True})

    # ---------------- Horaires (gestionnaire) ----------------
    @app.route("/admin/horaires", methods=["GET", "POST"])
    @owner_required
    def admin_hours():
        restaurant = active_restaurant()
        rec = RestaurantHours.query.get(restaurant.id)
        if request.method == "POST":
            action = request.form.get("action", "save")
            if rec is None:
                rec = RestaurantHours(restaurant_id=restaurant.id, week="{}")
                db.session.add(rec)
            try:
                if action == "suspend":
                    rec.suspended = True
                    rec.suspended_message = clean_message(request.form.get("message"))
                    note = "Commandes suspendues."
                elif action == "resume":
                    rec.suspended = False
                    rec.suspended_message = None
                    note = "Commandes reprises."
                else:
                    week = parse_week(request.form)
                    rec.enabled = bool(request.form.get("enabled"))
                    if rec.enabled and not any(week.values()):
                        raise HoursError("Indiquez au moins un créneau d'ouverture, ou désactivez les horaires.")
                    rec.week = json.dumps(week)
                    note = "Horaires d'ouverture " + ("activés et mis à jour." if rec.enabled else "désactivés.")
            except HoursError as exc:
                db.session.rollback()
                flash(str(exc), "error")
                return redirect(url_for("admin_hours"))
            if log_menu_change:
                log_menu_change(restaurant.id, current_user, "hours_updated", note)
            db.session.commit()
            flash(note, "success")
            return redirect(url_for("admin_hours"))

        slots = rec.slots() if rec else {d: [] for d in range(7)}
        st = opening_status(restaurant.id)
        return render_template(
            "admin_hours.html", restaurant=restaurant, rec=rec, slots=slots, days=DAYS_FR,
            status=st, next_text=next_opening_text(st.get("next")),
            now_local=local_now())

    # ---------------- Données pour les pages ----------------
    @app.context_processor
    def _inject_service():
        try:
            endpoint = request.endpoint or ""
            if endpoint == "public_menu":
                slug = (request.view_args or {}).get("slug")
                r = Restaurant.query.filter_by(slug=slug, is_active=True).first() if slug else None
                if r is None:
                    return {}
                st = opening_status(r.id)
                return {"mf_service": {
                    "mode": "public",
                    "call_url": url_for("service_call", slug=r.slug),
                    "table": _resolve_table(r),
                    "qr": request.args.get("qr", type=int),
                    "t": (request.args.get("t") or "")[:50],
                    "open": st["open"],
                    "reason": st["reason"],
                    "message": st["message"],
                    "next_fr": next_opening_text(st.get("next"), lang="fr"),
                    "next_en": next_opening_text(st.get("next"), lang="en"),
                }}
            if (current_user.is_authenticated and not getattr(current_user, "is_super_admin", False)
                    and (endpoint.startswith("admin_") or endpoint in ("change_password", "edit_item_composition"))
                    and endpoint not in ("admin_login", "admin_logout")):
                return {"mf_service": {
                    "mode": "staff",
                    "feed_url": url_for("service_calls_feed"),
                    "handle_url": "/admin/appels/{id}/traiter",
                }}
        except Exception:
            db.session.rollback()
        return {}
