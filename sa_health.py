"""
MenuFlash Mini — Santé d'activité des restaurants clients (super-admin).

Branché par sa_suite.py (voir sa_suite.init_sa_suite).

1. SANTÉ D'ACTIVITÉ PAR RESTAURANT
   Page « Clients > Santé d'activité » : pour chaque client, dernière commande,
   commandes sur 7 et 30 jours, dernière activité d'un compte (connexion ou
   session ouverte, donc aussi la tablette de cuisine), étapes d'installation
   (menu, QR codes, compte cuisine, première commande, logo) et un niveau :

     En activité     une commande dans les N derniers jours
     À surveiller    dernière commande entre N et 2 x N jours
     Décroche        aucune commande depuis plus de 2 x N jours
     Aucune commande installation terminée, mais jamais de commande
     Installation bloquée   plus de 3 jours après l'inscription, sans menu
                     ni QR code de table (ou sans à emporter)
     Démarrage       inscrit depuis moins de 3 jours

   N = seuil réglable (par défaut 7 jours ; 2 à 3 jours conseillés pendant un pilote).
   Export CSV. Aussi affiché dans la liste des clients et sur chaque fiche.

2. DIAGNOSTIC D'UN RESTAURANT (fiche client et vue support)
   Liste de problèmes probables, expliqués en clair : compte suspendu, abonnement
   expiré, menu vide, aucun QR, « À emporter » activé mais non inclus dans la
   formule, commandes « Nouvelle » oubliées depuis plus de 15 minutes, commandes
   refusées par la protection anti-abus, limites bientôt atteintes, etc.

3. TYPE DE COMPTE : Client / Pilote offert / Démo
   Un restaurant « Démo » ou « Pilote offert » n'est plus compté dans le revenu
   mensuel récurrent ; un « Démo » n'est pas compté non plus dans la conversion
   essai -> payant (voir super_admin.py). Chaque changement est tracé.

Nouvelle table « restaurant_flags », créée automatiquement, incluse dans les
sauvegardes et supprimée avec le restaurant. Aucune table existante n'est modifiée.
"""
import logging
from datetime import datetime, timedelta

from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import case, func

from models import (MenuCategory, MenuItem, Order, QRCode, Restaurant, User, db)
from super_admin import (PLAN_LABELS, PLATFORM_SLUG, STATUS_LABELS, RestaurantAdminMeta,
                         _client_restaurants_query, _csv_response, _meta, get_setting, set_setting)

log = logging.getLogger("menuflash.sa_health")

ACCOUNT_TYPES = {"client": "Client", "pilot": "Pilote offert", "demo": "Démo / test"}
ACCOUNT_BADGES = {"client": "", "pilot": "mf-sa-badge-info", "demo": "mf-sa-badge-muted"}

RISK_DEFAULT, RISK_MIN, RISK_MAX = 7, 1, 30
STUCK_MINUTES = 15            # commande « Nouvelle » non prise en charge
NEW_ACCOUNT_DAYS = 3          # avant, on laisse le temps de s'installer
NOTE_MAX = 300

LEVELS = {
    "risk": ("Décroche", "mf-sa-badge-danger"),
    "blocked": ("Installation bloquée", "mf-sa-badge-danger"),
    "dormant": ("Aucune commande", "mf-sa-badge-warning"),
    "watch": ("À surveiller", "mf-sa-badge-warning"),
    "new": ("Démarrage", "mf-sa-badge-info"),
    "ok": ("En activité", "mf-sa-badge-ok"),
    "off": ("Hors service", "mf-sa-badge-muted"),
}
LEVEL_ORDER = ["risk", "blocked", "dormant", "watch", "new", "ok", "off"]


# ==========================================================================
# Modèle
# ==========================================================================
class RestaurantFlag(db.Model):
    __tablename__ = "restaurant_flags"
    restaurant_id = db.Column(db.Integer, primary_key=True)
    account_type = db.Column(db.String(10), nullable=False, default="client")   # client / pilot / demo
    note = db.Column(db.String(NOTE_MAX))
    updated_by = db.Column(db.String(120))
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


