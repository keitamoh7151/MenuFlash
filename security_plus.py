"""
MenuFlash Mini — Sécurité avancée (priorités 1, 2 et 3).

Branchement dans app.py, TOUT EN BAS, juste après init_two_factor(...) :

    from security_plus import init_security_plus
    init_security_plus(app, limiter=limiter, log_audit=log_audit,
                       owner_required=owner_required,
                       active_restaurant=active_restaurant)

PRIORITÉ 1 — Comptes
  1. Mots de passe déjà piratés : tout NOUVEAU mot de passe est comparé à la
     base publique Have I Been Pwned (méthode « k-anonymity » : seuls les
     5 premiers caractères de l'empreinte SHA-1 sont envoyés, jamais le mot de
     passe). Si le service ne répond pas, on n'empêche pas l'utilisateur.
  2. Alerte de nouvelle connexion : email quand un compte se connecte depuis
     un appareil inconnu, avec un lien « Ce n'était pas moi » qui verrouille le
     compte, ferme toutes les sessions et envoie un lien de réinitialisation.
  3. Durée des sessions : déconnexion après inactivité (super-admin 2 h,
     gestionnaire 8 h, équipe 7 jours pour la tablette de cuisine) et durée
     maximale de 30 jours.
  4. Page « Appareils connectés » : liste des sessions ouvertes, fermeture
     d'une session à distance ou de toutes les autres.

PRIORITÉ 2 — Équipe
  5. Invitation par email : le gestionnaire ne choisit plus le mot de passe de
     l'employé. L'employé reçoit un lien (72 h, usage unique) pour le choisir.
     Le lien peut aussi être copié (WhatsApp) si l'email n'arrive pas.
  6. Désactivation au lieu de suppression : sessions fermées immédiatement,
     connexion bloquée, historique conservé. Réactivable.
  7. Confirmation par mot de passe pour les actions sensibles sur l'équipe
     (valable 10 minutes, comme sur GitHub ou Google).

PRIORITÉ 3 — Contenu et commandes
  8. Limites par restaurant : plats, catégories, QR codes et espace images.
  9. Protection contre les fausses commandes : limites par client, par
     adresse IP, par table et par restaurant, avec un message clair.
 10. Photos : les informations cachées (position GPS, modèle de téléphone)
     sont retirées et l'orientation est corrigée (voir app.py).

Les nouvelles tables sont créées automatiquement : rien à faire sur Render.
"""
import hashlib
import hmac
import logging
import os
import secrets
import threading
import time
import urllib.request
from datetime import datetime, timedelta
from functools import wraps

from flask import (render_template, request, redirect, url_for, flash, session,
                   jsonify, abort, g, Response)
from flask_login import (current_user, login_required, logout_user, login_user,
                         user_logged_in, user_logged_out)
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

from models import db, User, MenuItem, MenuCategory, QRCode, Order

logger = logging.getLogger("menuflash.security")

# ---------------------------------------------------------------- réglages
IDLE_LIMITS = {                      # inactivité maximale avant déconnexion
    "super_admin": timedelta(hours=2),
    "owner": timedelta(hours=8),
    "staff": timedelta(days=7),      # tablette de cuisine allumée toute la semaine
}
ABSOLUTE_SESSION_LIMIT = timedelta(days=30)
TOUCH_EVERY_SECONDS = 60             # mise à jour de « dernière activité »

INVITE_MAX_AGE = 72 * 3600
NOT_ME_MAX_AGE = 7 * 24 * 3600
NOT_ME_LOCK_MINUTES = 60
SUDO_MINUTES = 10

MAX_ITEMS_PER_RESTAURANT = 300
MAX_CATEGORIES_PER_RESTAURANT = 50
MAX_QRCODES_PER_RESTAURANT = 200
MAX_IMAGES_MB_PER_RESTAURANT = 60

ORDER_LIMIT_PER_CLIENT = "6 per 10 minutes;20 per hour"
ORDER_LIMIT_PER_IP = "60 per 10 minutes"
ADD_TO_ORDER_LIMIT_PER_CLIENT = "15 per 10 minutes"
def _env_int(name, default, low, high):
    try:
        return max(low, min(int(os.environ.get(name, default)), high))
    except (TypeError, ValueError):
        return default


# Commandes « Nouvelle » (pas encore prises en charge par la cuisine) sur 15 min.
# Une table de plusieurs convives peut commander depuis plusieurs téléphones : 4 était
# trop strict. Réglable sans modifier le code (Render > Environment).
MAX_PENDING_ORDERS_PER_TABLE = _env_int("MAX_PENDING_ORDERS_PER_TABLE", 10, 2, 50)
MAX_PENDING_ORDERS_PER_RESTAURANT = _env_int("MAX_PENDING_ORDERS_PER_RESTAURANT", 80, 10, 500)

def _restaurant_limit(restaurant_id):
    """Commandes en attente maximum pour tout le restaurant : jamais moins que sa limite à emporter."""
    try:
        from table_limits import restaurant_wide_limit
        return restaurant_wide_limit(restaurant_id, MAX_PENDING_ORDERS_PER_RESTAURANT)
    except Exception:
        return MAX_PENDING_ORDERS_PER_RESTAURANT


