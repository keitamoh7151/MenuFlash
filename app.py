"""
MenuFlash Mini — point d'entrée Flask
Menu affiché par QR code + commande en ligne (sans paiement en ligne).
+ Page de présentation (landing page) publique + inscription en
  libre-service, avec le même langage visuel appliqué à l'ensemble du
  back-office.
+ Notifications cuisine instantanées (Server-Sent Events).
+ Annulation de commande par le client tant qu'elle est "nouvelle".
+ Thème de couleur par restaurant, appliqué partout et propagé en direct.
+ Panel super-admin, QR codes par table, glisser-déposer, panneau "Mes
  commandes" côté client, ajout à une commande existante, statistiques
  de ventes, comptes d'équipe à rôles (owner / staff).
+ Avis clients par plat, interface multi-langue FR/EN, temps de
  préparation estimé, mode "rush" (pause de catégorie), historique des
  modifications du menu, modification en place des plats/catégories.
+ SUGGESTIONS DE VENTE ADDITIONNELLE : un plat peut en "suggérer" un autre.
+ MODE MULTI-RESTAURANT : un même compte "owner" peut créer et gérer
  plusieurs établissements, et basculer entre eux depuis un sélecteur.
+ IDENTITÉ VISUELLE DU RESTAURANT (marque blanche partielle) : la barre
  de navigation du back-office affiche le NOM et le LOGO du restaurant
  actif, à la place de la marque "MenuFlash Mini".
+ NAVIGATION RÉORGANISÉE EN MENUS DÉROULANTS (templates/_admin_navbar.html).
+ IMAGES PERMANENTES : logos et photos de plats sont stockés dans la base
  de données (voir media_store.py) au lieu du disque du serveur, qui est
  effacé à chaque redéploiement / mise en veille sur Render.
+ "MES COMMANDES" CÔTÉ CLIENT LIMITÉ À LA VISITE EN COURS : une commande
  annulée disparaît après 5 min, une commande servie après 30 min, et
  toute commande disparaît après 12 h (voir CLIENT_ORDER_* ci-dessous).
+ PANEL SUPER-ADMIN COMPLET (super_admin.py) : pilotage, gestion des
  clients, finances, relances, communication, maintenance, santé.

SÉCURITÉ :
  - Protection CSRF (Flask-WTF) sur tous les formulaires et appels AJAX.
  - Limitation du débit (Flask-Limiter) sur la connexion et l'inscription.
  - En-têtes de sécurité HTTP (CSP, anti-clickjacking, anti-sniffing).
  - Cookies de session HttpOnly + SameSite=Lax (+ Secure en production).
  - HSTS + redirection forcée vers HTTPS (via FORCE_HTTPS=true en prod).
  - Verrouillage de compte après 5 échecs de connexion (15 minutes).
  - Invalidation de TOUTES les sessions actives ailleurs dès qu'un mot de
    passe est changé ou réinitialisé (tampon de sécurité par utilisateur).
  - Vérification d'adresse email à l'inscription (lien à usage unique).
  - Réinitialisation de mot de passe sécurisée (jeton signé, à durée de
    vie limitée, à usage unique).
  - Journal d'audit des événements de sécurité.
  - Validation et troncature serveur de toutes les entrées utilisateur.
  - Mots de passe hachés (PBKDF2 via Werkzeug), jamais stockés en clair.

Lancement local (Windows / PowerShell) :
    $env:FLASK_SECRET_KEY = "change-moi"
    python app.py
"""
import os
import io
import json
import re
import logging
import unicodedata
from datetime import datetime, timedelta
from functools import wraps

import qrcode
from flask import (Flask, render_template, redirect, url_for, request,
                    flash, send_file, abort, jsonify, Response, session)
from flask_login import (LoginManager, login_user, logout_user,
                          login_required, current_user)
from flask_wtf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.utils import secure_filename
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from PIL import Image, UnidentifiedImageError

from models import (db, Restaurant, User, MenuCategory, MenuItem, QRCode,
                     SubscriptionPayment, Order, OrderItem, DishReview, MenuChangeLog,
                     AuditLog, RestaurantAccess, MenuItemSuggestion,
                     THEME_PRESETS, DEFAULT_THEME, DEFAULT_PREP_MINUTES,
                     TRIAL_DAYS, MAX_FAILED_LOGIN_ATTEMPTS, LOCKOUT_DURATION_MINUTES)
from pdf_utils import make_table_tent_pdf
from stats_utils import build_dashboard_stats
from i18n import TRANSLATIONS
import realtime
from mailer import init_mailer, send_transactional_email
from super_admin import init_super_admin, get_trial_days
from media_store import StoredImage, ensure_media_table, store_image, delete_stored_image

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "static", "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

MAX_IMAGE_SIDE = 800
MAX_LOGO_SIDE = 400
ALLOWED_EXT = {"jpg", "jpeg", "png", "webp"}
MIN_PASSWORD_LENGTH = 8

EMAIL_VERIFY_MAX_AGE = 60 * 60 * 24       # 24 heures
PASSWORD_RESET_MAX_AGE = 60 * 60          # 1 heure
MAX_UPSELL_SUGGESTIONS_PER_ITEM = 5

# Visibilité des commandes dans le panneau "Mes commandes" du client.
# Le délai se compte à partir du dernier changement de statut (updated_at),
# sauf la durée maximale qui se compte depuis la création de la commande.
CLIENT_ORDER_CANCELLED_VISIBLE_MINUTES = 5    # commande annulée
CLIENT_ORDER_SERVED_VISIBLE_MINUTES = 30      # commande servie (temps de laisser un avis)
CLIENT_ORDER_MAX_AGE_HOURS = 12               # filet de sécurité, quel que soit le statut
CLIENT_ORDER_MAX_IDS = 50                     # nombre max de commandes interrogées par requête

ORDER_STATUS_FLOW = ["nouvelle", "en_preparation", "prete", "servie"]
EDITABLE_STATUS = "nouvelle"
ORDER_STATUS_LABELS = {
    "nouvelle": "Nouvelle",
    "en_preparation": "En préparation",
    "prete": "Prête",
    "servie": "Servie",
    "annulee": "Annulée",
}

RESERVED_SLUGS = {
    "admin", "super-admin", "static", "inscription", "m", "api",
    "login", "logout", "menuflash-admin",
}

app = Flask(__name__)
logger = logging.getLogger("menuflash")

# --------------------------------------------------------------------------
# SÉCURITÉ — configuration de base
# --------------------------------------------------------------------------
_default_secret = "dev-key-a-changer"
app.config["SECRET_KEY"] = os.environ.get("FLASK_SECRET_KEY", _default_secret)
if app.config["SECRET_KEY"] == _default_secret:
    logger.warning(
        "FLASK_SECRET_KEY n'est pas défini : une clé de développement est "
        "utilisée. Définissez une variable d'environnement FLASK_SECRET_KEY "
        "unique et secrète avant tout déploiement en production."
    )

_db_url = os.environ.get("DATABASE_URL", f"sqlite:///{os.path.join(BASE_DIR, 'menuflash.db')}")
# Force le pilote psycopg2, quelle que soit la forme de l'adresse fournie
# (postgres://, postgresql:// ou postgresql+psycopg2://).
if _db_url.startswith("postgres://"):
    _db_url = _db_url.replace("postgres://", "postgresql+psycopg2://", 1)
elif _db_url.startswith("postgresql://"):
    _db_url = _db_url.replace("postgresql://", "postgresql+psycopg2://", 1)
app.config["SQLALCHEMY_DATABASE_URI"] = _db_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024

app.config["SESSION_COOKIE_NAME"] = "menuflash_session"
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("SECURE_COOKIES", "false").lower() == "true"

app.config["WTF_CSRF_TIME_LIMIT"] = None

FORCE_HTTPS = os.environ.get("FORCE_HTTPS", "false").lower() == "true"

app.config["MAIL_SERVER"] = os.environ.get("MAIL_SERVER", "")
app.config["MAIL_PORT"] = int(os.environ.get("MAIL_PORT", "587"))
app.config["MAIL_USE_TLS"] = os.environ.get("MAIL_USE_TLS", "true").lower() == "true"
app.config["MAIL_USERNAME"] = os.environ.get("MAIL_USERNAME", "")
app.config["MAIL_PASSWORD"] = os.environ.get("MAIL_PASSWORD", "")
app.config["MAIL_DEFAULT_SENDER"] = os.environ.get("MAIL_DEFAULT_SENDER", "no-reply@menuflash.local")

db.init_app(app)
ensure_media_table(app)
init_mailer(app)

login_manager = LoginManager(app)
login_manager.login_view = "admin_login"

csrf = CSRFProtect(app)

limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    default_limits=["1000 per minute"],
    storage_uri="memory://",
)
limiter.exempt(app.view_functions["static"])
from security_forms import init_form_security
init_form_security(app, limiter)


@app.before_request
def force_https_redirect():
    if not FORCE_HTTPS:
        return None
    proto = request.headers.get("X-Forwarded-Proto", "https" if request.is_secure else "http")
    if proto != "https":
        url = request.url.replace("http://", "https://", 1)
        return redirect(url, code=301)
    return None