def account_types_map():
    """{restaurant_id: 'pilot' | 'demo'} (les clients normaux n'y figurent pas)."""
    try:
        return {f.restaurant_id: f.account_type for f in RestaurantFlag.query.all()
                if f.account_type in ("pilot", "demo")}
    except Exception:
        db.session.rollback()
        return {}


def account_type_of(restaurant_id):
    try:
        f = db.session.get(RestaurantFlag, restaurant_id)
    except Exception:
        db.session.rollback()
        return "client"
    return f.account_type if f and f.account_type in ACCOUNT_TYPES else "client"


def risk_days():
    """Seuil « sans commande » du tableau de bord et de la page Santé (1 à 30 jours)."""
    try:
        value = int(get_setting("risk_days") or RISK_DEFAULT)
    except (TypeError, ValueError):
        value = RISK_DEFAULT
    return max(RISK_MIN, min(value, RISK_MAX))


# ==========================================================================
# Présentation
# ==========================================================================
def ago(dt, now=None):
    if not dt:
        return "jamais"
    now = now or datetime.utcnow()
    seconds = int((now - dt).total_seconds())
    if seconds < 0:
        return "à l'instant"
    if seconds < 60:
        return "à l'instant"
    if seconds < 3600:
        return f"il y a {seconds // 60} min"
    if seconds < 48 * 3600:
        return f"il y a {seconds // 3600} h"
    return f"il y a {seconds // 86400} j"


def local_dt(dt):
    """Date/heure du restaurant (décalage MENUFLASH_UTC_OFFSET_MINUTES), format jj/mm/aaaa hh:mm."""
    if not dt:
        return "—"
    try:
        from orders_board import _local
        dt = _local(dt)
    except Exception:
        pass
    return dt.strftime("%d/%m/%Y %H:%M")


# ==========================================================================
# Niveau de santé (fonction pure, testée)
# ==========================================================================
def classify(status, created_at, last_order, setup_ok, now, risk):
    """Renvoie (niveau, libellé, couleur). `status` : actif/essai/expire/suspendu/archive."""
    if status in ("archive", "suspendu", "expire"):
        label = STATUS_LABELS.get(status, (status,))[0]
        return "off", label, LEVELS["off"][1]
    age_days = (now - created_at).days if created_at else 999
    if last_order is None:
        if age_days < NEW_ACCOUNT_DAYS:
            level = "new"
        elif not setup_ok:
            level = "blocked"
        else:
            level = "dormant"
    else:
        days = (now - last_order).days
        if days <= risk:
            level = "ok"
        elif days <= 2 * risk:
            level = "watch"
        else:
            level = "risk"
    return level, LEVELS[level][0], LEVELS[level][1]


# ==========================================================================
# Collecte des indicateurs (requêtes groupées : pas de requête par restaurant)
# ==========================================================================
def _takeaway_enabled_map(ids):
    try:
        from takeaway import TakeawaySettings
        return {s.restaurant_id: bool(s.enabled) for s in
                TakeawaySettings.query.filter(TakeawaySettings.restaurant_id.in_(ids)).all()}
    except Exception:
        db.session.rollback()
        return {}


def _session_map(user_ids):
    if not user_ids:
        return {}
    try:
        from security_plus import UserSession
        return dict(db.session.query(UserSession.user_id, func.max(UserSession.last_seen_at))
                    .filter(UserSession.user_id.in_(user_ids)).group_by(UserSession.user_id).all())
    except Exception:
        db.session.rollback()
        return {}


