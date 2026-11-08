"""
MenuFlash Mini — Contrôle des restaurants clients par le super-admin.

Branchement dans app.py, TOUT EN BAS (après init_tickets(...)) :

    from sa_controls import init_sa_controls
    init_sa_controls(app, super_admin_required=super_admin_required, log_audit=log_audit)

Ce module COMPLÈTE super_admin.py (qui gère déjà suspension, réactivation,
archivage, jours offerts, changement d'offre, transfert, etc.).

1. TROIS NIVEAUX DE CONTRÔLE (du plus doux au plus fort)
   a. Lecture seule : le gestionnaire ne peut plus rien modifier (menu,
      équipe, apparence, réglages…). Les commandes continuent d'arriver et
      la cuisine les traite normalement. Idéal pour un impayé récent.
   b. Commandes bloquées : le menu reste visible mais en consultation ;
      aucune nouvelle commande ni ajout à une commande n'est accepté.
   c. Suspension (super_admin.py) : menu public et tableau de bord bloqués.
   Le restaurateur voit un bandeau avec le motif dans son tableau de bord.

2. PAGE PUBLIQUE PROPRE : le menu d'un restaurant suspendu ou archivé affiche
   « Menu temporairement indisponible » (au lieu d'une erreur 404), et les
   appels techniques reçoivent une réponse JSON claire.

3. EMAIL AU GESTIONNAIRE (case à cocher) lors d'une suspension, d'une
   réactivation, d'un blocage des commandes ou d'une mise en lecture seule.

4. ACTIONS GROUPÉES depuis la liste des clients : suspendre, réactiver,
   offrir des jours, changer l'offre, bloquer/débloquer les commandes,
   lecture seule. Chaque restaurant est tracé dans le journal d'audit.

5. SUPPRESSION DÉFINITIVE COMPLÈTE : la suppression de super_admin.py ne
   connaissait pas les tables ajoutées depuis (compositions, à emporter,
   références, horaires, appels, profils, sessions, 2FA, formules…). Sur
   PostgreSQL, la table des compositions (clé étrangère vers les plats)
   faisait même ÉCHOUER la suppression. Toutes les tables liées au restaurant
   ou à ses comptes sont maintenant nettoyées, dans la MÊME transaction :
   si quelque chose échoue, rien n'est supprimé.

Nouvelle table « restaurant_controls » créée automatiquement au démarrage.
Aucune table existante n'est modifiée.
"""
import logging
from datetime import datetime, timedelta
from functools import wraps
from types import SimpleNamespace

from flask import (abort, flash, jsonify, redirect, render_template, request,
                   session, url_for)
from flask_login import current_user

from mailer import send_transactional_email
from models import Restaurant, RestaurantAccess, User, db
from super_admin import (PLAN_LABELS, PLATFORM_SLUG, RestaurantAdminMeta, _meta)

log = logging.getLogger("menuflash.sa_controls")

MAX_BULK = 200
REASON_MAX = 300

# Tables supprimées par super_admin.py lui-même (ou à ne jamais vider ici).
_CORE_TABLES = {
    "restaurants", "users", "audit_logs", "orders", "order_items", "dish_reviews",
    "menu_item_suggestions", "menu_items", "menu_categories", "qr_codes",
    "menu_change_logs", "stored_images", "restaurant_access", "subscription_payments",
    "admin_notes", "restaurant_admin_meta",
}

# En lecture seule, ces actions restent possibles (commandes, appels, sécurité du compte).
_READ_ONLY_ALLOWED_PREFIXES = (
    "/admin/commandes", "/admin/appels", "/admin/login", "/admin/logout",
    "/admin/mot-de-passe", "/admin/reinitialiser", "/admin/2fa",
    "/admin/restaurants/switch", "/admin/verifier-email",
)


class RestaurantControl(db.Model):
    __tablename__ = "restaurant_controls"
    restaurant_id = db.Column(db.Integer, primary_key=True)
    orders_blocked = db.Column(db.Boolean, nullable=False, default=False)
    read_only = db.Column(db.Boolean, nullable=False, default=False)
    reason = db.Column(db.String(REASON_MAX))
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    updated_by = db.Column(db.String(120))

    @property
    def any(self):
        return bool(self.orders_blocked or self.read_only)


