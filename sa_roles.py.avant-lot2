"""
MenuFlash Mini — Plusieurs comptes super-admin, avec des rôles.

Branché par sa_suite.py (voir sa_suite.init_sa_suite).

Rôles
-----
  admin     Administrateur : accès complet (c'est le rôle de tous les super-admins
            existants, sans rien à faire).
  support   Support : voit les clients, la santé d'activité et la VUE SUPPORT d'un
            restaurant (lecture seule) ; peut ajouter des notes, débloquer des
            comptes, envoyer un lien de réinitialisation et fermer les sessions.
            Ne voit pas les finances, les sauvegardes, le journal de sécurité…
  finance   Finances en lecture seule : tableau de bord, paiements (avec export
            et reçus), relances, liste et fiche des clients. Ne modifie rien.

Principe de sécurité : REFUS PAR DÉFAUT. Toute adresse du panel (nom de route
commençant par « super_admin ») doit figurer dans la liste du rôle pour être
accessible à un rôle autre qu'administrateur. Une page ajoutée plus tard, ou
oubliée, reste donc réservée aux administrateurs.

- Les nouveaux comptes sont créés par invitation : la personne choisit son mot de
  passe (lien valable 72 h, à usage unique), puis la double authentification (2FA)
  est exigée dès la première connexion, comme pour tout super-admin.
- Créer, changer de rôle, désactiver ou réactiver un compte demande le mot de
  passe de l'administrateur. On ne peut pas modifier son propre compte, ni retirer
  le dernier administrateur actif.
- Un compte désactivé est déconnecté immédiatement ; son historique est conservé.
- Toutes les actions sont tracées dans le journal de sécurité.

Nouvelle table « staff_roles » (créée automatiquement). Un super-admin sans ligne
dans cette table est administrateur.
"""
import logging
import secrets
from datetime import datetime

from flask import (abort, flash, redirect, render_template, request, session, url_for)
from flask_login import current_user, login_user, logout_user
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from models import Restaurant, User, db
from super_admin import PLATFORM_SLUG

log = logging.getLogger("menuflash.sa_roles")

INVITE_MAX_AGE = 72 * 3600

ROLES = {
    "admin": ("Administrateur", "Accès complet au panel.", "mf-sa-badge-ok"),
    "support": ("Support", "Clients, santé d'activité et vue support en lecture seule ; notes, déblocage, "
                           "lien de réinitialisation, fermeture des sessions.", "mf-sa-badge-info"),
    "finance": ("Finances (lecture seule)", "Tableau de bord, paiements, reçus, relances et fiches clients, "
                                            "sans aucune modification.", "mf-sa-badge-warning"),
}

READ = "R"        # GET / HEAD seulement
WRITE = "W"       # toutes méthodes

RULES = {
    "support": {
        "super_admin_clients": READ,
        "super_admin_restaurant_detail": READ,
        "super_admin_clients_health": READ,
        "super_admin_clients_health_export": READ,
        "super_admin_support_view": READ,
        "super_admin_add_note": WRITE,
        "super_admin_delete_note": WRITE,
        "super_admin_restaurant_action": (WRITE, {"unlock", "send_reset", "force_logout"}),
    },
    "finance": {
        "super_admin_dashboard": READ,
        "super_admin_clients": READ,
        "super_admin_restaurant_detail": READ,
        "super_admin_finances": READ,
        "super_admin_finances_export": READ,
        "super_admin_payment_receipt": READ,
        "super_admin_reminders": READ,
    },
}
LANDING = {"support": "super_admin_clients_health", "finance": "super_admin_dashboard"}


class StaffRole(db.Model):
    __tablename__ = "staff_roles"
    user_id = db.Column(db.Integer, primary_key=True)
    role = db.Column(db.String(20), nullable=False, default="admin")
    created_by = db.Column(db.String(120))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


# ==========================================================================
# Règles d'accès (fonctions pures, testées)
# ==========================================================================
def is_allowed(role, endpoint, method, action=None):
    """Un rôle peut-il utiliser cette adresse ? `admin` : toujours ; inconnu : jamais."""
    if role == "admin":
        return True
    rule = RULES.get(role, {}).get(endpoint)
    if rule is None:
        return False
    allowed_actions = None
    if isinstance(rule, tuple):
        rule, allowed_actions = rule
    if rule == READ:
        return method in ("GET", "HEAD", "OPTIONS")
    if allowed_actions is not None and method not in ("GET", "HEAD", "OPTIONS"):
        return action in allowed_actions
    return True


