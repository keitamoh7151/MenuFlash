"""
MenuFlash Mini - Fonctionnalités et limites par formule.

Source unique des droits commerciaux :
- Basic / Pro configurables par le super-admin ;
- essai actif = droits Pro ;
- exception temporaire ou permanente par restaurant ;
- vérification serveur, pas seulement masquage visuel ;
- aucune donnée supprimée lors d'un downgrade.
"""
from datetime import datetime
from functools import wraps
import csv
import io

from flask import Response, abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user

from models import MenuItem, Order, Restaurant, User, db

FEATURES = {
    "takeaway": {"label": "Commandes à emporter", "kind": "bool", "basic": "0", "pro": "1"},
    "advanced_stats": {"label": "Statistiques avancées", "kind": "bool", "basic": "0", "pro": "1"},
    "data_export": {"label": "Exports de données", "kind": "bool", "basic": "0", "pro": "1"},
    "max_staff": {"label": "Comptes employés maximum", "kind": "int", "basic": "3", "pro": "20"},
    "max_menu_items": {"label": "Articles au menu maximum", "kind": "int", "basic": "50", "pro": "500"},
}
PLAN_LABELS = {"basic": "Basic", "pro": "Pro", "trial": "Essai Pro"}


class PlanEntitlement(db.Model):
    __tablename__ = "plan_entitlements"
    id = db.Column(db.Integer, primary_key=True)
    plan = db.Column(db.String(20), nullable=False, index=True)
    feature_key = db.Column(db.String(50), nullable=False, index=True)
    value = db.Column(db.String(100), nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    __table_args__ = (db.UniqueConstraint("plan", "feature_key", name="uq_plan_feature"),)


class RestaurantFeatureOverride(db.Model):
    __tablename__ = "restaurant_feature_overrides"
    id = db.Column(db.Integer, primary_key=True)
    restaurant_id = db.Column(db.Integer, nullable=False, index=True)
    feature_key = db.Column(db.String(50), nullable=False, index=True)
    value = db.Column(db.String(100), nullable=False)
    expires_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    __table_args__ = (db.UniqueConstraint("restaurant_id", "feature_key", name="uq_restaurant_feature"),)

    @property
    def active(self):
        return self.expires_at is None or self.expires_at >= datetime.utcnow()


def _parse(feature_key, raw):
    meta = FEATURES[feature_key]
    if meta["kind"] == "bool":
        return str(raw).lower() in ("1", "true", "yes", "on")
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return 0


def effective_plan(restaurant):
    # Un essai n'est Pro que jusqu'à sa date d'expiration.
    if (restaurant.subscription_plan == "trial" and restaurant.subscription_active_until
            and restaurant.subscription_active_until >= datetime.utcnow()):
        return "pro"
    return restaurant.subscription_plan if restaurant.subscription_plan in ("basic", "pro") else "basic"


def entitlement(restaurant, feature_key):
    if not restaurant or feature_key not in FEATURES:
        return False
    override = RestaurantFeatureOverride.query.filter_by(
        restaurant_id=restaurant.id, feature_key=feature_key).first()
    if override and override.active:
        return _parse(feature_key, override.value)
    plan = effective_plan(restaurant)
    rule = PlanEntitlement.query.filter_by(plan=plan, feature_key=feature_key).first()
    raw = rule.value if rule else FEATURES[feature_key][plan]
    return _parse(feature_key, raw)


def has_feature(restaurant, feature_key):
    return bool(entitlement(restaurant, feature_key))


def plan_limit(restaurant, feature_key):
    return int(entitlement(restaurant, feature_key) or 0)


def init_plans(app, *, super_admin_required, active_restaurant, log_audit=None):
    with app.app_context():
        for model in (PlanEntitlement, RestaurantFeatureOverride):
            model.__table__.create(db.engine, checkfirst=True)
        changed = False
        for plan in ("basic", "pro"):
            for key, meta in FEATURES.items():
                if not PlanEntitlement.query.filter_by(plan=plan, feature_key=key).first():
                    db.session.add(PlanEntitlement(plan=plan, feature_key=key, value=meta[plan]))
                    changed = True
        if changed:
            db.session.commit()

    app.jinja_env.globals.update(
        plan_has=lambda restaurant, key: has_feature(restaurant, key),
        plan_value=lambda restaurant, key: entitlement(restaurant, key),
        plan_effective=lambda restaurant: effective_plan(restaurant),
        PLAN_FEATURES=FEATURES,
        PLAN_LABELS=PLAN_LABELS,
    )

    @app.context_processor
    def _plan_context():
        if current_user.is_authenticated and not current_user.is_super_admin:
            try:
                restaurant = active_restaurant()
                return {"current_plan": effective_plan(restaurant),
                        "current_plan_label": ("Essai Pro" if restaurant.subscription_plan == "trial"
                                               and effective_plan(restaurant) == "pro"
                                               else PLAN_LABELS.get(effective_plan(restaurant), "Basic"))}
            except Exception:
                db.session.rollback()
        return {}

    # ------------------------------------------------------------------
    # Écran restaurateur : explique ce qui est compris / verrouillé.
    # ------------------------------------------------------------------
    @app.route("/admin/formule")
    def admin_plan():
        if not current_user.is_authenticated or not current_user.is_owner:
            abort(403)
        restaurant = active_restaurant()
        values = {k: entitlement(restaurant, k) for k in FEATURES}
        return render_template("admin_plan.html", restaurant=restaurant, values=values,
                               effective=effective_plan(restaurant))

    # ------------------------------------------------------------------
    # Super-admin : règles Basic / Pro.
    # ------------------------------------------------------------------
    @app.route("/super-admin/formules", methods=["GET", "POST"])
    @super_admin_required
    def super_admin_plans():
        if request.method == "POST":
            for plan in ("basic", "pro"):
                for key, meta in FEATURES.items():
                    field = f"{plan}_{key}"
                    if meta["kind"] == "bool":
                        raw = "1" if request.form.get(field) == "on" else "0"
                    else:
                        try:
                            raw = str(max(0, min(10000, int(request.form.get(field, 0)))))
                        except ValueError:
                            flash(f"Valeur incorrecte pour {meta['label']} ({PLAN_LABELS[plan]}).", "error")
                            return redirect(url_for("super_admin_plans"))
                    rule = PlanEntitlement.query.filter_by(plan=plan, feature_key=key).first()
                    if rule:
                        rule.value = raw
                    else:
                        db.session.add(PlanEntitlement(plan=plan, feature_key=key, value=raw))
            db.session.commit()
            if log_audit:
                log_audit("plan_features_updated", details="Matrice Basic/Pro mise à jour")
            flash("Fonctionnalités des formules enregistrées. Effet immédiat.", "success")
            return redirect(url_for("super_admin_plans"))
        rules = {(r.plan, r.feature_key): r.value for r in PlanEntitlement.query.all()}
        restaurants = Restaurant.query.filter(Restaurant.is_active.is_(True)).order_by(Restaurant.name).all()
        overrides = RestaurantFeatureOverride.query.order_by(RestaurantFeatureOverride.updated_at.desc()).all()
        return render_template("super_admin_plans.html", features=FEATURES, rules=rules,
                               restaurants=restaurants, overrides=overrides)

    @app.route("/super-admin/formules/exception", methods=["POST"])
    @super_admin_required
    def super_admin_plan_override():
        try:
            restaurant_id = int(request.form.get("restaurant_id", "0"))
        except ValueError:
            restaurant_id = 0
        restaurant = db.session.get(Restaurant, restaurant_id)
        key = request.form.get("feature_key", "")
        if not restaurant or key not in FEATURES:
            abort(404)
        action = request.form.get("action", "save")
        row = RestaurantFeatureOverride.query.filter_by(
            restaurant_id=restaurant.id, feature_key=key).first()
        if action == "delete":
            if row:
                db.session.delete(row)
            db.session.commit()
            flash("Exception supprimée : la formule s'applique de nouveau.", "success")
            return redirect(url_for("super_admin_plans"))
        raw = request.form.get("value", "")
        if FEATURES[key]["kind"] == "bool":
            raw = "1" if raw == "1" else "0"
        else:
            try:
                raw = str(max(0, min(10000, int(raw))))
            except ValueError:
                flash("La limite doit être un nombre.", "error")
                return redirect(url_for("super_admin_plans"))
        expiration = None
        if request.form.get("expires_at"):
            try:
                expiration = datetime.strptime(request.form["expires_at"], "%Y-%m-%d")
                expiration = expiration.replace(hour=23, minute=59, second=59)
            except ValueError:
                flash("Date d'expiration invalide.", "error")
                return redirect(url_for("super_admin_plans"))
        if row:
            row.value, row.expires_at = raw, expiration
        else:
            db.session.add(RestaurantFeatureOverride(restaurant_id=restaurant.id,
                           feature_key=key, value=raw, expires_at=expiration))
        db.session.commit()
        if log_audit:
            log_audit("restaurant_feature_override", restaurant_id=restaurant.id,
                      details=f"{key}={raw}; expiration={expiration or 'aucune'}")
        flash(f"Exception enregistrée pour {restaurant.name}.", "success")
        return redirect(url_for("super_admin_plans"))

    def locked(feature_key):
        flash(f"« {FEATURES[feature_key]['label']} » est disponible avec la formule Pro.", "warning")
        return redirect(url_for("admin_plan", feature=feature_key))

    # ------------------------------------------------------------------
    # Verrous serveur. Ils sont installés après les autres modules afin de
    # protéger aussi leurs routes, y compris takeaway.py.
    # ------------------------------------------------------------------
    def wrap_feature(endpoint, feature_key):
        original = app.view_functions.get(endpoint)
        if not original:
            return
        @wraps(original)
        def protected(*args, **kwargs):
            restaurant = active_restaurant()
            if not has_feature(restaurant, feature_key):
                return locked(feature_key)
            return original(*args, **kwargs)
        app.view_functions[endpoint] = protected

    wrap_feature("admin_takeaway_settings", "takeaway")
    wrap_feature("admin_statistics", "advanced_stats")

    @app.route("/admin/statistiques/export.csv")
    def admin_statistics_export():
        if not current_user.is_authenticated or not current_user.is_owner:
            abort(403)
        restaurant = active_restaurant()
        if not has_feature(restaurant, "data_export"):
            return locked("data_export")
        output = io.StringIO(newline="")
        output.write("\ufeff")
        writer = csv.writer(output, delimiter=";")
        writer.writerow(["Commande", "Date UTC", "Statut", "Table / retrait", "Total",
                         "Devise", "Articles", "Notes"])
        orders = (Order.query.filter_by(restaurant_id=restaurant.id)
                  .order_by(Order.created_at.desc()).limit(10000).all())
        for order in orders:
            items = " | ".join(f"{it.quantity} x {it.item_name}" for it in order.items)
            writer.writerow([order.id, order.created_at.strftime("%Y-%m-%d %H:%M:%S"),
                             order.status, order.table_label or "", order.total,
                             restaurant.currency, items, order.notes or ""])
        filename = f"commandes-{restaurant.slug}-{datetime.utcnow():%Y%m%d}.csv"
        return Response(output.getvalue(), mimetype="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{filename}"',
                                 "Cache-Control": "no-store"})

    # L'API publique ne révèle pas / n'active pas la fonction si le plan la bloque.
    original_config = app.view_functions.get("takeaway_config")
    if original_config:
        @wraps(original_config)
        def takeaway_config(slug, *args, **kwargs):
            restaurant = Restaurant.query.filter_by(slug=slug, is_active=True).first_or_404()
            if not has_feature(restaurant, "takeaway"):
                resp = jsonify(enabled=False, paused=False, plan_locked=True,
                               allow_asap=False, allow_scheduled=False)
                resp.headers["Cache-Control"] = "no-store"
                return resp
            return original_config(slug, *args, **kwargs)
        app.view_functions["takeaway_config"] = takeaway_config

    # Empêche un appel direct POST, même si le bouton est caché côté navigateur.
    original_place_order = app.view_functions.get("place_order")
    if original_place_order:
        @wraps(original_place_order)
        def place_order(slug, *args, **kwargs):
            if request.form.get("fulfillment") == "takeaway":
                restaurant = Restaurant.query.filter_by(slug=slug, is_active=True).first_or_404()
                if not has_feature(restaurant, "takeaway"):
                    if request.headers.get("X-Requested-With") == "fetch":
                        return jsonify(ok=False, error="Les commandes à emporter nécessitent la formule Pro."), 403
                    return locked("takeaway")
            return original_place_order(slug, *args, **kwargs)
        app.view_functions["place_order"] = place_order

    # Limite de comptes employés.
    original_staff_add = app.view_functions.get("admin_team_add")
    if original_staff_add:
        @wraps(original_staff_add)
        def admin_team_add(*args, **kwargs):
            restaurant = active_restaurant()
            limit = plan_limit(restaurant, "max_staff")
            count = User.query.filter_by(restaurant_id=restaurant.id, role="staff").count()
            if count >= limit:
                flash(f"Votre formule permet {limit} compte(s) employé(s). Passez à Pro pour en ajouter davantage.", "warning")
                return redirect(url_for("admin_plan", feature="max_staff"))
            return original_staff_add(*args, **kwargs)
        app.view_functions["admin_team_add"] = admin_team_add

    # Limite d'articles au menu.
    original_add_item = app.view_functions.get("add_item")
    if original_add_item:
        @wraps(original_add_item)
        def add_item(*args, **kwargs):
            restaurant = active_restaurant()
            limit = plan_limit(restaurant, "max_menu_items")
            count = MenuItem.query.filter_by(restaurant_id=restaurant.id).count()
            if count >= limit:
                flash(f"Votre formule permet {limit} article(s) au menu. Passez à Pro pour augmenter cette limite.", "warning")
                return redirect(url_for("admin_plan", feature="max_menu_items"))
            return original_add_item(*args, **kwargs)
        app.view_functions["add_item"] = add_item
