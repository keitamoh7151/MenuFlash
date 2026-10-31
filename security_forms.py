"""
MenuFlash Mini — sécurité des formulaires (inscription, connexion, mots de passe).

Ce module se branche sur l'application avec deux lignes dans app.py :

    from security_forms import init_form_security
    init_form_security(app, limiter)

Il vérifie TOUS les formulaires sensibles AVANT que la route ne s'exécute.
Si une vérification échoue, l'utilisateur est renvoyé sur le formulaire avec
des messages d'erreur clairs, et la route n'est jamais exécutée.

Mesures appliquées
------------------
1. Politique de mot de passe (nouveaux mots de passe uniquement) :
   - 10 à 64 caractères ;
   - UNIQUEMENT des lettres non accentuées et des chiffres (aucun espace,
     aucune ponctuation, aucun accent) ;
   - au moins une majuscule, une minuscule et un chiffre ;
   - pas 4 caractères identiques à la suite, pas de suite évidente
     (123456, abcdef, azerty, qwerty) ;
   - pas un mot de passe courant (motdepasse, password, admin...) ;
   - ne contient ni l'identifiant de l'email ni le nom du restaurant ;
   - confirmation obligatoire (les deux saisies doivent être identiques) ;
   - le nouveau mot de passe doit être différent de l'actuel.
   Les comptes existants peuvent toujours se connecter avec leur ancien
   mot de passe : la règle s'applique à la création et au changement.
2. Anti-robot invisible (inscription, connexion, mot de passe oublié) :
   - champ piège caché ("honeypot") que seuls les robots remplissent ;
   - jeton horodaté et signé : refuse les formulaires envoyés trop vite
     (moins de 3 s pour l'inscription) ou trop vieux (plus de 24 h), et
     les formulaires fabriqués hors de la page.
3. Validation stricte des champs : email (format, longueur), nom du
   restaurant (pas de < > { } ` \\, pas de caractères de contrôle),
   téléphone (chiffres et + - ( ) uniquement), identifiant d'URL.
4. Connexion à temps constant : un email inconnu prend le même temps
   qu'un mauvais mot de passe (empêche de deviner quels emails existent).
5. Limitation des tentatives de changement de mot de passe (10 par heure).
6. Pages de connexion et de mot de passe jamais mises en cache par le
   navigateur (pas de retour arrière qui réaffiche un formulaire rempli).
7. Mots de passe ultra-longs refusés avant le hachage (anti-saturation).

La CSRF, la limitation des connexions, le verrouillage après 5 échecs et le
journal d'audit existants restent en place : ce module s'y ajoute.
"""
import re
import time
import unicodedata

from flask import request, flash, redirect, url_for, session
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from werkzeug.security import generate_password_hash, check_password_hash

PASSWORD_MIN_LENGTH = 10
PASSWORD_MAX_LENGTH = 64
PASSWORD_HARD_LIMIT = 256          # au-delà, refus immédiat sans hachage

SIGNUP_MIN_SECONDS = 3             # un humain met plus de 3 s à remplir l'inscription
FORM_MAX_AGE_SECONDS = 24 * 3600   # un formulaire plus vieux doit être rechargé

HONEYPOT_FIELD = "website"
TIMESTAMP_FIELD = "form_ts"

_EMAIL_RE = re.compile(r"^[a-z0-9._%+\-]+@[a-z0-9\-]+(\.[a-z0-9\-]+)*\.[a-z]{2,}$")
_PHONE_ALLOWED_RE = re.compile(r"^[0-9+\-() ]+$")
_SLUG_ALLOWED_RE = re.compile(r"^[a-z0-9\-]*$")
_FORBIDDEN_NAME_CHARS = set("<>{}`\\")

_COMMON_PASSWORDS = {
    "password", "motdepasse", "motdepass", "azerty", "azertyuiop", "qwerty",
    "qwertyuiop", "admin", "administrateur", "menuflash", "bienvenue",
    "welcome", "restaurant", "letmein", "soleil", "bonjour", "iloveyou",
    "jetaime", "secret", "changeme", "utilisateur", "user", "test",
}
_SEQUENCES = ("0123456789", "abcdefghijklmnopqrstuvwxyz", "azertyuiop", "qwertyuiop")