@app.after_request
def set_security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com https://cdn.jsdelivr.net; "
        "img-src 'self' data: blob:; "
        "connect-src 'self';"
    )
    is_https = request.is_secure or request.headers.get("X-Forwarded-Proto") == "https"
    if is_https or FORCE_HTTPS:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


@login_manager.user_loader
def load_user(composite_id):
    try:
        uid_str, stamp = composite_id.split(":", 1)
        uid = int(uid_str)
    except (ValueError, AttributeError):
        return None
    user = User.query.get(uid)
    if not user or user.security_stamp != stamp:
        return None
    return user


# --------------------------------------------------------------------------
# MODE MULTI-RESTAURANT — helpers de résolution du "restaurant actif"
# --------------------------------------------------------------------------
def get_owner_restaurants(user):
    if not user or not getattr(user, "is_owner", False) or getattr(user, "is_super_admin", False):
        return []
    ids = {user.restaurant_id}
    extra = RestaurantAccess.query.filter_by(user_id=user.id).all()
    ids.update(a.restaurant_id for a in extra)
    return Restaurant.query.filter(Restaurant.id.in_(ids)).order_by(Restaurant.created_at.asc()).all()


def active_restaurant():
    if not current_user.is_authenticated:
        return None
    if current_user.is_super_admin or not current_user.is_owner:
        return current_user.restaurant

    rid = session.get("active_restaurant_id")
    if rid:
        allowed_ids = {current_user.restaurant_id} | {
            a.restaurant_id for a in RestaurantAccess.query.filter_by(user_id=current_user.id).all()
        }
        if rid in allowed_ids:
            r = Restaurant.query.get(rid)
            if r:
                return r
    return current_user.restaurant


def active_restaurant_id():
    r = active_restaurant()
    return r.id if r else None


@app.context_processor
def inject_theme_colors():
    colors = THEME_PRESETS[DEFAULT_THEME]
    try:
        if current_user.is_authenticated and not getattr(current_user, "is_super_admin", False):
            restaurant = active_restaurant()
            if restaurant:
                colors = restaurant.theme_colors
    except Exception:
        pass
    return {"theme_colors": colors}


@app.context_processor
def inject_owner_restaurants():
    try:
        if current_user.is_authenticated and current_user.is_owner and not current_user.is_super_admin:
            return {"owner_restaurants": get_owner_restaurants(current_user)}
    except Exception:
        pass
    return {"owner_restaurants": []}


@app.context_processor
def inject_nav_active_orders_count():
    """
    Rend `nav_active_orders_count` disponible dans TOUTES les pages du
    back-office, pour que le lien "Commandes" de la barre de navigation
    partagée puisse toujours afficher le badge de commandes en cours.
    """
    try:
        if current_user.is_authenticated and not current_user.is_super_admin:
            restaurant_id = active_restaurant_id()
            if restaurant_id:
                count = Order.query.filter_by(restaurant_id=restaurant_id).filter(
                    Order.status.in_(["nouvelle", "en_preparation", "prete"])
                ).count()
                return {"nav_active_orders_count": count}
    except Exception:
        pass
    return {"nav_active_orders_count": 0}


def slugify(value):
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    value = value.lower().strip()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = re.sub(r"-+", "-", value).strip("-")
    return value[:60] or "restaurant"


def slug_is_available(slug):
    if not slug or slug in RESERVED_SLUGS:
        return False
    return Restaurant.query.filter_by(slug=slug).first() is None


def safe_int(raw_value, default=None, min_value=None, max_value=None):
    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        return default
    if min_value is not None:
        value = max(value, min_value)
    if max_value is not None:
        value = min(value, max_value)
    return value


def get_client_ip():
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()[:64]
    return (request.remote_addr or "unknown")[:64]


def log_audit(action, success=True, restaurant_id=None, user_email=None, details=None):
    try:
        db.session.add(AuditLog(
            restaurant_id=restaurant_id,
            user_email=user_email,
            action=action,
            success=success,
            ip_address=get_client_ip(),
            details=(details or "")[:300],
        ))
        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.exception("Échec d'écriture du journal d'audit (action=%s)", action)


def _token_serializer():
    return URLSafeTimedSerializer(app.config["SECRET_KEY"])


def generate_security_token(user, salt):
    return _token_serializer().dumps({"uid": user.id, "stamp": user.security_stamp}, salt=salt)


def verify_security_token(token, salt, max_age):
    try:
        data = _token_serializer().loads(token, salt=salt, max_age=max_age)
    except (BadSignature, SignatureExpired):
        return None
    user = User.query.get(data.get("uid"))
    if not user or user.security_stamp != data.get("stamp"):
        return None
    return user


def send_verification_email(user):
    token = generate_security_token(user, salt="email-verify")
    link = url_for("verify_email", token=token, _external=True)
    send_transactional_email(
        app, user.email,
        subject="Confirmez votre adresse email — MenuFlash Mini",
        body_text=(
            f"Bonjour,\n\nConfirmez votre adresse email en cliquant sur ce lien "
            f"(valable 24 heures) :\n{link}\n\nSi vous n'êtes pas à l'origine de "
            f"cette inscription, ignorez ce message.\n\nL'équipe MenuFlash Mini"
        ),
    )


def send_password_reset_email(user):
    token = generate_security_token(user, salt="pwd-reset")
    link = url_for("reset_password", token=token, _external=True)
    send_transactional_email(
        app, user.email,
        subject="Réinitialisation de votre mot de passe — MenuFlash Mini",
        body_text=(
            f"Bonjour,\n\nVous avez demandé à réinitialiser votre mot de passe. "
            f"Ce lien est valable 1 heure et ne peut être utilisé qu'une seule "
            f"fois :\n{link}\n\nSi vous n'êtes pas à l'origine de cette demande, "
            f"ignorez ce message : votre mot de passe actuel reste inchangé.\n\n"
            f"L'équipe MenuFlash Mini"
        ),
    )


# --------------------------------------------------------------------------
# Page de présentation (landing page) publique + inscription en libre-service
# --------------------------------------------------------------------------
@app.route("/")
def landing():
    return render_template("landing.html", now_year=datetime.utcnow().year)


@app.route("/inscription", methods=["GET", "POST"])
@limiter.limit("8 per hour", methods=["POST"])
def signup():
    if request.method == "GET":
        return render_template("signup.html")

    restaurant_name = request.form.get("restaurant_name", "").strip()[:120]
    slug_input = request.form.get("slug", "").strip().lower()[:80]
    owner_email = request.form.get("email", "").strip().lower()[:120]
    owner_password = request.form.get("password", "")
    phone = request.form.get("phone", "").strip()[:30]

    errors = []
    if not restaurant_name:
        errors.append("Le nom du restaurant est obligatoire.")
    if not owner_email or "@" not in owner_email:
        errors.append("Une adresse email valide est requise.")
    if len(owner_password) < MIN_PASSWORD_LENGTH:
        errors.append(f"Le mot de passe doit contenir au moins {MIN_PASSWORD_LENGTH} caractères.")

    slug = slugify(slug_input or restaurant_name)
    if not slug_is_available(slug):
        base_slug = slug
        suffix = 2
        while not slug_is_available(f"{base_slug}-{suffix}") and suffix < 50:
            suffix += 1
        slug = f"{base_slug}-{suffix}"

    if User.query.filter_by(email=owner_email).first():
        errors.append("Un compte existe déjà avec cet email.")

    if errors:
        for message in errors:
            flash(message, "error")
        log_audit("signup_failed", success=False, user_email=owner_email,
                   details="; ".join(errors)[:300])
        return render_template(
            "signup.html",
            form_values={"restaurant_name": restaurant_name, "slug": slug_input,
                         "email": owner_email, "phone": phone},
        ), 400

    restaurant = Restaurant(
        name=restaurant_name,
        slug=slug,
        phone=phone or None,
        subscription_plan="trial",
        subscription_active_until=datetime.utcnow() + timedelta(days=get_trial_days()),
    )
    db.session.add(restaurant)
    db.session.commit()

    owner = User(restaurant_id=restaurant.id, email=owner_email, role="owner")
    owner.set_password(owner_password)
    db.session.add(owner)
    db.session.commit()

    send_verification_email(owner)
    log_audit("signup", restaurant_id=restaurant.id, user_email=owner_email,
               details=f"Restaurant « {restaurant_name} » créé (slug={slug}).")

    login_user(owner)
    flash(
        f"Bienvenue sur MenuFlash Mini ! Votre essai gratuit de {get_trial_days()} jours a commencé. "
        f"Votre menu est accessible à l'adresse /m/{slug}. Pensez à confirmer votre email "
        f"(lien envoyé à {owner_email}), et à ajouter le logo de votre restaurant depuis "
        f"la page Apparence.",
        "success",
    )
    return redirect(url_for("admin_dashboard"))


@app.route("/inscription/verifier-slug")
def signup_check_slug():
    raw = request.args.get("slug", "")[:120]
    slug = slugify(raw)
    return jsonify({"slug": slug, "available": slug_is_available(slug)})


