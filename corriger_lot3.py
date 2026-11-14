"""
MenuFlash — Lot de corrections n°3 (deuxième passe de l'audit, 14/11/2026).

  LOT3-01  Un numéro géant dans une adresse (/media/99999999999999999999, images de QR, 39 routes) ou dans
           un formulaire faisait déborder la base de données : erreur 500 enregistrée avec sa trace, déclenchable
           SANS COMPTE. Désormais : page « introuvable » (adresses) ou réponse 400 (formulaires).
  LOT3-02  POST /admin/items/add sans être connecté : erreur 500 (le contrôle des limites de formule
           passait avant le contrôle de connexion). Même défaut corrigé pour l'ajout d'un employé.
  LOT3-03  Listes de réordonnancement du menu malformées : erreur 500 (gérant connecté seulement).
  LOT3-04  Mode rush : un plat d'une catégorie EN PAUSE restait commandable (la pause n'agissait que sur
           l'affichage). Désormais refusé côté serveur, y compris pour « ajouter à une commande ».
  LOT3-05  Pages privées (tableau de bord, journal de sécurité, exports CSV, suivi de commande…) sans
           Cache-Control : le bouton « Retour » du navigateur pouvait les réafficher après la déconnexion
           (poste ou tablette partagés). Désormais « private, no-store ».
  LOT3-06  « Mot de passe oublié » : 316 ms si le compte existe, 14 ms sinon (l'envoi de l'email se faisait
           pendant la requête) : on pouvait deviner quelles adresses ont un compte ; et un email par demande,
           sans limite par compte. Désormais : envoi en arrière-plan, 3 emails par heure et par compte.
  LOT3-07  Commande à emporter : l'email saisi par un visiteur recevait une confirmation sans limite (harcèlement
           d'une adresse, épuisement du quota quotidien du service d'envoi, partagé avec les réinitialisations).
           Désormais : 6 emails par jour et par adresse, 150 par jour pour la plateforme
           (variable EMAIL_CUSTOMER_DAILY_CAP).
  LOT3-08  « redirect(request.referrer) » (5 endroits) limité aux pages de ce site ; seules les options
           attendues sont reprises dans l'adresse de la configuration de la 2FA.

Chaque correction est vérifiée séparément : si ses repères ne sont pas trouvés, ELLE SEULE est ignorée (et
signalée) ; les autres sont appliquées.

UTILISATION (à la racine du projet) :  python corriger_lot3.py
Copies de sécurité : *.avant-lot3
Contrôle :  python verifier_lot3.py
"""
import os
import re
import shutil
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
SUFFIX = ".avant-lot3"

# ==========================================================================
# Modules créés
# ==========================================================================
EMAIL_BUDGET = '''"""
Plafonds d'envoi d'emails (audit lot 3) : un visiteur anonyme ne doit pas pouvoir faire envoyer
des emails en rafale à une adresse (harcèlement) ni épuiser le quota quotidien du service
d'envoi (300 par jour sur l'offre gratuite de Brevo), qui sert aussi aux réinitialisations de
mot de passe et aux invitations.

allow(nom, clé, limite, fenêtre) -> True si l'envoi est permis (et le compte). Utilise Redis
(shared_counters.py, MF-08) s'il est installé, sinon la mémoire du processus.
L'adresse n'est jamais conservée en clair : seule une empreinte sert de clé.
"""
import hashlib
import threading
import time

_lock = threading.Lock()
_memory = {}


def _digest(key):
    return hashlib.sha256(str(key).strip().lower().encode("utf-8")).hexdigest()[:24]


def allow(name, key, limit, window_seconds):
    digest = _digest(key)
    try:
        import shared_counters
        return int(shared_counters.hit(f"email-{name}", digest, window_seconds)) <= limit
    except Exception:
        pass
    now = time.monotonic()
    with _lock:
        if len(_memory) > 5000:
            for k in [k for k, (start, _) in _memory.items() if now - start > 86400]:
                _memory.pop(k, None)
        start, count = _memory.get((name, digest), (now, 0))
        if now - start > window_seconds:
            start, count = now, 0
        count += 1
        _memory[(name, digest)] = (start, count)
        return count <= limit
'''