# Hachage factice : sert à faire "travailler" le serveur autant pour un email
# inconnu que pour un email existant (connexion à temps constant).
_DUMMY_HASH = generate_password_hash("MenuFlashDummy2026xyz")


# --------------------------------------------------------------------------
# Validations de champs
# --------------------------------------------------------------------------
def normalize_email(raw):
    return unicodedata.normalize("NFKC", (raw or "")).strip().lower()[:254]


def validate_email(raw):
    """Renvoie (email_normalisé, message_d'erreur_ou_None)."""
    email = normalize_email(raw)
    if not email:
        return email, "L'adresse email est obligatoire."
    if len(email) > 120 or ".." in email or not _EMAIL_RE.match(email):
        return email, "L'adresse email n'est pas valide."
    return email, None


def password_errors(password, email="", extra_words=()):
    """Liste des règles non respectées par un NOUVEAU mot de passe (vide = valide)."""
    password = password or ""
    if len(password) > PASSWORD_HARD_LIMIT:
        return [f"Le mot de passe doit contenir au maximum {PASSWORD_MAX_LENGTH} caractères."]

    errors = []
    if not (PASSWORD_MIN_LENGTH <= len(password) <= PASSWORD_MAX_LENGTH):
        errors.append(f"Le mot de passe doit contenir entre {PASSWORD_MIN_LENGTH} et "
                      f"{PASSWORD_MAX_LENGTH} caractères.")
    if not re.fullmatch(r"[A-Za-z0-9]*", password):
        errors.append("Le mot de passe ne doit contenir que des lettres (sans accent) et des "
                      "chiffres : aucun espace, aucune ponctuation.")
    if not re.search(r"[A-Z]", password):
        errors.append("Le mot de passe doit contenir au moins une lettre majuscule.")
    if not re.search(r"[a-z]", password):
        errors.append("Le mot de passe doit contenir au moins une lettre minuscule.")
    if not re.search(r"[0-9]", password):
        errors.append("Le mot de passe doit contenir au moins un chiffre.")
    if errors:
        return errors

    low = password.lower()
    letters_only = re.sub(r"[0-9]+", "", low)
    if low in _COMMON_PASSWORDS or letters_only in _COMMON_PASSWORDS:
        errors.append("Ce mot de passe est trop courant, choisissez-en un plus personnel.")
    if re.search(r"(.)\1{3,}", low):
        errors.append("Le mot de passe ne doit pas contenir 4 caractères identiques à la suite.")
    if any(seq[i:i + 6] in low for seq in _SEQUENCES for i in range(len(seq) - 5)):
        errors.append("Le mot de passe ne doit pas contenir de suite évidente "
                      "(123456, abcdef, azerty...).")

    words = []
    local_part = (email or "").split("@")[0]
    words.extend(re.split(r"[^a-z0-9]+", local_part.lower()))
    for w in extra_words:
        words.extend(re.split(r"[^a-z0-9]+", _ascii(w).lower()))
    if any(len(w) >= 4 and w in low for w in words):
        errors.append("Le mot de passe ne doit pas contenir votre email ni le nom du restaurant.")
    return errors


def validate_display_name(raw, label):
    """Nom affiché (restaurant, membre d'équipe). Renvoie (valeur_nettoyée, erreur)."""
    value = unicodedata.normalize("NFC", raw or "")
    value = "".join(ch for ch in value if unicodedata.category(ch)[0] != "C")
    value = re.sub(r"\s+", " ", value).strip()
    if not value:
        return value, f"{label} est obligatoire."
    if len(value) < 2 or len(value) > 120:
        return value, f"{label} doit contenir entre 2 et 120 caractères."
    if any(ch in _FORBIDDEN_NAME_CHARS for ch in value):
        return value, f"{label} contient des caractères non autorisés (< > {{ }} ` \\)."
    if not re.search(r"[^\W\d_]", value):
        return value, f"{label} doit contenir au moins une lettre."
    return value, None