def _takeaway_over_limit(restaurant_id, since):
    """True si le restaurant a déjà atteint sa limite de commandes à emporter en attente
    (réglable par le gestionnaire, table_limits.py). Ne bloque jamais le service en cas d'erreur."""
    try:
        from table_limits import get_takeaway_limit, pending_takeaway_count
        return pending_takeaway_count(restaurant_id, since) >= get_takeaway_limit(restaurant_id)
    except Exception:
        db.session.rollback()
        return False


def _table_limit(restaurant_id):
    """Limite de commandes en attente par table : réglage du restaurant (table_limits.py),
    sinon valeur par défaut. Ne bloque jamais le service si le module est absent."""
    try:
        from table_limits import get_table_limit
        return get_table_limit(restaurant_id)
    except Exception:
        return MAX_PENDING_ORDERS_PER_TABLE


DEVICE_COOKIE = "mf_dev"
PWNED_TIMEOUT = 2.5
PWNED_CACHE_SECONDS = 3600

SUDO_ENDPOINTS = {"admin_team_add", "admin_team_delete", "staff_disable", "staff_enable"}


# ========================================================================
# Modèles (nouvelles tables uniquement)
# ========================================================================
class UserSession(db.Model):
    __tablename__ = "user_sessions"
    id = db.Column(db.String(40), primary_key=True)
    user_id = db.Column(db.Integer, nullable=False, index=True)
    stamp = db.Column(db.String(32))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    last_seen_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    ip_address = db.Column(db.String(64))
    user_agent = db.Column(db.String(250))
    revoked_at = db.Column(db.DateTime)
    revoked_reason = db.Column(db.String(60))


class UserDevice(db.Model):
    __tablename__ = "user_devices"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, nullable=False, index=True)
    device_hash = db.Column(db.String(64), nullable=False, index=True)
    label = db.Column(db.String(80))
    first_seen_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_seen_at = db.Column(db.DateTime, default=datetime.utcnow)


class StaffMeta(db.Model):
    __tablename__ = "staff_meta"
    user_id = db.Column(db.Integer, primary_key=True)
    display_name = db.Column(db.String(80))
    invited_at = db.Column(db.DateTime)
    invited_by = db.Column(db.String(120))
    accepted_at = db.Column(db.DateTime)
    disabled_at = db.Column(db.DateTime)
    disabled_by = db.Column(db.String(120))


_TABLES = (UserSession, UserDevice, StaffMeta)


# ========================================================================
# Outils
# ========================================================================
def client_ip():
    fwd = request.headers.get("X-Forwarded-For", "")
    return (fwd.split(",")[0].strip() if fwd else (request.remote_addr or "?"))[:64]


def describe_user_agent(ua):
    ua = ua or ""
    if "iPhone" in ua:
        system = "iPhone"
    elif "iPad" in ua:
        system = "iPad"
    elif "Android" in ua:
        system = "Android"
    elif "Windows" in ua:
        system = "Windows"
    elif "Macintosh" in ua or "Mac OS" in ua:
        system = "Mac"
    elif "Linux" in ua:
        system = "Linux"
    else:
        system = "appareil inconnu"
    if "Edg/" in ua:
        browser = "Edge"
    elif "OPR/" in ua or "Opera" in ua:
        browser = "Opera"
    elif "SamsungBrowser" in ua:
        browser = "Samsung Internet"
    elif "Firefox/" in ua or "FxiOS" in ua:
        browser = "Firefox"
    elif "Chrome/" in ua or "CriOS" in ua:
        browser = "Chrome"
    elif "Safari/" in ua:
        browser = "Safari"
    else:
        browser = "Navigateur"
    return f"{browser} · {system}"


def is_mobile_user_agent(ua):
    return any(k in (ua or "") for k in ("iPhone", "Android", "Mobile"))


def _role(user):
    if getattr(user, "is_super_admin", False):
        return "super_admin"
    return "owner" if getattr(user, "is_owner", False) else "staff"


def _local_label(dt):
    try:
        minutes = int(os.environ.get("MENUFLASH_UTC_OFFSET_MINUTES", "0"))
    except ValueError:
        minutes = 0
    return (dt + timedelta(minutes=minutes)).strftime("%d/%m/%Y à %H:%M")


# ------------------------------------------------------------------ pwned
_pwned_cache = {}
_pwned_lock = threading.Lock()


def _pwned_suffixes(prefix):
    now = time.time()
    with _pwned_lock:
        hit = _pwned_cache.get(prefix)
        if hit and now - hit[0] < PWNED_CACHE_SECONDS:
            return hit[1]
    req = urllib.request.Request(
        f"https://api.pwnedpasswords.com/range/{prefix}",
        headers={"User-Agent": "MenuFlash-Mini-password-check", "Add-Padding": "true"})
    with urllib.request.urlopen(req, timeout=PWNED_TIMEOUT) as resp:
        body = resp.read().decode("utf-8", "ignore")
    suffixes = {}
    for line in body.splitlines():
        if ":" in line:
            suffix, count = line.split(":", 1)
            try:
                suffixes[suffix.strip().upper()] = int(count.strip())
            except ValueError:
                continue
    with _pwned_lock:
        _pwned_cache[prefix] = (now, suffixes)
        if len(_pwned_cache) > 500:
            _pwned_cache.clear()
    return suffixes


