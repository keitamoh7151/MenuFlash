"""
MenuFlash Mini — Composition des plats (ingrédients, allergènes, régimes).

Branchement dans app.py, JUSTE APRÈS la définition de `owner_required`
(n'importe où après, par exemple juste avant `from backups import init_backups`) :

    from composition import init_composition
    init_composition(app, owner_required=owner_required,
                     active_restaurant=active_restaurant,
                     log_menu_change=log_menu_change)

Ce que fait le module
---------------------
- Le restaurateur renseigne, pour chaque plat, la liste des ingrédients,
  les allergènes présents (liste fermée) et des mentions (végétarien,
  halal, épicé…), depuis un bouton « Composition » ajouté à côté du
  crayon de chaque plat dans le tableau de bord.
- Le client voit cette composition sous chaque plat du menu public, en
  français ou en anglais selon la langue choisie.

Sécurité
--------
- Écriture réservée au gestionnaire (owner_required), CSRF obligatoire,
  et le plat doit appartenir au restaurant actif (isolation stricte).
- Ingrédients : 30 au maximum, 60 caractères chacun, caractères autorisés
  limités (lettres, chiffres, espaces et ' ’ - . ( ) / % &), doublons
  supprimés. Tout le reste est refusé avec un message clair.
- Allergènes et mentions : uniquement des valeurs d'une liste fermée.
- Affichage : données passées en JSON échappé (|tojson) et insérées dans
  la page uniquement via textContent : aucune injection HTML possible.
- Chaque modification est tracée dans l'historique du menu.
- Les compositions sont automatiquement incluses dans les sauvegardes et
  supprimées avec le plat.
"""
import json
import re
import unicodedata
from datetime import datetime

from flask import request, redirect, url_for, flash, abort
from flask_login import current_user
from sqlalchemy import event

from models import db, MenuItem, Restaurant

MAX_INGREDIENTS = 30
MAX_INGREDIENT_LEN = 60
_ALLOWED_CHARS = re.compile(r"^[\w\s'’\-.()/%&]+$", re.UNICODE)

# Les 14 allergènes réglementaires (+ libellés FR/EN). Liste FERMÉE.
ALLERGENS = {
    "gluten":      ("Gluten", "Gluten"),
    "crustaces":   ("Crustacés", "Crustaceans"),
    "oeufs":       ("Œufs", "Eggs"),
    "poisson":     ("Poisson", "Fish"),
    "arachides":   ("Arachides", "Peanuts"),
    "soja":        ("Soja", "Soy"),
    "lait":        ("Lait", "Milk"),
    "fruits_coque": ("Fruits à coque", "Tree nuts"),
    "celeri":      ("Céleri", "Celery"),
    "moutarde":    ("Moutarde", "Mustard"),
    "sesame":      ("Sésame", "Sesame"),
    "sulfites":    ("Sulfites", "Sulphites"),
    "lupin":       ("Lupin", "Lupin"),
    "mollusques":  ("Mollusques", "Molluscs"),
}

# Mentions affichées en vert. Liste FERMÉE.
TAGS = {
    "vegetarien":  ("Végétarien", "Vegetarian"),
    "vegan":       ("Végan", "Vegan"),
    "sans_gluten": ("Sans gluten", "Gluten-free"),
    "halal":       ("Halal", "Halal"),
    "epice":       ("Épicé", "Spicy"),
    "fait_maison": ("Fait maison", "Homemade"),
}

LABELS = {
    "fr": {"composition": "Composition", "contains": "Contient", "allergens_none": ""},
    "en": {"composition": "Ingredients", "contains": "Contains", "allergens_none": ""},
}


class CompositionError(ValueError):
    """Erreur de saisie, avec un message lisible par le restaurateur."""


# ==========================================================================
# Modèle (nouvelle table, aucune table existante n'est modifiée)
# ==========================================================================
class MenuItemComposition(db.Model):
    __tablename__ = "menu_item_compositions"
    menu_item_id = db.Column(db.Integer, db.ForeignKey("menu_items.id"), primary_key=True)
    restaurant_id = db.Column(db.Integer, db.ForeignKey("restaurants.id"), nullable=False, index=True)
    ingredients = db.Column(db.Text, nullable=False, default="[]")      # JSON : liste de textes
    allergens = db.Column(db.String(300), nullable=False, default="[]")  # JSON : clés de ALLERGENS
    tags = db.Column(db.String(200), nullable=False, default="[]")       # JSON : clés de TAGS
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def as_dict(self):
        return {
            "ingredients": _loads(self.ingredients),
            "allergens": [a for a in _loads(self.allergens) if a in ALLERGENS],
            "tags": [t for t in _loads(self.tags) if t in TAGS],
        }

    @property
    def is_empty(self):
        d = self.as_dict()
        return not (d["ingredients"] or d["allergens"] or d["tags"])


def _loads(raw):
    try:
        value = json.loads(raw or "[]")
        return [str(v) for v in value] if isinstance(value, list) else []
    except (ValueError, TypeError):
        return []


