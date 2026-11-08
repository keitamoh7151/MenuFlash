"""
MenuFlash Mini — Double authentification (2FA) du super-admin.

Branchement dans app.py, JUSTE APRÈS l'appel à init_super_admin(...) :

    from two_factor import init_two_factor
    init_two_factor(app, log_audit=log_audit, limiter=limiter)

Fonctionnement
--------------
- Obligatoire pour tout compte super-admin. Au premier accès, le panel est
  bloqué tant que la 2FA n'est pas activée (QR code à scanner avec Google
  Authenticator, Microsoft Authenticator, Authy…).
- Ensuite, après chaque connexion, un code à 6 chiffres est demandé avant
  d'accéder au panel. Le mot de passe seul ne suffit plus.
- 10 codes de secours à usage unique sont fournis à l'activation (en cas de
  perte du téléphone). Ils sont stockés hachés : ils ne sont visibles qu'une
  seule fois.
- Codes TOTP standard (RFC 6238, 30 s, 6 chiffres), tolérance de ±30 s pour
  le décalage d'horloge, et un même code ne peut jamais servir deux fois.
- 5 codes faux de suite : déconnexion et verrouillage du compte 15 minutes.
- La clé secrète TOTP est chiffrée en base (dérivée de FLASK_SECRET_KEY).
- Un changement ou une réinitialisation de mot de passe redemande le code.
- Toutes les actions sont tracées dans le journal de sécurité.

Secours ultime (téléphone ET codes de secours perdus)
-----------------------------------------------------
Dans Render > Environment, ajouter temporairement :
    TWO_FACTOR_EMERGENCY_RESET = admin@menuflash.com
Se connecter : la 2FA de ce compte est supprimée (tracé dans le journal) et
une nouvelle activation est demandée. SUPPRIMER la variable juste après.
"""
import base64
import hashlib
import hmac
import io
import logging
import os
import secrets
import struct
import time
from datetime import datetime, timedelta
from urllib.parse import quote

from flask import (render_template, redirect, url_for, request, flash, session,
                   Response)
from flask_login import current_user, logout_user
from werkzeug.security import generate_password_hash, check_password_hash

from models import db, User, LOCKOUT_DURATION_MINUTES

logger = logging.getLogger("menuflash.2fa")

ISSUER = "MenuFlash Mini"
PERIOD = 30
DIGITS = 6
WINDOW = 1                      # ±1 période (±30 s) de décalage d'horloge
MAX_FAILURES = 5
RECOVERY_CODES_COUNT = 10
SESSION_KEY = "mf_2fa_ok"
FAIL_KEY = "mf_2fa_fail"
NEXT_KEY = "mf_2fa_next"

# Pages accessibles avant la validation du code.
_OPEN_ENDPOINTS = {
    "static", "media", "admin_login", "admin_logout", "backup_cron",
    "two_factor_setup", "two_factor_verify",
}


# ==========================================================================
# Modèle
# ==========================================================================
class UserTwoFactor(db.Model):
    __tablename__ = "user_two_factor"
    user_id = db.Column(db.Integer, primary_key=True)
    secret_enc = db.Column(db.String(400), nullable=False)
    enabled = db.Column(db.Boolean, nullable=False, default=False)
    enabled_at = db.Column(db.DateTime)
    last_used_step = db.Column(db.Integer, nullable=False, default=0)
    recovery_hashes = db.Column(db.Text, nullable=False, default="")   # séparés par \n
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    @property
    def recovery_left(self):
        return len([h for h in (self.recovery_hashes or "").split("\n") if h])


# ==========================================================================
# TOTP (RFC 6238) — sans dépendance externe
# ==========================================================================
def new_secret():
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def _b32decode(secret):
    pad = "=" * (-len(secret) % 8)
    return base64.b32decode(secret.upper() + pad)


