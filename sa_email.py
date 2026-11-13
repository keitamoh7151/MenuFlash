"""
MenuFlash Mini — Changement de l'adresse email d'un compte super-admin.

Branchement dans app.py, JUSTE APRÈS init_two_factor(...) :

    from sa_email import init_sa_email
    init_sa_email(app, log_audit=log_audit, limiter=limiter)

Parcours
--------
1. Super-admin > menu Compte > « Adresse email ».
2. Il saisit la nouvelle adresse, son mot de passe ET un code de l'application 2FA.
3. Un lien de confirmation (valable 1 heure) est envoyé à la NOUVELLE adresse :
   on prouve qu'on la possède. Rien ne change tant que le lien n'est pas confirmé.
4. L'ancienne adresse reçoit une alerte (« si ce n'est pas vous… »).
5. À la confirmation (bouton, pas un simple clic sur le lien) : l'email est changé,
   toutes les sessions sont fermées, il faut se reconnecter avec la nouvelle adresse.
   La 2FA et les codes de secours restent valables (liés au compte, pas à l'email).

Sécurité
--------
- Réservé aux comptes super-admin (tous rôles : chacun gère SON adresse uniquement).
- Mot de passe + code 2FA (anti-rejeu) exigés ; 5 demandes par heure maximum.
- Lien signé, lié au tampon de sécurité : usage unique, invalide après un changement
  de mot de passe, et refusé si le compte a été désactivé entre-temps.
- L'unicité de l'adresse est revérifiée à la confirmation.
- Message identique si l'adresse est déjà prise (pas de divulgation des comptes).
- Tout est tracé dans le journal de sécurité.
- Si APP_BASE_URL est définie (ex. https://menuflash.app), elle sert à construire le
  lien envoyé par email (protection contre la falsification de l'en-tête Host).
"""
import logging
import os
from datetime import datetime

from flask import (Response, abort, flash, redirect, render_template, request,
                   session, url_for)
from flask_login import current_user, login_required, logout_user
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from mailer import send_transactional_email
from models import User, db
import security_forms as sf

log = logging.getLogger("menuflash.sa_email")

SALT = "sa-email-change"
TOKEN_MAX_AGE = 3600          # 1 heure


def _mask(email):
    """a***@domaine.com — pour les messages affichés et les journaux."""
    local, _, domain = (email or "").partition("@")
    return (local[:1] + "***@" + domain) if domain else "***"


def _external(endpoint, **values):
    base = os.environ.get("APP_BASE_URL", "").strip().rstrip("/")
    if base.startswith("https://") or base.startswith("http://"):
        return base + url_for(endpoint, **values)
    return url_for(endpoint, _external=True, **values)


def _no_store(resp):
    resp.headers["Cache-Control"] = "no-store"
    return resp