def validate_phone(raw):
    """Téléphone optionnel. Renvoie (valeur_nettoyée_ou_vide, erreur)."""
    value = re.sub(r"\s+", " ", (raw or "")).strip()
    if not value:
        return "", None
    digits = re.sub(r"\D", "", value)
    if not _PHONE_ALLOWED_RE.match(value) or not (8 <= len(digits) <= 15):
        return value, "Le numéro de téléphone n'est pas valide (8 à 15 chiffres)."
    return value, None


def validate_slug(raw):
    value = (raw or "").strip().lower()
    if value and (len(value) > 60 or not _SLUG_ALLOWED_RE.match(value)):
        return value, ("L'adresse du menu ne peut contenir que des lettres minuscules, "
                       "des chiffres et des tirets.")
    return value, None


def _ascii(text):
    return unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode("ascii")


# --------------------------------------------------------------------------
# Anti-robot : champ piège + jeton horodaté signé
# --------------------------------------------------------------------------
def _serializer(secret):
    return URLSafeTimedSerializer(secret, salt="mf-form-guard")


def make_form_token(secret):
    return _serializer(secret).dumps({"t": time.time()})


def check_form_guard(form, secret, min_seconds=0):
    """Renvoie un message d'erreur si l'envoi semble automatisé, sinon None."""
    if (form.get(HONEYPOT_FIELD) or "").strip():
        return "Envoi refusé. Veuillez réessayer."
    token = form.get(TIMESTAMP_FIELD, "")
    if not token:
        return "Le formulaire est incomplet. Rechargez la page et réessayez."
    try:
        data = _serializer(secret).loads(token, max_age=FORM_MAX_AGE_SECONDS)
    except SignatureExpired:
        return "Le formulaire a expiré. Rechargez la page et réessayez."
    except BadSignature:
        return "Le formulaire est invalide. Rechargez la page et réessayez."
    try:
        elapsed = time.time() - float(data.get("t", 0))
    except (TypeError, ValueError):
        return "Le formulaire est invalide. Rechargez la page et réessayez."
    if elapsed > FORM_MAX_AGE_SECONDS:
        return "Le formulaire a expiré. Rechargez la page et réessayez."
    if elapsed < min_seconds:
        return "Envoi trop rapide. Patientez quelques secondes et réessayez."
    return None


# --------------------------------------------------------------------------
# Branchement sur l'application
# --------------------------------------------------------------------------
_AUTH_ENDPOINTS = {
    "signup", "admin_login", "forgot_password", "reset_password",
    "change_password", "admin_team", "admin_team_add",
    "super_admin_dashboard", "super_admin_clients", "super_admin_create_restaurant",
}


def init_form_security(app, limiter=None):
    secret = app.config["SECRET_KEY"]

    @app.context_processor
    def _inject_form_guard():
        return {
            "form_timestamp": lambda: make_form_token(secret),
            "PASSWORD_MIN_LENGTH": PASSWORD_MIN_LENGTH,
            "PASSWORD_MAX_LENGTH": PASSWORD_MAX_LENGTH,
        }

    @app.after_request
    def _no_store_on_auth_pages(response):
        if request.endpoint in _AUTH_ENDPOINTS:
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response.headers["Pragma"] = "no-cache"
        return response

    @app.before_request
    def _validate_sensitive_forms():
        if request.method != "POST":
            return None
        handler = _HANDLERS.get(request.endpoint)
        if handler is None:
            return None
        errors, prefill = handler(request.form, secret)
        if not errors:
            return None
        for message in errors:
            flash(message, "error")
        if prefill:
            session["mf_prefill"] = prefill
        _audit(app, request.endpoint, errors)
        target = _REDIRECT_TO.get(request.endpoint, request.endpoint)
        return redirect(url_for(target, **(request.view_args or {})))

    if limiter is not None and "change_password" in app.view_functions:
        app.view_functions["change_password"] = limiter.limit(
            "10 per hour", methods=["POST"])(app.view_functions["change_password"])


def _audit(app, endpoint, errors):
    """Trace le refus dans le journal d'audit existant (si disponible)."""
    try:
        log_audit = app.view_functions[endpoint].__globals__.get("log_audit")
        if log_audit:
            log_audit(f"form_rejected_{endpoint}"[:40], success=False,
                      user_email=normalize_email(request.form.get("email")
                                                 or request.form.get("owner_email"))[:120] or None,
                      details="; ".join(errors)[:300])
    except Exception:
        pass


