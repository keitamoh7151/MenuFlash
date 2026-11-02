"""
MenuFlash Mini — Profils : gestionnaire, restaurant et membres de l'équipe.

Branchement dans app.py, TOUT EN BAS, juste après init_security_plus(...) :

    from profiles import init_profiles
    init_profiles(app, owner_required=owner_required, active_restaurant=active_restaurant,
                  log_menu_change=log_menu_change)

Ce que fait le module
---------------------
1. INSCRIPTION : prénom, nom, téléphone (WhatsApp) OBLIGATOIRE et ville du
   gestionnaire. Le reste se complète plus tard.
2. PROFIL DU RESTAURANT (Boutique > Profil du restaurant) : coordonnées du
   gestionnaire, ville / quartier, adresse, numéro affiché aux clients, type de
   cuisine, description, nombre de tables, Facebook et Instagram. L'adresse,
   le numéro, la description et les réseaux s'affichent sur le menu client.
3. LISTE « COMPLÉTEZ VOTRE PROFIL » sur le tableau de bord (logo, horaires,
   description, adresse, numéro public…), masquable.
4. ÉQUIPE : prénom (obligatoire), nom, poste (Cuisine ou Serveur) et
   téléphone optionnel. Si un téléphone est donné, l'invitation peut aussi
   être envoyée par WhatsApp en un clic.
5. SUPER-ADMIN : nom, téléphone (lien WhatsApp), ville et type de cuisine dans
   la liste des clients et dans la fiche client.

Sécurité
--------
- Tous les champs sont validés côté serveur : longueur, caractères autorisés
  (pas de < > { } ` \\), téléphone au format international, liens Facebook /
  Instagram limités à ces deux sites en https, listes fermées (poste, cuisine).
- Profil du restaurant : modifiable uniquement par le gestionnaire, CSRF,
  chaque modification est notée dans l'historique.
- Aucune donnée sensible demandée (ni date de naissance, ni pièce d'identité).
- Affichage échappé par Jinja (aucune injection HTML possible).
- Nouvelles tables créées automatiquement, incluses dans les sauvegardes,
  supprimées avec le compte ou le restaurant.
"""
import logging
import re
import unicodedata
from datetime import datetime
from urllib.parse import urlparse

from flask import render_template, request, redirect, url_for, flash, session
from flask_login import current_user, user_logged_in
from sqlalchemy import event
from werkzeug.datastructures import ImmutableMultiDict

from models import db, User, Restaurant

logger = logging.getLogger("menuflash.profiles")

JOBS = {"cuisine": "Cuisine", "serveur": "Serveur"}
CUISINES = ["Malienne", "Africaine", "Maquis", "Grillades", "Fast-food", "Pizzeria",
            "Café / salon de thé", "Pâtisserie", "Internationale", "Autre"]

_NAME_RE = re.compile(r"^[^\W\d_]+(?:[ '’\-][^\W\d_]+)*$", re.UNICODE)
_PLACE_RE = re.compile(r"^[\w\s'’\-.,()/]+$", re.UNICODE)
_FORBIDDEN = set("<>{}`\\")


# ==========================================================================
# Modèles (nouvelles tables uniquement)
# ==========================================================================
class UserProfile(db.Model):
    __tablename__ = "user_profiles"
    user_id = db.Column(db.Integer, primary_key=True)
    first_name = db.Column(db.String(50))
    last_name = db.Column(db.String(50))
    phone = db.Column(db.String(30))
    job = db.Column(db.String(20))                 # équipe : cuisine / serveur
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    @property
    def full_name(self):
        return " ".join(p for p in (self.first_name, self.last_name) if p) or None


class RestaurantProfile(db.Model):
    __tablename__ = "restaurant_profiles"
    restaurant_id = db.Column(db.Integer, primary_key=True)
    city = db.Column(db.String(60))
    district = db.Column(db.String(60))
    address = db.Column(db.String(200))
    public_phone = db.Column(db.String(30))
    cuisine = db.Column(db.String(40))
    description = db.Column(db.String(300))
    table_count = db.Column(db.Integer)
    facebook = db.Column(db.String(200))
    instagram = db.Column(db.String(200))
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ==========================================================================
# Validation
# ==========================================================================
def _clean(raw, max_len):
    value = unicodedata.normalize("NFC", raw or "")
    value = "".join(ch for ch in value if unicodedata.category(ch)[0] != "C" or ch == "\n")
    return re.sub(r"\s+", " ", value).strip()[:max_len + 1]