# --------------------------------------------------------------------------
# MODE MULTI-RESTAURANT — créer un restaurant additionnel / basculer
# --------------------------------------------------------------------------
@app.route("/admin/restaurants/new", methods=["POST"])
@login_required
def admin_restaurants_new():
    if not current_user.is_owner or current_user.is_super_admin:
        abort(403)

    name = request.form.get("name", "").strip()[:120]
    slug_input = request.form.get("slug", "").strip().lower()[:80]
    if not name:
        flash("Le nom du restaurant est obligatoire.", "error")
        return redirect(url_for("admin_dashboard"))

    slug = slugify(slug_input or name)
    if not slug_is_available(slug):
        base_slug = slug
        suffix = 2
        while not slug_is_available(f"{base_slug}-{suffix}") and suffix < 50:
            suffix += 1
        slug = f"{base_slug}-{suffix}"

    restaurant = Restaurant(
        name=name, slug=slug,
        subscription_plan="trial",
        subscription_active_until=datetime.utcnow() + timedelta(days=get_trial_days()),
    )
    db.session.add(restaurant)
    db.session.commit()

    db.session.add(RestaurantAccess(user_id=current_user.id, restaurant_id=restaurant.id))
    db.session.commit()

    log_audit("additional_restaurant_created", restaurant_id=restaurant.id,
               user_email=current_user.email,
               details=f"« {name} » créé comme restaurant additionnel (slug={slug}).")

    session["active_restaurant_id"] = restaurant.id
    flash(
        f"Restaurant « {name} » créé avec un essai gratuit de {get_trial_days()} jours. "
        f"Vous pouvez maintenant y ajouter votre menu.",
        "success",
    )
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/restaurants/switch", methods=["POST"])
@login_required
def admin_restaurants_switch():
    if not current_user.is_owner or current_user.is_super_admin:
        abort(403)

    target_id = safe_int(request.form.get("restaurant_id"))
    allowed_ids = {current_user.restaurant_id} | {
        a.restaurant_id for a in RestaurantAccess.query.filter_by(user_id=current_user.id).all()
    }
    if target_id not in allowed_ids:
        flash("Vous n'avez pas accès à ce restaurant.", "error")
        return redirect(url_for("admin_dashboard"))

    session["active_restaurant_id"] = target_id
    r = Restaurant.query.get(target_id)
    log_audit("restaurant_switched", restaurant_id=target_id, user_email=current_user.email,
               details=f"Bascule vers « {r.name if r else target_id} ».")
    return redirect(url_for("admin_dashboard"))


# --------------------------------------------------------------------------
# SÉCURITÉ — vérification d'email
# --------------------------------------------------------------------------
@app.route("/verifier-email/<token>")
def verify_email(token):
    user = verify_security_token(token, salt="email-verify", max_age=EMAIL_VERIFY_MAX_AGE)
    if not user:
        flash("Ce lien de vérification est invalide ou a expiré. Demandez-en un nouveau depuis votre tableau de bord.", "error")
        return redirect(url_for("admin_login"))

    if not user.email_verified:
        user.email_verified = True
        user.email_verified_at = datetime.utcnow()
        db.session.commit()
        log_audit("email_verified", restaurant_id=user.restaurant_id, user_email=user.email)
        flash("Votre adresse email a été confirmée. Merci !", "success")
    else:
        flash("Votre adresse email était déjà confirmée.", "success")

    if current_user.is_authenticated:
        return redirect(url_for("admin_orders") if not current_user.is_owner else url_for("admin_dashboard"))
    return redirect(url_for("admin_login"))


@app.route("/admin/verifier-email/renvoyer", methods=["POST"])
@login_required
@limiter.limit("3 per hour")
def resend_verification_email():
    if current_user.email_verified:
        flash("Votre email est déjà confirmé.", "success")
    else:
        send_verification_email(current_user)
        log_audit("email_verification_resent", restaurant_id=current_user.restaurant_id,
                   user_email=current_user.email)
        flash("Un nouveau lien de confirmation a été envoyé à votre adresse email.", "success")
    return redirect(request.referrer or url_for("admin_dashboard"))


# --------------------------------------------------------------------------
# SÉCURITÉ — mot de passe oublié / réinitialisation
# --------------------------------------------------------------------------
@app.route("/admin/mot-de-passe-oublie", methods=["GET", "POST"])
@limiter.limit("5 per hour", methods=["POST"])
def forgot_password():
    if request.method == "GET":
        return render_template("forgot_password.html")

    email = request.form.get("email", "").strip().lower()[:120]
    user = User.query.filter_by(email=email).first()
    if user:
        send_password_reset_email(user)
        log_audit("password_reset_requested", restaurant_id=user.restaurant_id, user_email=email)
    else:
        log_audit("password_reset_requested", success=False, user_email=email,
                   details="email inconnu")

    flash("Si un compte existe avec cet email, un lien de réinitialisation vient de lui être envoyé.", "success")
    return redirect(url_for("admin_login"))


@app.route("/admin/reinitialiser/<token>", methods=["GET", "POST"])
@limiter.limit("10 per hour", methods=["POST"])
def reset_password(token):
    user = verify_security_token(token, salt="pwd-reset", max_age=PASSWORD_RESET_MAX_AGE)
    if not user:
        flash("Ce lien de réinitialisation est invalide, a expiré, ou a déjà été utilisé.", "error")
        return redirect(url_for("forgot_password"))

    if request.method == "GET":
        return render_template("reset_password.html", token=token)

    new_password = request.form.get("password", "")
    confirm_password = request.form.get("password_confirm", "")

    if len(new_password) < MIN_PASSWORD_LENGTH:
        flash(f"Le mot de passe doit contenir au moins {MIN_PASSWORD_LENGTH} caractères.", "error")
        return render_template("reset_password.html", token=token), 400
    if new_password != confirm_password:
        flash("Les deux mots de passe ne correspondent pas.", "error")
        return render_template("reset_password.html", token=token), 400

    user.set_password(new_password)
    db.session.commit()
    log_audit("password_reset_completed", restaurant_id=user.restaurant_id, user_email=user.email)

    login_user(user)
    flash("Votre mot de passe a été réinitialisé. Vous êtes maintenant connecté.", "success")
    return redirect(url_for("admin_dashboard") if user.is_owner else url_for("admin_orders"))


# --------------------------------------------------------------------------
# SÉCURITÉ — changement de mot de passe en libre-service
# --------------------------------------------------------------------------
@app.route("/admin/mot-de-passe", methods=["GET", "POST"])
@login_required
def change_password():
    restaurant = None if current_user.is_super_admin else active_restaurant()

    if request.method == "GET":
        return render_template("change_password.html", restaurant=restaurant)

    current_password = request.form.get("current_password", "")
    new_password = request.form.get("new_password", "")
    confirm_password = request.form.get("new_password_confirm", "")

    if not current_user.check_password(current_password):
        flash("Mot de passe actuel incorrect.", "error")
        log_audit("password_change_failed", success=False,
                   restaurant_id=current_user.restaurant_id, user_email=current_user.email,
                   details="mot de passe actuel incorrect")
        return render_template("change_password.html", restaurant=restaurant), 400

    if len(new_password) < MIN_PASSWORD_LENGTH:
        flash(f"Le nouveau mot de passe doit contenir au moins {MIN_PASSWORD_LENGTH} caractères.", "error")
        return render_template("change_password.html", restaurant=restaurant), 400
    if new_password != confirm_password:
        flash("Les deux mots de passe ne correspondent pas.", "error")
        return render_template("change_password.html", restaurant=restaurant), 400

    real_user = current_user._get_current_object()
    real_user.set_password(new_password)
    db.session.commit()
    log_audit("password_changed", restaurant_id=real_user.restaurant_id, user_email=real_user.email)

    login_user(real_user)
    flash("Votre mot de passe a été mis à jour. Vos autres sessions ont été déconnectées par sécurité.", "success")
    return redirect(url_for("admin_dashboard") if real_user.is_owner else url_for("admin_orders"))


# --------------------------------------------------------------------------
# SÉCURITÉ — journal d'audit
# --------------------------------------------------------------------------
@app.route("/admin/journal-securite")
@login_required
def admin_security_log():
    if current_user.is_super_admin:
        return redirect(url_for("super_admin_security_log"))
    if not current_user.is_owner:
        flash("Cette page est réservée au gestionnaire du restaurant.", "error")
        return redirect(url_for("admin_orders"))
    restaurant = active_restaurant()
    logs = (AuditLog.query
            .filter_by(restaurant_id=restaurant.id)
            .order_by(AuditLog.created_at.desc())
            .limit(200)
            .all())
    return render_template("admin_security_log.html", restaurant=restaurant, logs=logs)


@app.route("/super-admin/journal-securite")
@login_required
def super_admin_security_log():
    if not current_user.is_super_admin:
        abort(403)
    logs = AuditLog.query.order_by(AuditLog.created_at.desc()).limit(300).all()
    return render_template("super_admin_security_log.html", logs=logs)


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXT


def save_compressed_image(file_storage, restaurant_slug, restaurant_id=None):
    """
    Recompresse une photo de plat (aplatie en RGB, format WEBP) et
    l'enregistre dans la base de données (stockage permanent, voir
    media_store.py). Renvoie l'URL /media/<id>, ou None si le fichier
    n'est pas une image valide.
    """
    filename = secure_filename(file_storage.filename)
    if not allowed_file(filename):
        return None

    try:
        img = Image.open(file_storage.stream)
        img.verify()
        file_storage.stream.seek(0)
        img = Image.open(file_storage.stream).convert("RGB")
    except (UnidentifiedImageError, OSError, ValueError):
        return None

    img.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))

    buf = io.BytesIO()
    img.save(buf, "WEBP", quality=70)
    return store_image(buf.getvalue(), "image/webp", kind="photo", restaurant_id=restaurant_id)


def save_logo_image(file_storage, restaurant_slug, restaurant_id=None):
    """
    Recompresse un logo de restaurant en préservant la transparence (PNG)
    si l'image source en a une ; sinon conversion en WEBP. Le logo est
    enregistré dans la base de données (stockage permanent) : il ne
    disparaît plus lors d'un redéploiement ou d'une mise en veille.
    """
    filename = secure_filename(file_storage.filename)
    if not allowed_file(filename):
        return None

    try:
        img = Image.open(file_storage.stream)
        img.verify()
        file_storage.stream.seek(0)
        img = Image.open(file_storage.stream)
        has_alpha = img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info)
        img = img.convert("RGBA") if has_alpha else img.convert("RGB")
    except (UnidentifiedImageError, OSError, ValueError):
        return None

    img.thumbnail((MAX_LOGO_SIDE, MAX_LOGO_SIDE))

    buf = io.BytesIO()
    if has_alpha:
        img.save(buf, "PNG", optimize=True)
        mimetype = "image/png"
    else:
        img.save(buf, "WEBP", quality=85)
        mimetype = "image/webp"
    return store_image(buf.getvalue(), mimetype, kind="logo", restaurant_id=restaurant_id)


@app.route("/media/<int:image_id>")
def media(image_id):
    """
    Sert une image stockée en base. Cache navigateur d'un an : une image
    ne change jamais (un nouveau logo ou une nouvelle photo crée une
    nouvelle entrée avec un nouvel id).
    """
    image = StoredImage.query.get_or_404(image_id)
    response = Response(image.data, mimetype=image.mimetype)
    response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    return response


def tenant_or_404(slug):
    r = Restaurant.query.filter_by(slug=slug, is_active=True).first()
    if not r:
        abort(404)
    return r


def super_admin_required(view_func):
    @wraps(view_func)
    @login_required
    def wrapper(*args, **kwargs):
        if not current_user.is_super_admin:
            abort(403)
        return view_func(*args, **kwargs)
    return wrapper


def owner_required(view_func):
    @wraps(view_func)
    @login_required
    def wrapper(*args, **kwargs):
        if current_user.is_super_admin:
            return view_func(*args, **kwargs)
        if not current_user.is_owner:
            flash("Cette page est réservée au gestionnaire du restaurant.", "error")
            return redirect(url_for("admin_orders"))
        return view_func(*args, **kwargs)
    return wrapper


def next_order_status(current_status):
    if current_status in ORDER_STATUS_FLOW:
        idx = ORDER_STATUS_FLOW.index(current_status)
        if idx < len(ORDER_STATUS_FLOW) - 1:
            return ORDER_STATUS_FLOW[idx + 1]
    return current_status


def log_menu_change(restaurant_id, user, action, description):
    db.session.add(MenuChangeLog(
        restaurant_id=restaurant_id,
        user_email=user.email if user else None,
        action=action,
        description=description,
    ))


def _order_to_dict(order):
    return {
        "id": order.id,
        "status": order.status,
        "status_label": ORDER_STATUS_LABELS.get(order.status, order.status),
        "editable": order.status == EDITABLE_STATUS,
        "table_label": order.table_label,
        "notes": order.notes,
        "total": order.total,
        "items": [{"name": it.item_name, "quantity": it.quantity, "price": it.item_price}
                  for it in order.items],
    }