def _signup(form, secret):
    prefill = {
        "restaurant_name": (form.get("restaurant_name") or "")[:120],
        "slug": (form.get("slug") or "")[:80],
        "email": (form.get("email") or "")[:120],
        "phone": (form.get("phone") or "")[:30],
    }
    guard = check_form_guard(form, secret, min_seconds=SIGNUP_MIN_SECONDS)
    if guard:
        return [guard], prefill
    errors = []
    name, err = validate_display_name(form.get("restaurant_name"), "Le nom du restaurant")
    if err:
        errors.append(err)
    _, err = validate_slug(form.get("slug"))
    if err:
        errors.append(err)
    email, err = validate_email(form.get("email"))
    if err:
        errors.append(err)
    _, err = validate_phone(form.get("phone"))
    if err:
        errors.append(err)
    password = form.get("password", "")
    errors.extend(password_errors(password, email=email, extra_words=[name]))
    if password != form.get("password_confirm", ""):
        errors.append("Les deux mots de passe ne correspondent pas.")
    return errors, prefill


def _login(form, secret):
    guard = check_form_guard(form, secret)
    if guard:
        return [guard], {"email": (form.get("email") or "")[:120]}
    password = form.get("password", "")
    if len(password) > PASSWORD_HARD_LIMIT:
        return ["Identifiants incorrects."], None
    # Temps constant : si l'email n'existe pas, on calcule quand même un hachage
    # pour que la réponse prenne le même temps qu'avec un vrai compte.
    try:
        from models import User
        if not User.query.filter_by(email=normalize_email(form.get("email"))).first():
            check_password_hash(_DUMMY_HASH, password)
    except Exception:
        pass
    return [], None


def _forgot(form, secret):
    guard = check_form_guard(form, secret)
    if guard:
        return [guard], None
    _, err = validate_email(form.get("email"))
    return ([err] if err else []), None


def _reset(form, secret):
    password = form.get("password", "")
    errors = password_errors(password)
    if password != form.get("password_confirm", ""):
        errors.append("Les deux mots de passe ne correspondent pas.")
    return errors, None


def _change(form, secret):
    new = form.get("new_password", "")
    current = form.get("current_password", "")
    email = ""
    try:
        from flask_login import current_user
        email = current_user.email if current_user.is_authenticated else ""
    except Exception:
        pass
    errors = password_errors(new, email=email)
    if new != form.get("new_password_confirm", ""):
        errors.append("Les deux nouveaux mots de passe ne correspondent pas.")
    if new and new == current:
        errors.append("Le nouveau mot de passe doit être différent de l'actuel.")
    return errors, None


def _team_add(form, secret):
    errors = []
    email, err = validate_email(form.get("email"))
    if err:
        errors.append(err)
    if (form.get("name_note") or "").strip():
        _, err = validate_display_name(form.get("name_note"), "Le nom")
        if err:
            errors.append(err)
    errors.extend(password_errors(form.get("password", ""), email=email))
    return errors, None


def _super_admin_create(form, secret):
    errors = []
    name, err = validate_display_name(form.get("name"), "Le nom du restaurant")
    if err:
        errors.append(err)
    _, err = validate_slug(form.get("slug"))
    if err:
        errors.append(err)
    email, err = validate_email(form.get("owner_email"))
    if err:
        errors.append(err)
    errors.extend(password_errors(form.get("owner_password", ""), email=email, extra_words=[name]))
    return errors, None


# Routes POST-only : en cas d'erreur, on renvoie vers la page du formulaire.
_REDIRECT_TO = {
    "admin_team_add": "admin_team",
    "super_admin_create_restaurant": "super_admin_clients",
}

_HANDLERS = {
    "signup": _signup,
    "admin_login": _login,
    "forgot_password": _forgot,
    "reset_password": _reset,
    "change_password": _change,
    "admin_team_add": _team_add,
    "super_admin_create_restaurant": _super_admin_create,
}