def collect(now=None, only_ids=None):
    """{restaurant_id: indicateurs} pour tous les clients (ou ceux demandés)."""
    now = now or datetime.utcnow()
    risk = risk_days()
    query = _client_restaurants_query()
    if only_ids is not None:
        query = query.filter(Restaurant.id.in_(list(only_ids) or [0]))
    restaurants = query.all()
    ids = [r.id for r in restaurants]
    if not ids:
        return {}

    metas = {m.restaurant_id: m for m in RestaurantAdminMeta.query.filter(
        RestaurantAdminMeta.restaurant_id.in_(ids)).all()}
    flags = {f.restaurant_id: f for f in RestaurantFlag.query.filter(
        RestaurantFlag.restaurant_id.in_(ids)).all()}
    d7, d30 = now - timedelta(days=7), now - timedelta(days=30)

    orders = {}
    for rid, last, total, n7, n30 in (
            db.session.query(Order.restaurant_id, func.max(Order.created_at), func.count(Order.id),
                             func.sum(case((Order.created_at >= d7, 1), else_=0)),
                             func.sum(case((Order.created_at >= d30, 1), else_=0)))
            .filter(Order.restaurant_id.in_(ids)).group_by(Order.restaurant_id).all()):
        orders[rid] = (last, int(total or 0), int(n7 or 0), int(n30 or 0))
    stuck = dict(db.session.query(Order.restaurant_id, func.count(Order.id))
                 .filter(Order.restaurant_id.in_(ids), Order.status == "nouvelle",
                         Order.created_at < now - timedelta(minutes=STUCK_MINUTES),
                         Order.created_at >= now - timedelta(hours=12))
                 .group_by(Order.restaurant_id).all())
    items_total = dict(db.session.query(MenuItem.restaurant_id, func.count(MenuItem.id))
                       .filter(MenuItem.restaurant_id.in_(ids)).group_by(MenuItem.restaurant_id).all())
    items_avail = dict(db.session.query(MenuItem.restaurant_id, func.count(MenuItem.id))
                       .filter(MenuItem.restaurant_id.in_(ids), MenuItem.is_available.is_(True))
                       .group_by(MenuItem.restaurant_id).all())
    cats = dict(db.session.query(MenuCategory.restaurant_id, func.count(MenuCategory.id))
                .filter(MenuCategory.restaurant_id.in_(ids)).group_by(MenuCategory.restaurant_id).all())
    qrs = {rid: (int(n or 0), int(scans or 0)) for rid, n, scans in
           db.session.query(QRCode.restaurant_id, func.count(QRCode.id), func.sum(QRCode.scan_count))
           .filter(QRCode.restaurant_id.in_(ids)).group_by(QRCode.restaurant_id).all()}
    users = User.query.filter(User.restaurant_id.in_(ids), User.is_super_admin.is_(False)).all()
    sessions = _session_map([u.id for u in users])
    takeaway = _takeaway_enabled_map(ids)

    by_resto = {}
    for u in users:
        by_resto.setdefault(u.restaurant_id, []).append(u)

    result = {}
    for r in restaurants:
        meta = metas.get(r.id)
        status = _status_of(r, meta)
        last, total, n7, n30 = orders.get(r.id, (None, 0, 0, 0))
        team = by_resto.get(r.id, [])
        owners = [u for u in team if u.role == "owner"]
        staff = [u for u in team if u.role != "owner"]
        activity = [d for u in team for d in (u.last_login_at, sessions.get(u.id)) if d]
        n_items, n_avail = items_total.get(r.id, 0), items_avail.get(r.id, 0)
        n_qr, scans = qrs.get(r.id, (0, 0))
        steps = [
            {"key": "menu", "label": "Menu créé", "done": n_avail >= 1},
            {"key": "qr", "label": "QR codes de table créés", "done": n_qr >= 1,
             "optional": bool(takeaway.get(r.id))},
            {"key": "team", "label": "Compte cuisine créé", "done": len(staff) >= 1},
            {"key": "order", "label": "Première commande reçue", "done": total >= 1},
            {"key": "logo", "label": "Logo ajouté", "done": bool(r.logo_url)},
        ]
        setup_ok = n_avail >= 1 and (n_qr >= 1 or bool(takeaway.get(r.id)))
        level, label, color = classify(status, r.created_at, last, setup_ok, now, risk)
        ftype = flags[r.id].account_type if r.id in flags else "client"
        result[r.id] = {
            "id": r.id, "name": r.name, "slug": r.slug, "plan": r.subscription_plan,
            "plan_label": PLAN_LABELS.get(r.subscription_plan, r.subscription_plan),
            "status": status, "status_label": STATUS_LABELS[status][0],
            "account_type": ftype if ftype in ACCOUNT_TYPES else "client",
            "type_note": flags[r.id].note if r.id in flags else None,
            "created_at": r.created_at, "age_days": (now - r.created_at).days if r.created_at else None,
            "last_order": last, "days_since_order": (now - last).days if last else None,
            "orders_7d": n7, "orders_30d": n30, "orders_total": total, "stuck_orders": stuck.get(r.id, 0),
            "dishes": n_items, "dishes_available": n_avail, "categories": cats.get(r.id, 0),
            "qr_count": n_qr, "qr_scans": scans, "staff_count": len(staff),
            "owner_last_login": max([u.last_login_at for u in owners if u.last_login_at], default=None),
            "last_activity": max(activity, default=None),
            "takeaway_enabled": bool(takeaway.get(r.id)),
            "steps": steps, "steps_done": sum(1 for s in steps if s["done"]), "steps_total": len(steps),
            "setup_ok": setup_ok, "level": level, "level_label": label, "level_color": color,
        }
    return result