SAFE_REDIRECT = '''"""
Retour à la page précédente sans redirection ouverte (audit lot 3).

Plusieurs écrans faisaient « redirect(request.referrer or ...) ». L'en-tête Referer vient du
navigateur : on ne redirige que vers une page de CE site, sinon vers la page par défaut.
"""
from urllib.parse import urlsplit

from flask import redirect, request


def safe_back(default):
    ref = request.referrer or ""
    try:
        parts = urlsplit(ref)
        if parts.scheme in ("http", "https") and parts.netloc == request.host:
            return redirect(ref)
    except ValueError:
        pass
    return redirect(default)
'''

# ==========================================================================
# LOT3-01 — numéros géants dans les adresses et les formulaires
# ==========================================================================
BIGINT = {
"app.py": [
('''app = Flask(__name__)
''', '''app = Flask(__name__)

# Audit lot 3 : un numéro géant dans une adresse (ex. /media/99999999999999999999) faisait déborder la
# base de données (erreur 500, enregistrée avec sa trace). Au-delà de 2 147 483 647 : page « introuvable ».
from werkzeug.routing import IntegerConverter as _IntegerConverter, ValidationError as _ValidationError


class _BoundedIntConverter(_IntegerConverter):
    def to_python(self, value):
        number = int(value)
        if number > 2147483647:
            raise _ValidationError()
        return number


app.url_map.converters["int"] = _BoundedIntConverter
'''),
('''if __name__ == "__main__":''', '''# Audit lot 3 : valeur numérique hors limites envoyée par un formulaire -> réponse 400 propre.
import sqlalchemy.exc as _sa_exc


@app.errorhandler(OverflowError)
@app.errorhandler(_sa_exc.DataError)
def _out_of_range_value(error):
    db.session.rollback()
    wants_json = (request.headers.get("X-Requested-With") == "fetch" or request.is_json
                  or "application/json" in (request.headers.get("Accept") or ""))
    if wants_json:
        return jsonify(ok=False, error="Valeur invalide."), 400
    return "Valeur invalide.", 400


if __name__ == "__main__":'''),
],
}

# ==========================================================================
# LOT3-02 — add_item / admin_team_add : erreur 500 en anonyme
# ==========================================================================
PLANS_GUARD = {
"plans.py": [
('''        def admin_team_add(*args, **kwargs):
            restaurant = active_restaurant()
            limit = plan_limit(restaurant, "max_staff")''',
'''        def admin_team_add(*args, **kwargs):
            restaurant = active_restaurant()
            if restaurant is None or not current_user.is_authenticated:      # audit lot 3 : visiteur non connecté
                return original_staff_add(*args, **kwargs)
            limit = plan_limit(restaurant, "max_staff")'''),
('''        def add_item(*args, **kwargs):
            restaurant = active_restaurant()
            limit = plan_limit(restaurant, "max_menu_items")''',
'''        def add_item(*args, **kwargs):
            restaurant = active_restaurant()
            if restaurant is None or not current_user.is_authenticated:      # audit lot 3 : visiteur non connecté
                return original_add_item(*args, **kwargs)
            limit = plan_limit(restaurant, "max_menu_items")'''),
],
}

