"""
MenuFlash Mini — Panel super-admin complet.

Branché depuis app.py par :

    init_super_admin(app, log_audit=..., send_password_reset_email=..., ...)

Contenu
-------
1. PILOTAGE       : revenus du mois, revenu mensuel récurrent (MRR), revenus
                    sur 12 mois, clients (actifs / essai / expirés /
                    suspendus / archivés), conversion essai -> payant,
                    restaurants inactifs, alertes.
2. CLIENTS        : recherche et filtres, fiche détaillée, actions rapides
                    (jours offerts, changement de plan, suspension avec
                    motif, réactivation, déblocage, lien de réinitialisation,
                    déconnexion forcée, transfert de propriété), notes
                    internes, export des données, archivage puis
                    suppression définitive.
4. FINANCES       : prix des offres et durée d'essai configurables, reçus
                    PDF, codes promo, relances, exports CSV (Excel).
5. COMMUNICATION  : annonces dans le tableau de bord des restaurants,
                    email groupé, mode maintenance.
7. SANTÉ          : volumes, taille de la base, version déployée, dernières
                    erreurs de l'application.

Toutes les actions sensibles sont tracées dans le journal d'audit.

Les nouvelles tables (paramètres, notes, codes promo, annonces, erreurs,
métadonnées d'administration) sont créées automatiquement au démarrage si
elles n'existent pas. Aucune table existante n'est modifiée : rien à faire
manuellement sur Render.
"""
import csv
import io
import json
import logging
import os
import time
import traceback
from datetime import datetime, timedelta

from flask import (render_template, redirect, url_for, request, flash, abort,
                   Response, send_file, session, got_request_exception)
from flask_login import current_user

from models import (db, Restaurant, User, MenuCategory, MenuItem, QRCode,
                    SubscriptionPayment, Order, OrderItem, DishReview,
                    MenuChangeLog, AuditLog, RestaurantAccess, MenuItemSuggestion,
                    TRIAL_DAYS)

logger = logging.getLogger("menuflash.superadmin")

PLATFORM_SLUG = "menuflash-admin"          # restaurant technique du super-admin
APP_STARTED_AT = datetime.utcnow()
ACTIVE_STATUSES = ("nouvelle", "en_preparation", "prete")

DEFAULT_SETTINGS = {
    "price_basic": "10000",
    "price_pro": "20000",
    "trial_days": str(TRIAL_DAYS),
    "maintenance_on": "0",
    "maintenance_message": "Nous effectuons une maintenance. Le service revient très vite.",
}
PLAN_LABELS = {"trial": "Essai", "basic": "Basic", "pro": "Pro"}


# ==========================================================================
# Modèles (nouvelles tables uniquement)
# ==========================================================================
class PlatformSetting(db.Model):
    __tablename__ = "platform_settings"
    key = db.Column(db.String(50), primary_key=True)
    value = db.Column(db.String(500), nullable=False, default="")
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class RestaurantAdminMeta(db.Model):
    """Informations d'administration d'un restaurant (sans toucher à la table restaurants)."""
    __tablename__ = "restaurant_admin_meta"
    restaurant_id = db.Column(db.Integer, primary_key=True)
    suspension_reason = db.Column(db.String(300))
    suspended_at = db.Column(db.DateTime)
    archived_at = db.Column(db.DateTime)


class AdminNote(db.Model):
    __tablename__ = "admin_notes"
    id = db.Column(db.Integer, primary_key=True)
    restaurant_id = db.Column(db.Integer, nullable=False, index=True)
    author_email = db.Column(db.String(120))
    body = db.Column(db.String(1000), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class PromoCode(db.Model):
    __tablename__ = "promo_codes"
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(30), unique=True, nullable=False)
    percent_off = db.Column(db.Integer, nullable=False)
    max_uses = db.Column(db.Integer)                 # vide = illimité
    uses = db.Column(db.Integer, nullable=False, default=0)
    valid_until = db.Column(db.DateTime)             # vide = sans limite
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    @property
    def is_usable(self):
        if not self.is_active:
            return False
        if self.valid_until and self.valid_until < datetime.utcnow():
            return False
        if self.max_uses is not None and self.uses >= self.max_uses:
            return False
        return True


class Announcement(db.Model):
    __tablename__ = "announcements"
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(120), nullable=False)
    body = db.Column(db.String(1000), nullable=False)
    level = db.Column(db.String(20), nullable=False, default="info")   # info / warning / success
    target = db.Column(db.String(30), nullable=False, default="all")   # all / trial / basic / pro
    starts_at = db.Column(db.DateTime, default=datetime.utcnow)
    ends_at = db.Column(db.DateTime)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class AppError(db.Model):
    __tablename__ = "app_errors"
    id = db.Column(db.Integer, primary_key=True)
    path = db.Column(db.String(300))
    method = db.Column(db.String(10))
    error_type = db.Column(db.String(120))
    message = db.Column(db.String(500))
    trace = db.Column(db.Text)
    user_email = db.Column(db.String(120))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)


_NEW_TABLES = [PlatformSetting, RestaurantAdminMeta, AdminNote, PromoCode, Announcement, AppError]


# ==========================================================================
# Paramètres de la plateforme (petit cache mémoire de 15 s)
# ==========================================================================
_settings_cache = {"at": 0.0, "data": {}}


def _load_settings():
    if time.time() - _settings_cache["at"] < 15:
        return _settings_cache["data"]
    data = dict(DEFAULT_SETTINGS)
    try:
        for row in PlatformSetting.query.all():
            data[row.key] = row.value
    except Exception:
        db.session.rollback()
    _settings_cache.update(at=time.time(), data=data)
    return data


def get_setting(key):
    return _load_settings().get(key, DEFAULT_SETTINGS.get(key, ""))


def set_setting(key, value):
    row = PlatformSetting.query.get(key)
    if row is None:
        row = PlatformSetting(key=key)
        db.session.add(row)
    row.value = str(value)[:500]
    _settings_cache["at"] = 0.0


def _int_setting(key, default):
    try:
        return int(get_setting(key))
    except (TypeError, ValueError):
        return default


def get_trial_days():
    """Durée de l'essai gratuit (configurable dans Finances > Paramètres)."""
    return max(1, min(_int_setting("trial_days", TRIAL_DAYS), 90))


def plan_price(plan):
    return {"basic": _int_setting("price_basic", 0), "pro": _int_setting("price_pro", 0)}.get(plan, 0)


# ==========================================================================
# Outils
# ==========================================================================
def _client_restaurants_query():
    return Restaurant.query.filter(Restaurant.slug != PLATFORM_SLUG)