def _order_feed_dict(order, now=None):
    now = now or datetime.utcnow()
    return {
        "id": order.id,
        "status": order.status,
        "table_label": order.table_label or "Sans table",
        "notes": order.notes,
        "total": order.total,
        "elapsed_minutes": max(int((now - order.created_at).total_seconds() // 60), 0),
        "items": [{"name": it.item_name, "quantity": it.quantity} for it in order.items],
    }


def _is_visible_to_client(order, now=None):
    """
    Règle d'affichage d'une commande dans le panneau "Mes commandes" du
    client (la visite en cours, pas un historique) :
      - plus de CLIENT_ORDER_MAX_AGE_HOURS depuis sa création -> masquée ;
      - annulée : visible CLIENT_ORDER_CANCELLED_VISIBLE_MINUTES après
        l'annulation, puis masquée ;
      - servie : visible CLIENT_ORDER_SERVED_VISIBLE_MINUTES après le
        service (le temps de laisser un avis), puis masquée ;
      - nouvelle / en préparation / prête : toujours visible.
    Le délai se compte depuis le dernier changement de statut (updated_at).
    """
    now = now or datetime.utcnow()
    created = order.created_at or now
    if created < now - timedelta(hours=CLIENT_ORDER_MAX_AGE_HOURS):
        return False
    changed = order.updated_at or created
    if order.status == "annulee":
        return changed >= now - timedelta(minutes=CLIENT_ORDER_CANCELLED_VISIBLE_MINUTES)
    if order.status == "servie":
        return changed >= now - timedelta(minutes=CLIENT_ORDER_SERVED_VISIBLE_MINUTES)
    return True


def _parse_requested_items(restaurant):
    available_items = MenuItem.query.filter_by(
        restaurant_id=restaurant.id, is_available=True).all()
    items_by_id = {str(mi.id): mi for mi in available_items}

    items_to_order = []
    for key, value in request.form.items():
        if key.startswith("qty_"):
            item_id = key[4:]
            qty = safe_int(value, default=0)
            if qty and qty > 0 and item_id in items_by_id:
                items_to_order.append((items_by_id[item_id], min(qty, 50)))
    return items_to_order


def _dish_rating_map(restaurant_id):
    rows = (
        db.session.query(DishReview.menu_item_id,
                          db.func.avg(DishReview.rating),
                          db.func.count(DishReview.id))
        .filter(DishReview.restaurant_id == restaurant_id, DishReview.menu_item_id.isnot(None))
        .group_by(DishReview.menu_item_id)
        .all()
    )
    return {mid: {"avg": round(float(avg), 1), "count": int(count)} for mid, avg, count in rows}


def _build_item_suggestions_map(restaurant_id):
    rows = (
        db.session.query(MenuItemSuggestion.menu_item_id, MenuItem)
        .join(MenuItem, MenuItem.id == MenuItemSuggestion.suggested_item_id)
        .filter(
            MenuItemSuggestion.restaurant_id == restaurant_id,
            MenuItem.is_available.is_(True),
            MenuItemSuggestion.menu_item_id != MenuItemSuggestion.suggested_item_id,
        )
        .all()
    )
    mapping = {}
    for source_id, suggested_item in rows:
        bucket = mapping.setdefault(source_id, [])
        if len(bucket) < MAX_UPSELL_SUGGESTIONS_PER_ITEM:
            bucket.append({
                "id": suggested_item.id,
                "name": suggested_item.name,
                "price": suggested_item.price,
            })
    return mapping


# --------------------------------------------------------------------------
# Routes PUBLIQUES — vues par le client qui scanne le QR code
# --------------------------------------------------------------------------
@app.route("/m/<slug>")
def public_menu(slug):
    restaurant = tenant_or_404(slug)

    if not restaurant.is_subscription_valid:
        return render_template("subscription_expired.html", restaurant=restaurant)

    table_label = request.args.get("t")

    qr_id = request.args.get("qr", type=int)
    if qr_id:
        qr = QRCode.query.filter_by(id=qr_id, restaurant_id=restaurant.id).first()
        if qr:
            qr.scan_count = (qr.scan_count or 0) + 1
            db.session.commit()
            table_label = qr.label

    categories = (MenuCategory.query
                  .filter_by(restaurant_id=restaurant.id, is_paused=False)
                  .order_by(MenuCategory.position)
                  .all())

    ratings = _dish_rating_map(restaurant.id)
    item_suggestions = _build_item_suggestions_map(restaurant.id)

    return render_template(
        "public_menu.html",
        restaurant=restaurant,
        categories=categories,
        table_label=table_label,
        ratings=ratings,
        item_suggestions=item_suggestions,
        translations=TRANSLATIONS,
    )


def _build_qr_target_url(restaurant, qr=None, table_label=None):
    target_url = url_for("public_menu", slug=restaurant.slug, _external=True)
    params = []
    if qr is not None:
        params.append(f"qr={qr.id}")
    elif table_label:
        params.append(f"t={table_label}")
    if params:
        target_url += "?" + "&".join(params)
    return target_url


@app.route("/m/<slug>/qrcode.png")
def menu_qrcode(slug):
    restaurant = tenant_or_404(slug)
    table_label = request.args.get("t")
    target_url = _build_qr_target_url(restaurant, table_label=table_label)

    qr = qrcode.QRCode(box_size=10, border=2)
    qr.add_data(target_url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return send_file(buf, mimetype="image/png")


@app.route("/m/<slug>/qrcode.pdf")
def menu_qrcode_pdf(slug):
    restaurant = tenant_or_404(slug)
    table_label = request.args.get("t")
    target_url = _build_qr_target_url(restaurant, table_label=table_label)

    pdf_buf = make_table_tent_pdf(restaurant.name, target_url, table_label,
                                   accent_hex=restaurant.theme_colors["accent"])
    return send_file(pdf_buf, mimetype="application/pdf",
                      as_attachment=True,
                      download_name=f"menuflash_{restaurant.slug}.pdf")


@app.route("/m/<slug>/qrcodes/<int:qr_id>.png")
def table_qrcode_png(slug, qr_id):
    restaurant = tenant_or_404(slug)
    qr_record = QRCode.query.filter_by(id=qr_id, restaurant_id=restaurant.id).first_or_404()
    target_url = _build_qr_target_url(restaurant, qr=qr_record)

    qr = qrcode.QRCode(box_size=10, border=2)
    qr.add_data(target_url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return send_file(buf, mimetype="image/png")


@app.route("/m/<slug>/qrcodes/<int:qr_id>.pdf")
def table_qrcode_pdf(slug, qr_id):
    restaurant = tenant_or_404(slug)
    qr_record = QRCode.query.filter_by(id=qr_id, restaurant_id=restaurant.id).first_or_404()
    target_url = _build_qr_target_url(restaurant, qr=qr_record)

    pdf_buf = make_table_tent_pdf(restaurant.name, target_url, qr_record.label,
                                   accent_hex=restaurant.theme_colors["accent"])
    safe_label = secure_filename(qr_record.label) or f"table_{qr_record.id}"
    return send_file(pdf_buf, mimetype="application/pdf",
                      as_attachment=True,
                      download_name=f"menuflash_{restaurant.slug}_{safe_label}.pdf")


@app.route("/m/<slug>/theme-stream")
def public_theme_stream(slug):
    restaurant = tenant_or_404(slug)
    q = realtime.subscribe(restaurant.id)

    def event_stream():
        try:
            yield "retry: 3000\n\n"
            while True:
                try:
                    message = q.get(timeout=15)
                    try:
                        parsed = json.loads(message)
                    except Exception:
                        continue
                    if parsed.get("type") == "theme_changed":
                        yield realtime.format_sse(message)
                except Exception:
                    yield ": ping\n\n"
        finally:
            realtime.unsubscribe(restaurant.id, q)

    return Response(event_stream(), mimetype="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    })


# --------------------------------------------------------------------------
# Commande en ligne (côté client) — pas de paiement en ligne.
# --------------------------------------------------------------------------
@app.route("/m/<slug>/commande", methods=["POST"])
def place_order(slug):
    restaurant = tenant_or_404(slug)
    wants_json = request.headers.get("X-Requested-With") == "fetch"

    if not restaurant.is_subscription_valid:
        abort(404)

    table_label = (request.form.get("table_label") or "").strip()[:50] or None
    qr_id = request.form.get("qr_id", type=int)
    items_to_order = _parse_requested_items(restaurant)

    if not items_to_order:
        message = "Veuillez sélectionner au moins un plat avant d'envoyer la commande."
        if wants_json:
            return jsonify({"ok": False, "error": message}), 400
        flash(message, "error")
        params = {}
        if table_label:
            params["t"] = table_label
        if qr_id:
            params["qr"] = qr_id
        return redirect(url_for("public_menu", slug=slug, **params))

    order = Order(
        restaurant_id=restaurant.id,
        qr_code_id=qr_id,
        table_label=table_label,
        notes=(request.form.get("notes") or "").strip()[:300],
    )
    db.session.add(order)
    db.session.flush()

    for menu_item, qty in items_to_order:
        db.session.add(OrderItem(
            order_id=order.id,
            menu_item_id=menu_item.id,
            item_name=menu_item.name,
            item_price=menu_item.price,
            quantity=qty,
            prep_minutes=menu_item.prep_minutes or DEFAULT_PREP_MINUTES,
        ))
    db.session.commit()

    realtime.publish(restaurant.id, "new_order", _order_feed_dict(order))

    if wants_json:
        data = _order_to_dict(order)
        data["ok"] = True
        data["detail_url"] = url_for("order_status", slug=slug, order_id=order.id)
        return jsonify(data)

    return redirect(url_for("order_status", slug=slug, order_id=order.id))


@app.route("/m/<slug>/commande/<int:order_id>/ajouter", methods=["POST"])
def add_to_order(slug, order_id):
    restaurant = tenant_or_404(slug)
    order = Order.query.filter_by(id=order_id, restaurant_id=restaurant.id).first_or_404()

    if not restaurant.is_subscription_valid:
        return jsonify({"ok": False, "error": "Ce restaurant n'accepte plus de commandes."}), 404

    if order.status != EDITABLE_STATUS:
        return jsonify({
            "ok": False,
            "error": "Cette commande est déjà en préparation et ne peut plus être modifiée. "
                     "Vous pouvez envoyer les plats supplémentaires en tant que nouvelle commande.",
            "locked": True,
            "status": order.status,
            "status_label": ORDER_STATUS_LABELS.get(order.status, order.status),
        }), 409

    items_to_add = _parse_requested_items(restaurant)
    if not items_to_add:
        return jsonify({"ok": False, "error": "Veuillez sélectionner au moins un plat à ajouter."}), 400

    for menu_item, qty in items_to_add:
        db.session.add(OrderItem(
            order_id=order.id,
            menu_item_id=menu_item.id,
            item_name=menu_item.name,
            item_price=menu_item.price,
            quantity=qty,
            prep_minutes=menu_item.prep_minutes or DEFAULT_PREP_MINUTES,
        ))
    db.session.commit()

    realtime.publish(restaurant.id, "order_updated", _order_feed_dict(order))

    data = _order_to_dict(order)
    data["ok"] = True
    return jsonify(data)


@app.route("/m/<slug>/commande/<int:order_id>/annuler-client", methods=["POST"])
def cancel_order_by_client(slug, order_id):
    restaurant = tenant_or_404(slug)
    order = Order.query.filter_by(id=order_id, restaurant_id=restaurant.id).first_or_404()

    if order.status != EDITABLE_STATUS:
        return jsonify({
            "ok": False,
            "error": "Cette commande est déjà en préparation et ne peut plus être annulée.",
            "locked": True,
            "status": order.status,
            "status_label": ORDER_STATUS_LABELS.get(order.status, order.status),
        }), 409

    order.status = "annulee"
    order.cancelled_by = "client"
    db.session.commit()

    realtime.publish(restaurant.id, "order_cancelled", _order_feed_dict(order))

    return jsonify({"ok": True, "status": order.status})


@app.route("/m/<slug>/commande/<int:order_id>")
def order_status(slug, order_id):
    restaurant = tenant_or_404(slug)
    order = Order.query.filter_by(id=order_id, restaurant_id=restaurant.id).first_or_404()
    return render_template(
        "order_status.html",
        restaurant=restaurant,
        order=order,
        status_labels=ORDER_STATUS_LABELS,
        translations=TRANSLATIONS,
    )


@app.route("/m/<slug>/commande/<int:order_id>/etat.json")
def order_status_json(slug, order_id):
    restaurant = tenant_or_404(slug)
    order = Order.query.filter_by(id=order_id, restaurant_id=restaurant.id).first_or_404()
    return jsonify({
        "status": order.status,
        "status_label": ORDER_STATUS_LABELS.get(order.status, order.status),
    })


@app.route("/m/<slug>/commandes/etats.json")
def orders_bulk_status(slug):
    """
    Alimente le panneau "Mes commandes" du client. Ne renvoie QUE les
    commandes encore pertinentes pour la visite en cours (voir
    _is_visible_to_client). Renvoie aussi `expired_ids` : les numéros que
    le téléphone peut oublier définitivement (commandes terminées,
    anciennes, inexistantes ou d'un autre restaurant).
    """
    restaurant = tenant_or_404(slug)
    ids_param = request.args.get("ids", "")
    ids = []
    for part in ids_param.split(","):
        part = part.strip()
        if part.isdigit():
            ids.append(int(part))
    # On garde les numéros les plus RÉCENTS (fin de liste) si le téléphone
    # en a accumulé beaucoup.
    ids = ids[-CLIENT_ORDER_MAX_IDS:]

    if not ids:
        return jsonify({"orders": [], "expired_ids": []})

    now = datetime.utcnow()
    candidates = (Order.query
                  .filter(Order.restaurant_id == restaurant.id,
                          Order.id.in_(ids),
                          Order.created_at >= now - timedelta(hours=CLIENT_ORDER_MAX_AGE_HOURS))
                  .order_by(Order.created_at.asc())
                  .all())
    visible = [o for o in candidates if _is_visible_to_client(o, now)]
    visible_ids = {o.id for o in visible}
    expired_ids = [i for i in ids if i not in visible_ids]

    return jsonify({
        "orders": [_order_to_dict(o) for o in visible],
        "expired_ids": expired_ids,
    })


@app.route("/m/<slug>/commande/<int:order_id>/avis", methods=["POST"])
def submit_review(slug, order_id):
    restaurant = tenant_or_404(slug)
    order = Order.query.filter_by(id=order_id, restaurant_id=restaurant.id).first_or_404()

    if order.status != "servie":
        return jsonify({"ok": False, "error": "Cette commande n'est pas encore marquée comme servie."}), 409
    if order.reviewed:
        return jsonify({"ok": False, "error": "Vous avez déjà donné votre avis pour cette commande."}), 409

    any_rating = False
    for item in order.items:
        raw = request.form.get(f"rating_{item.id}")
        if not raw:
            continue
        rating = safe_int(raw)
        if rating is not None and 1 <= rating <= 5:
            db.session.add(DishReview(
                restaurant_id=restaurant.id,
                order_id=order.id,
                menu_item_id=item.menu_item_id,
                item_name=item.item_name,
                rating=rating,
            ))
            any_rating = True

    if not any_rating:
        return jsonify({"ok": False, "error": "Veuillez noter au moins un plat."}), 400

    order.comment = (request.form.get("comment") or "").strip()[:500] or None
    order.reviewed = True
    db.session.commit()

    return jsonify({"ok": True})


# --------------------------------------------------------------------------
# Back-office restaurant (authentifié) — isolation stricte par restaurant_id
# --------------------------------------------------------------------------
@app.route("/admin/login", methods=["GET", "POST"])
@limiter.limit("15 per minute;50 per hour", methods=["POST"])
def admin_login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()[:120]
        password = request.form.get("password", "")
        user = User.query.filter_by(email=email).first()

        if user and user.is_locked:
            minutes = user.lock_minutes_remaining
            flash(f"Compte temporairement verrouillé suite à plusieurs échecs de connexion. "
                  f"Réessayez dans environ {minutes} minute(s).", "error")
            log_audit("login_blocked_locked", success=False, restaurant_id=user.restaurant_id,
                       user_email=email, details=f"verrouille encore {minutes} min")
            return render_template("admin_login.html")

        if user and user.check_password(password):
            user.register_successful_login()
            db.session.commit()
            login_user(user)
            session.pop("active_restaurant_id", None)
            log_audit("login_success", restaurant_id=user.restaurant_id, user_email=email)
            if user.is_super_admin:
                return redirect(url_for("super_admin_dashboard"))
            if not user.is_owner:
                return redirect(url_for("admin_orders"))
            return redirect(url_for("admin_dashboard"))

        if user:
            user.register_failed_login()
            db.session.commit()
            just_locked = user.is_locked
            log_audit("login_failed", success=False, restaurant_id=user.restaurant_id,
                       user_email=email,
                       details=f"tentative {user.failed_login_attempts}/{MAX_FAILED_LOGIN_ATTEMPTS}"
                               + (" - compte verrouille" if just_locked else ""))
            if just_locked:
                flash(f"Trop d'échecs : compte verrouillé pendant {LOCKOUT_DURATION_MINUTES} minutes.", "error")
            else:
                flash("Identifiants incorrects.", "error")
        else:
            log_audit("login_failed", success=False, user_email=email, details="compte inconnu")
            flash("Identifiants incorrects.", "error")

    return render_template("admin_login.html")


@app.route("/admin/logout")
@login_required
def admin_logout():
    log_audit("logout", restaurant_id=current_user.restaurant_id, user_email=current_user.email)
    session.pop("active_restaurant_id", None)
    logout_user()
    return redirect(url_for("admin_login"))


@app.route("/admin")
@owner_required
def admin_dashboard():
    restaurant = active_restaurant()
    categories = (MenuCategory.query
                  .filter_by(restaurant_id=restaurant.id)
                  .order_by(MenuCategory.position)
                  .all())
    active_orders_count = Order.query.filter_by(restaurant_id=restaurant.id).filter(
        Order.status.in_(["nouvelle", "en_preparation", "prete"])
    ).count()

    suggestion_rows = MenuItemSuggestion.query.filter_by(restaurant_id=restaurant.id).all()
    item_suggestion_ids = {}
    for s in suggestion_rows:
        item_suggestion_ids.setdefault(s.menu_item_id, []).append(s.suggested_item_id)

    return render_template("admin_dashboard.html", restaurant=restaurant, categories=categories,
                            active_orders_count=active_orders_count,
                            item_suggestion_ids=item_suggestion_ids)


@app.route("/admin/categories/add", methods=["POST"])
@owner_required
def add_category():
    restaurant = active_restaurant()
    name = request.form.get("name", "").strip()[:80]
    if name:
        last = db.session.query(db.func.max(MenuCategory.position)).filter_by(
            restaurant_id=restaurant.id).scalar() or 0
        category = MenuCategory(
            restaurant_id=restaurant.id, name=name, position=last + 1)
        db.session.add(category)
        db.session.flush()
        log_menu_change(restaurant.id, current_user, "category_added",
                         f"Catégorie « {name} » créée.")
        db.session.commit()
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/categories/<int:category_id>/edit", methods=["POST"])
@owner_required
def edit_category(category_id):
    restaurant = active_restaurant()
    category = MenuCategory.query.filter_by(
        id=category_id, restaurant_id=restaurant.id).first_or_404()

    new_name = request.form.get("name", "").strip()[:80]
    if not new_name:
        flash("Le nom de la catégorie ne peut pas être vide.", "error")
        return redirect(url_for("admin_dashboard"))

    old_name = category.name
    if new_name != old_name:
        category.name = new_name
        log_menu_change(restaurant.id, current_user, "category_edited",
                         f"Catégorie « {old_name} » renommée en « {new_name} ».")
        db.session.commit()
        flash(f"Catégorie renommée en « {new_name} ».", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/categories/<int:category_id>/toggle-pause", methods=["POST"])
@owner_required
def toggle_category_pause(category_id):
    restaurant = active_restaurant()
    category = MenuCategory.query.filter_by(
        id=category_id, restaurant_id=restaurant.id).first_or_404()
    category.is_paused = not category.is_paused
    action = "category_paused" if category.is_paused else "category_resumed"
    verb = "mise en pause" if category.is_paused else "réactivée"
    log_menu_change(restaurant.id, current_user, action,
                     f"Catégorie « {category.name} » {verb}.")
    db.session.commit()
    flash(f"Catégorie « {category.name} » {verb}.", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/categories/reorder", methods=["POST"])
@owner_required
def reorder_categories():
    restaurant_id = active_restaurant_id()
    data = request.get_json(silent=True) or {}
    order = data.get("order", [])
    for index, cat_id in enumerate(order):
        MenuCategory.query.filter_by(
            id=cat_id, restaurant_id=restaurant_id
        ).update({"position": index})
    db.session.commit()
    return jsonify({"ok": True})


@app.route("/admin/items/reorder", methods=["POST"])
@owner_required
def reorder_items():
    restaurant_id = active_restaurant_id()
    data = request.get_json(silent=True) or {}
    order = data.get("order", [])
    for index, item_id in enumerate(order):
        MenuItem.query.filter_by(
            id=item_id, restaurant_id=restaurant_id
        ).update({"position": index})
    db.session.commit()
    return jsonify({"ok": True})


@app.route("/admin/items/add", methods=["POST"])
@owner_required
def add_item():
    restaurant = active_restaurant()
    category = MenuCategory.query.filter_by(
        id=request.form.get("category_id"), restaurant_id=restaurant.id
    ).first_or_404()

    name = request.form.get("name", "").strip()[:120]
    price = safe_int(request.form.get("price"), min_value=0, max_value=10_000_000)
    if not name or price is None:
        flash("Le nom et le prix du plat sont obligatoires.", "error")
        return redirect(url_for("admin_dashboard"))

    photo_url = None
    file = request.files.get("photo")
    if file and file.filename:
        photo_url = save_compressed_image(file, restaurant.slug, restaurant.id)

    last = db.session.query(db.func.max(MenuItem.position)).filter_by(
        category_id=category.id).scalar() or 0

    prep_minutes = safe_int(request.form.get("prep_minutes"), default=DEFAULT_PREP_MINUTES,
                             min_value=1, max_value=180)

    item = MenuItem(
        restaurant_id=restaurant.id,
        category_id=category.id,
        name=name,
        description=request.form.get("description", "").strip()[:300],
        price=price,
        photo_url=photo_url,
        position=last + 1,
        prep_minutes=prep_minutes,
    )
    db.session.add(item)
    log_menu_change(restaurant.id, current_user, "item_added",
                     f"Plat « {name} » ajouté à « {category.name} » ({price} {restaurant.currency}).")
    db.session.commit()
    flash("Plat ajouté.", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/items/<int:item_id>/edit", methods=["POST"])
@owner_required
def edit_item(item_id):
    restaurant = active_restaurant()
    item = MenuItem.query.filter_by(
        id=item_id, restaurant_id=restaurant.id).first_or_404()

    new_name = request.form.get("name", "").strip()[:120]
    new_price = safe_int(request.form.get("price"), min_value=0, max_value=10_000_000)
    if not new_name or new_price is None:
        flash("Le nom et le prix sont obligatoires.", "error")
        return redirect(url_for("admin_dashboard"))

    new_prep = safe_int(request.form.get("prep_minutes"), default=DEFAULT_PREP_MINUTES,
                         min_value=1, max_value=180)
    new_description = request.form.get("description", "").strip()[:300]

    changes = []
    if new_name != item.name:
        changes.append(f"nom « {item.name} » → « {new_name} »")
    if new_price != item.price:
        changes.append(f"prix {item.price} → {new_price} {restaurant.currency}")
    if new_prep != item.prep_minutes:
        changes.append(f"préparation {item.prep_minutes} → {new_prep} min")
    if new_description != (item.description or ""):
        changes.append("description modifiée")

    file = request.files.get("photo")
    if file and file.filename:
        photo_url = save_compressed_image(file, restaurant.slug, restaurant.id)
        if photo_url:
            delete_stored_image(item.photo_url)
            item.photo_url = photo_url
            changes.append("photo mise à jour")

    item.name = new_name
    item.description = new_description
    item.price = new_price
    item.prep_minutes = new_prep

    submitted_ids = set()
    for raw in request.form.getlist("suggested_item_ids"):
        sid = safe_int(raw)
        if sid is not None and sid != item.id:
            submitted_ids.add(sid)

    if submitted_ids:
        valid_ids = {mi.id for mi in MenuItem.query.filter(
            MenuItem.restaurant_id == restaurant.id, MenuItem.id.in_(submitted_ids)
        ).all()}
    else:
        valid_ids = set()

    existing_ids = {s.suggested_item_id for s in
                    MenuItemSuggestion.query.filter_by(menu_item_id=item.id).all()}

    if valid_ids != existing_ids:
        MenuItemSuggestion.query.filter_by(menu_item_id=item.id).delete()
        for sid in valid_ids:
            db.session.add(MenuItemSuggestion(
                restaurant_id=restaurant.id, menu_item_id=item.id, suggested_item_id=sid))
        changes.append("suggestions de vente additionnelle mises à jour")

    if changes:
        log_menu_change(restaurant.id, current_user, "item_edited",
                         f"Plat « {new_name} » modifié : {', '.join(changes)}.")
        db.session.commit()
        flash(f"Plat « {new_name} » mis à jour.", "success")
    else:
        db.session.commit()
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/items/<int:item_id>/toggle", methods=["POST"])
@owner_required
def toggle_item(item_id):
    restaurant_id = active_restaurant_id()
    item = MenuItem.query.filter_by(
        id=item_id, restaurant_id=restaurant_id).first_or_404()
    item.is_available = not item.is_available
    state = "disponible" if item.is_available else "indisponible"
    log_menu_change(restaurant_id, current_user, "item_toggled",
                     f"Plat « {item.name} » marqué {state}.")
    db.session.commit()
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/items/<int:item_id>/delete", methods=["POST"])
@owner_required
def delete_item(item_id):
    restaurant_id = active_restaurant_id()
    item = MenuItem.query.filter_by(
        id=item_id, restaurant_id=restaurant_id).first_or_404()
    name = item.name
    delete_stored_image(item.photo_url)
    MenuItemSuggestion.query.filter(
        (MenuItemSuggestion.menu_item_id == item.id) |
        (MenuItemSuggestion.suggested_item_id == item.id)
    ).delete(synchronize_session=False)
    log_menu_change(restaurant_id, current_user, "item_deleted",
                     f"Plat « {name} » supprimé.")
    db.session.delete(item)
    db.session.commit()
    return redirect(url_for("admin_dashboard"))


# --------------------------------------------------------------------------
# Historique des modifications du menu
# --------------------------------------------------------------------------
@app.route("/admin/historique")
@owner_required
def admin_history():
    restaurant = active_restaurant()
    logs = (MenuChangeLog.query
            .filter_by(restaurant_id=restaurant.id)
            .order_by(MenuChangeLog.created_at.desc())
            .limit(200)
            .all())
    return render_template("admin_history.html", restaurant=restaurant, logs=logs)


# --------------------------------------------------------------------------
# Apparence : sélection du thème de couleur + logo du restaurant
# --------------------------------------------------------------------------
@app.route("/admin/apparence")
@owner_required
def admin_appearance():
    restaurant = active_restaurant()
    return render_template("admin_appearance.html", restaurant=restaurant,
                            themes=THEME_PRESETS)


@app.route("/admin/apparence/save", methods=["POST"])
@owner_required
def admin_appearance_save():
    wants_json = request.headers.get("X-Requested-With") == "fetch"
    theme_key = request.form.get("theme")

    if theme_key not in THEME_PRESETS:
        message = "Thème invalide."
        if wants_json:
            return jsonify({"ok": False, "error": message}), 400
        flash(message, "error")
        return redirect(url_for("admin_appearance"))

    restaurant = active_restaurant()
    restaurant.theme = theme_key
    db.session.commit()

    realtime.publish(restaurant.id, "theme_changed", THEME_PRESETS[theme_key])

    if wants_json:
        return jsonify({"ok": True, "theme": theme_key, "colors": THEME_PRESETS[theme_key]})

    flash(f"Thème « {THEME_PRESETS[theme_key]['label']} » appliqué à votre menu public.", "success")
    return redirect(url_for("admin_appearance"))


@app.route("/admin/apparence/logo", methods=["POST"])
@owner_required
def admin_appearance_logo():
    restaurant = active_restaurant()
    file = request.files.get("logo")
    if not file or not file.filename:
        flash("Veuillez choisir une image.", "error")
        return redirect(url_for("admin_appearance"))

    logo_url = save_logo_image(file, restaurant.slug, restaurant.id)
    if not logo_url:
        flash("Format d'image non reconnu. Utilisez un fichier JPG, PNG ou WEBP.", "error")
        return redirect(url_for("admin_appearance"))

    delete_stored_image(restaurant.logo_url)
    restaurant.logo_url = logo_url
    log_menu_change(restaurant.id, current_user, "logo_updated",
                     "Logo du restaurant mis à jour.")
    db.session.commit()
    flash("Logo mis à jour avec succès. Il remplace désormais la marque "
          "« MenuFlash Mini » sur votre tableau de bord et sur votre menu public.", "success")
    return redirect(url_for("admin_appearance"))


@app.route("/admin/apparence/logo/supprimer", methods=["POST"])
@owner_required
def admin_appearance_logo_delete():
    restaurant = active_restaurant()
    delete_stored_image(restaurant.logo_url)
    restaurant.logo_url = None
    log_menu_change(restaurant.id, current_user, "logo_removed",
                     "Logo du restaurant supprimé.")
    db.session.commit()
    flash("Logo supprimé.", "success")
    return redirect(url_for("admin_appearance"))


# --------------------------------------------------------------------------
# Gestion des QR codes par table (back-office restaurant)
# --------------------------------------------------------------------------
@app.route("/admin/qrcodes")
@owner_required
def admin_qrcodes():
    restaurant = active_restaurant()
    qrcodes = (QRCode.query
               .filter_by(restaurant_id=restaurant.id)
               .order_by(QRCode.created_at)
               .all())
    return render_template("admin_qrcodes.html", restaurant=restaurant, qrcodes=qrcodes)


@app.route("/admin/qrcodes/add", methods=["POST"])
@owner_required
def admin_qrcodes_add():
    restaurant_id = active_restaurant_id()
    label = request.form.get("label", "").strip()[:50]
    if not label:
        flash("Merci de donner un nom à cette table (ex: Table 5).", "error")
        return redirect(url_for("admin_qrcodes"))
    db.session.add(QRCode(restaurant_id=restaurant_id, label=label))
    db.session.commit()
    flash(f"QR code créé pour « {label} ».", "success")
    return redirect(url_for("admin_qrcodes"))


@app.route("/admin/qrcodes/<int:qr_id>/delete", methods=["POST"])
@owner_required
def admin_qrcodes_delete(qr_id):
    restaurant_id = active_restaurant_id()
    qr = QRCode.query.filter_by(
        id=qr_id, restaurant_id=restaurant_id).first_or_404()
    db.session.delete(qr)
    db.session.commit()
    flash("QR code supprimé.", "success")
    return redirect(url_for("admin_qrcodes"))


# --------------------------------------------------------------------------
# Statistiques de ventes
# --------------------------------------------------------------------------
@app.route("/admin/statistiques")
@owner_required
def admin_statistics():
    restaurant = active_restaurant()
    stats = build_dashboard_stats(restaurant.id)
    return render_template("admin_statistics.html", restaurant=restaurant, stats=stats)


# --------------------------------------------------------------------------
# Équipe : comptes "staff" (cuisiniers) à accès limité (page Commandes)
# --------------------------------------------------------------------------
@app.route("/admin/equipe")
@owner_required
def admin_team():
    restaurant = active_restaurant()
    staff_members = User.query.filter_by(
        restaurant_id=restaurant.id, role="staff"
    ).order_by(User.created_at.asc()).all()
    return render_template("admin_team.html", restaurant=restaurant, staff_members=staff_members)


@app.route("/admin/equipe/add", methods=["POST"])
@owner_required
def admin_team_add():
    restaurant_id = active_restaurant_id()
    email = request.form.get("email", "").strip().lower()[:120]
    password = request.form.get("password", "")
    name_note = request.form.get("name_note", "").strip()[:80]

    if not email or "@" not in email or not password:
        flash("Email et mot de passe sont requis.", "error")
        return redirect(url_for("admin_team"))

    if User.query.filter_by(email=email).first():
        flash("Cet email est déjà utilisé par un autre compte.", "error")
        return redirect(url_for("admin_team"))

    if len(password) < MIN_PASSWORD_LENGTH:
        flash(f"Le mot de passe doit contenir au moins {MIN_PASSWORD_LENGTH} caractères.", "error")
        return redirect(url_for("admin_team"))

    staff = User(
        restaurant_id=restaurant_id,
        email=email,
        role="staff",
    )
    staff.set_password(password)
    db.session.add(staff)
    db.session.commit()

    label = f" ({name_note})" if name_note else ""
    log_audit("staff_account_created", restaurant_id=restaurant_id,
               user_email=current_user.email, details=f"compte cuisinier créé : {email}")
    flash(f"Compte cuisinier créé{label} : {email}", "success")
    return redirect(url_for("admin_team"))


@app.route("/admin/equipe/<int:user_id>/delete", methods=["POST"])
@owner_required
def admin_team_delete(user_id):
    restaurant_id = active_restaurant_id()
    staff = User.query.filter_by(
        id=user_id, restaurant_id=restaurant_id, role="staff"
    ).first_or_404()
    staff_email = staff.email
    db.session.delete(staff)
    db.session.commit()
    log_audit("staff_account_deleted", restaurant_id=restaurant_id,
               user_email=current_user.email, details=f"compte cuisinier supprimé : {staff_email}")
    flash("Compte supprimé.", "success")
    return redirect(url_for("admin_team"))


# --------------------------------------------------------------------------
# Tableau de bord cuisine — réception et suivi des commandes
# --------------------------------------------------------------------------
@app.route("/admin/commandes")
@login_required
def admin_orders():
    restaurant = active_restaurant()
    show_all = request.args.get("all") == "1"

    query = Order.query.filter_by(restaurant_id=restaurant.id)
    if not show_all:
        query = query.filter(Order.status.in_(["nouvelle", "en_preparation", "prete"]))
    orders = query.order_by(Order.created_at.asc()).all()

    now = datetime.utcnow()
    for order in orders:
        order.elapsed_minutes = max(int((now - order.created_at).total_seconds() // 60), 0)

    columns = {
        "nouvelle": [o for o in orders if o.status == "nouvelle"],
        "en_preparation": [o for o in orders if o.status == "en_preparation"],
        "prete": [o for o in orders if o.status == "prete"],
    }
    history = [o for o in orders if o.status in ("servie", "annulee")] if show_all else []

    return render_template(
        "admin_orders.html",
        restaurant=restaurant,
        columns=columns,
        history=history,
        show_all=show_all,
        status_labels=ORDER_STATUS_LABELS,
    )


@app.route("/admin/commandes/feed.json")
@login_required
def admin_orders_feed():
    restaurant_id = active_restaurant_id()
    orders = (Order.query.filter_by(restaurant_id=restaurant_id)
              .filter(Order.status.in_(["nouvelle", "en_preparation", "prete"]))
              .order_by(Order.created_at.asc()).all())
    now = datetime.utcnow()
    data = [_order_feed_dict(o, now) for o in orders]
    return jsonify({"orders": data, "count": len(data)})


@app.route("/admin/commandes/compteur.json")
@login_required
def admin_orders_counter():
    """
    Point de contrôle ultra-léger, interrogé périodiquement par toutes les
    pages du back-office (sauf la page Commandes, qui a déjà le temps réel
    SSE). Renvoie le nombre de commandes en cours et l'identifiant de la
    dernière commande, pour détecter une nouvelle commande sans garder de
    connexion ouverte en permanence.
    """
    restaurant_id = active_restaurant_id()
    count = Order.query.filter_by(restaurant_id=restaurant_id).filter(
        Order.status.in_(["nouvelle", "en_preparation", "prete"])
    ).count()
    latest = (Order.query.filter_by(restaurant_id=restaurant_id)
              .order_by(Order.id.desc()).first())
    return jsonify({
        "count": count,
        "latest_id": latest.id if latest else 0,
        "latest_table": (latest.table_label or "Sans table") if latest else None,
    })


@app.route("/admin/commandes/stream")
@login_required
def admin_orders_stream():
    restaurant_id = active_restaurant_id()
    q = realtime.subscribe(restaurant_id)

    def event_stream():
        import time
        started = time.time()
        try:
            yield "retry: 3000\n\n"
            # Ferme le flux après 5 minutes : le navigateur se reconnecte
            # automatiquement, ce qui libère les connexions abandonnées
            # (onglets fermés, téléphones en veille).
            while time.time() - started < 300:
                try:
                    message = q.get(timeout=15)
                    yield realtime.format_sse(message)
                except Exception:
                    yield ": ping\n\n"
        finally:
            realtime.unsubscribe(restaurant_id, q)

    return Response(event_stream(), mimetype="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    })


@app.route("/admin/commandes/<int:order_id>/avancer", methods=["POST"])
@login_required
def admin_order_advance(order_id):
    restaurant_id = active_restaurant_id()
    order = Order.query.filter_by(
        id=order_id, restaurant_id=restaurant_id).first_or_404()
    order.status = next_order_status(order.status)
    db.session.commit()
    if request.headers.get("X-Requested-With") == "fetch":
        return jsonify({"ok": True, "status": order.status})
    return redirect(url_for("admin_orders"))


@app.route("/admin/commandes/<int:order_id>/annuler", methods=["POST"])
@login_required
def admin_order_cancel(order_id):
    restaurant_id = active_restaurant_id()
    order = Order.query.filter_by(
        id=order_id, restaurant_id=restaurant_id).first_or_404()
    order.status = "annulee"
    order.cancelled_by = "restaurant"
    db.session.commit()
    if request.headers.get("X-Requested-With") == "fetch":
        return jsonify({"ok": True})
    return redirect(url_for("admin_orders"))


# --------------------------------------------------------------------------
# PANEL SUPER-ADMIN : toutes les routes sont dans super_admin.py
# (branchées en bas de ce fichier par init_super_admin).
# --------------------------------------------------------------------------


def _ensure_column(conn, table, column, ddl_type_default):
    cols = [row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})")]
    if column not in cols:
        conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type_default}")


def _ensure_schema_migrations():
    try:
        with db.engine.connect() as conn:
            _ensure_column(conn, "restaurants", "theme", f"VARCHAR(20) DEFAULT '{DEFAULT_THEME}'")
            _ensure_column(conn, "menu_categories", "is_paused", "BOOLEAN DEFAULT 0")
            _ensure_column(conn, "menu_items", "prep_minutes", f"INTEGER DEFAULT {DEFAULT_PREP_MINUTES}")
            _ensure_column(conn, "orders", "comment", "VARCHAR(500)")
            _ensure_column(conn, "orders", "reviewed", "BOOLEAN DEFAULT 0")
            _ensure_column(conn, "orders", "cancelled_by", "VARCHAR(20)")
            _ensure_column(conn, "order_items", "prep_minutes", f"INTEGER DEFAULT {DEFAULT_PREP_MINUTES}")
            _ensure_column(conn, "users", "failed_login_attempts", "INTEGER DEFAULT 0")
            _ensure_column(conn, "users", "locked_until", "DATETIME")
            _ensure_column(conn, "users", "last_login_at", "DATETIME")
            _ensure_column(conn, "users", "security_stamp", "VARCHAR(32) DEFAULT ''")
            _ensure_column(conn, "users", "email_verified", "BOOLEAN DEFAULT 0")
            _ensure_column(conn, "users", "email_verified_at", "DATETIME")
    except Exception:
        pass


def _ensure_security_defaults():
    try:
        users_without_stamp = User.query.filter(
            (User.security_stamp.is_(None)) | (User.security_stamp == "")
        ).all()
        for u in users_without_stamp:
            u.rotate_security_stamp()
        if users_without_stamp:
            db.session.commit()
    except Exception:
        db.session.rollback()

from backups import init_backups
init_backups(app, log_audit=log_audit, super_admin_required=super_admin_required)

init_super_admin(
    app,
    log_audit=log_audit,
    send_password_reset_email=send_password_reset_email,
    super_admin_required=super_admin_required,
    slugify=slugify,
    slug_is_available=slug_is_available,
    safe_int=safe_int,
    min_password_length=MIN_PASSWORD_LENGTH,
)

from two_factor import init_two_factor
init_two_factor(app, log_audit=log_audit, limiter=limiter)


if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        _ensure_schema_migrations()
        _ensure_security_defaults()
    app.run(debug=True, port=5000, host="0.0.0.0", threaded=True)