# ==========================================================================
# LOT3-03 — listes « reorder » malformées
# ==========================================================================
REORDER = {
"app.py": [
('''def get_client_ip():''', '''def _clean_id_list(raw, limit=500):
    """Audit lot 3 : liste d'identifiants entiers valides (1 à 2 147 483 647), sans doublon, 500 au maximum."""
    out = []
    if not isinstance(raw, list):
        return out
    for value in raw[:limit]:
        if isinstance(value, bool) or not isinstance(value, (int, str)):
            continue
        try:
            number = int(value)
        except (TypeError, ValueError):
            continue
        if 0 < number <= 2147483647 and number not in out:
            out.append(number)
    return out


def get_client_ip():'''),
]
}
REORDER_REPLACE_ALL = ('''    data = request.get_json(silent=True) or {}
    order = data.get("order", [])
''', '''    data = request.get_json(silent=True)
    order = _clean_id_list(data.get("order") if isinstance(data, dict) else None)    # audit lot 3
''', 2)

# ==========================================================================
# LOT3-04 — mode rush appliqué côté serveur
# ==========================================================================
RUSH = {
"app.py": [
('''    available_items = MenuItem.query.filter_by(
        restaurant_id=restaurant.id, is_available=True).all()''',
'''    # Audit lot 3 : un plat dont la catégorie est EN PAUSE (mode rush) n'est plus commandable, même par
    # une page restée ouverte ou une requête fabriquée (la pause n'agissait que sur l'affichage).
    available_items = (MenuItem.query
                       .join(MenuCategory, MenuCategory.id == MenuItem.category_id)
                       .filter(MenuItem.restaurant_id == restaurant.id,
                               MenuItem.is_available.is_(True),
                               MenuCategory.is_paused.isnot(True))
                       .all())'''),
],
"order_edit.py": [
('''        if menu_item is None or not menu_item.is_available:
            return "Ce plat n'est plus disponible : vous ne pouvez plus en ajouter."''',
'''        if menu_item is not None:                                       # audit lot 3 : catégorie en pause
            from models import MenuCategory
            _category = db.session.get(MenuCategory, menu_item.category_id)
            if _category is not None and _category.is_paused:
                return "Cette catégorie est momentanément indisponible."
        if menu_item is None or not menu_item.is_available:
            return "Ce plat n'est plus disponible : vous ne pouvez plus en ajouter."'''),
],
}

# ==========================================================================
# LOT3-05 — pages privées jamais mises en cache
# ==========================================================================
CACHE = {
"app.py": [
('''if __name__ == "__main__":''', '''# Audit lot 3 : une page réservée à un compte connecté (ou le suivi d'une commande) n'est jamais conservée
# par le navigateur : après « Déconnexion », le bouton « Retour » ne réaffiche plus de données (poste partagé).
_NO_STORE_ENDPOINTS = {"order_status", "order_status_json", "orders_bulk_status", "takeaway_public"}


@app.after_request
def _no_store_for_private_pages(response):
    try:
        private = current_user.is_authenticated or request.endpoint in _NO_STORE_ENDPOINTS
        if private and request.endpoint not in ("static", "media") and "Cache-Control" not in response.headers:
            response.headers["Cache-Control"] = "private, no-store"
    except Exception:
        pass
    return response


if __name__ == "__main__":'''),
],
}

# ==========================================================================
# LOT3-06 — « Mot de passe oublié » : temps de réponse + emails en rafale
# ==========================================================================
FORGOT = {
"app.py": [
('''@app.route("/admin/mot-de-passe-oublie", methods=["GET", "POST"])''',
'''def _send_reset_email_in_background(user):
    """Audit lot 3 : l'email part en arrière-plan. Avant, la réponse prenait ~300 ms de plus quand le compte
    existait (appel au service d'envoi) : on pouvait deviner quelles adresses ont un compte."""
    import threading
    token = generate_security_token(user, salt="pwd-reset")
    link = url_for("reset_password", token=token, _external=True)
    to = user.email
    body = (f"Bonjour,\\n\\nVous avez demandé à réinitialiser votre mot de passe. "
            f"Ce lien est valable 1 heure et ne peut être utilisé qu'une seule "
            f"fois :\\n{link}\\n\\nSi vous n'êtes pas à l'origine de cette demande, "
            f"ignorez ce message : votre mot de passe actuel reste inchangé.\\n\\n"
            f"L'équipe MenuFlash")

    def run():
        try:
            send_transactional_email(app, to, "Réinitialisation de votre mot de passe — MenuFlash", body)
        except Exception:
            logger.exception("Email de réinitialisation non envoyé")
    threading.Thread(target=run, daemon=True).start()


@app.route("/admin/mot-de-passe-oublie", methods=["GET", "POST"])'''),
('''        send_password_reset_email(user)
        log_audit("password_reset_requested", restaurant_id=user.restaurant_id, user_email=email)''',
'''        import email_budget
        if email_budget.allow("forgot", email, 3, 3600):          # audit lot 3 : 3 emails par heure et par compte
            _send_reset_email_in_background(user)
        log_audit("password_reset_requested", restaurant_id=user.restaurant_id, user_email=email)'''),
],
}