def _meta(restaurant_id, create=False):
    meta = RestaurantAdminMeta.query.get(restaurant_id)
    if meta is None and create:
        meta = RestaurantAdminMeta(restaurant_id=restaurant_id)
        db.session.add(meta)
    return meta


def _status(r, meta):
    if meta and meta.archived_at:
        return "archive"
    if not r.is_active:
        return "suspendu"
    if not r.is_subscription_valid:
        return "expire"
    if r.subscription_plan == "trial":
        return "essai"
    return "actif"


STATUS_LABELS = {
    "actif": ("Actif", "mf-sa-badge-ok"),
    "essai": ("Essai", "mf-sa-badge-info"),
    "expire": ("Expiré", "mf-sa-badge-danger"),
    "suspendu": ("Suspendu", "mf-sa-badge-warning"),
    "archive": ("Archivé", "mf-sa-badge-muted"),
}


def _owners(restaurant_id):
    return User.query.filter_by(restaurant_id=restaurant_id, role="owner").all()


def _month_start(dt):
    return datetime(dt.year, dt.month, 1)


def _shift_month(dt, n):
    m = dt.month - 1 + n
    return datetime(dt.year + m // 12, m % 12 + 1, 1)


def _parse_date(raw):
    try:
        return datetime.strptime((raw or "").strip(), "%Y-%m-%d")
    except ValueError:
        return None


def _csv_response(filename, header, rows):
    buf = io.StringIO()
    buf.write("\ufeff")  # BOM : Excel reconnaît l'UTF-8 (accents)
    writer = csv.writer(buf, delimiter=";")
    writer.writerow(header)
    for row in rows:
        writer.writerow(row)
    return Response(buf.getvalue(), mimetype="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


def _fmt(n):
    return "{:,}".format(int(n or 0)).replace(",", " ")


# ==========================================================================
# Branchement
# ==========================================================================
def init_super_admin(app, *, log_audit, send_password_reset_email, super_admin_required,
                     slugify, slug_is_available, safe_int, min_password_length=8):

    with app.app_context():
        for model in _NEW_TABLES:
            try:
                model.__table__.create(db.engine, checkfirst=True)
            except Exception:
                logger.exception("Création de la table %s impossible", model.__tablename__)

    app.jinja_env.filters["fcfa"] = _fmt

    def audit(action, restaurant_id=None, details=None, success=True):
        log_audit(action[:40], success=success, restaurant_id=restaurant_id,
                  user_email=current_user.email if current_user.is_authenticated else None,
                  details=details)

    # ----------------------------------------------------------------------
    # Contexte commun aux templates
    # ----------------------------------------------------------------------
    @app.context_processor
    def _inject_platform():
        ctx = {"maintenance_on": get_setting("maintenance_on") == "1",
               "plan_labels": PLAN_LABELS}
        try:
            if current_user.is_authenticated and not current_user.is_super_admin:
                r = Restaurant.query.get(current_user.restaurant_id)
                plan = r.subscription_plan if r else None
                now = datetime.utcnow()
                q = Announcement.query.filter(
                    Announcement.is_active.is_(True),
                    Announcement.starts_at <= now,
                    (Announcement.ends_at.is_(None)) | (Announcement.ends_at >= now),
                    Announcement.target.in_(["all", plan or "all"]),
                ).order_by(Announcement.created_at.desc()).limit(3)
                ctx["platform_announcements"] = q.all()
        except Exception:
            db.session.rollback()
        return ctx

    # ----------------------------------------------------------------------
    # Mode maintenance + comptes suspendus (avant chaque requête)
    # ----------------------------------------------------------------------
    _ALWAYS_ALLOWED = {"static", "media", "admin_login", "admin_logout"}

    @app.before_request
    def _platform_gates():
        endpoint = request.endpoint or ""
        if endpoint in _ALWAYS_ALLOWED:
            return None
        is_super = current_user.is_authenticated and current_user.is_super_admin
        if is_super:
            return None

        if get_setting("maintenance_on") == "1":
            return render_template("maintenance.html",
                                   message=get_setting("maintenance_message")), 503

        if current_user.is_authenticated:
            r = Restaurant.query.get(current_user.restaurant_id)
            if r and not r.is_active:
                meta = _meta(r.id)
                return render_template("account_suspended.html", restaurant=r,
                                       reason=(meta.suspension_reason if meta else None)), 403
        return None

    # ----------------------------------------------------------------------
    # Capture des erreurs (page Santé)
    # ----------------------------------------------------------------------
    def _record_error(sender, exception, **extra):
        try:
            db.session.rollback()
            db.session.add(AppError(
                path=(request.path or "")[:300],
                method=request.method[:10],
                error_type=type(exception).__name__[:120],
                message=str(exception)[:500],
                trace="".join(traceback.format_exception(type(exception), exception,
                                                         exception.__traceback__))[-6000:],
                user_email=(current_user.email if current_user.is_authenticated else None),
            ))
            db.session.commit()
            old = AppError.query.order_by(AppError.id.desc()).offset(500).all()
            for e in old:
                db.session.delete(e)
            db.session.commit()
        except Exception:
            db.session.rollback()

    got_request_exception.connect(_record_error, app, weak=False)

    # ======================================================================
    # 1. PILOTAGE
    # ======================================================================
    @app.route("/super-admin")
    @super_admin_required
    def super_admin_dashboard():
        now = datetime.utcnow()
        restaurants = _client_restaurants_query().all()
        metas = {m.restaurant_id: m for m in RestaurantAdminMeta.query.all()}
        counts = {k: 0 for k in STATUS_LABELS}
        mrr = 0
        for r in restaurants:
            st = _status(r, metas.get(r.id))
            counts[st] += 1
            if st == "actif":
                mrr += plan_price(r.subscription_plan)

        client_ids = [r.id for r in restaurants]
        month_start = _month_start(now)
        pay_q = SubscriptionPayment.query.filter(SubscriptionPayment.restaurant_id.in_(client_ids or [0]))
        revenue_month = sum(p.amount for p in pay_q.filter(SubscriptionPayment.created_at >= month_start))

        first = _shift_month(month_start, -11)
        months = [_shift_month(first, i) for i in range(12)]
        monthly = {m: 0 for m in months}
        for p in pay_q.filter(SubscriptionPayment.created_at >= first).all():
            key = _month_start(p.created_at)
            if key in monthly:
                monthly[key] += p.amount
        chart = [{"label": m.strftime("%m/%y"), "value": monthly[m]} for m in months]

        paying_ids = {p.restaurant_id for p in pay_q.with_entities(SubscriptionPayment.restaurant_id).distinct()}
        conversion = round(100 * len(paying_ids) / len(restaurants)) if restaurants else 0

        week_ago = now - timedelta(days=7)
        new_this_week = sum(1 for r in restaurants if r.created_at and r.created_at >= week_ago)
        orders_week = Order.query.filter(Order.restaurant_id.in_(client_ids or [0]),
                                         Order.created_at >= week_ago).count()
        orders_month = Order.query.filter(Order.restaurant_id.in_(client_ids or [0]),
                                          Order.created_at >= month_start).count()
        recent_active = {rid for (rid,) in db.session.query(Order.restaurant_id)
                         .filter(Order.created_at >= week_ago).distinct()}
        inactive = [r for r in restaurants
                    if _status(r, metas.get(r.id)) in ("actif", "essai") and r.id not in recent_active]

        expiring = sorted([r for r in restaurants
                           if _status(r, metas.get(r.id)) in ("actif", "essai") and r.days_remaining <= 5],
                          key=lambda r: r.subscription_active_until or now)
        locked = User.query.filter(User.locked_until > now).all()
        failed_24h = AuditLog.query.filter(AuditLog.success.is_(False),
                                           AuditLog.action == "login_failed",
                                           AuditLog.created_at >= now - timedelta(hours=24)).count()
        errors_24h = AppError.query.filter(AppError.created_at >= now - timedelta(hours=24)).count()

        return render_template(
            "super_admin_dashboard.html",
            counts=counts, total=len(restaurants), mrr=mrr, revenue_month=revenue_month,
            chart=chart, conversion=conversion, paying=len(paying_ids),
            new_this_week=new_this_week, orders_week=orders_week, orders_month=orders_month,
            inactive=inactive[:10], inactive_count=len(inactive),
            expiring=expiring[:10], locked=locked, failed_24h=failed_24h, errors_24h=errors_24h,
        )

    # ======================================================================
    # 2. CLIENTS
    # ======================================================================
    @app.route("/super-admin/clients")
    @super_admin_required
    def super_admin_clients():
        q = (request.args.get("q") or "").strip()[:80]
        status_filter = request.args.get("statut", "")
        plan_filter = request.args.get("plan", "")
        sort = request.args.get("tri", "recent")

        query = _client_restaurants_query()
        if q:
            like = f"%{q.lower()}%"
            owner_rids = [u.restaurant_id for u in User.query.filter(db.func.lower(User.email).like(like)).all()]
            query = query.filter(db.or_(db.func.lower(Restaurant.name).like(like),
                                        db.func.lower(Restaurant.slug).like(like),
                                        Restaurant.id.in_(owner_rids or [0])))
        if plan_filter in PLAN_LABELS:
            query = query.filter(Restaurant.subscription_plan == plan_filter)
        restaurants = query.all()

        metas = {m.restaurant_id: m for m in RestaurantAdminMeta.query.all()}
        owners = {}
        for u in User.query.filter_by(role="owner").all():
            owners.setdefault(u.restaurant_id, u)
        rows = []
        for r in restaurants:
            st = _status(r, metas.get(r.id))
            if status_filter and st != status_filter:
                continue
            if not status_filter and st == "archive":
                continue   # les archivés ne s'affichent qu'avec le filtre "Archivé"
            rows.append({"r": r, "status": st, "owner": owners.get(r.id)})

        far = datetime(2100, 1, 1)
        if sort == "expiration":
            rows.sort(key=lambda x: x["r"].subscription_active_until or far)
        elif sort == "nom":
            rows.sort(key=lambda x: x["r"].name.lower())
        else:
            rows.sort(key=lambda x: x["r"].created_at or far, reverse=True)

        return render_template("super_admin_clients.html", rows=rows, q=q,
                               status_filter=status_filter, plan_filter=plan_filter, sort=sort,
                               status_labels=STATUS_LABELS, trial_days=get_trial_days())

    @app.route("/super-admin/clients/export.csv")
    @super_admin_required
    def super_admin_clients_export():
        metas = {m.restaurant_id: m for m in RestaurantAdminMeta.query.all()}
        owners = {}
        for u in User.query.filter_by(role="owner").all():
            owners.setdefault(u.restaurant_id, u)
        rows = []
        for r in _client_restaurants_query().order_by(Restaurant.created_at.desc()).all():
            o = owners.get(r.id)
            rows.append([r.id, r.name, r.slug, o.email if o else "", r.phone or "",
                         PLAN_LABELS.get(r.subscription_plan, r.subscription_plan),
                         STATUS_LABELS[_status(r, metas.get(r.id))][0],
                         r.subscription_active_until.strftime("%d/%m/%Y") if r.subscription_active_until else "",
                         r.created_at.strftime("%d/%m/%Y") if r.created_at else ""])
        audit("sa_export_clients", details=f"{len(rows)} clients exportés")
        return _csv_response("clients_menuflash.csv",
                             ["ID", "Restaurant", "Adresse menu", "Email gestionnaire", "Téléphone",
                              "Plan", "Statut", "Expire le", "Créé le"], rows)

    @app.route("/super-admin/restaurants/new", methods=["POST"])
    @super_admin_required
    def super_admin_create_restaurant():
        name = request.form.get("name", "").strip()[:120]
        slug = slugify(request.form.get("slug", "").strip()[:80] or name)
        owner_email = request.form.get("owner_email", "").strip().lower()[:120]
        owner_password = request.form.get("owner_password", "")

        if not name or not owner_email or len(owner_password) < min_password_length:
            flash("Tous les champs sont requis.", "error")
            return redirect(url_for("super_admin_clients"))
        if not slug_is_available(slug):
            flash("Cette adresse de menu est déjà utilisée par un autre restaurant.", "error")
            return redirect(url_for("super_admin_clients"))
        if User.query.filter_by(email=owner_email).first():
            flash("Cet email est déjà utilisé par un autre compte.", "error")
            return redirect(url_for("super_admin_clients"))

        days = get_trial_days()
        restaurant = Restaurant(name=name, slug=slug, subscription_plan="trial",
                                subscription_active_until=datetime.utcnow() + timedelta(days=days))
        db.session.add(restaurant)
        db.session.commit()
        owner = User(restaurant_id=restaurant.id, email=owner_email, role="owner")
        owner.set_password(owner_password)
        db.session.add(owner)
        db.session.commit()
        audit("super_admin_restaurant_created", restaurant.id, f"« {name} » créé par le super-admin")
        flash(f"Restaurant « {name} » créé avec un essai gratuit de {days} jours.", "success")
        return redirect(url_for("super_admin_restaurant_detail", restaurant_id=restaurant.id))

    @app.route("/super-admin/restaurants/<int:restaurant_id>")
    @super_admin_required
    def super_admin_restaurant_detail(restaurant_id):
        r = Restaurant.query.get_or_404(restaurant_id)
        if r.slug == PLATFORM_SLUG:
            abort(404)
        meta = _meta(r.id)
        users = User.query.filter_by(restaurant_id=r.id).order_by(User.role, User.created_at).all()
        owners = [u for u in users if u.role == "owner"]
        linked_ids = set()
        for o in owners:
            linked_ids |= {a.restaurant_id for a in RestaurantAccess.query.filter_by(user_id=o.id)}
        linked_ids |= {u.restaurant_id for u in User.query.join(
            RestaurantAccess, RestaurantAccess.user_id == User.id).filter(
            RestaurantAccess.restaurant_id == r.id)}
        linked_ids.discard(r.id)
        linked = Restaurant.query.filter(Restaurant.id.in_(linked_ids or [0])).all()

        now = datetime.utcnow()
        stats = {
            "dishes": MenuItem.query.filter_by(restaurant_id=r.id).count(),
            "categories": MenuCategory.query.filter_by(restaurant_id=r.id).count(),
            "qrcodes": QRCode.query.filter_by(restaurant_id=r.id).count(),
            "orders_total": Order.query.filter_by(restaurant_id=r.id).count(),
            "orders_30d": Order.query.filter(Order.restaurant_id == r.id,
                                             Order.created_at >= now - timedelta(days=30)).count(),
        }
        last_order = Order.query.filter_by(restaurant_id=r.id).order_by(Order.created_at.desc()).first()
        payments = (SubscriptionPayment.query.filter_by(restaurant_id=r.id)
                    .order_by(SubscriptionPayment.created_at.desc()).all())
        notes = AdminNote.query.filter_by(restaurant_id=r.id).order_by(AdminNote.created_at.desc()).all()
        logs = (AuditLog.query.filter_by(restaurant_id=r.id)
                .order_by(AuditLog.created_at.desc()).limit(15).all())
        promos = [p for p in PromoCode.query.order_by(PromoCode.code).all() if p.is_usable]
        return render_template(
            "super_admin_restaurant_detail.html", restaurant=r, meta=meta,
            status=_status(r, meta), status_labels=STATUS_LABELS, users=users, owners=owners,
            linked=linked, stats=stats, last_order=last_order, payments=payments,
            notes=notes, logs=logs, promos=promos,
            price_basic=plan_price("basic"), price_pro=plan_price("pro"),
        )

    def _client_or_404(restaurant_id):
        r = Restaurant.query.get_or_404(restaurant_id)
        if r.slug == PLATFORM_SLUG:
            abort(404)
        return r

    def _back(r):
        return redirect(url_for("super_admin_restaurant_detail", restaurant_id=r.id))

    @app.route("/super-admin/restaurants/<int:restaurant_id>/action", methods=["POST"])
    @super_admin_required
    def super_admin_restaurant_action(restaurant_id):
        """Toutes les actions rapides de la fiche client, regroupées en une route."""
        r = _client_or_404(restaurant_id)
        action = request.form.get("action", "")
        now = datetime.utcnow()

        if action == "add_days":
            days = safe_int(request.form.get("days"), min_value=1, max_value=365)
            if not days:
                flash("Nombre de jours invalide.", "error")
                return _back(r)
            base = r.subscription_active_until if (r.subscription_active_until and
                                                   r.subscription_active_until > now) else now
            r.subscription_active_until = base + timedelta(days=days)
            db.session.commit()
            audit("sa_days_offered", r.id, f"+{days} jours (jusqu'au {r.subscription_active_until:%d/%m/%Y})")
            flash(f"{days} jour(s) offert(s). Actif jusqu'au {r.subscription_active_until:%d/%m/%Y}.", "success")

        elif action == "change_plan":
            plan = request.form.get("plan", "")
            if plan not in PLAN_LABELS:
                flash("Offre invalide.", "error")
                return _back(r)
            old = r.subscription_plan
            r.subscription_plan = plan
            db.session.commit()
            audit("sa_plan_changed", r.id, f"{old} -> {plan}")
            flash(f"Offre changée : {PLAN_LABELS[plan]}.", "success")

        elif action == "suspend":
            reason = (request.form.get("reason") or "").strip()[:300]
            if not reason:
                flash("Indiquez le motif de la suspension : le restaurateur le verra.", "error")
                return _back(r)
            meta = _meta(r.id, create=True)
            meta.suspension_reason = reason
            meta.suspended_at = now
            r.is_active = False
            db.session.commit()
            audit("sa_restaurant_suspended", r.id, reason)
            flash("Restaurant suspendu. Son menu public et son tableau de bord sont bloqués.", "success")

        elif action == "reactivate":
            meta = _meta(r.id)
            if meta:
                meta.suspension_reason = None
                meta.suspended_at = None
                meta.archived_at = None
            r.is_active = True
            db.session.commit()
            audit("sa_restaurant_reactivated", r.id)
            flash("Restaurant réactivé.", "success")

        elif action == "unlock":
            n = 0
            for u in User.query.filter_by(restaurant_id=r.id).all():
                if u.locked_until or u.failed_login_attempts:
                    u.locked_until = None
                    u.failed_login_attempts = 0
                    n += 1
            db.session.commit()
            audit("sa_accounts_unlocked", r.id, f"{n} compte(s)")
            flash(f"{n} compte(s) débloqué(s).", "success")

        elif action == "send_reset":
            sent = 0
            for o in _owners(r.id):
                send_password_reset_email(o)
                sent += 1
            audit("sa_reset_link_sent", r.id, f"{sent} lien(s)")
            flash(f"Lien de réinitialisation envoyé à {sent} gestionnaire(s) "
                  f"(visible dans les logs Render tant que le SMTP n'est pas configuré).", "success")

        elif action == "force_logout":
            users = User.query.filter_by(restaurant_id=r.id).all()
            for u in users:
                u.rotate_security_stamp()
            db.session.commit()
            audit("sa_force_logout", r.id, f"{len(users)} compte(s)")
            flash(f"Toutes les sessions de ce restaurant ont été fermées ({len(users)} compte(s)).", "success")

        elif action == "transfer":
            new_email = (request.form.get("new_email") or "").strip().lower()[:120]
            owner = User.query.filter_by(restaurant_id=r.id, role="owner").order_by(User.id).first()
            if not owner or "@" not in new_email or "." not in new_email.split("@")[-1]:
                flash("Email invalide ou aucun gestionnaire à transférer.", "error")
                return _back(r)
            if User.query.filter_by(email=new_email).first():
                flash("Cet email est déjà utilisé par un autre compte.", "error")
                return _back(r)
            old = owner.email
            owner.email = new_email
            owner.email_verified = False
            owner.rotate_security_stamp()
            db.session.commit()
            send_password_reset_email(owner)
            audit("sa_ownership_transferred", r.id, f"{old} -> {new_email}")
            flash(f"Propriété transférée à {new_email}. Un lien pour choisir son mot de passe lui a été envoyé.",
                  "success")

        elif action == "archive":
            meta = _meta(r.id, create=True)
            meta.archived_at = now
            r.is_active = False
            if not meta.suspension_reason:
                meta.suspension_reason = "Compte fermé."
            db.session.commit()
            audit("sa_restaurant_archived", r.id)
            flash("Restaurant archivé. Vous pouvez maintenant exporter ses données puis le supprimer.", "success")

        else:
            flash("Action inconnue.", "error")
        return _back(r)

    @app.route("/super-admin/restaurants/<int:restaurant_id>/notes", methods=["POST"])
    @super_admin_required
    def super_admin_add_note(restaurant_id):
        r = _client_or_404(restaurant_id)
        body = (request.form.get("body") or "").strip()[:1000]
        if body:
            db.session.add(AdminNote(restaurant_id=r.id, author_email=current_user.email, body=body))
            db.session.commit()
            flash("Note ajoutée.", "success")
        return _back(r)

    @app.route("/super-admin/notes/<int:note_id>/delete", methods=["POST"])
    @super_admin_required
    def super_admin_delete_note(note_id):
        note = AdminNote.query.get_or_404(note_id)
        rid = note.restaurant_id
        db.session.delete(note)
        db.session.commit()
        return redirect(url_for("super_admin_restaurant_detail", restaurant_id=rid))

    @app.route("/super-admin/restaurants/<int:restaurant_id>/export.json")
    @super_admin_required
    def super_admin_restaurant_export(restaurant_id):
        r = _client_or_404(restaurant_id)
        cats = MenuCategory.query.filter_by(restaurant_id=r.id).order_by(MenuCategory.position).all()
        data = {
            "export_date": datetime.utcnow().isoformat(),
            "restaurant": {"id": r.id, "nom": r.name, "adresse_menu": r.slug, "telephone": r.phone,
                           "devise": r.currency, "offre": r.subscription_plan,
                           "abonnement_jusqu_au": r.subscription_active_until.isoformat()
                           if r.subscription_active_until else None,
                           "cree_le": r.created_at.isoformat() if r.created_at else None},
            "comptes": [{"email": u.email, "role": u.role} for u in User.query.filter_by(restaurant_id=r.id)],
            "menu": [{"categorie": c.name, "plats": [
                {"nom": i.name, "description": i.description, "prix": i.price,
                 "disponible": i.is_available} for i in c.items]} for c in cats],
            "commandes": [{"id": o.id, "date": o.created_at.isoformat() if o.created_at else None,
                           "table": o.table_label, "statut": o.status, "total": o.total,
                           "plats": [{"nom": it.item_name, "quantite": it.quantity, "prix": it.item_price}
                                     for it in o.items]}
                          for o in Order.query.filter_by(restaurant_id=r.id).order_by(Order.created_at)],
            "paiements": [{"date": p.created_at.isoformat() if p.created_at else None, "montant": p.amount,
                           "methode": p.method, "reference": p.reference, "mois": p.months_added}
                          for p in SubscriptionPayment.query.filter_by(restaurant_id=r.id)],
        }
        audit("sa_restaurant_exported", r.id)
        return Response(json.dumps(data, ensure_ascii=False, indent=2), mimetype="application/json",
                        headers={"Content-Disposition": f'attachment; filename="export_{r.slug}.json"'})

    @app.route("/super-admin/restaurants/<int:restaurant_id>/delete", methods=["POST"])
    @super_admin_required
    def super_admin_restaurant_delete(restaurant_id):
        r = _client_or_404(restaurant_id)
        meta = _meta(r.id)
        if not (meta and meta.archived_at):
            flash("Archivez d'abord le restaurant avant de le supprimer.", "error")
            return _back(r)
        if (request.form.get("confirm_slug") or "").strip() != r.slug:
            flash(f"Confirmation incorrecte : tapez exactement « {r.slug} ».", "error")
            return _back(r)

        users = User.query.filter_by(restaurant_id=r.id).all()
        user_ids = [u.id for u in users]
        if any(u.is_super_admin for u in users):
            flash("Impossible : un compte super-admin est rattaché à ce restaurant.", "error")
            return _back(r)
        other_access = RestaurantAccess.query.filter(RestaurantAccess.user_id.in_(user_ids or [0]),
                                                     RestaurantAccess.restaurant_id != r.id).count()
        if other_access:
            flash("Impossible : le gestionnaire possède aussi d'autres restaurants. "
                  "Supprimez ou transférez-les d'abord.", "error")
            return _back(r)

        name, rid = r.name, r.id
        try:
            from media_store import StoredImage
            order_ids = [o.id for o in Order.query.filter_by(restaurant_id=rid)]
            if order_ids:
                OrderItem.query.filter(OrderItem.order_id.in_(order_ids)).delete(synchronize_session=False)
            DishReview.query.filter_by(restaurant_id=rid).delete(synchronize_session=False)
            Order.query.filter_by(restaurant_id=rid).delete(synchronize_session=False)
            MenuItemSuggestion.query.filter_by(restaurant_id=rid).delete(synchronize_session=False)
            MenuItem.query.filter_by(restaurant_id=rid).delete(synchronize_session=False)
            MenuCategory.query.filter_by(restaurant_id=rid).delete(synchronize_session=False)
            QRCode.query.filter_by(restaurant_id=rid).delete(synchronize_session=False)
            MenuChangeLog.query.filter_by(restaurant_id=rid).delete(synchronize_session=False)
            StoredImage.query.filter_by(restaurant_id=rid).delete(synchronize_session=False)
            RestaurantAccess.query.filter(db.or_(RestaurantAccess.restaurant_id == rid,
                                                 RestaurantAccess.user_id.in_(user_ids or [0]))
                                          ).delete(synchronize_session=False)
            if user_ids:
                SubscriptionPayment.query.filter(SubscriptionPayment.recorded_by.in_(user_ids)
                                                 ).update({"recorded_by": None}, synchronize_session=False)
            SubscriptionPayment.query.filter_by(restaurant_id=rid).delete(synchronize_session=False)
            AuditLog.query.filter_by(restaurant_id=rid).update({"restaurant_id": None},
                                                                synchronize_session=False)
            User.query.filter_by(restaurant_id=rid).delete(synchronize_session=False)
            AdminNote.query.filter_by(restaurant_id=rid).delete(synchronize_session=False)
            RestaurantAdminMeta.query.filter_by(restaurant_id=rid).delete(synchronize_session=False)
            db.session.delete(r)
            db.session.commit()
        except Exception:
            db.session.rollback()
            logger.exception("Suppression du restaurant %s impossible", rid)
            flash("La suppression a échoué, aucune donnée n'a été modifiée.", "error")
            return redirect(url_for("super_admin_restaurant_detail", restaurant_id=rid))

        audit("sa_restaurant_deleted", None, f"« {name} » (id {rid}) supprimé définitivement")
        flash(f"Restaurant « {name} » supprimé définitivement.", "success")
        return redirect(url_for("super_admin_clients"))

    # ======================================================================
    # 4. FINANCES
    # ======================================================================
    @app.route("/super-admin/restaurants/<int:restaurant_id>/payment", methods=["POST"])
    @super_admin_required
    def super_admin_record_payment(restaurant_id):
        r = _client_or_404(restaurant_id)
        plan = request.form.get("plan", "basic")
        if plan not in ("basic", "pro"):
            plan = "basic"
        months = safe_int(request.form.get("months"), default=1, min_value=1, max_value=36)
        method = (request.form.get("method") or "Non précisé")[:30]
        reference = (request.form.get("reference") or "").strip()[:60]

        promo = None
        code = (request.form.get("promo_code") or "").strip().upper()
        if code:
            promo = PromoCode.query.filter_by(code=code).first()
            if not promo or not promo.is_usable:
                flash("Code promo invalide, expiré ou épuisé.", "error")
                return _back(r)

        raw_amount = (request.form.get("amount") or "").strip()
        if raw_amount:
            amount = safe_int(raw_amount, min_value=0, max_value=100_000_000)
        else:
            amount = plan_price(plan) * months
            if promo:
                amount = round(amount * (100 - promo.percent_off) / 100)
        if amount is None:
            flash("Montant invalide.", "error")
            return _back(r)

        now = datetime.utcnow()
        base = r.subscription_active_until if (r.subscription_active_until and
                                               r.subscription_active_until > now) else now
        r.subscription_active_until = base + timedelta(days=30 * months)
        r.subscription_plan = plan
        meta = _meta(r.id)
        if not (meta and (meta.suspension_reason or meta.archived_at)):
            r.is_active = True
        if promo:
            promo.uses += 1
            reference = f"{reference} [promo {promo.code} -{promo.percent_off}%]".strip()[:80]
        db.session.add(SubscriptionPayment(restaurant_id=r.id, amount=amount, method=method,
                                           reference=reference, months_added=months,
                                           recorded_by=current_user.id))
        db.session.commit()
        audit("super_admin_payment_recorded", r.id,
              f"{amount} FCFA, {months} mois, {plan}, {method}" + (f", promo {promo.code}" if promo else ""))
        flash(f"Paiement de {_fmt(amount)} FCFA enregistré : actif jusqu'au "
              f"{r.subscription_active_until:%d/%m/%Y}.", "success")
        return _back(r)

    @app.route("/super-admin/paiements/<int:payment_id>/recu.pdf")
    @super_admin_required
    def super_admin_payment_receipt(payment_id):
        from reportlab.lib.pagesizes import A5
        from reportlab.lib.units import mm
        from reportlab.pdfgen import canvas
        from reportlab.lib.colors import HexColor

        p = SubscriptionPayment.query.get_or_404(payment_id)
        r = Restaurant.query.get(p.restaurant_id)
        owner = User.query.filter_by(restaurant_id=p.restaurant_id, role="owner").first()
        buf = io.BytesIO()
        w, h = A5
        c = canvas.Canvas(buf, pagesize=A5)
        c.setFillColor(HexColor("#1A1D23"))
        c.rect(0, h - 28 * mm, w, 28 * mm, fill=1, stroke=0)
        c.setFillColor(HexColor("#FFFFFF"))
        c.setFont("Helvetica-Bold", 16)
        c.drawString(14 * mm, h - 16 * mm, "MenuFlash Mini")
        c.setFont("Helvetica", 10)
        c.drawString(14 * mm, h - 22 * mm, "Reçu de paiement d'abonnement")
        c.setFillColor(HexColor("#1A1D23"))
        y = h - 42 * mm
        lines = [
            ("Reçu n°", f"MF-{p.created_at:%Y}-{p.id:05d}" if p.created_at else f"MF-{p.id:05d}"),
            ("Date", p.created_at.strftime("%d/%m/%Y %H:%M") if p.created_at else "—"),
            ("Client", r.name if r else f"Restaurant #{p.restaurant_id}"),
            ("Email", owner.email if owner else "—"),
            ("Offre", PLAN_LABELS.get(r.subscription_plan, "") if r else "—"),
            ("Durée", f"{p.months_added} mois"),
            ("Moyen de paiement", p.method or "—"),
            ("Référence", p.reference or "—"),
        ]
        for label, value in lines:
            c.setFont("Helvetica", 10)
            c.setFillColor(HexColor("#6B6A66"))
            c.drawString(14 * mm, y, label)
            c.setFillColor(HexColor("#1A1D23"))
            c.drawString(55 * mm, y, str(value)[:48])
            y -= 8 * mm
        y -= 4 * mm
        c.setStrokeColor(HexColor("#E7E4DD"))
        c.line(14 * mm, y + 4 * mm, w - 14 * mm, y + 4 * mm)
        c.setFont("Helvetica-Bold", 14)
        c.drawString(14 * mm, y - 4 * mm, "Montant payé")
        c.setFillColor(HexColor("#B5730F"))
        c.drawRightString(w - 14 * mm, y - 4 * mm, f"{_fmt(p.amount)} FCFA")
        c.setFillColor(HexColor("#6B6A66"))
        c.setFont("Helvetica", 8)
        c.drawCentredString(w / 2, 10 * mm, "Merci pour votre confiance. — MenuFlash Mini")
        c.showPage()
        c.save()
        buf.seek(0)
        return send_file(buf, mimetype="application/pdf", as_attachment=True,
                         download_name=f"recu_MF_{p.id:05d}.pdf")

    @app.route("/super-admin/finances")
    @super_admin_required
    def super_admin_finances():
        start = _parse_date(request.args.get("du")) or _month_start(datetime.utcnow())
        end = _parse_date(request.args.get("au"))
        end_excl = (end + timedelta(days=1)) if end else None
        q = SubscriptionPayment.query.filter(SubscriptionPayment.created_at >= start)
        if end_excl:
            q = q.filter(SubscriptionPayment.created_at < end_excl)
        payments = q.order_by(SubscriptionPayment.created_at.desc()).all()
        names = {r.id: r.name for r in Restaurant.query.all()}
        total = sum(p.amount for p in payments)
        by_method = {}
        for p in payments:
            by_method[p.method or "—"] = by_method.get(p.method or "—", 0) + p.amount
        return render_template(
            "super_admin_finances.html", payments=payments, names=names, total=total,
            by_method=sorted(by_method.items(), key=lambda x: -x[1]),
            start=start, end=end, promos=PromoCode.query.order_by(PromoCode.created_at.desc()).all(),
            price_basic=plan_price("basic"), price_pro=plan_price("pro"), trial_days=get_trial_days(),
        )

    @app.route("/super-admin/finances/export.csv")
    @super_admin_required
    def super_admin_finances_export():
        start = _parse_date(request.args.get("du"))
        end = _parse_date(request.args.get("au"))
        q = SubscriptionPayment.query
        if start:
            q = q.filter(SubscriptionPayment.created_at >= start)
        if end:
            q = q.filter(SubscriptionPayment.created_at < end + timedelta(days=1))
        names = {r.id: r.name for r in Restaurant.query.all()}
        rows = [[f"MF-{p.id:05d}", p.created_at.strftime("%d/%m/%Y") if p.created_at else "",
                 names.get(p.restaurant_id, p.restaurant_id), p.amount, p.method or "",
                 p.months_added, p.reference or ""]
                for p in q.order_by(SubscriptionPayment.created_at).all()]
        audit("sa_export_payments", details=f"{len(rows)} paiements exportés")
        return _csv_response("paiements_menuflash.csv",
                             ["Reçu", "Date", "Restaurant", "Montant (FCFA)", "Méthode", "Mois", "Référence"],
                             rows)

    @app.route("/super-admin/finances/parametres", methods=["POST"])
    @super_admin_required
    def super_admin_settings_save():
        basic = safe_int(request.form.get("price_basic"), min_value=0, max_value=10_000_000)
        pro = safe_int(request.form.get("price_pro"), min_value=0, max_value=10_000_000)
        trial = safe_int(request.form.get("trial_days"), min_value=1, max_value=90)
        if basic is None or pro is None or trial is None:
            flash("Valeurs invalides.", "error")
            return redirect(url_for("super_admin_finances"))
        set_setting("price_basic", basic)
        set_setting("price_pro", pro)
        set_setting("trial_days", trial)
        db.session.commit()
        audit("sa_settings_updated", details=f"basic={basic} pro={pro} essai={trial}j")
        flash("Paramètres enregistrés.", "success")
        return redirect(url_for("super_admin_finances"))

    @app.route("/super-admin/promos", methods=["POST"])
    @super_admin_required
    def super_admin_promo_create():
        code = "".join(ch for ch in (request.form.get("code") or "").upper() if ch.isalnum())[:30]
        pct = safe_int(request.form.get("percent_off"), min_value=1, max_value=100)
        max_uses = safe_int(request.form.get("max_uses"), min_value=1, max_value=100000)
        valid_until = _parse_date(request.form.get("valid_until"))
        if len(code) < 3 or not pct:
            flash("Code (3 caractères minimum, lettres et chiffres) et pourcentage requis.", "error")
            return redirect(url_for("super_admin_finances"))
        if PromoCode.query.filter_by(code=code).first():
            flash("Ce code existe déjà.", "error")
            return redirect(url_for("super_admin_finances"))
        db.session.add(PromoCode(code=code, percent_off=pct, max_uses=max_uses,
                                 valid_until=(valid_until + timedelta(days=1)) if valid_until else None))
        db.session.commit()
        audit("sa_promo_created", details=f"{code} -{pct}%")
        flash(f"Code promo {code} créé.", "success")
        return redirect(url_for("super_admin_finances"))

    @app.route("/super-admin/promos/<int:promo_id>/toggle", methods=["POST"])
    @super_admin_required
    def super_admin_promo_toggle(promo_id):
        p = PromoCode.query.get_or_404(promo_id)
        p.is_active = not p.is_active
        db.session.commit()
        audit("sa_promo_toggled", details=f"{p.code} {'activé' if p.is_active else 'désactivé'}")
        return redirect(url_for("super_admin_finances"))

    # ---------------- Relances ----------------
    def _reminder_rows():
        now = datetime.utcnow()
        metas = {m.restaurant_id: m for m in RestaurantAdminMeta.query.all()}
        rows = []
        for r in _client_restaurants_query().all():
            st = _status(r, metas.get(r.id))
            if st in ("archive", "suspendu") or not r.subscription_active_until:
                continue
            delta = (r.subscription_active_until - now).days
            if -30 <= delta <= 7:
                rows.append({"r": r, "status": st, "days": delta, "owners": _owners(r.id)})
        rows.sort(key=lambda x: x["r"].subscription_active_until)
        return rows

    def _send_reminder(r):
        sent = 0
        price = plan_price(r.subscription_plan if r.subscription_plan in ("basic", "pro") else "basic")
        expired = not r.is_subscription_valid
        for o in _owners(r.id):
            from mailer import send_transactional_email
            send_transactional_email(
                app, o.email,
                subject=("Votre abonnement MenuFlash Mini a expiré" if expired
                         else "Votre abonnement MenuFlash Mini arrive à échéance"),
                body_text=(
                    f"Bonjour,\n\nL'abonnement de « {r.name} » "
                    + (f"a expiré le {r.subscription_active_until:%d/%m/%Y}. Votre menu n'est plus "
                       f"visible par vos clients." if expired else
                       f"expire le {r.subscription_active_until:%d/%m/%Y}.")
                    + f"\n\nPour renouveler ({_fmt(price)} FCFA / mois), réglez par Wave, Orange Money "
                      f"ou Moov Money et envoyez-nous la référence de la transaction.\n\n"
                      f"Merci pour votre confiance,\nL'équipe MenuFlash Mini"),
            )
            sent += 1
        return sent

    @app.route("/super-admin/relances")
    @super_admin_required
    def super_admin_reminders():
        return render_template("super_admin_reminders.html", rows=_reminder_rows())

    @app.route("/super-admin/relances/envoyer", methods=["POST"])
    @super_admin_required
    def super_admin_reminders_send():
        rid = safe_int(request.form.get("restaurant_id"))
        targets = ([Restaurant.query.get_or_404(rid)] if rid else [row["r"] for row in _reminder_rows()])
        total = 0
        for r in targets:
            n = _send_reminder(r)
            total += n
            db.session.add(AdminNote(restaurant_id=r.id, author_email=current_user.email,
                                     body=f"Rappel d'abonnement envoyé ({n} destinataire(s))."))
            log_audit("sa_reminder_sent", restaurant_id=r.id, user_email=current_user.email,
                      details=f"{n} destinataire(s)")
        db.session.commit()
        flash(f"Rappel envoyé à {total} destinataire(s) pour {len(targets)} restaurant(s).", "success")
        return redirect(url_for("super_admin_reminders"))

    # ======================================================================
    # 5. COMMUNICATION
    # ======================================================================
    @app.route("/super-admin/communication")
    @super_admin_required
    def super_admin_communication():
        return render_template(
            "super_admin_communication.html",
            announcements=Announcement.query.order_by(Announcement.created_at.desc()).all(),
            maintenance_message=get_setting("maintenance_message"),
            now=datetime.utcnow(),
        )

    @app.route("/super-admin/annonces", methods=["POST"])
    @super_admin_required
    def super_admin_announcement_create():
        title = (request.form.get("title") or "").strip()[:120]
        body = (request.form.get("body") or "").strip()[:1000]
        level = request.form.get("level") if request.form.get("level") in ("info", "warning", "success") else "info"
        target = request.form.get("target") if request.form.get("target") in ("all", "trial", "basic", "pro") else "all"
        ends = _parse_date(request.form.get("ends_at"))
        if not title or not body:
            flash("Titre et message requis.", "error")
            return redirect(url_for("super_admin_communication"))
        db.session.add(Announcement(title=title, body=body, level=level, target=target,
                                    ends_at=(ends + timedelta(days=1)) if ends else None))
        db.session.commit()
        audit("sa_announcement_created", details=f"{title} ({target})")
        flash("Annonce publiée.", "success")
        return redirect(url_for("super_admin_communication"))

    @app.route("/super-admin/annonces/<int:ann_id>/toggle", methods=["POST"])
    @super_admin_required
    def super_admin_announcement_toggle(ann_id):
        a = Announcement.query.get_or_404(ann_id)
        a.is_active = not a.is_active
        db.session.commit()
        return redirect(url_for("super_admin_communication"))

    @app.route("/super-admin/annonces/<int:ann_id>/delete", methods=["POST"])
    @super_admin_required
    def super_admin_announcement_delete(ann_id):
        db.session.delete(Announcement.query.get_or_404(ann_id))
        db.session.commit()
        return redirect(url_for("super_admin_communication"))

    @app.route("/super-admin/email-groupe", methods=["POST"])
    @super_admin_required
    def super_admin_bulk_email():
        from mailer import send_transactional_email, is_mail_configured
        subject = (request.form.get("subject") or "").strip()[:150]
        body = (request.form.get("body") or "").strip()[:5000]
        target = request.form.get("target", "all")
        if not subject or not body:
            flash("Objet et message requis.", "error")
            return redirect(url_for("super_admin_communication"))
        metas = {m.restaurant_id: m for m in RestaurantAdminMeta.query.all()}
        emails = set()
        for r in _client_restaurants_query().all():
            st = _status(r, metas.get(r.id))
            if st == "archive":
                continue
            if target == "all" or target == st or target == r.subscription_plan:
                emails |= {o.email for o in _owners(r.id)}
        for e in sorted(emails):
            send_transactional_email(app, e, subject, body)
        audit("sa_bulk_email", details=f"« {subject} » -> {len(emails)} destinataire(s) ({target})")
        msg = f"Email envoyé à {len(emails)} gestionnaire(s)."
        if not is_mail_configured(app):
            msg += " Attention : SMTP non configuré, les emails sont seulement écrits dans les logs."
        flash(msg, "success")
        return redirect(url_for("super_admin_communication"))

    @app.route("/super-admin/maintenance", methods=["POST"])
    @super_admin_required
    def super_admin_maintenance():
        turn_on = request.form.get("state") == "on"
        message = (request.form.get("message") or "").strip()[:500]
        if message:
            set_setting("maintenance_message", message)
        set_setting("maintenance_on", "1" if turn_on else "0")
        db.session.commit()
        audit("sa_maintenance_" + ("on" if turn_on else "off"))
        flash("Mode maintenance activé : seuls les super-admins ont accès à l'application."
              if turn_on else "Mode maintenance désactivé.", "success")
        return redirect(url_for("super_admin_communication"))

    # ======================================================================
    # 7. SANTÉ TECHNIQUE
    # ======================================================================
    @app.route("/super-admin/sante")
    @super_admin_required
    def super_admin_health():
        from media_store import StoredImage
        counts = {
            "Restaurants": _client_restaurants_query().count(),
            "Comptes": User.query.count(),
            "Plats": MenuItem.query.count(),
            "Commandes": Order.query.count(),
            "Avis": DishReview.query.count(),
            "Images": StoredImage.query.count(),
            "Entrées du journal": AuditLog.query.count(),
        }
        images_bytes = db.session.query(db.func.coalesce(
            db.func.sum(db.func.length(StoredImage.data)), 0)).scalar() or 0

        db_size, engine = None, db.engine.dialect.name
        try:
            if engine == "postgresql":
                db_size = db.session.execute(db.text("SELECT pg_database_size(current_database())")).scalar()
            elif engine == "sqlite":
                path = db.engine.url.database
                db_size = os.path.getsize(path) if path and os.path.exists(path) else None
        except Exception:
            db.session.rollback()

        db_ok = True
        try:
            db.session.execute(db.text("SELECT 1"))
        except Exception:
            db.session.rollback()
            db_ok = False

        from mailer import is_mail_configured
        env = {
            "Base de données": engine,
            "Version (commit)": (os.environ.get("RENDER_GIT_COMMIT") or "inconnue")[:10],
            "Branche": os.environ.get("RENDER_GIT_BRANCH") or "—",
            "Service": os.environ.get("RENDER_SERVICE_NAME") or "local",
            "Démarré le": APP_STARTED_AT.strftime("%d/%m/%Y %H:%M") + " (UTC)",
            "Envoi d'emails (SMTP)": "configuré" if is_mail_configured(app) else "NON configuré",
            "HTTPS forcé": "oui" if os.environ.get("FORCE_HTTPS", "").lower() == "true" else "non",
            "Mode maintenance": "ACTIF" if get_setting("maintenance_on") == "1" else "désactivé",
        }
        errors = AppError.query.order_by(AppError.created_at.desc()).limit(30).all()
        return render_template("super_admin_health.html", counts=counts, env=env,
                               db_size=db_size, images_bytes=images_bytes, db_ok=db_ok,
                               errors=errors, uptime=datetime.utcnow() - APP_STARTED_AT)

    @app.route("/super-admin/sante/erreurs/vider", methods=["POST"])
    @super_admin_required
    def super_admin_errors_clear():
        n = AppError.query.delete()
        db.session.commit()
        audit("sa_errors_cleared", details=f"{n} erreur(s)")
        flash(f"{n} erreur(s) effacée(s).", "success")
        return redirect(url_for("super_admin_health"))