# ==========================================================================
# Validation
# ==========================================================================
def clean_ingredients(raw):
    """Découpe la saisie (une ligne ou une virgule par ingrédient) et la valide."""
    raw = unicodedata.normalize("NFC", raw or "")
    parts = re.split(r"[\n\r,;]+", raw)
    result, seen = [], set()
    for part in parts:
        text = re.sub(r"\s+", " ", part).strip(" .-")
        if not text:
            continue
        if len(text) > MAX_INGREDIENT_LEN:
            raise CompositionError(f"« {text[:25]}… » est trop long ({MAX_INGREDIENT_LEN} caractères maximum).")
        if not _ALLOWED_CHARS.match(text) or any(ord(ch) < 32 for ch in text):
            raise CompositionError(f"« {text[:30]} » contient des caractères non autorisés.")
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(text)
    if len(result) > MAX_INGREDIENTS:
        raise CompositionError(f"{MAX_INGREDIENTS} ingrédients au maximum.")
    return result


def clean_choices(values, allowed):
    """Ne garde que les valeurs de la liste fermée, sans doublon, dans l'ordre de la liste."""
    chosen = {v for v in values if v in allowed}
    return [k for k in allowed if k in chosen]


def compositions_for_restaurant(restaurant_id):
    """{menu_item_id: {"ingredients": [...], "allergens": [...], "tags": [...]}}"""
    rows = MenuItemComposition.query.filter_by(restaurant_id=restaurant_id).all()
    return {r.menu_item_id: r.as_dict() for r in rows if not r.is_empty}


def _label_dict():
    return {
        "allergens": {k: {"fr": v[0], "en": v[1]} for k, v in ALLERGENS.items()},
        "tags": {k: {"fr": v[0], "en": v[1]} for k, v in TAGS.items()},
        "ui": LABELS,
    }


# ==========================================================================
# Branchement
# ==========================================================================
def init_composition(app, *, owner_required, active_restaurant, log_menu_change):

    with app.app_context():
        MenuItemComposition.__table__.create(db.engine, checkfirst=True)

    @event.listens_for(MenuItem, "after_delete")
    def _delete_composition(mapper, connection, target):
        connection.execute(MenuItemComposition.__table__.delete().where(
            MenuItemComposition.__table__.c.menu_item_id == target.id))

    @app.route("/admin/items/<int:item_id>/composition", methods=["POST"])
    @owner_required
    def edit_item_composition(item_id):
        restaurant = active_restaurant()
        if restaurant is None:
            abort(403)
        item = MenuItem.query.filter_by(id=item_id, restaurant_id=restaurant.id).first_or_404()

        try:
            ingredients = clean_ingredients(request.form.get("ingredients", "")[:5000])
        except CompositionError as exc:
            flash(f"Composition de « {item.name} » non enregistrée : {exc}", "error")
            return redirect(url_for("admin_dashboard"))
        allergens = clean_choices(request.form.getlist("allergens"), ALLERGENS)
        tags = clean_choices(request.form.getlist("tags"), TAGS)

        comp = MenuItemComposition.query.get(item.id)
        before = comp.as_dict() if comp else {"ingredients": [], "allergens": [], "tags": []}
        after = {"ingredients": ingredients, "allergens": allergens, "tags": tags}
        if before == after:
            flash("Aucune modification.", "success")
            return redirect(url_for("admin_dashboard"))

        if comp is None:
            comp = MenuItemComposition(menu_item_id=item.id, restaurant_id=restaurant.id)
            db.session.add(comp)
        comp.ingredients = json.dumps(ingredients, ensure_ascii=False)
        comp.allergens = json.dumps(allergens)
        comp.tags = json.dumps(tags)

        details = []
        if before["ingredients"] != ingredients:
            details.append(f"{len(ingredients)} ingrédient(s)")
        if before["allergens"] != allergens:
            details.append("allergènes : " + (", ".join(ALLERGENS[a][0] for a in allergens) or "aucun"))
        if before["tags"] != tags:
            details.append("mentions : " + (", ".join(TAGS[t][0] for t in tags) or "aucune"))
        log_menu_change(restaurant.id, current_user, "item_edited",
                        f"Composition de « {item.name} » mise à jour : {' ; '.join(details)}."[:300])
        db.session.commit()
        flash(f"Composition de « {item.name} » enregistrée.", "success")
        return redirect(url_for("admin_dashboard"))

    @app.context_processor
    def _inject_composition():
        endpoint = request.endpoint or ""
        try:
            if endpoint == "admin_dashboard" and current_user.is_authenticated:
                restaurant = active_restaurant()
                if restaurant:
                    return {"mf_composition": {
                        "mode": "admin",
                        "items": compositions_for_restaurant(restaurant.id),
                        "labels": _label_dict(),
                        "max_ingredients": MAX_INGREDIENTS,
                    }}
            if endpoint == "public_menu":
                slug = (request.view_args or {}).get("slug")
                restaurant = Restaurant.query.filter_by(slug=slug, is_active=True).first() if slug else None
                if restaurant:
                    return {"mf_composition": {
                        "mode": "public",
                        "items": compositions_for_restaurant(restaurant.id),
                        "labels": _label_dict(),
                    }}
        except Exception:
            db.session.rollback()
        return {}

    app.jinja_env.globals["mf_allergen_choices"] = [(k, v[0]) for k, v in ALLERGENS.items()]
    app.jinja_env.globals["mf_tag_choices"] = [(k, v[0]) for k, v in TAGS.items()]