# ==========================================================================
# LOT3-07 — emails de commande à emporter : plafonds
# ==========================================================================
TAKEAWAY_MAIL = {
"takeaway.py": [
('''def resolve_table(restaurant_id, qr_id=None, label=None):''',
'''def _customer_email_allowed(address):
    """Audit lot 3 : l'adresse est saisie par un visiteur anonyme. Au plus 6 emails par jour et par adresse
    (confirmation + « prête » de 3 commandes), et EMAIL_CUSTOMER_DAILY_CAP par jour pour toute la plateforme
    (150 par défaut) : le quota du service d'envoi reste disponible pour les réinitialisations de mot de passe."""
    import os as _os
    import email_budget
    try:
        cap = max(10, int(_os.environ.get("EMAIL_CUSTOMER_DAILY_CAP", "150")))
    except ValueError:
        cap = 150
    return (email_budget.allow("cust", address, 6, 86400)
            and email_budget.allow("cust-all", "plateforme", cap, 86400))


def resolve_table(restaurant_id, qr_id=None, label=None):'''),
('''        if data["email"]:
            try:
                link = url_for("order_status"''',
'''        if data["email"] and _customer_email_allowed(data["email"]):
            try:
                link = url_for("order_status"'''),
('''if t and o and o.status == "prete" and t.customer_email and not t.ready_sent:''',
'''if t and o and o.status == "prete" and t.customer_email and not t.ready_sent \\
                    and _customer_email_allowed(t.customer_email):'''),
],
}

# ==========================================================================
# LOT3-08 — redirections « retour » et paramètres de la 2FA
# ==========================================================================
SAFE_BACK = {
"app.py": [
('''import realtime
''', '''import realtime
from safe_redirect import safe_back                 # audit lot 3
'''),
('''    return redirect(request.referrer or url_for("admin_dashboard"))''', '''    return safe_back(url_for("admin_dashboard"))'''),
],
"sa_controls.py": [
('''from mailer import send_transactional_email
''', '''from mailer import send_transactional_email
from safe_redirect import safe_back                 # audit lot 3
'''),
('''        back = redirect(request.referrer or url_for("super_admin_clients"))''', '''        back = safe_back(url_for("super_admin_clients"))'''),
('''                return redirect(request.referrer or url_for("admin_dashboard"))''', '''                return safe_back(url_for("admin_dashboard"))'''),
],
"takeaway.py": [
('''from mailer import send_transactional_email
''', '''from mailer import send_transactional_email
from safe_redirect import safe_back                 # audit lot 3
'''),
('''    return redirect(request.referrer or "/")''', '''    return safe_back("/")'''),
],
"two_factor.py": [
('''                return redirect(url_for("two_factor_setup", **request.args))''',
'''                return redirect(url_for("two_factor_setup",      # audit lot 3 : seul « reconfigurer » est repris
                                        **{k: v for k, v in request.args.items() if k == "reconfigurer"}))'''),
],
}