def _status_of(r, meta):
    from super_admin import _status
    return _status(r, meta)


def sort_rows(rows, key):
    far = datetime(1970, 1, 1)
    if key == "nom":
        return sorted(rows, key=lambda h: h["name"].lower())
    if key == "commande":
        return sorted(rows, key=lambda h: h["last_order"] or far)          # les plus anciennes d'abord
    if key == "activite":
        return sorted(rows, key=lambda h: h["last_activity"] or far)
    # défaut : les plus urgents d'abord, puis les plus anciennes commandes
    return sorted(rows, key=lambda h: (LEVEL_ORDER.index(h["level"]), h["last_order"] or far, h["name"].lower()))


# ==========================================================================
# Diagnostic d'un restaurant
# ==========================================================================
def diagnose(r, h=None, now=None):
    """Liste de problèmes / remarques : [{'level': danger|warning|info|ok, 'text', 'hint'}]."""
    now = now or datetime.utcnow()
    h = h or collect(now, [r.id]).get(r.id)
    out = []

    def add(level, text, hint=None):
        out.append({"level": level, "text": text, "hint": hint})

    meta = _meta(r.id)
    if meta and meta.archived_at:
        add("danger", "Compte archivé : plus aucun accès ni menu public.",
            "Réactivez-le depuis les actions rapides pour le remettre en service.")
    elif not r.is_active:
        add("danger", "Compte suspendu" + (f" — motif : « {meta.suspension_reason} »" if meta and meta.suspension_reason else "."),
            "Le menu public et le tableau de bord sont bloqués.")
    if h and h["status"] == "expire":
        add("danger", "Abonnement expiré : le menu n'est plus visible par les clients.",
            "Enregistrez un paiement ou offrez des jours.")
    elif r.subscription_active_until and 0 <= r.days_remaining <= 5 and r.is_active:
        add("warning", f"L'abonnement expire dans {r.days_remaining} jour(s).", "Pensez à envoyer un rappel.")

    try:
        from sa_controls import control_for
        c = control_for(r.id)
        if c and c.read_only:
            add("warning", "Compte en lecture seule : le gestionnaire ne peut plus rien modifier.",
                f"Motif : {c.reason}" if c.reason else None)
        if c and c.orders_blocked:
            add("warning", "Commandes bloquées par le super-admin : le menu est en consultation.",
                f"Motif : {c.reason}" if c.reason else None)
    except Exception:
        db.session.rollback()

    owners = User.query.filter_by(restaurant_id=r.id, role="owner", is_super_admin=False).all()
    if owners and not any(o.email_verified for o in owners):
        add("info", "L'email du gestionnaire n'est pas confirmé.",
            "Il peut renvoyer le lien depuis son tableau de bord ; vous pouvez aussi lui envoyer un lien de réinitialisation.")
    if any(o.is_locked for o in owners):
        add("warning", "Un compte du restaurant est verrouillé après trop d'échecs de connexion.",
            "Utilisez « Débloquer les comptes ».")

    if h:
        if h["dishes"] == 0:
            add("danger", "Menu vide : les clients ne voient aucun plat.",
                "Proposez-lui d'importer son menu depuis un fichier (Boutique > Importer un menu).")
        elif h["dishes_available"] == 0:
            add("danger", "Tous les plats sont marqués « Épuisé » : rien n'est commandable.")
        if h["qr_count"] == 0 and not h["takeaway_enabled"]:
            add("warning", "Aucun QR code de table créé et « À emporter » désactivé : les clients n'ont aucun moyen de commander.",
                "Il doit créer ses QR codes (Boutique > Codes QR) ou activer « À emporter ».")
        elif h["qr_count"] and h["qr_scans"] == 0 and (h["age_days"] or 0) >= NEW_ACCOUNT_DAYS:
            add("info", "Aucun QR code n'a encore été scanné.", "Les chevalets sont-ils imprimés et posés ?")
        if h["staff_count"] == 0 and h["orders_total"] > 0:
            add("info", "Aucun compte cuisine : seul le gestionnaire voit les commandes.",
                "Équipe > Inviter un membre.")
        if h["stuck_orders"]:
            add("danger", f"{h['stuck_orders']} commande(s) « Nouvelle » attendent depuis plus de {STUCK_MINUTES} min.",
                "Personne ne les prend en charge : écran cuisine éteint, son désactivé ou personne connecté ?")
        risk = risk_days()
        if h["level"] in ("risk", "watch") and h["last_order"]:
            add("warning", f"Dernière commande {ago(h['last_order'], now)} (seuil : {risk} jour(s)).",
                "Appelez le restaurateur : problème technique, saison creuse ou désintérêt ?")
        elif h["level"] == "dormant":
            add("warning", "Installation terminée mais aucune commande reçue.",
                "Faites une commande de test avec lui : QR code, son et écran cuisine.")
        elif h["level"] == "blocked":
            add("danger", "Installation bloquée : menu ou accès clients manquants.",
                "Proposez un appel ou une session d'aide à distance.")

    # Formule et fonctionnalités
    try:
        from plans import entitlement, has_feature
        from takeaway import TakeawaySettings
        ts = db.session.get(TakeawaySettings, r.id)
        if ts and ts.enabled and not has_feature(r, "takeaway"):
            add("danger", "« À emporter » est activé mais non inclus dans sa formule : son lien général est en consultation.",
                "Passez-le en Pro ou accordez une exception dans Formules.")
        elif ts and ts.enabled and ts.paused:
            add("warning", "Les commandes à emporter sont suspendues par le restaurateur.")
        if h and h["dishes"]:
            limit = int(entitlement(r, "max_menu_items") or 0)
            if limit and h["dishes"] >= 0.9 * limit:
                add("info", f"Menu presque plein : {h['dishes']} plats sur {limit} autorisés.")
    except Exception:
        db.session.rollback()

    # Horaires et suspension des commandes
    try:
        from service import opening_status
        st = opening_status(r.id)
        if st["reason"] == "suspended":
            add("warning", "Le restaurateur a suspendu les commandes" + (f" : « {st['message']} »." if st.get("message") else "."))
        elif not st["open"]:
            add("info", "Actuellement fermé selon ses horaires d'ouverture.")
    except Exception:
        db.session.rollback()

    # Protection anti-abus : commandes refusées (24 h)
    try:
        from models import AuditLog
        blocked = AuditLog.query.filter(AuditLog.restaurant_id == r.id, AuditLog.action == "order_flood_blocked",
                                        AuditLog.created_at >= now - timedelta(hours=24)).count()
        if blocked:
            add("warning", f"{blocked} commande(s) refusée(s) par la protection anti-abus ces dernières 24 h.",
                "Si ce sont de vrais clients, le restaurateur peut augmenter ses limites (Codes QR / À emporter).")
    except Exception:
        db.session.rollback()

    # Invitations d'équipe en attente depuis plus de 3 jours
    try:
        from security_plus import StaffMeta
        pending = [m for m in StaffMeta.query.filter(
            StaffMeta.user_id.in_([u.id for u in User.query.filter_by(restaurant_id=r.id, role="staff")] or [0])).all()
            if m.invited_at and not m.accepted_at and not m.disabled_at and m.invited_at < now - timedelta(days=3)]
        if pending:
            add("info", f"{len(pending)} invitation(s) d'équipe jamais acceptée(s) depuis plus de 3 jours.",
                "Renvoyez l'invitation ou envoyez le lien par WhatsApp.")
    except Exception:
        db.session.rollback()

    if not any(i["level"] in ("danger", "warning") for i in out):
        out.insert(0, {"level": "ok", "text": "Aucun problème détecté.", "hint": None})
    order = {"danger": 0, "warning": 1, "info": 2, "ok": 3}
    out.sort(key=lambda i: order[i["level"]])
    return out