def pwned_count(password):
    """Nombre d'apparitions du mot de passe dans les fuites publiques (0 = inconnu).
    Renvoie None si la vérification est impossible (réseau) : on n'empêche alors rien."""
    if os.environ.get("MENUFLASH_PWNED_CHECK", "true").lower() == "false" or not password:
        return 0
    digest = hashlib.sha1(password.encode("utf-8")).hexdigest().upper()
    try:
        return _pwned_suffixes(digest[:5]).get(digest[5:], 0)
    except Exception as exc:
        logger.warning("Vérification Have I Been Pwned impossible : %s", exc)
        return None


# ========================================================================
# Branchement
# ========================================================================
def init_security_plus(app, *, limiter, log_audit, owner_required, active_restaurant):
    import security_forms as sf
    from mailer import send_transactional_email

    with app.app_context():
        for model in _TABLES:
            try:
                model.__table__.create(db.engine, checkfirst=True)
            except Exception:
                logger.exception("Création de la table %s impossible", model.__tablename__)

    secret = app.config["SECRET_KEY"]
    serializer = URLSafeTimedSerializer(secret)

    def audit(action, success=True, user=None, details=None):
        user = user or (current_user if current_user.is_authenticated else None)
        log_audit(action[:40], success=success,
                  restaurant_id=getattr(user, "restaurant_id", None),
                  user_email=getattr(user, "email", None), details=(details or "")[:300])

    def send_mail(to, subject, body):
        """Envoi en arrière-plan (la connexion n'attend pas le serveur d'emails)."""
        if app.config.get("TESTING"):
            return send_transactional_email(app, to, subject, body)

        def run():
            with app.app_context():
                send_transactional_email(app, to, subject, body)
        threading.Thread(target=run, daemon=True).start()
        return True

    app.extensions["mf_security_send_mail"] = send_mail

    def no_store(resp):
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        resp.headers["Pragma"] = "no-cache"
        return resp

    def wants_json():
        return request.headers.get("X-Requested-With") == "fetch" or request.is_json

    # ====================================================================
    # 1. Mots de passe déjà piratés (tous les formulaires de nouveau mot de passe)
    # ====================================================================
    original_password_errors = sf.password_errors

    def password_errors(password, email="", extra_words=()):
        errors = original_password_errors(password, email=email, extra_words=extra_words)
        if not errors:
            count = pwned_count(password)
            if count:
                errors.append("Ce mot de passe figure dans des fuites de données publiques "
                              f"(vu {count:,} fois). Choisissez-en un autre, que vous "
                              "n'utilisez nulle part ailleurs.".replace(",", " "))
        return errors

    sf.password_errors = password_errors

    # ====================================================================
    # 2-4. Sessions, appareils et alertes de connexion
    # ====================================================================
    def device_hash(raw):
        return hmac.new(secret.encode(), (raw or "").encode(), hashlib.sha256).hexdigest()

    def current_device_id():
        raw = request.cookies.get(DEVICE_COOKIE, "")
        if len(raw) != 32 or not all(c in "0123456789abcdef" for c in raw):
            raw = getattr(g, "mf_new_device", None) or secrets.token_hex(16)
            g.mf_new_device = raw
        return raw

    @app.after_request
    def _set_device_cookie(resp):
        raw = getattr(g, "mf_new_device", None)
        if raw:
            resp.set_cookie(DEVICE_COOKIE, raw, max_age=2 * 365 * 24 * 3600, httponly=True,
                            samesite="Lax", secure=app.config.get("SESSION_COOKIE_SECURE", False))
        return resp

    def open_session(user):
        sid = secrets.token_urlsafe(24)
        db.session.add(UserSession(
            id=sid, user_id=user.id, stamp=user.security_stamp,
            ip_address=client_ip(), user_agent=(request.headers.get("User-Agent") or "")[:250]))
        session["mf_sid"] = sid
        return sid

    def send_new_device_alert(user, sid):
        token = serializer.dumps({"uid": user.id, "sid": sid}, salt="not-me")
        link = url_for("security_not_me", token=token, _external=True)
        ua = describe_user_agent(request.headers.get("User-Agent"))
        send_mail(
            user.email,
            "Nouvelle connexion à votre compte MenuFlash",
            f"Bonjour,\n\nVotre compte ({user.email}) vient de se connecter depuis un nouvel "
            f"appareil :\n\n  • Appareil : {ua}\n  • Adresse IP : {client_ip()}\n"
            f"  • Date : {_local_label(datetime.utcnow())}\n\n"
            f"Si c'était vous, vous n'avez rien à faire.\n\n"
            f"Si ce n'était PAS vous, cliquez ici immédiatement : votre compte sera bloqué, "
            f"toutes les sessions seront fermées et vous recevrez un lien pour choisir un "
            f"nouveau mot de passe :\n{link}\n\nL'équipe MenuFlash")
        audit("security_new_device_alert", user=user, details=ua)

    def _on_login(sender, user, **extra):
        try:
            sid = open_session(user)
            dev = device_hash(current_device_id())
            known = UserDevice.query.filter_by(user_id=user.id, device_hash=dev).first()
            had_devices = UserDevice.query.filter_by(user_id=user.id).count() > 0
            label = describe_user_agent(request.headers.get("User-Agent"))[:80]
            if known:
                known.last_seen_at = datetime.utcnow()
                known.label = label
            else:
                db.session.add(UserDevice(user_id=user.id, device_hash=dev, label=label))
            db.session.commit()
            if not known and had_devices:
                send_new_device_alert(user, sid)
        except Exception:
            db.session.rollback()
            logger.exception("Suivi de session impossible à la connexion")

    def _on_logout(sender, user, **extra):
        sid = session.get("mf_sid")
        if sid:
            try:
                rec = db.session.get(UserSession, sid)
                if rec and not rec.revoked_at:
                    rec.revoked_at = datetime.utcnow()
                    rec.revoked_reason = "déconnexion"
                    db.session.commit()
            except Exception:
                db.session.rollback()
        session.pop("mf_sid", None)
        session.pop("mf_sudo_until", None)

    user_logged_in.connect(_on_login, app, weak=False)
    user_logged_out.connect(_on_logout, app, weak=False)

    def end_session(reason, message):
        sid = session.get("mf_sid")
        user = current_user._get_current_object() if current_user.is_authenticated else None
        logout_user()
        session.clear()
        if user:
            audit("session_ended", user=user, details=f"{reason} ({sid[:8] if sid else '?'})")
        flash(message, "error")
        if wants_json() or request.path.endswith(".json") or request.path.endswith("/stream"):
            return jsonify({"ok": False, "error": message, "logged_out": True}), 401
        return redirect(url_for("admin_login"))

    _SESSION_SKIP = {"static", "media", "backup_cron", "admin_logout", "security_not_me",
                     "team_invite_accept"}

    @app.before_request
    def _track_session():
        if not current_user.is_authenticated or (request.endpoint or "") in _SESSION_SKIP:
            return None
        now = datetime.utcnow()
        sid = session.get("mf_sid")
        rec = db.session.get(UserSession, sid) if sid else None
        if rec is None or rec.user_id != current_user.id:
            # Session ouverte avant cette mise à jour : on l'enregistre sans alerte.
            try:
                open_session(current_user)
                db.session.commit()
            except Exception:
                db.session.rollback()
            return None
        if rec.revoked_at:
            return end_session("session fermée à distance",
                               "Cette session a été fermée depuis un autre appareil. Reconnectez-vous.")
        idle = IDLE_LIMITS[_role(current_user)]
        if now - rec.last_seen_at > idle or now - rec.created_at > ABSOLUTE_SESSION_LIMIT:
            rec.revoked_at = now
            rec.revoked_reason = "expiration"
            db.session.commit()
            return end_session("session expirée",
                               "Votre session a expiré après une période d'inactivité. Reconnectez-vous.")
        if (now - rec.last_seen_at).total_seconds() > TOUCH_EVERY_SECONDS:
            rec.last_seen_at = now
            rec.ip_address = client_ip()
            db.session.commit()
        return None

    def active_sessions(user):
        rows = (UserSession.query.filter_by(user_id=user.id)
                .filter(UserSession.revoked_at.is_(None))
                .order_by(UserSession.last_seen_at.desc()).all())
        now = datetime.utcnow()
        idle = IDLE_LIMITS[_role(user)]
        return [r for r in rows if r.stamp == user.security_stamp
                and now - r.last_seen_at <= idle and now - r.created_at <= ABSOLUTE_SESSION_LIMIT]

    @app.route("/compte/appareils")
    @login_required
    def account_devices():
        restaurant = None if current_user.is_super_admin else active_restaurant()
        rows = []
        for r in active_sessions(current_user):
            rows.append({"id": r.id, "label": describe_user_agent(r.user_agent),
                         "mobile": is_mobile_user_agent(r.user_agent), "ip": r.ip_address,
                         "created": _local_label(r.created_at), "seen": _local_label(r.last_seen_at),
                         "current": r.id == session.get("mf_sid")})
        idle = IDLE_LIMITS[_role(current_user)]
        idle_text = (f"{idle.days} jours" if idle.days else f"{int(idle.total_seconds() // 3600)} heures")
        return no_store(Response(render_template("account_devices.html", restaurant=restaurant,
                                                 sessions=rows, idle_text=idle_text)))

    @app.route("/compte/appareils/<sid>/fermer", methods=["POST"])
    @login_required
    def account_device_revoke(sid):
        rec = UserSession.query.filter_by(id=sid[:40], user_id=current_user.id).first_or_404()
        if rec.id == session.get("mf_sid"):
            flash("Pour fermer cette session, utilisez « Déconnexion ».", "error")
            return redirect(url_for("account_devices"))
        rec.revoked_at = datetime.utcnow()
        rec.revoked_reason = "fermée à distance"
        db.session.commit()
        audit("session_revoked", details=describe_user_agent(rec.user_agent))
        flash("Session fermée : cet appareil devra se reconnecter.", "success")
        return redirect(url_for("account_devices"))

    @app.route("/compte/appareils/fermer-les-autres", methods=["POST"])
    @login_required
    def account_devices_revoke_others():
        now = datetime.utcnow()
        n = 0
        for rec in UserSession.query.filter_by(user_id=current_user.id).filter(
                UserSession.revoked_at.is_(None)).all():
            if rec.id != session.get("mf_sid"):
                rec.revoked_at = now
                rec.revoked_reason = "fermée à distance"
                n += 1
        db.session.commit()
        audit("sessions_revoked_others", details=f"{n} session(s)")
        flash(f"{n} autre(s) session(s) fermée(s)." if n else "Aucune autre session ouverte.", "success")
        return redirect(url_for("account_devices"))

    # ---------------------------------------------- « Ce n'était pas moi »
    def security_not_me(token):
        try:
            data = serializer.loads(token, salt="not-me", max_age=NOT_ME_MAX_AGE)
        except (BadSignature, SignatureExpired):
            return no_store(Response(render_template("security_not_me.html", state="invalid"))), 400
        user = db.session.get(User, data.get("uid"))
        if user is None:
            return no_store(Response(render_template("security_not_me.html", state="invalid"))), 400
        if request.method == "GET":
            # Page de confirmation : un simple clic (ou un antivirus qui ouvre le
            # lien) ne bloque pas le compte, il faut appuyer sur le bouton.
            return no_store(Response(render_template("security_not_me.html", state="confirm", token=token)))
        now = datetime.utcnow()
        user.rotate_security_stamp()
        user.locked_until = now + timedelta(minutes=NOT_ME_LOCK_MINUTES)
        for rec in UserSession.query.filter_by(user_id=user.id).filter(UserSession.revoked_at.is_(None)).all():
            rec.revoked_at = now
            rec.revoked_reason = "signalement « ce n'était pas moi »"
        UserDevice.query.filter_by(user_id=user.id).delete()
        db.session.commit()
        audit("security_not_me", success=False, user=user,
              details="compte bloqué, sessions fermées, appareils oubliés")
        try:
            reset = app.view_functions.get("reset_password")
            tok = serializer.dumps({"uid": user.id, "stamp": user.security_stamp}, salt="pwd-reset")
            link = url_for("reset_password", token=tok, _external=True) if reset else ""
            send_mail(user.email, "Votre compte MenuFlash a été sécurisé",
                      "Bonjour,\n\nSuite à votre signalement, votre compte a été bloqué et toutes "
                      "les sessions ont été fermées.\n\nChoisissez dès maintenant un nouveau mot de "
                      f"passe (lien valable 1 heure) :\n{link}\n\nL'équipe MenuFlash")
        except Exception:
            logger.exception("Envoi du lien de réinitialisation impossible")
        if current_user.is_authenticated and current_user.id == user.id:
            logout_user()
            session.clear()
        return no_store(Response(render_template("security_not_me.html", state="done", email=user.email)))

    view = limiter.limit("10 per hour", methods=["POST"])(security_not_me)
    app.add_url_rule("/securite/ce-n-etait-pas-moi/<token>", "security_not_me", view,
                     methods=["GET", "POST"])

    # ====================================================================
    # 5-7. Équipe : invitation, désactivation, confirmation par mot de passe
    # ====================================================================
    def staff_meta(user_id, create=False):
        meta = db.session.get(StaffMeta, user_id)
        if meta is None and create:
            meta = StaffMeta(user_id=user_id)
            db.session.add(meta)
        return meta

    def is_disabled(user):
        meta = db.session.get(StaffMeta, user.id)
        return bool(meta and meta.disabled_at)

    def invite_link(user):
        token = serializer.dumps({"uid": user.id, "stamp": user.security_stamp}, salt="team-invite")
        return url_for("team_invite_accept", token=token, _external=True)

    def send_invite(user, restaurant_name, name=None):
        link = invite_link(user)
        send_mail(user.email, f"Invitation à rejoindre l'équipe de {restaurant_name}",
                  f"Bonjour{(' ' + name) if name else ''},\n\n{restaurant_name} vous invite à "
                  f"rejoindre son équipe sur MenuFlash pour recevoir et gérer les commandes.\n\n"
                  f"Choisissez votre mot de passe ici (lien valable 72 heures, à usage unique) :\n"
                  f"{link}\n\nSi vous n'attendiez pas cette invitation, ignorez ce message.\n\n"
                  f"L'équipe MenuFlash")
        session["mf_last_invite"] = {"email": user.email, "link": link}
        return link

    # ---- confirmation par mot de passe (« mode sécurisé » de 10 minutes)
    def sudo_active():
        until = session.get("mf_sudo_until", 0)
        return isinstance(until, (int, float)) and until > time.time()

    @app.context_processor
    def _inject_sudo():
        try:
            if current_user.is_authenticated:
                return {"mf_sudo_active": sudo_active()}
        except Exception:
            pass
        return {}

    @app.before_request
    def _require_sudo():
        if request.method != "POST" or (request.endpoint or "") not in SUDO_ENDPOINTS:
            return None
        if not current_user.is_authenticated or sudo_active():
            return None
        pwd = request.form.get("confirm_password", "")
        if pwd and len(pwd) <= 256 and current_user.check_password(pwd):
            session["mf_sudo_until"] = time.time() + SUDO_MINUTES * 60
            audit("sudo_granted", details=request.endpoint)
            return None
        audit("sudo_refused", success=False, details=request.endpoint)
        flash("Mot de passe incorrect : cette action demande de confirmer votre mot de passe.", "error")
        return redirect(url_for("admin_team"))

    # ---- connexion refusée pour un compte désactivé
    @app.before_request
    def _block_disabled_login():
        if request.endpoint != "admin_login" or request.method != "POST":
            return None
        email = sf.normalize_email(request.form.get("email"))
        user = User.query.filter_by(email=email).first() if email else None
        if user and is_disabled(user):
            log_audit("login_blocked_disabled", success=False, restaurant_id=user.restaurant_id,
                      user_email=email, details="compte d'équipe désactivé")
            flash("Identifiants incorrects.", "error")
            return redirect(url_for("admin_login"))
        return None

    @app.before_request
    def _logout_disabled():
        if current_user.is_authenticated and not current_user.is_owner \
                and (request.endpoint or "") not in _SESSION_SKIP and is_disabled(current_user):
            return end_session("compte désactivé", "Ce compte a été désactivé par le gestionnaire.")
        return None

    # ---- validation du nouveau formulaire d'équipe (sans mot de passe)
    def _team_add_form(form, _secret):
        errors = []
        _, err = sf.validate_email(form.get("email"))
        if err:
            errors.append(err)
        if (form.get("name_note") or "").strip():
            _, err = sf.validate_display_name(form.get("name_note"), "Le nom")
            if err:
                errors.append(err)
        return errors, None

    sf._HANDLERS["admin_team_add"] = _team_add_form

    # ---- pages Équipe (remplacent les anciennes)
    def admin_team():
        restaurant = active_restaurant()
        staff = (User.query.filter_by(restaurant_id=restaurant.id, role="staff")
                 .order_by(User.created_at.asc()).all())
        metas = {m.user_id: m for m in StaffMeta.query.filter(
            StaffMeta.user_id.in_([u.id for u in staff] or [0])).all()}
        rows = []
        for u in staff:
            m = metas.get(u.id)
            if m and m.disabled_at:
                status = "disabled"
            elif m and m.invited_at and not m.accepted_at and not u.last_login_at:
                status = "pending"
            else:
                status = "active"
            rows.append({"user": u, "meta": m, "status": status,
                         "name": (m.display_name if m and m.display_name else None),
                         "last_login": _local_label(u.last_login_at) if u.last_login_at else None,
                         "invited": _local_label(m.invited_at) if m and m.invited_at else None})
        last_invite = session.pop("mf_last_invite", None)
        return no_store(Response(render_template(
            "admin_team.html", restaurant=restaurant, rows=rows, last_invite=last_invite,
            sudo_minutes=SUDO_MINUTES)))

    def admin_team_add():
        restaurant = active_restaurant()
        email = sf.normalize_email(request.form.get("email"))
        name = " ".join((request.form.get("name_note") or "").split())[:80] or None
        if User.query.filter_by(email=email).first():
            flash("Cet email est déjà utilisé par un autre compte.", "error")
            return redirect(url_for("admin_team"))
        user = User(restaurant_id=restaurant.id, email=email, role="staff", email_verified=False)
        user.set_password(secrets.token_urlsafe(32))       # inutilisable : remplacé via l'invitation
        db.session.add(user)
        db.session.flush()
        meta = staff_meta(user.id, create=True)
        meta.display_name = name
        meta.invited_at = datetime.utcnow()
        meta.invited_by = current_user.email
        db.session.commit()
        send_invite(user, restaurant.name, name)
        audit("staff_invited", details=f"invitation envoyée à {email}")
        flash(f"Invitation envoyée à {email}. Le lien est aussi affiché ci-dessous si l'email n'arrive pas.",
              "success")
        return redirect(url_for("admin_team"))

    def _staff_or_404(user_id):
        restaurant = active_restaurant()
        return User.query.filter_by(id=user_id, restaurant_id=restaurant.id, role="staff").first_or_404()

    def staff_resend(user_id):
        user = _staff_or_404(user_id)
        meta = staff_meta(user.id, create=True)
        if meta.disabled_at:
            flash("Réactivez d'abord ce compte.", "error")
            return redirect(url_for("admin_team"))
        user.rotate_security_stamp()          # l'ancien lien d'invitation ne fonctionne plus
        meta.invited_at = datetime.utcnow()
        meta.invited_by = current_user.email
        db.session.commit()
        send_invite(user, active_restaurant().name, meta.display_name)
        audit("staff_invite_resent", details=user.email)
        flash(f"Nouvelle invitation envoyée à {user.email}.", "success")
        return redirect(url_for("admin_team"))

    def staff_disable(user_id):
        user = _staff_or_404(user_id)
        meta = staff_meta(user.id, create=True)
        now = datetime.utcnow()
        meta.disabled_at = now
        meta.disabled_by = current_user.email
        user.rotate_security_stamp()          # ferme immédiatement toutes ses sessions
        for rec in UserSession.query.filter_by(user_id=user.id).filter(UserSession.revoked_at.is_(None)).all():
            rec.revoked_at = now
            rec.revoked_reason = "compte désactivé"
        db.session.commit()
        audit("staff_disabled", details=user.email)
        flash(f"Compte {user.email} désactivé : il est déconnecté et ne peut plus se connecter.", "success")
        return redirect(url_for("admin_team"))

    def staff_enable(user_id):
        user = _staff_or_404(user_id)
        meta = staff_meta(user.id, create=True)
        meta.disabled_at = None
        meta.disabled_by = None
        db.session.commit()
        audit("staff_enabled", details=user.email)
        flash(f"Compte {user.email} réactivé.", "success")
        return redirect(url_for("admin_team"))

    original_delete = app.view_functions["admin_team_delete"]

    def admin_team_delete(user_id):
        user = _staff_or_404(user_id)
        uid = user.id
        StaffMeta.query.filter_by(user_id=uid).delete()
        UserSession.query.filter_by(user_id=uid).delete()
        UserDevice.query.filter_by(user_id=uid).delete()
        db.session.flush()
        return original_delete(user_id=user_id)

    app.view_functions["admin_team"] = owner_required(admin_team)
    app.view_functions["admin_team_add"] = owner_required(admin_team_add)
    app.view_functions["admin_team_delete"] = owner_required(admin_team_delete)
    app.add_url_rule("/admin/equipe/<int:user_id>/inviter", "staff_resend",
                     owner_required(limiter.limit("10 per hour")(staff_resend)), methods=["POST"])
    app.add_url_rule("/admin/equipe/<int:user_id>/desactiver", "staff_disable",
                     owner_required(staff_disable), methods=["POST"])
    app.add_url_rule("/admin/equipe/<int:user_id>/reactiver", "staff_enable",
                     owner_required(staff_enable), methods=["POST"])

    # ---- acceptation de l'invitation par l'employé
    def team_invite_accept(token):
        try:
            data = serializer.loads(token, salt="team-invite", max_age=INVITE_MAX_AGE)
        except (BadSignature, SignatureExpired):
            data = None
        user = db.session.get(User, data.get("uid")) if data else None
        if not user or user.security_stamp != data.get("stamp") or user.role != "staff" or is_disabled(user):
            return no_store(Response(render_template("team_invite.html", state="invalid"))), 400
        meta = staff_meta(user.id, create=True)
        restaurant_name = user.restaurant.name if user.restaurant else "votre restaurant"
        if request.method == "POST":
            password = request.form.get("password", "")
            errors = sf.password_errors(password, email=user.email, extra_words=[restaurant_name])
            if password != request.form.get("password_confirm", ""):
                errors.append("Les deux mots de passe ne correspondent pas.")
            if errors:
                for e in errors:
                    flash(e, "error")
                return redirect(url_for("team_invite_accept", token=token))
            user.set_password(password)           # change le tampon : lien à usage unique
            user.email_verified = True
            user.email_verified_at = datetime.utcnow()
            meta.accepted_at = datetime.utcnow()
            db.session.commit()
            log_audit("staff_invite_accepted", restaurant_id=user.restaurant_id, user_email=user.email,
                      details="mot de passe choisi par l'employé")
            if current_user.is_authenticated:
                logout_user()
            login_user(user)
            session.pop("active_restaurant_id", None)
            flash("Bienvenue ! Votre compte est prêt.", "success")
            return redirect(url_for("admin_orders"))
        return no_store(Response(render_template(
            "team_invite.html", state="form", email=user.email, name=meta.display_name,
            restaurant_name=restaurant_name, token=token)))

    app.add_url_rule("/invitation/<token>", "team_invite_accept",
                     limiter.limit("10 per hour", methods=["POST"])(team_invite_accept),
                     methods=["GET", "POST"])

    # ====================================================================
    # 8. Limites de contenu par restaurant
    # ====================================================================
    def images_bytes(restaurant_id):
        from media_store import StoredImage
        return db.session.query(db.func.coalesce(db.func.sum(db.func.length(StoredImage.data)), 0)).filter(
            StoredImage.restaurant_id == restaurant_id).scalar() or 0

    _QUOTA_ENDPOINTS = {"add_item", "add_category", "admin_qrcodes_add", "edit_item", "admin_appearance_logo"}

    @app.before_request
    def _content_quotas():
        endpoint = request.endpoint or ""
        if request.method != "POST" or endpoint not in _QUOTA_ENDPOINTS or not current_user.is_authenticated:
            return None
        restaurant = active_restaurant()
        if restaurant is None:
            return None
        rid = restaurant.id
        message, target = None, "admin_dashboard"
        if endpoint == "add_item" and MenuItem.query.filter_by(restaurant_id=rid).count() >= MAX_ITEMS_PER_RESTAURANT:
            message = f"Limite atteinte : {MAX_ITEMS_PER_RESTAURANT} plats maximum par restaurant."
        elif endpoint == "add_category" and \
                MenuCategory.query.filter_by(restaurant_id=rid).count() >= MAX_CATEGORIES_PER_RESTAURANT:
            message = f"Limite atteinte : {MAX_CATEGORIES_PER_RESTAURANT} catégories maximum."
        elif endpoint == "admin_qrcodes_add" and \
                QRCode.query.filter_by(restaurant_id=rid).count() >= MAX_QRCODES_PER_RESTAURANT:
            message, target = f"Limite atteinte : {MAX_QRCODES_PER_RESTAURANT} codes QR maximum.", "admin_qrcodes"
        if not message:
            f = request.files.get("photo") or request.files.get("logo")
            if f and f.filename and images_bytes(rid) >= MAX_IMAGES_MB_PER_RESTAURANT * 1024 * 1024:
                message = (f"Espace images plein ({MAX_IMAGES_MB_PER_RESTAURANT} Mo). Supprimez des photos "
                           f"inutilisées ou contactez-nous.")
                if endpoint == "admin_appearance_logo":
                    target = "admin_appearance"
        if message:
            audit("quota_reached", success=False, details=f"{endpoint} : {message}")
            flash(message, "error")
            return redirect(url_for(target))
        return None

    # ====================================================================
    # 9. Protection contre les fausses commandes
    # ====================================================================
    def client_key():
        cid = session.get("mf_cid")
        if not cid:
            cid = secrets.token_hex(12)
            session["mf_cid"] = cid
        slug = (request.view_args or {}).get("slug", "")
        return f"order:{slug}:{cid}"

    def ip_key():
        return f"order-ip:{(request.view_args or {}).get('slug', '')}:{client_ip()}"

    for endpoint, per_client in (("place_order", ORDER_LIMIT_PER_CLIENT),
                                 ("add_to_order", ADD_TO_ORDER_LIMIT_PER_CLIENT)):
        view = app.view_functions.get(endpoint)
        if view is None:
            continue
        view = limiter.limit(per_client, key_func=client_key, methods=["POST"])(view)
        view = limiter.limit(ORDER_LIMIT_PER_IP, key_func=ip_key, methods=["POST"])(view)
        app.view_functions[endpoint] = view

    @app.before_request
    def _order_flood_guard():
        if request.endpoint != "place_order" or request.method != "POST":
            return None
        from models import Restaurant
        slug = (request.view_args or {}).get("slug")
        r = Restaurant.query.filter_by(slug=slug, is_active=True).first() if slug else None
        if r is None:
            return None
        since = datetime.utcnow() - timedelta(minutes=15)
        pending = Order.query.filter(Order.restaurant_id == r.id, Order.status == "nouvelle",
                                     Order.created_at >= since)
        table = (request.form.get("table_label") or "").strip()[:50]
        qr_id = request.form.get("qr_id", type=int)
        if qr_id:
            qr = QRCode.query.filter_by(id=qr_id, restaurant_id=r.id).first()
            table = qr.label if qr else table
        message = None
        if pending.count() >= _restaurant_limit(r.id):
            message = "Le restaurant reçoit beaucoup de commandes en ce moment. Réessayez dans quelques minutes."
        elif (not table and request.form.get("fulfillment") == "takeaway"
              and _takeaway_over_limit(r.id, since)):
            message = ("Le restaurant a beaucoup de commandes à emporter en attente. "
                       "Réessayez dans quelques minutes.")
        elif table and pending.filter(Order.table_label == table).count() >= _table_limit(r.id):
            message = ("Plusieurs commandes de votre table attendent déjà d'être prises en charge. "
                       "Patientez un instant ou appelez un serveur.")
        if not message:
            return None
        log_audit("order_flood_blocked", success=False, restaurant_id=r.id, user_email=None,
                  details=f"table={table or '—'} ip={client_ip()}"
                          + (" (à emporter)" if not table else ""))
        if wants_json():
            return jsonify({"ok": False, "error": message}), 429
        flash(message, "error")
        return redirect(url_for("public_menu", slug=slug))

    @app.errorhandler(429)
    def _too_many(exc):
        message = ("Trop de demandes envoyées en peu de temps. Patientez quelques minutes, "
                   "ou appelez un serveur.")
        if wants_json():
            return jsonify({"ok": False, "error": message}), 429
        return Response(f"<!doctype html><meta charset='utf-8'><title>Trop de demandes</title>"
                        f"<p style='font-family:sans-serif;max-width:480px;margin:80px auto;text-align:center'>"
                        f"{message}</p>", 429, {"Retry-After": "60"})