def validate_name(raw, label, required=True):
    value = _clean(raw, 50)
    if not value:
        return value, (f"{label} est obligatoire." if required else None)
    if len(value) < 2 or len(value) > 50 or not _NAME_RE.match(value):
        return value, f"{label} doit contenir uniquement des lettres (2 à 50 caractères)."
    return value, None


def validate_place(raw, label, max_len=60, required=False):
    value = _clean(raw, max_len)
    if not value:
        return value, (f"{label} est obligatoire." if required else None)
    if len(value) < 2 or len(value) > max_len or not _PLACE_RE.match(value):
        return value, f"{label} contient des caractères non autorisés ({max_len} caractères maximum)."
    return value, None


def validate_text(raw, label, max_len):
    value = _clean(raw, max_len)
    if len(value) > max_len:
        return value, f"{label} : {max_len} caractères maximum."
    if any(ch in _FORBIDDEN for ch in value):
        return value, f"{label} contient des caractères non autorisés (< > {{ }} ` \\)."
    return value, None


def validate_phone(raw, label="Le téléphone", required=False):
    value = re.sub(r"\s+", " ", raw or "").strip()
    if not value:
        return "", (f"{label} est obligatoire." if required else None)
    digits = re.sub(r"\D", "", value)
    if not re.fullmatch(r"\+?[0-9 ()\-]+", value) or not (8 <= len(digits) <= 15):
        return value, f"{label} n'est pas valide (8 à 15 chiffres, ex : +223 70 00 00 00)."
    return value, None


def validate_social(raw, network):
    value = (raw or "").strip()
    if not value:
        return "", None
    if len(value) > 200:
        return value, f"Le lien {network.capitalize()} est trop long."
    if not value.startswith("http"):
        handle = value.lstrip("@")
        if not re.fullmatch(r"[A-Za-z0-9._\-]{1,60}", handle):
            return value, f"Lien {network.capitalize()} invalide."
        value = f"https://www.{network}.com/{handle}"
    u = urlparse(value)
    host = (u.hostname or "").lower()
    allowed = {f"{network}.com", f"www.{network}.com", f"m.{network}.com"}
    if network == "facebook":
        allowed |= {"fb.com", "web.facebook.com"}
    if u.scheme != "https" or host not in allowed or u.username or u.password or u.port:
        return value, f"Le lien doit être une page {network.capitalize()} (https://www.{network}.com/…)."
    return value, None


def whatsapp_number(phone):
    digits = re.sub(r"\D", "", phone or "")
    if not digits:
        return None
    if len(digits) == 8:                       # numéro malien sans indicatif
        digits = "223" + digits
    return digits