def init_sa_email(app, *, log_audit, limiter=None):
    serializer = URLSafeTimedSerializer(app.config["SECRET_KEY"])

    def audit(action, details=None, success=True, email=None):
        try:
            log_audit(action[:40], success=success, restaurant_id=None,
                      user_email=email or (current_user.email if current_user.is_authenticated else None),
                      details=(details or "")[:300])
        except Exception:
            db.session.rollback()
            log.exception("Journal d'audit indisponible")

    def is_disabled(user):
        try:
            from sa_roles import StaffMeta
            meta = db.session.get(StaffMeta, user.id)
            return bool(meta and meta.disabled_at)
        except Exception:
            return False

    def totp_ok(user, code):
        """Vérifie un code de l'application 2FA, avec anti-rejeu. True / False."""
        from two_factor import UserTwoFactor, decrypt_secret, match_step
        rec = db.session.get(UserTwoFactor, user.id)
        if rec is None or not rec.enabled:
            return False
        secret = decrypt_secret(app, rec.secret_enc)
        if not secret:
            return False
        step = match_step(secret, code, rec.last_used_step)
        if step is None:
            return False
        rec.last_used_step = step
        return True

    # ------------------------------------------------------------------ 1. demande
    @login_required
    def account_email():
        if not current_user.is_super_admin:
            abort(403)
        if request.method == "POST":
            new_email, err = sf.validate_email(request.form.get("new_email"))
            password = request.form.get("password", "")
            code = request.form.get("code", "")

            if err:
                flash(err, "error")
                return redirect(url_for("account_email"))
            if new_email == current_user.email.lower():
                flash("C'est déjà votre adresse actuelle.", "error")
                return redirect(url_for("account_email"))
            if len(password) > sf.PASSWORD_HARD_LIMIT or not current_user.check_password(password):
                audit("sa_email_change_denied", "mot de passe incorrect", success=False)
                flash("Mot de passe ou code de l'application incorrect.", "error")
                return redirect(url_for("account_email"))
            if not totp_ok(current_user, code):
                db.session.rollback()
                audit("sa_email_change_denied", "code 2FA incorrect", success=False)
                flash("Mot de passe ou code de l'application incorrect.", "error")
                return redirect(url_for("account_email"))
            db.session.commit()                                   # enregistre l'anti-rejeu du code

            taken = User.query.filter(db.func.lower(User.email) == new_email).first() is not None
            if taken:
                # Même message qu'en cas de succès : on ne révèle pas qu'un compte existe.
                audit("sa_email_change_taken", _mask(new_email), success=False)
            else:
                token = serializer.dumps({"uid": current_user.id, "stamp": current_user.security_stamp,
                                          "new": new_email}, salt=SALT)
                link = _external("account_email_confirm", token=token)
                send_transactional_email(
                    app, new_email, "Confirmez votre nouvelle adresse — MenuFlash",
                    "Bonjour,\n\nUne demande a été faite pour utiliser cette adresse comme identifiant "
                    "d'un compte d'administration MenuFlash.\n\nPour confirmer (lien valable 1 heure, "
                    f"usage unique) :\n{link}\n\nSi vous n'êtes pas à l'origine de cette demande, "
                    "ignorez ce message : rien ne sera modifié.\n\nL'équipe MenuFlash")
                send_transactional_email(
                    app, current_user.email, "Changement d'adresse demandé — MenuFlash",
                    "Bonjour,\n\nUn changement de l'adresse de connexion de votre compte "
                    f"d'administration MenuFlash vers {_mask(new_email)} a été demandé.\n\n"
                    "Si ce n'est pas vous : changez immédiatement votre mot de passe et "
                    "fermez les autres sessions (Compte > Appareils connectés).\n\nL'équipe MenuFlash")
                audit("sa_email_change_requested", f"vers {_mask(new_email)}")
            flash(f"Un lien de confirmation a été envoyé à {_mask(new_email)} (valable 1 heure). "
                  "Votre adresse ne change qu'après la confirmation.", "success")
            return redirect(url_for("account_email"))

        return _no_store(Response(render_template("sa_email.html", state="form",
                                                  current_email=current_user.email)))

    # ------------------------------------------------------------------ 2. confirmation
    def account_email_confirm(token):
        try:
            data = serializer.loads(token, salt=SALT, max_age=TOKEN_MAX_AGE)
        except (BadSignature, SignatureExpired):
            data = None
        user = db.session.get(User, data.get("uid")) if isinstance(data, dict) else None
        new_email = (data or {}).get("new", "") if isinstance(data, dict) else ""
        if (user is None or not user.is_super_admin or user.security_stamp != data.get("stamp")
                or is_disabled(user) or sf.validate_email(new_email)[1]):
            return _no_store(Response(render_template("sa_email.html", state="invalid"))), 400

        if request.method == "GET":                                # un antivirus qui ouvre le lien ne valide rien
            return _no_store(Response(render_template("sa_email.html", state="confirm",
                                                      new_email=new_email, token=token)))

        if User.query.filter(db.func.lower(User.email) == new_email, User.id != user.id).first():
            audit("sa_email_change_taken", _mask(new_email), success=False, email=user.email)
            return _no_store(Response(render_template("sa_email.html", state="invalid"))), 400

        old_email = user.email
        user.email = new_email
        user.email_verified, user.email_verified_at = True, datetime.utcnow()
        user.rotate_security_stamp()                              # lien à usage unique + toutes les sessions fermées
        db.session.commit()
        audit("sa_email_changed", f"{_mask(old_email)} -> {_mask(new_email)}", email=new_email)
        send_transactional_email(
            app, old_email, "Adresse de connexion modifiée — MenuFlash",
            "Bonjour,\n\nL'adresse de connexion de votre compte d'administration MenuFlash a été "
            f"remplacée par {_mask(new_email)}.\n\nSi ce n'est pas vous, contactez immédiatement "
            "un autre administrateur MenuFlash.\n\nL'équipe MenuFlash")

        if current_user.is_authenticated:
            logout_user()
        session.clear()
        flash("Adresse modifiée. Reconnectez-vous avec votre nouvelle adresse.", "success")
        return redirect(url_for("admin_login"))

    # ------------------------------------------------------------------ routes
    request_view, confirm_view = account_email, account_email_confirm
    if limiter is not None:
        request_view = limiter.limit("5 per hour", methods=["POST"])(account_email)
        confirm_view = limiter.limit("10 per hour", methods=["POST"])(account_email_confirm)
    app.add_url_rule("/compte/adresse-email", "account_email", request_view, methods=["GET", "POST"])
    app.add_url_rule("/compte/adresse-email/confirmer/<token>", "account_email_confirm",
                     confirm_view, methods=["GET", "POST"])

    # La page de confirmation doit rester accessible avant la validation du code 2FA
    # (le lien peut être ouvert sur un autre appareil).
    try:
        import two_factor
        two_factor._OPEN_ENDPOINTS.add("account_email_confirm")
    except Exception:
        log.exception("Impossible d'ouvrir la page de confirmation avant la 2FA")