# ==========================================================================
# Branchement
# ==========================================================================
def init_sa_health(app, *, super_admin_required, log_audit):
    with app.app_context():
        try:
            RestaurantFlag.__table__.create(db.engine, checkfirst=True)
        except Exception:
            log.exception("Création de la table restaurant_flags impossible")

    app.jinja_env.filters["sa_ago"] = ago
    app.jinja_env.filters["sa_dt"] = local_dt

    def audit(action, restaurant_id=None, details=None):
        log_audit(action[:40], success=True, restaurant_id=restaurant_id,
                  user_email=current_user.email if current_user.is_authenticated else None, details=details)

    # ---------------------------------------------------------------- page Santé
    @app.route("/super-admin/sante-clients")
    @super_admin_required
    def super_admin_clients_health():
        now = datetime.utcnow()
        q = (request.args.get("q") or "").strip()[:80].lower()
        level_f = request.args.get("niveau", "")
        type_f = request.args.get("type", "")
        sort_key = request.args.get("tri", "priorite")
        everything = list(collect(now).values())
        counts = {k: 0 for k in LEVEL_ORDER}
        for h in everything:
            counts[h["level"]] += 1
        rows = [h for h in everything
                if (not q or q in h["name"].lower() or q in h["slug"].lower())
                and (not level_f or h["level"] == level_f)
                and (not type_f or h["account_type"] == type_f)
                and h["status"] != "archive"]
        return render_template(
            "sa_health.html", rows=sort_rows(rows, sort_key), counts=counts, total=len(everything),
            q=q, level_f=level_f, type_f=type_f, sort_key=sort_key, now=now, risk=risk_days(),
            levels=LEVELS, level_order=LEVEL_ORDER, account_types=ACCOUNT_TYPES,
            account_badges=ACCOUNT_BADGES, risk_min=RISK_MIN, risk_max=RISK_MAX)

    @app.route("/super-admin/sante-clients/export.csv")
    @super_admin_required
    def super_admin_clients_health_export():
        now = datetime.utcnow()
        rows = sort_rows([h for h in collect(now).values() if h["status"] != "archive"], "priorite")
        audit("sa_export_health", details=f"{len(rows)} restaurants exportés")
        return _csv_response("sante_clients_menuflash.csv",
                             ["Restaurant", "Adresse menu", "Type", "Statut", "Santé", "Dernière commande",
                              "Commandes 7 j", "Commandes 30 j", "Commandes total", "Dernière activité",
                              "Plats", "QR codes", "Comptes cuisine", "Étapes terminées"],
                             [[h["name"], h["slug"], ACCOUNT_TYPES[h["account_type"]], h["status_label"],
                               h["level_label"], local_dt(h["last_order"]), h["orders_7d"], h["orders_30d"],
                               h["orders_total"], local_dt(h["last_activity"]), h["dishes"], h["qr_count"],
                               h["staff_count"], f"{h['steps_done']}/{h['steps_total']}"] for h in rows])

    @app.route("/super-admin/sante-clients/seuil", methods=["POST"])
    @super_admin_required
    def super_admin_risk_days_save():
        try:
            value = int(request.form.get("risk_days", ""))
        except ValueError:
            value = 0
        if not RISK_MIN <= value <= RISK_MAX:
            flash(f"Le seuil doit être compris entre {RISK_MIN} et {RISK_MAX} jours.", "error")
            return redirect(url_for("super_admin_clients_health"))
        before = risk_days()
        set_setting("risk_days", value)
        db.session.commit()
        audit("sa_risk_days", details=f"{before} -> {value} jour(s)")
        flash(f"Seuil enregistré : un restaurant sans commande depuis {value} jour(s) est « à surveiller ».", "success")
        return redirect(url_for("super_admin_clients_health"))

    # ---------------------------------------------------------------- type de compte
    @app.route("/super-admin/restaurants/<int:restaurant_id>/type", methods=["POST"])
    @super_admin_required
    def super_admin_restaurant_type(restaurant_id):
        r = db.session.get(Restaurant, restaurant_id)
        if r is None or r.slug == PLATFORM_SLUG:
            abort(404)
        kind = request.form.get("account_type", "client")
        if kind not in ACCOUNT_TYPES:
            flash("Type de compte invalide.", "error")
            return redirect(url_for("super_admin_restaurant_detail", restaurant_id=r.id))
        note = " ".join((request.form.get("note") or "").split())[:NOTE_MAX] or None
        row = db.session.get(RestaurantFlag, r.id)
        before = row.account_type if row else "client"
        if row is None:
            row = RestaurantFlag(restaurant_id=r.id)
            db.session.add(row)
        row.account_type, row.note, row.updated_by = kind, note, current_user.email
        db.session.commit()
        audit("sa_account_type", r.id, f"{before} -> {kind}")
        flash(f"Type de compte : {ACCOUNT_TYPES[kind]}."
              + (" Il n'est plus compté dans le revenu récurrent." if kind != "client" else ""), "success")
        return redirect(url_for("super_admin_restaurant_detail", restaurant_id=r.id))

    # ---------------------------------------------------------------- contextes de pages
    @app.context_processor
    def _sa_health_context():
        endpoint = request.endpoint or ""
        try:
            if endpoint == "super_admin_clients":
                return {"mf_sa_health": collect(), "mf_sa_account_badges": ACCOUNT_BADGES,
                        "mf_sa_account_types": ACCOUNT_TYPES}
            if endpoint == "super_admin_restaurant_detail":
                rid = (request.view_args or {}).get("restaurant_id")
                r = db.session.get(Restaurant, rid) if rid else None
                if r is None or r.slug == PLATFORM_SLUG:
                    return {}
                now = datetime.utcnow()
                h = collect(now, [r.id]).get(r.id)
                return {"mf_sa_ops": {"h": h, "issues": diagnose(r, h, now), "types": ACCOUNT_TYPES,
                                      "badges": ACCOUNT_BADGES, "risk": risk_days(), "levels": LEVELS}}
        except Exception:
            db.session.rollback()
            log.exception("Contexte santé impossible")
        return {}