def role_of(user):
    """Rôle d'un compte super-admin ('admin' par défaut), None pour les autres comptes."""
    if user is None or not getattr(user, "is_authenticated", False) or not getattr(user, "is_super_admin", False):
        return None
    try:
        row = db.session.get(StaffRole, user.id)
    except Exception:
        db.session.rollback()
        return "none"
    if row is None:
        return "admin"
    return row.role if row.role in ROLES else "none"


def _role_of_id(user):
    row = db.session.get(StaffRole, user.id)
    return row.role if row and row.role in ROLES else ("admin" if row is None else "none")


def init_sa_roles(app, *, super_admin_required, log_audit, limiter=None):
    import security_forms as sf
    from mailer import send_transactional_email
    from security_plus import StaffMeta, UserSession
    from two_factor import UserTwoFactor

    with app.app_context():
        try:
            StaffRole.__table__.create(db.engine, checkfirst=True)
        except Exception:
            log.exception("Création de la table staff_roles impossible")

    serializer = URLSafeTimedSerializer(app.config["SECRET_KEY"])

    def audit(action, details=None, success=True, email=None):
        log_audit(action[:40], success=success, restaurant_id=None,
                  user_email=email or (current_user.email if current_user.is_authenticated else None),
                  details=(details or "")[:300])

    # ------------------------------------------------------------------ garde par rôle
    @app.before_request
    def _sa_role_gate():
        if not current_user.is_authenticated or not getattr(current_user, "is_super_admin", False):
            return None
        endpoint = request.endpoint or ""
        if not endpoint.startswith("super_admin"):
            return None
        role = role_of(current_user)
        if role == "admin":
            return None
        # On ne lit le formulaire que si la règle en a besoin (jamais pour un envoi de fichier volumineux).
        action = (request.form.get("action") if endpoint == "super_admin_restaurant_action"
                  and request.method == "POST" else None)
        if is_allowed(role, endpoint, request.method, action):
            return None
        landing = LANDING.get(role)
        if endpoint == "super_admin_dashboard" and landing and landing != endpoint:
            return redirect(url_for(landing))
        audit("sa_role_denied", f"{role} -> {endpoint} {request.method}", success=False)
        return render_template("sa_forbidden.html", role=role,
                               role_label=ROLES.get(role, ("Aucun rôle",))[0],
                               landing=landing), 403

    @app.context_processor
    def _sa_role_context():
        if not current_user.is_authenticated or not getattr(current_user, "is_super_admin", False):
            return {}
        role = role_of(current_user)

        def sa_can(endpoint, action=None):
            return is_allowed(role, endpoint, "POST" if action else "GET", action)

        return {"sa_role": role, "sa_admin": role == "admin", "sa_can": sa_can,
                "sa_role_label": ROLES.get(role, ("Aucun rôle",))[0]}

    # ------------------------------------------------------------------ utilitaires
    def platform_restaurant_id():
        r = Restaurant.query.filter_by(slug=PLATFORM_SLUG).first()
        return r.id if r else current_user.restaurant_id

    def active_admins():
        out = []
        for u in User.query.filter_by(is_super_admin=True).all():
            meta = db.session.get(StaffMeta, u.id)
            if _role_of_id(u) == "admin" and not (meta and meta.disabled_at):
                out.append(u)
        return out

    def password_ok():
        pwd = request.form.get("confirm_password", "")
        if pwd and len(pwd) <= 256 and current_user.check_password(pwd):
            return True
        audit("sa_admin_refused", "mot de passe de confirmation incorrect", success=False)
        flash("Mot de passe incorrect : cette action demande de confirmer votre mot de passe.", "error")
        return False

    def target_or_404(user_id):
        u = db.session.get(User, user_id)
        if u is None or not u.is_super_admin:
            abort(404)
        return u

    def back():
        return redirect(url_for("super_admin_admins"))

    def send_invite(user, role):
        token = serializer.dumps({"uid": user.id, "stamp": user.security_stamp}, salt="sa-invite")
        link = url_for("sa_invite_accept", token=token, _external=True)
        send_transactional_email(
            app, user.email, "Invitation à l'équipe d'administration MenuFlash",
            f"Bonjour,\n\nVous êtes invité(e) à rejoindre l'équipe d'administration de MenuFlash "
            f"avec le rôle « {ROLES[role][0]} ».\n\nChoisissez votre mot de passe ici (lien valable 72 heures, "
            f"à usage unique) :\n{link}\n\nUne double authentification (application sur votre téléphone) vous "
            f"sera demandée dès la première connexion.\n\nSi vous n'attendiez pas cette invitation, ignorez ce "
            f"message.\n\nL'équipe MenuFlash")
        session["mf_last_sa_invite"] = {"email": user.email, "link": link}
        return link

    # ------------------------------------------------------------------ liste
    @app.route("/super-admin/administrateurs")
    @super_admin_required
    def super_admin_admins():
        rows = []
        for u in User.query.filter_by(is_super_admin=True).order_by(User.created_at.asc()).all():
            meta = db.session.get(StaffMeta, u.id)
            tf = db.session.get(UserTwoFactor, u.id)
            if meta and meta.disabled_at:
                state = "disabled"
            elif meta and meta.invited_at and not meta.accepted_at and not u.last_login_at:
                state = "pending"
            else:
                state = "active"
            rows.append({"user": u, "role": _role_of_id(u), "state": state, "meta": meta,
                         "two_factor": bool(tf and tf.enabled), "me": u.id == current_user.id})
        return render_template("sa_admins.html", rows=rows, roles=ROLES,
                               last_invite=session.pop("mf_last_sa_invite", None),
                               admins_left=len(active_admins()))

    # ------------------------------------------------------------------ inviter
    @app.route("/super-admin/administrateurs/inviter", methods=["POST"])
    @super_admin_required
    def super_admin_admin_invite():
        if not password_ok():
            return back()
        email, err = sf.validate_email(request.form.get("email"))
        role = request.form.get("role", "")
        name = " ".join((request.form.get("name") or "").split())[:80] or None
        if err:
            flash(err, "error")
            return back()
        if role not in ROLES:
            flash("Choisissez un rôle.", "error")
            return back()
        if User.query.filter_by(email=email).first():
            flash("Cet email est déjà utilisé par un autre compte.", "error")
            return back()
        user = User(restaurant_id=platform_restaurant_id(), email=email, role="owner",
                    is_super_admin=True, email_verified=True, email_verified_at=datetime.utcnow())
        user.set_password(secrets.token_urlsafe(32))               # inutilisable : remplacé via l'invitation
        db.session.add(user)
        db.session.flush()
        db.session.add(StaffRole(user_id=user.id, role=role, created_by=current_user.email))
        meta = StaffMeta(user_id=user.id, display_name=name, invited_at=datetime.utcnow(),
                         invited_by=current_user.email)
        db.session.add(meta)
        db.session.commit()
        send_invite(user, role)
        audit("sa_admin_invited", f"{email} — rôle {role}")
        flash(f"Invitation envoyée à {email}. Le lien est aussi affiché ci-dessous si l'email n'arrive pas.", "success")
        return back()

    @app.route("/super-admin/administrateurs/<int:user_id>/inviter-encore", methods=["POST"])
    @super_admin_required
    def super_admin_admin_resend(user_id):
        u = target_or_404(user_id)
        meta = db.session.get(StaffMeta, u.id)
        if not (meta and meta.invited_at and not meta.accepted_at and not u.last_login_at) or meta.disabled_at:
            flash("Ce compte n'a pas d'invitation en attente.", "error")
            return back()
        u.rotate_security_stamp()                                    # l'ancien lien ne fonctionne plus
        meta.invited_at = datetime.utcnow()
        db.session.commit()
        send_invite(u, _role_of_id(u))
        audit("sa_admin_invite_resent", u.email)
        flash(f"Nouvelle invitation envoyée à {u.email}.", "success")
        return back()

    # ------------------------------------------------------------------ rôle
    @app.route("/super-admin/administrateurs/<int:user_id>/role", methods=["POST"])
    @super_admin_required
    def super_admin_admin_role(user_id):
        u = target_or_404(user_id)
        role = request.form.get("role", "")
        if u.id == current_user.id:
            flash("Vous ne pouvez pas modifier votre propre rôle.", "error")
            return back()
        if role not in ROLES:
            flash("Rôle invalide.", "error")
            return back()
        if not password_ok():
            return back()
        before = _role_of_id(u)
        if before == "admin" and role != "admin" and len(active_admins()) <= 1:
            flash("Impossible : il doit rester au moins un administrateur actif.", "error")
            return back()
        row = db.session.get(StaffRole, u.id)
        if row is None:
            row = StaffRole(user_id=u.id, created_by=current_user.email)
            db.session.add(row)
        row.role = role
        db.session.commit()
        audit("sa_admin_role", f"{u.email} : {before} -> {role}")
        flash(f"Rôle de {u.email} : {ROLES[role][0]}. Effet immédiat.", "success")
        return back()

    # ------------------------------------------------------------------ désactiver / réactiver
    @app.route("/super-admin/administrateurs/<int:user_id>/desactiver", methods=["POST"])
    @super_admin_required
    def super_admin_admin_disable(user_id):
        u = target_or_404(user_id)
        if u.id == current_user.id:
            flash("Vous ne pouvez pas désactiver votre propre compte.", "error")
            return back()
        if not password_ok():
            return back()
        if _role_of_id(u) == "admin" and len(active_admins()) <= 1:
            flash("Impossible : il doit rester au moins un administrateur actif.", "error")
            return back()
        meta = db.session.get(StaffMeta, u.id)
        if meta is None:
            meta = StaffMeta(user_id=u.id)
            db.session.add(meta)
        now = datetime.utcnow()
        meta.disabled_at, meta.disabled_by = now, current_user.email
        u.rotate_security_stamp()                                    # ferme immédiatement ses sessions
        for rec in UserSession.query.filter_by(user_id=u.id).filter(UserSession.revoked_at.is_(None)).all():
            rec.revoked_at, rec.revoked_reason = now, "compte désactivé"
        db.session.commit()
        audit("sa_admin_disabled", u.email)
        flash(f"Compte {u.email} désactivé : déconnecté immédiatement, connexion impossible.", "success")
        return back()

    @app.route("/super-admin/administrateurs/<int:user_id>/reactiver", methods=["POST"])
    @super_admin_required
    def super_admin_admin_enable(user_id):
        u = target_or_404(user_id)
        if not password_ok():
            return back()
        meta = db.session.get(StaffMeta, u.id)
        if meta:
            meta.disabled_at = meta.disabled_by = None
        db.session.commit()
        audit("sa_admin_enabled", u.email)
        flash(f"Compte {u.email} réactivé.", "success")
        return back()

    # ------------------------------------------------------------------ acceptation de l'invitation
    def sa_invite_accept(token):
        try:
            data = serializer.loads(token, salt="sa-invite", max_age=INVITE_MAX_AGE)
        except (BadSignature, SignatureExpired):
            data = None
        user = db.session.get(User, data.get("uid")) if data else None
        meta = db.session.get(StaffMeta, user.id) if user else None
        if (not user or not user.is_super_admin or user.security_stamp != data.get("stamp")
                or (meta and meta.disabled_at)):
            return render_template("sa_invite.html", state="invalid"), 400
        role = _role_of_id(user)
        if request.method == "POST":
            password = request.form.get("password", "")
            errors = sf.password_errors(password, email=user.email)
            if password != request.form.get("password_confirm", ""):
                errors.append("Les deux mots de passe ne correspondent pas.")
            if errors:
                for e in errors:
                    flash(e, "error")
                return redirect(url_for("sa_invite_accept", token=token))
            user.set_password(password)                              # change le tampon : lien à usage unique
            user.email_verified, user.email_verified_at = True, datetime.utcnow()
            if meta:
                meta.accepted_at = datetime.utcnow()
            db.session.commit()
            log_audit("sa_admin_accepted", success=True, restaurant_id=None, user_email=user.email,
                      details=f"rôle {role}")
            if current_user.is_authenticated:
                logout_user()
            login_user(user)
            session.pop("active_restaurant_id", None)
            flash("Bienvenue ! Configurez maintenant la double authentification.", "success")
            return redirect(url_for("super_admin_dashboard"))
        return render_template("sa_invite.html", state="form", email=user.email, role_label=ROLES[role][0],
                               name=(meta.display_name if meta else None), token=token)

    view = sa_invite_accept
    if limiter is not None:
        view = limiter.limit("10 per hour", methods=["POST"])(sa_invite_accept)
    app.add_url_rule("/invitation-admin/<token>", "sa_invite_accept", view, methods=["GET", "POST"])