def totp_at(secret, step):
    digest = hmac.new(_b32decode(secret), struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(value % (10 ** DIGITS)).zfill(DIGITS)


def current_step(now=None):
    return int((now if now is not None else time.time()) // PERIOD)


def match_step(secret, code, last_used_step=0, now=None):
    """Renvoie le pas de temps correspondant au code, ou None (code faux ou déjà utilisé)."""
    code = "".join(ch for ch in (code or "") if ch.isdigit())
    if len(code) != DIGITS:
        return None
    base = current_step(now)
    for delta in range(-WINDOW, WINDOW + 1):
        step = base + delta
        if step > (last_used_step or 0) and hmac.compare_digest(totp_at(secret, step), code):
            return step
    return None


def provisioning_uri(secret, email):
    label = quote(f"{ISSUER}:{email}")
    return (f"otpauth://totp/{label}?secret={secret}&issuer={quote(ISSUER)}"
            f"&algorithm=SHA1&digits={DIGITS}&period={PERIOD}")


def qr_data_uri(text):
    import qrcode
    img = qrcode.make(text, box_size=6, border=2)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


# ==========================================================================
# Chiffrement de la clé secrète et codes de secours
# ==========================================================================
def _fernet(app):
    from cryptography.fernet import Fernet
    raw = hashlib.sha256(b"menuflash-2fa:" + app.config["SECRET_KEY"].encode()).digest()
    return Fernet(base64.urlsafe_b64encode(raw))


def encrypt_secret(app, secret):
    return _fernet(app).encrypt(secret.encode()).decode("ascii")


def decrypt_secret(app, token):
    from cryptography.fernet import InvalidToken
    try:
        return _fernet(app).decrypt(token.encode()).decode("ascii")
    except (InvalidToken, ValueError):
        return None


def new_recovery_codes():
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"     # sans 0/O ni 1/I
    codes = []
    for _ in range(RECOVERY_CODES_COUNT):
        raw = "".join(secrets.choice(alphabet) for _ in range(8))
        codes.append(f"{raw[:4]}-{raw[4:]}")
    return codes


def _normalize_recovery(code):
    return "".join(ch for ch in (code or "").upper() if ch.isalnum())


def hash_recovery_codes(codes):
    return "\n".join(generate_password_hash(_normalize_recovery(c)) for c in codes)


def consume_recovery_code(record, code):
    """Vérifie un code de secours et le supprime s'il est valide (usage unique)."""
    norm = _normalize_recovery(code)
    if len(norm) != 8:
        return False
    hashes = [h for h in (record.recovery_hashes or "").split("\n") if h]
    for h in hashes:
        if check_password_hash(h, norm):
            hashes.remove(h)
            record.recovery_hashes = "\n".join(hashes)
            return True
    return False


# ==========================================================================
# Session
# ==========================================================================
def _session_token(user):
    return f"{user.id}:{user.security_stamp}"


def is_verified():
    return current_user.is_authenticated and session.get(SESSION_KEY) == _session_token(current_user)


def mark_verified(user):
    session[SESSION_KEY] = _session_token(user)
    session.pop(FAIL_KEY, None)


# ==========================================================================
# Branchement
# ==========================================================================
def init_two_factor(app, *, log_audit, limiter=None):

    with app.app_context():
        try:
            UserTwoFactor.__table__.create(db.engine, checkfirst=True)
        except Exception:
            logger.exception("Création de la table user_two_factor impossible")

    def audit(action, success=True, details=None, user=None):
        user = user or (current_user if current_user.is_authenticated else None)
        log_audit(action, success=success,
                  restaurant_id=getattr(user, "restaurant_id", None),
                  user_email=getattr(user, "email", None), details=details)

    def _record(user):
        return UserTwoFactor.query.get(user.id)

    def _no_store(resp):
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        resp.headers["Pragma"] = "no-cache"
        return resp

    def _after_verified():
        target = session.pop(NEXT_KEY, None)
        if target and target.startswith("/") and not target.startswith("//"):
            return redirect(target)
        return redirect(url_for("super_admin_dashboard"))

    # ----------------------------------------------------------------------
    # Porte d'entrée : tout le panel est bloqué tant que le code n'est pas validé
    # ----------------------------------------------------------------------
    @app.before_request
    def _two_factor_gate():
        if not current_user.is_authenticated or not getattr(current_user, "is_super_admin", False):
            return None
        if (request.endpoint or "") in _OPEN_ENDPOINTS:
            return None

        emergency = os.environ.get("TWO_FACTOR_EMERGENCY_RESET", "").strip().lower()
        if emergency and emergency == current_user.email.lower():
            rec = _record(current_user)
            if rec:
                db.session.delete(rec)
                db.session.commit()
                audit("2fa_emergency_reset", details="2FA supprimée via TWO_FACTOR_EMERGENCY_RESET")
                logger.warning("2FA réinitialisée en urgence pour %s : retirez la variable "
                               "TWO_FACTOR_EMERGENCY_RESET de Render.", current_user.email)
            session.pop(SESSION_KEY, None)

        if is_verified():
            return None
        if request.method == "GET":
            session[NEXT_KEY] = request.full_path.rstrip("?")
        rec = _record(current_user)
        if rec is None or not rec.enabled:
            return redirect(url_for("two_factor_setup"))
        return redirect(url_for("two_factor_verify"))

    # ----------------------------------------------------------------------
    # Activation
    # ----------------------------------------------------------------------
    def two_factor_setup():
        if not current_user.is_authenticated:
            return redirect(url_for("admin_login"))
        if not current_user.is_super_admin:
            return redirect(url_for("admin_dashboard"))
        rec = _record(current_user)
        if rec and rec.enabled and is_verified() and request.args.get("reconfigurer") != "1":
            return redirect(url_for("two_factor_manage"))
        if rec and rec.enabled and not is_verified():
            return redirect(url_for("two_factor_verify"))

        # Clé en attente : créée une fois, réutilisée tant que l'activation n'est pas confirmée.
        if rec is None:
            rec = UserTwoFactor(user_id=current_user.id, secret_enc=encrypt_secret(app, new_secret()),
                                enabled=False)
            db.session.add(rec)
            db.session.commit()
        pending = rec
        if rec.enabled:
            # Reconfiguration (changement de téléphone) : clé provisoire en session,
            # l'ancienne reste valide tant que la nouvelle n'est pas confirmée.
            if "mf_2fa_pending" not in session:
                session["mf_2fa_pending"] = encrypt_secret(app, new_secret())
            secret = decrypt_secret(app, session["mf_2fa_pending"])
        else:
            secret = decrypt_secret(app, pending.secret_enc)
            if secret is None:
                pending.secret_enc = encrypt_secret(app, new_secret())
                db.session.commit()
                secret = decrypt_secret(app, pending.secret_enc)

        if request.method == "POST":
            step = match_step(secret, request.form.get("code"))
            if step is None:
                audit("2fa_setup_failed", success=False, details="code d'activation incorrect")
                flash("Code incorrect. Vérifiez l'heure de votre téléphone et saisissez le code affiché.", "error")
                return redirect(url_for("two_factor_setup", **request.args))
            codes = new_recovery_codes()
            if rec.enabled:
                rec.secret_enc = session.pop("mf_2fa_pending")
            rec.enabled = True
            rec.enabled_at = datetime.utcnow()
            rec.last_used_step = step
            rec.recovery_hashes = hash_recovery_codes(codes)
            db.session.commit()
            mark_verified(current_user)
            audit("2fa_enabled", details="double authentification activée")
            return _no_store(Response(render_template("two_factor_recovery_codes.html", codes=codes,
                                                      email=current_user.email, first_time=True)))

        grouped = " ".join(secret[i:i + 4] for i in range(0, len(secret), 4))
        return _no_store(Response(render_template(
            "two_factor_setup.html", qr=qr_data_uri(provisioning_uri(secret, current_user.email)),
            secret=grouped, reconfigure=rec.enabled)))

    # ----------------------------------------------------------------------
    # Vérification après connexion
    # ----------------------------------------------------------------------
    def two_factor_verify():
        if not current_user.is_authenticated:
            return redirect(url_for("admin_login"))
        if not current_user.is_super_admin:
            return redirect(url_for("admin_dashboard"))
        rec = _record(current_user)
        if rec is None or not rec.enabled:
            return redirect(url_for("two_factor_setup"))
        if is_verified():
            return _after_verified()

        if request.method == "POST":
            mode = request.form.get("mode", "code")
            user = current_user._get_current_object()
            ok = False
            if mode == "recovery":
                if consume_recovery_code(rec, request.form.get("recovery_code")):
                    db.session.commit()
                    ok = True
                    audit("2fa_recovery_used", details=f"code de secours utilisé, {rec.recovery_left} restant(s)")
                    flash(f"Code de secours accepté. Il vous en reste {rec.recovery_left}. "
                          f"Pensez à en générer de nouveaux.", "success" if rec.recovery_left > 2 else "error")
            else:
                secret = decrypt_secret(app, rec.secret_enc)
                if secret is None:
                    flash("Impossible de lire votre clé 2FA (la clé FLASK_SECRET_KEY a changé). "
                          "Utilisez un code de secours.", "error")
                    return redirect(url_for("two_factor_verify", mode="recovery"))
                step = match_step(secret, request.form.get("code"), rec.last_used_step)
                if step is not None:
                    rec.last_used_step = step
                    db.session.commit()
                    ok = True

            if ok:
                mark_verified(user)
                audit("2fa_verified", details="mode " + mode)
                return _after_verified()

            fails = session.get(FAIL_KEY, 0) + 1
            session[FAIL_KEY] = fails
            audit("2fa_failed", success=False, details=f"tentative {fails}/{MAX_FAILURES} ({mode})")
            if fails >= MAX_FAILURES:
                user.locked_until = datetime.utcnow() + timedelta(minutes=LOCKOUT_DURATION_MINUTES)
                db.session.commit()
                audit("2fa_locked", success=False,
                      details=f"compte verrouillé {LOCKOUT_DURATION_MINUTES} min après {fails} codes faux")
                logout_user()
                session.clear()
                flash(f"Trop de codes incorrects : compte verrouillé {LOCKOUT_DURATION_MINUTES} minutes.", "error")
                return redirect(url_for("admin_login"))
            flash(f"Code incorrect ou déjà utilisé ({MAX_FAILURES - fails} essai(s) restant(s)).", "error")
            return redirect(url_for("two_factor_verify", mode=mode))

        return _no_store(Response(render_template(
            "two_factor_verify.html", mode=request.args.get("mode", "code"),
            recovery_left=rec.recovery_left)))

    # ----------------------------------------------------------------------
    # Gestion (panel) : statut, nouveaux codes de secours, changement de téléphone
    # ----------------------------------------------------------------------
    def two_factor_manage():
        rec = _record(current_user)
        return _no_store(Response(render_template("super_admin_two_factor.html", rec=rec)))

    def two_factor_regenerate():
        rec = _record(current_user)
        secret = decrypt_secret(app, rec.secret_enc) if rec else None
        step = match_step(secret, request.form.get("code"), rec.last_used_step) if secret else None
        if step is None:
            audit("2fa_regen_refused", success=False, details="code incorrect")
            flash("Code incorrect : saisissez le code actuel de votre application.", "error")
            return redirect(url_for("two_factor_manage"))
        codes = new_recovery_codes()
        rec.last_used_step = step
        rec.recovery_hashes = hash_recovery_codes(codes)
        db.session.commit()
        audit("2fa_codes_regenerated", details="nouveaux codes de secours, les anciens sont invalidés")
        return _no_store(Response(render_template("two_factor_recovery_codes.html", codes=codes,
                                                  email=current_user.email, first_time=False)))

    def two_factor_reconfigure():
        rec = _record(current_user)
        secret = decrypt_secret(app, rec.secret_enc) if rec else None
        step = match_step(secret, request.form.get("code"), rec.last_used_step) if secret else None
        if step is None or not current_user.check_password(request.form.get("password", "")):
            audit("2fa_reconfig_refused", success=False, details="code ou mot de passe incorrect")
            flash("Code ou mot de passe incorrect.", "error")
            return redirect(url_for("two_factor_manage"))
        rec.last_used_step = step
        db.session.commit()
        session.pop("mf_2fa_pending", None)
        audit("2fa_reconfig_started", details="changement de téléphone demandé")
        return redirect(url_for("two_factor_setup", reconfigurer=1))

    # ----------------------------------------------------------------------
    # Enregistrement des routes (+ limitation de débit)
    # ----------------------------------------------------------------------
    def _limited(view, rule):
        return limiter.limit(rule, methods=["POST"])(view) if limiter is not None else view

    app.add_url_rule("/admin/2fa/activer", "two_factor_setup",
                     _limited(two_factor_setup, "10 per 15 minutes"), methods=["GET", "POST"])
    app.add_url_rule("/admin/2fa/verifier", "two_factor_verify",
                     _limited(two_factor_verify, "15 per 15 minutes"), methods=["GET", "POST"])

    from functools import wraps
    from flask import abort
    from flask_login import login_required

    def _super_only(view):
        @wraps(view)
        @login_required
        def wrapper(*args, **kwargs):
            if not current_user.is_super_admin:
                abort(403)
            return view(*args, **kwargs)
        return wrapper

    app.add_url_rule("/super-admin/double-authentification", "two_factor_manage",
                     _super_only(two_factor_manage))
    app.add_url_rule("/super-admin/double-authentification/codes", "two_factor_regenerate",
                     _super_only(_limited(two_factor_regenerate, "10 per hour")), methods=["POST"])
    app.add_url_rule("/super-admin/double-authentification/telephone", "two_factor_reconfigure",
                     _super_only(_limited(two_factor_reconfigure, "10 per hour")), methods=["POST"])

    @app.context_processor
    def _inject_2fa():
        try:
            if current_user.is_authenticated and current_user.is_super_admin:
                rec = _record(current_user)
                return {"two_factor_enabled": bool(rec and rec.enabled)}
        except Exception:
            db.session.rollback()
        return {}