CORRECTIONS = [
    ("LOT3-01", "Numéros géants : plus d'erreur 500 (adresses et formulaires)", BIGINT, []),
    ("LOT3-02", "Ajout d'un plat / d'un employé : plus d'erreur 500 hors connexion", PLANS_GUARD, []),
    ("LOT3-03", "Listes de réordonnancement malformées", REORDER, [("app.py",) + REORDER_REPLACE_ALL]),
    ("LOT3-04", "Mode rush appliqué côté serveur", RUSH, []),
    ("LOT3-05", "Pages privées jamais mises en cache", CACHE, []),
    ("LOT3-06", "Mot de passe oublié : même temps de réponse, 3 emails/heure", FORGOT, []),
    ("LOT3-07", "Emails de commande à emporter : plafonds par adresse et par jour", TAKEAWAY_MAIL, []),
    ("LOT3-08", "Redirections « retour » limitées à ce site ; paramètres 2FA filtrés", SAFE_BACK, []),
]
NEW_FILES = {"LOT3-06": {"email_budget.py": EMAIL_BUDGET}, "LOT3-07": {"email_budget.py": EMAIL_BUDGET},
             "LOT3-08": {"safe_redirect.py": SAFE_REDIRECT}}


def _read(rel, texts):
    if rel not in texts:
        path = os.path.join(ROOT, rel)
        texts[rel] = open(path, encoding="utf-8").read() if os.path.exists(path) else None
    return texts[rel]


def _apply(spec, replace_all, texts):
    staged = {}
    for rel, repls in spec.items():
        text = staged.get(rel) or _read(rel, texts)
        if text is None:
            return None, f"{rel} introuvable"
        for old, new in repls:
            if text.count(old) != 1:
                return None, f"repère introuvable dans {rel} : « {old.strip().splitlines()[0][:64]} »"
            text = text.replace(old, new)
        staged[rel] = text
    for rel, old, new, expected in replace_all:
        text = staged.get(rel) or _read(rel, texts)
        if text is None or text.count(old) != expected:
            return None, f"repère introuvable dans {rel} : « {old.strip().splitlines()[0][:64]} » (attendu {expected} fois)"
        staged[rel] = text.replace(old, new)
    return staged, None


def main():
    if not os.path.exists(os.path.join(ROOT, "app.py")):
        sys.exit("ARRÊT : lancez le script à la racine du projet (là où se trouve app.py).")
    if "_BoundedIntConverter" in open(os.path.join(ROOT, "app.py"), encoding="utf-8").read():
        print("Ce lot est déjà appliqué : rien à faire.")
        return
    texts, original, done, skipped = {}, {}, [], []
    for code, label, spec, extra in CORRECTIONS:
        result, err = _apply(spec, extra, texts)
        if err:
            skipped.append((code, label, err))
            continue
        for rel, new in result.items():
            original.setdefault(rel, _read(rel, texts))
            texts[rel] = new
        done.append((code, label))
    for rel, text in texts.items():
        if rel in original and text != original[rel]:
            path = os.path.join(ROOT, rel)
            shutil.copy2(path, path + SUFFIX)
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)
            print(f"  modifié : {rel}")
    created = set()
    for code, _ in done:
        for name, content in NEW_FILES.get(code, {}).items():
            if name not in created:
                with open(os.path.join(ROOT, name), "w", encoding="utf-8", newline="\n") as fh:
                    fh.write(content)
                created.add(name)
                print(f"  créé    : {name}")
    print("\nAppliquées :")
    for code, label in done:
        print(f"  ✔ {code}  {label}")
    if skipped:
        print("\nNON appliquées (fichiers laissés intacts pour ces corrections) :")
        for code, label, err in skipped:
            print(f"  ✘ {code}  {label}\n      -> {err}")
    print(f"\nCopies de sécurité : *{SUFFIX} (ignorées par Git si « *.avant* » est dans .gitignore).")


if __name__ == "__main__":
    main()