def control_for(restaurant_id, create=False):
    row = db.session.get(RestaurantControl, restaurant_id) if restaurant_id else None
    if row is None and create:
        row = RestaurantControl(restaurant_id=restaurant_id)
        db.session.add(row)
    return row


def _wants_json():
    return (request.headers.get("X-Requested-With") == "fetch"
            or request.path.endswith(".json")
            or request.accept_mimetypes.best == "application/json")


def init_sa_controls(app, *, super_admin_required, log_audit):
    with app.app_context():
        try:
            RestaurantControl.__table__.create(db.engine, checkfirst=True)
        except Exception:
            log.exception("Création de la table restaurant_controls impossible")

    def audit(action, restaurant_id=None, details=None):
        log_audit(action[:40], success=True, restaurant_id=restaurant_id,
                  user_email=current_user.email if current_user.is_authenticated else None,
                  details=details)

    # ------------------------------------------------------------------
    # Emails au gestionnaire
    # ------------------------------------------------------------------
    MESSAGES = {
        "suspend": ("Votre compte {name} est suspendu",
                    "Bonjour,\n\nLe compte de « {name} » sur MenuFlash a été suspendu.\n"
                    "Motif : {reason}\n\nVotre menu n'est plus visible par vos clients et votre "
                    "tableau de bord est bloqué.\nContactez l'équipe MenuFlash pour régulariser "
                    "la situation.\n"),
        "reactivate": ("Votre compte {name} est réactivé",
                       "Bonjour,\n\nBonne nouvelle : le compte de « {name} » sur MenuFlash est "
                       "de nouveau actif.\nVotre menu est visible et vos clients peuvent commander.\n"),
        "block_orders": ("Commandes en ligne bloquées pour {name}",
                         "Bonjour,\n\nLes commandes en ligne de « {name} » sont temporairement bloquées.\n"
                         "Motif : {reason}\n\nVotre menu reste visible en consultation.\n"
                         "Contactez l'équipe MenuFlash pour les réactiver.\n"),
        "unblock_orders": ("Commandes en ligne réactivées pour {name}",
                           "Bonjour,\n\nLes commandes en ligne de « {name} » sont de nouveau ouvertes.\n"),
        "read_only_on": ("Compte {name} en lecture seule",
                         "Bonjour,\n\nLe tableau de bord de « {name} » passe en lecture seule.\n"
                         "Motif : {reason}\n\nVos clients peuvent toujours commander et votre équipe "
                         "traite les commandes normalement, mais les modifications (menu, équipe, "
                         "réglages) sont bloquées.\nContactez l'équipe MenuFlash pour régulariser.\n"),
        "read_only_off": ("Compte {name} : modifications réactivées",
                          "Bonjour,\n\nVous pouvez de nouveau modifier votre menu et vos réglages sur "
                          "MenuFlash pour « {name} ».\n"),
    }

    def notify(restaurant, kind, reason=None):
        subject, body = MESSAGES[kind]
        sent = 0
        for owner in User.query.filter_by(restaurant_id=restaurant.id, role="owner").all():
            if send_transactional_email(app, owner.email,
                                        subject.format(name=restaurant.name),
                                        body.format(name=restaurant.name, reason=reason or "—")):
                sent += 1
        return sent

    # ------------------------------------------------------------------
    # Application d'un contrôle (fiche client et actions groupées)
    # ------------------------------------------------------------------
    def apply_control(r, action, reason=None):
        """Renvoie True si quelque chose a changé."""
        c = control_for(r.id, create=True)
        before = (c.orders_blocked, c.read_only)
        if action == "block_orders":
            c.orders_blocked = True
        elif action == "unblock_orders":
            c.orders_blocked = False
        elif action == "read_only_on":
            c.read_only = True
        elif action == "read_only_off":
            c.read_only = False
        else:
            return False
        if action in ("block_orders", "read_only_on"):
            c.reason = reason
        elif not (c.orders_blocked or c.read_only):
            c.reason = None
        c.updated_by = current_user.email
        c.updated_at = datetime.utcnow()
        return before != (c.orders_blocked, c.read_only)

    def _client_or_404(restaurant_id):
        r = Restaurant.query.get_or_404(restaurant_id)
        if r.slug == PLATFORM_SLUG:
            abort(404)
        return r

    @app.route("/super-admin/restaurants/<int:restaurant_id>/controles", methods=["POST"])
    @super_admin_required
    def super_admin_restaurant_controls(restaurant_id):
        r = _client_or_404(restaurant_id)
        action = request.form.get("action", "")
        reason = " ".join((request.form.get("reason") or "").split())[:REASON_MAX]
        if action not in MESSAGES or action in ("suspend", "reactivate"):
            flash("Action inconnue.", "error")
        elif action in ("block_orders", "read_only_on") and not reason:
            flash("Indiquez le motif : le restaurateur le verra.", "error")
        else:
            changed = apply_control(r, action, reason)
            db.session.commit()
            audit("sa_" + action, r.id, reason)
            labels = {"block_orders": "Commandes bloquées.", "unblock_orders": "Commandes réactivées.",
                      "read_only_on": "Tableau de bord en lecture seule.",
                      "read_only_off": "Modifications réactivées."}
            msg = labels[action] if changed else "Aucun changement : c'était déjà le cas."
            if changed and request.form.get("notify") == "1":
                msg += f" Email envoyé à {notify(r, action, reason)} gestionnaire(s)."
            flash(msg, "success")
        return redirect(url_for("super_admin_restaurant_detail", restaurant_id=r.id))

    # ------------------------------------------------------------------
    # Email lors d'une suspension / réactivation depuis la fiche client
    # ------------------------------------------------------------------
    original_action = app.view_functions.get("super_admin_restaurant_action")
    if original_action:
        @wraps(original_action)
        def super_admin_restaurant_action(restaurant_id):
            action = request.form.get("action", "")
            resp = original_action(restaurant_id)
            if request.form.get("notify") == "1" and action in ("suspend", "reactivate"):
                try:
                    r = db.session.get(Restaurant, restaurant_id)
                    ok = r is not None and ((action == "suspend" and not r.is_active)
                                            or (action == "reactivate" and r.is_active))
                    if ok:
                        meta = _meta(r.id)
                        n = notify(r, action, meta.suspension_reason if meta else None)
                        flash(f"Email envoyé à {n} gestionnaire(s).", "success")
                except Exception:
                    db.session.rollback()
                    log.exception("Email de suspension/réactivation impossible")
            return resp
        app.view_functions["super_admin_restaurant_action"] = super_admin_restaurant_action

    # ------------------------------------------------------------------
    # Actions groupées (liste des clients)
    # ------------------------------------------------------------------
    @app.route("/super-admin/clients/actions-groupees", methods=["POST"])
    @super_admin_required
    def super_admin_bulk_actions():
        back = redirect(request.referrer or url_for("super_admin_clients"))
        ids = []
        for raw in request.form.getlist("ids")[:MAX_BULK]:
            try:
                ids.append(int(raw))
            except ValueError:
                pass
        action = request.form.get("action", "")
        reason = " ".join((request.form.get("reason") or "").split())[:REASON_MAX]
        notify_on = request.form.get("notify") == "1"
        if not ids:
            flash("Sélectionnez au moins un restaurant.", "error")
            return back
        if action in ("suspend", "block_orders", "read_only_on") and not reason:
            flash("Indiquez le motif : les restaurateurs le verront.", "error")
            return back

        days = plan = None
        if action == "add_days":
            try:
                days = int(request.form.get("days", ""))
            except ValueError:
                days = 0
            if not 1 <= days <= 365:
                flash("Nombre de jours invalide (1 à 365).", "error")
                return back
        if action == "change_plan":
            plan = request.form.get("plan", "")
            if plan not in PLAN_LABELS:
                flash("Offre invalide.", "error")
                return back

        restaurants = (Restaurant.query.filter(Restaurant.id.in_(ids), Restaurant.slug != PLATFORM_SLUG)
                       .all())
        now = datetime.utcnow()
        changed, skipped, to_notify = [], 0, []
        for r in restaurants:
            meta = _meta(r.id)
            archived = bool(meta and meta.archived_at)
            if action == "suspend":
                if not r.is_active:
                    skipped += 1
                    continue
                meta = _meta(r.id, create=True)
                meta.suspension_reason, meta.suspended_at = reason, now
                r.is_active = False
            elif action == "reactivate":
                if r.is_active or archived:          # un archivé se réactive depuis sa fiche
                    skipped += 1
                    continue
                if meta:
                    meta.suspension_reason = meta.suspended_at = None
                r.is_active = True
            elif action == "add_days":
                base = r.subscription_active_until if (r.subscription_active_until and
                                                       r.subscription_active_until > now) else now
                r.subscription_active_until = base + timedelta(days=days)
            elif action == "change_plan":
                if r.subscription_plan == plan:
                    skipped += 1
                    continue
                r.subscription_plan = plan
            elif action in ("block_orders", "unblock_orders", "read_only_on", "read_only_off"):
                if not apply_control(r, action, reason):
                    skipped += 1
                    continue
            else:
                flash("Action inconnue.", "error")
                return back
            changed.append(r)
            if action in MESSAGES:
                to_notify.append(r)

        db.session.commit()
        details = {"add_days": f"+{days} jours", "change_plan": f"offre {plan}"}.get(action, reason)
        for r in changed:
            audit("sa_bulk_" + action, r.id, details)

        sent = 0
        if notify_on:
            for r in to_notify:
                sent += notify(r, action, reason)
        msg = f"{len(changed)} restaurant(s) mis à jour."
        if skipped:
            msg += f" {skipped} ignoré(s) (déjà dans cet état ou archivé)."
        if notify_on and to_notify:
            msg += f" {sent} email(s) envoyé(s)."
        flash(msg, "success")
        return back

    # ------------------------------------------------------------------
    # Contexte des pages super-admin (badges, état des contrôles)
    # ------------------------------------------------------------------
    @app.context_processor
    def _inject_controls():
        if not (request.endpoint or "").startswith("super_admin"):
            return {}
        try:
            rows = RestaurantControl.query.filter(
                (RestaurantControl.orders_blocked.is_(True)) | (RestaurantControl.read_only.is_(True))).all()
            return {"mf_sa_controls": {c.restaurant_id: c for c in rows},
                    "mf_sa_control_for": control_for}
        except Exception:
            db.session.rollback()
            return {}

    # ------------------------------------------------------------------
    # Bandeau dans le tableau de bord du restaurateur
    # ------------------------------------------------------------------
    def _active_restaurant_id():
        rid = session.get("active_restaurant_id")
        return rid or getattr(current_user, "restaurant_id", None)

    procs = app.template_context_processors[None]
    for i, proc in enumerate(procs):
        if getattr(proc, "__name__", "") == "_inject_platform":
            def _with_control_banner(proc=proc):
                ctx = proc()
                try:
                    if current_user.is_authenticated and not current_user.is_super_admin:
                        c = control_for(_active_restaurant_id())
                        if c and c.any:
                            parts = []
                            if c.read_only:
                                parts.append("les modifications sont bloquées (lecture seule)")
                            if c.orders_blocked:
                                parts.append("les commandes en ligne sont bloquées")
                            body = "Votre compte est limité : " + " et ".join(parts) + "."
                            if c.reason:
                                body += f" Motif : {c.reason}."
                            body += " Contactez l'équipe MenuFlash."
                            stamp = int(c.updated_at.timestamp()) if c.updated_at else 0
                            banner = SimpleNamespace(id=f"ctrl-{c.restaurant_id}-{stamp}", level="warning",
                                                     title="Compte limité", body=body)
                            ctx["platform_announcements"] = [banner] + list(ctx.get("platform_announcements") or [])
                except Exception:
                    db.session.rollback()
                return ctx
            _with_control_banner.__name__ = "_inject_platform_with_controls"
            procs[i] = _with_control_banner
            break

    # ------------------------------------------------------------------
    # Garde-fous appliqués à chaque requête
    # ------------------------------------------------------------------
    @app.before_request
    def _sa_control_gates():
        path = request.path or ""

        # 1) Menu public d'un restaurant suspendu ou archivé : page propre.
        if path.startswith("/m/"):
            slug = (request.view_args or {}).get("slug")
            if not slug:
                return None
            if current_user.is_authenticated and current_user.is_super_admin:
                return None
            r = Restaurant.query.filter_by(slug=slug).first()
            if r is None or r.is_active or r.slug == PLATFORM_SLUG:
                return None
            if _wants_json() or request.method != "GET":
                return jsonify(ok=False, error="Ce restaurant n'accepte pas de commandes pour le moment."), 503
            return render_template("restaurant_unavailable.html", restaurant=r), 503

        # 2) Lecture seule : aucune modification dans /admin, sauf commandes et sécurité.
        if (request.method in ("POST", "PUT", "PATCH", "DELETE") and path.startswith("/admin")
                and current_user.is_authenticated and not current_user.is_super_admin
                and not path.startswith(_READ_ONLY_ALLOWED_PREFIXES)):
            c = control_for(_active_restaurant_id())
            if c and c.read_only:
                msg = "Votre compte est en lecture seule : les modifications sont bloquées. " \
                      "Contactez l'équipe MenuFlash."
                if _wants_json():
                    return jsonify(ok=False, error=msg), 403
                flash(msg, "error")
                return redirect(request.referrer or url_for("admin_dashboard"))
        return None

    # ------------------------------------------------------------------
    # Commandes bloquées
    # ------------------------------------------------------------------
    BLOCKED_MSG = "Ce restaurant n'accepte pas de commandes en ligne pour le moment."

    def _blocked(slug):
        r = Restaurant.query.filter_by(slug=slug).first()
        c = control_for(r.id) if r else None
        return bool(c and c.orders_blocked)

    for endpoint in ("place_order", "add_to_order"):
        original = app.view_functions.get(endpoint)
        if not original:
            continue

        def make(original):
            @wraps(original)
            def guarded(slug, *args, **kwargs):
                if _blocked(slug):
                    if _wants_json():
                        return jsonify(ok=False, error=BLOCKED_MSG), 403
                    flash(BLOCKED_MSG, "error")
                    return redirect(url_for("public_menu", slug=slug))
                return original(slug, *args, **kwargs)
            return guarded
        app.view_functions[endpoint] = make(original)

    @app.context_processor
    def _orders_blocked_menu():
        # Menu en consultation : même affichage que « à emporter indisponible ».
        if request.endpoint != "public_menu":
            return {}
        try:
            slug = (request.view_args or {}).get("slug")
            if slug and _blocked(slug):
                return {"mf_takeaway": {"mode": "remote", "table": None, "state": "disabled"},
                        "mf_orders_blocked": True}
        except Exception:
            db.session.rollback()
        return {}

    # ------------------------------------------------------------------
    # Suppression définitive : nettoyage de TOUTES les tables liées
    # ------------------------------------------------------------------
    original_delete = app.view_functions.get("super_admin_restaurant_delete")
    if original_delete:
        def _purge_related(rid, user_ids):
            for table in reversed(db.metadata.sorted_tables):     # dépendants d'abord
                if table.name in _CORE_TABLES:
                    continue
                cols = table.c
                if "restaurant_id" in cols:
                    db.session.execute(table.delete().where(cols.restaurant_id == rid))
                elif "user_id" in cols and user_ids:
                    db.session.execute(table.delete().where(cols.user_id.in_(user_ids)))
                elif "order_id" in cols and "restaurant_id" not in cols:
                    # Tables rattachées aux commandes sans restaurant_id.
                    from models import Order
                    sub = db.session.query(Order.id).filter(Order.restaurant_id == rid)
                    db.session.execute(table.delete().where(cols.order_id.in_(sub.scalar_subquery())))

        @wraps(original_delete)
        def super_admin_restaurant_delete(restaurant_id):
            r = db.session.get(Restaurant, restaurant_id)
            meta = db.session.get(RestaurantAdminMeta, restaurant_id) if r else None
            if (r is not None and r.slug != PLATFORM_SLUG and meta and meta.archived_at
                    and (request.form.get("confirm_slug") or "").strip() == r.slug):
                users = User.query.filter_by(restaurant_id=r.id).all()
                user_ids = [u.id for u in users]
                other_access = RestaurantAccess.query.filter(
                    RestaurantAccess.user_id.in_(user_ids or [0]),
                    RestaurantAccess.restaurant_id != r.id).count()
                if not any(u.is_super_admin for u in users) and not other_access:
                    try:
                        # Pas de commit ici : super_admin.py valide tout en une fois,
                        # ou annule tout si la suppression échoue.
                        _purge_related(r.id, user_ids)
                    except Exception:
                        db.session.rollback()
                        log.exception("Nettoyage avant suppression impossible (restaurant %s)", r.id)
                        flash("La suppression a échoué, aucune donnée n'a été modifiée.", "error")
                        return redirect(url_for("super_admin_restaurant_detail", restaurant_id=r.id))
            return original_delete(restaurant_id)
        app.view_functions["super_admin_restaurant_delete"] = super_admin_restaurant_delete