# ==========================================================================
# Branchement
# ==========================================================================
def init_profiles(app, *, owner_required, active_restaurant, log_menu_change=None):
    import security_forms as sf

    with app.app_context():
        for model in (UserProfile, RestaurantProfile):
            try:
                model.__table__.create(db.engine, checkfirst=True)
            except Exception:
                logger.exception("Création de la table %s impossible", model.__tablename__)

    app.jinja_env.filters["wa_number"] = whatsapp_number
    app.jinja_env.globals["mf_jobs"] = JOBS
    app.jinja_env.globals["mf_cuisines"] = CUISINES

    def user_profile(user_id, create=False):
        p = db.session.get(UserProfile, user_id)
        if p is None and create:
            p = UserProfile(user_id=user_id)
            db.session.add(p)
        return p

    def restaurant_profile(restaurant_id, create=False):
        p = db.session.get(RestaurantProfile, restaurant_id)
        if p is None and create:
            p = RestaurantProfile(restaurant_id=restaurant_id)
            db.session.add(p)
        return p

    # ---------------- Nettoyage à la suppression ----------------
    @event.listens_for(User, "after_delete")
    def _delete_user_profile(mapper, connection, target):
        connection.execute(UserProfile.__table__.delete().where(UserProfile.user_id == target.id))

    @event.listens_for(Restaurant, "after_delete")
    def _delete_restaurant_profile(mapper, connection, target):
        connection.execute(RestaurantProfile.__table__.delete().where(
            RestaurantProfile.restaurant_id == target.id))
        # Comptes supprimés en masse avec le restaurant (super-admin) : profils orphelins.
        connection.execute(UserProfile.__table__.delete().where(
            ~UserProfile.user_id.in_(db.select(User.__table__.c.id))))

    # ======================================================================
    # 1. Inscription
    # ======================================================================
    original_signup_handler = sf._HANDLERS["signup"]

    def _signup(form, secret):
        errors, prefill = original_signup_handler(form, secret)
        prefill = dict(prefill or {})
        for key in ("first_name", "last_name", "city"):
            prefill[key] = (form.get(key) or "")[:60]
        if errors and len(errors) == 1 and not form.get("email"):
            return errors, prefill
        extra = []
        for field, label in (("first_name", "Le prénom"), ("last_name", "Le nom")):
            _, err = validate_name(form.get(field), label)
            if err:
                extra.append(err)
        _, err = validate_phone(form.get("phone"), "Le téléphone (WhatsApp)", required=True)
        if err and err not in errors:
            extra.append(err)
        _, err = validate_place(form.get("city"), "La ville", required=True)
        if err:
            extra.append(err)
        return errors + extra, prefill

    sf._HANDLERS["signup"] = _signup

    original_signup = app.view_functions["signup"]

    def signup(*args, **kwargs):
        resp = original_signup(*args, **kwargs)
        if request.method == "POST" and current_user.is_authenticated and current_user.is_owner:
            email = sf.normalize_email(request.form.get("email"))
            if current_user.email == email and db.session.get(UserProfile, current_user.id) is None:
                try:
                    up = user_profile(current_user.id, create=True)
                    up.first_name = validate_name(request.form.get("first_name"), "")[0]
                    up.last_name = validate_name(request.form.get("last_name"), "")[0]
                    up.phone = validate_phone(request.form.get("phone"))[0] or None
                    rp = restaurant_profile(current_user.restaurant_id, create=True)
                    rp.city = validate_place(request.form.get("city"), "")[0] or None
                    db.session.commit()
                except Exception:
                    db.session.rollback()
                    logger.exception("Enregistrement du profil à l'inscription impossible")
        return resp

    app.view_functions["signup"] = signup

    # ======================================================================
    # 2. Profil du restaurant
    # ======================================================================
    def admin_profile():
        restaurant = active_restaurant()
        rp = restaurant_profile(restaurant.id)
        up = user_profile(current_user.id)
        if request.method == "POST":
            f = request.form
            errors, values = [], {}
            checks = [
                ("first_name", validate_name(f.get("first_name"), "Le prénom")),
                ("last_name", validate_name(f.get("last_name"), "Le nom")),
                ("phone", validate_phone(f.get("phone"), "Votre téléphone (WhatsApp)", required=True)),
                ("city", validate_place(f.get("city"), "La ville", required=True)),
                ("district", validate_place(f.get("district"), "Le quartier")),
                ("address", validate_text(f.get("address"), "L'adresse", 200)),
                ("public_phone", validate_phone(f.get("public_phone"), "Le numéro affiché aux clients")),
                ("description", validate_text(f.get("description"), "La description", 300)),
                ("facebook", validate_social(f.get("facebook"), "facebook")),
                ("instagram", validate_social(f.get("instagram"), "instagram")),
            ]
            for key, (value, err) in checks:
                values[key] = value
                if err:
                    errors.append(err)
            cuisine = f.get("cuisine", "")
            if cuisine and cuisine not in CUISINES:
                errors.append("Type de cuisine invalide.")
            raw_tables = (f.get("table_count") or "").strip()
            tables = None
            if raw_tables:
                if not raw_tables.isdigit() or not (0 <= int(raw_tables) <= 500):
                    errors.append("Le nombre de tables doit être compris entre 0 et 500.")
                else:
                    tables = int(raw_tables)
            if errors:
                for e in errors:
                    flash(e, "error")
                return redirect(url_for("admin_profile"))
            up = user_profile(current_user.id, create=True)
            up.first_name, up.last_name = values["first_name"], values["last_name"]
            up.phone = values["phone"] or None
            rp = restaurant_profile(restaurant.id, create=True)
            for key in ("city", "district", "address", "public_phone", "description", "facebook", "instagram"):
                setattr(rp, key, values[key] or None)
            rp.cuisine = cuisine or None
            rp.table_count = tables
            if log_menu_change:
                log_menu_change(restaurant.id, current_user, "profile_updated", "Profil du restaurant mis à jour.")
            db.session.commit()
            flash("Profil enregistré.", "success")
            return redirect(url_for("admin_profile"))
        return render_template("admin_profile.html", restaurant=restaurant, rp=rp, up=up)

    app.add_url_rule("/admin/profil", "admin_profile", owner_required(admin_profile),
                     methods=["GET", "POST"])

    # ======================================================================
    # 3. Liste « Complétez votre profil »
    # ======================================================================
    def checklist(restaurant):
        rp = restaurant_profile(restaurant.id)
        up = user_profile(current_user.id)
        hours_on = False
        try:
            from service import RestaurantHours
            h = db.session.get(RestaurantHours, restaurant.id)
            hours_on = bool(h and h.enabled)
        except Exception:
            db.session.rollback()
        items = [
            ("Votre prénom et votre téléphone", bool(up and up.first_name and up.phone), url_for("admin_profile")),
            ("Le logo du restaurant", bool(restaurant.logo_url), url_for("admin_appearance")),
            ("Une courte description", bool(rp and rp.description), url_for("admin_profile")),
            ("L'adresse et le quartier", bool(rp and rp.address and rp.district), url_for("admin_profile")),
            ("Le numéro affiché aux clients", bool(rp and rp.public_phone), url_for("admin_profile")),
            ("Les horaires d'ouverture", hours_on, url_for("admin_hours")),
        ]
        return {"items": items, "done": sum(1 for i in items if i[1]), "total": len(items)}

    # ======================================================================
    # 4. Équipe : prénom, nom, poste, téléphone
    # ======================================================================
    def _team_add_form(form, _secret):
        errors = []
        _, err = sf.validate_email(form.get("email"))
        if err:
            errors.append(err)
        _, err = validate_name(form.get("first_name"), "Le prénom")
        if err:
            errors.append(err)
        _, err = validate_name(form.get("last_name"), "Le nom", required=False)
        if err:
            errors.append(err)
        if form.get("job") not in JOBS:
            errors.append("Choisissez le poste : Cuisine ou Serveur.")
        _, err = validate_phone(form.get("phone"))
        if err:
            errors.append(err)
        return errors, None

    sf._HANDLERS["admin_team_add"] = _team_add_form

    @app.before_request
    def _team_name_note():
        """Transmet « Prénom Nom » au module d'invitation (email « Bonjour Fatou »)."""
        if request.endpoint != "admin_team_add" or request.method != "POST":
            return None
        first = validate_name(request.form.get("first_name"), "")[0]
        last = validate_name(request.form.get("last_name"), "", required=False)[0]
        data = request.form.to_dict(flat=False)
        data["name_note"] = [" ".join(p for p in (first, last) if p)]
        request.form = ImmutableMultiDict(data)
        return None

    original_team_add = app.view_functions["admin_team_add"]

    def admin_team_add(*args, **kwargs):
        resp = original_team_add(*args, **kwargs)
        email = sf.normalize_email(request.form.get("email"))
        restaurant = active_restaurant()
        user = User.query.filter_by(email=email, role="staff").first()
        if user and restaurant and user.restaurant_id == restaurant.id and db.session.get(UserProfile, user.id) is None:
            try:
                up = user_profile(user.id, create=True)
                up.first_name = validate_name(request.form.get("first_name"), "")[0] or None
                up.last_name = validate_name(request.form.get("last_name"), "", required=False)[0] or None
                up.job = request.form.get("job") if request.form.get("job") in JOBS else None
                up.phone = validate_phone(request.form.get("phone"))[0] or None
                db.session.commit()
                invite = session.get("mf_last_invite")
                if invite and invite.get("email") == email:
                    invite.update({"name": up.full_name, "phone": up.phone, "restaurant": restaurant.name})
                    session["mf_last_invite"] = invite
            except Exception:
                db.session.rollback()
                logger.exception("Enregistrement du profil d'équipe impossible")
        return resp

    app.view_functions["admin_team_add"] = admin_team_add

    # ======================================================================
    # Données pour les pages
    # ======================================================================
    @app.context_processor
    def _inject_profiles():
        try:
            endpoint = request.endpoint or ""
            if endpoint == "public_menu":
                slug = (request.view_args or {}).get("slug")
                r = Restaurant.query.filter_by(slug=slug, is_active=True).first() if slug else None
                rp = restaurant_profile(r.id) if r else None
                if rp:
                    return {"mf_info": rp}
                return {}
            if not current_user.is_authenticated:
                return {}
            if endpoint == "admin_dashboard" and current_user.is_owner and not current_user.is_super_admin:
                r = active_restaurant()
                return {"mf_checklist": checklist(r)} if r else {}
            if endpoint == "admin_team":
                r = active_restaurant()
                ids = [u.id for u in User.query.filter_by(restaurant_id=r.id, role="staff")] if r else []
                rows = UserProfile.query.filter(UserProfile.user_id.in_(ids or [0])).all()
                return {"mf_staff_profiles": {p.user_id: p for p in rows}}
            if current_user.is_super_admin and endpoint in ("super_admin_clients", "super_admin_restaurant_detail"):
                return {"mf_user_profiles": {p.user_id: p for p in UserProfile.query.all()},
                        "mf_restaurant_profiles": {p.restaurant_id: p for p in RestaurantProfile.query.all()}}
        except Exception:
            db.session.rollback()
        return {}
