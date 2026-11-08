"""
MenuFlash Mini — Accès aux commandes : modifier une adresse ramène TOUJOURS le client sur sa page.

Branchement dans app.py, JUSTE AVANT « from hardening import init_hardening »
(le script ajouter_acces_commandes.py le fait pour vous) :

    from order_access import init_order_access
    init_order_access(app, log_audit=log_audit)

Le problème
-----------
Le client est sur /m/chez-awa/commande/87 et remplace 87 par 88 : il obtenait une page « Not Found ».
Ce n'était pas dangereux (il ne voyait rien), mais :
  - « introuvable » est une réponse que le client ne comprend pas ;
  - une commande qui existe chez un autre client et une commande qui n'existe pas donnaient des réponses
    DIFFÉRENTES sur plusieurs adresses (JSON, ajout de plats…) : on pouvait deviner quels numéros existent.

Le comportement
---------------
1. Toute adresse de commande qui n'est pas la SIENNE (numéro modifié, commande d'un autre client, commande
   inexistante, commande d'un autre restaurant, adresse mal écrite, mauvaise méthode…) ramène le client,
   SANS AUCUN MESSAGE, sur la page où il se trouvait :
     - s'il était sur sa page de commande : cette même page de commande ;
     - s'il était sur le menu : le menu (avec sa table s'il a scanné un QR) ;
     - si on ne sait pas (premier passage, autre téléphone, cookie expiré) : le menu du restaurant.
   « La page où il se trouve » est retenue par le serveur dans un cookie signé « mf_here » (12 h), mis à jour
   chaque fois qu'il affiche SA page de commande ou le menu. Elle n'est JAMAIS lue dans l'adresse.
2. Commande inexistante, commande d'un autre, adresse invalide : réponse IDENTIQUE. Rien ne permet plus de
   savoir si un numéro existe.
3. Les appels du navigateur (JSON : état, lignes, modifier, annuler, ajouter, avis) ne peuvent pas être
   « redirigés » au milieu d'un script : ils reçoivent un JSON sans texte, {"ok": false, "redirect": "..."},
   avec le code 404 ; le script de la page (order_edit.js) suit ce redirect sans rien afficher.
4. Le paramètre ?ajouter=<numéro> du menu (mode « ajouter des plats ») est retiré de l'adresse, sans message,
   s'il ne désigne pas une commande du client.
5. Ce qui reste autorisé : ses propres commandes (cookie signé « mf_my_orders »), le lien de suivi reçu par
   email pour une commande à emporter (?k=<jeton secret>), et l'équipe connectée du restaurant (lecture seule).
6. Les tentatives sont tracées dans le journal de sécurité (« order_access_denied »), 3 lignes au plus par
   adresse IP et par tranche de 10 minutes, sans donnée personnelle.

Ce qui ne change pas : un restaurant qui n'existe pas (adresse /m/xxx) reste « introuvable » : ce n'est pas
une commande. Rien n'est modifié dans les routes existantes ; si ce module ne peut pas démarrer,
l'application fonctionne comme avant. Aucune table, aucune dépendance.
"""
import hmac
import logging
import re
import time
from collections import deque
from urllib.parse import parse_qsl, urlencode

from flask import g, jsonify, redirect, request, url_for
from flask_login import current_user
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from models import Order, Restaurant, RestaurantAccess, db

log = logging.getLogger("menuflash.order_access")

COOKIE = "mf_here"
COOKIE_TTL = 12 * 3600
COOKIE_MAX_RESTAURANTS = 8

AUDIT_WINDOW = 600
AUDIT_PER_WINDOW = 3
AUDIT_MAX_IPS = 2000

PAGE_ENDPOINTS = {"order_status"}
DATA_ENDPOINTS = {"order_status_json", "order_lines_json"}
ACTION_ENDPOINTS = {"cancel_order_by_client", "add_to_order", "submit_review", "order_modify_item"}
READ_ENDPOINTS = PAGE_ENDPOINTS | DATA_ENDPOINTS
PROTECTED_ENDPOINTS = READ_ENDPOINTS | ACTION_ENDPOINTS
ORDER_PATH = re.compile(r"^/m/([^/]+)/commande/")        # y compris les adresses que Flask ne reconnaît pas

_state = {"serializer": None, "secure": False, "audits": {}}


# ==========================================================================
# Fonctions pures (testées)
# ==========================================================================
def sanitize_here(data):
    """Contenu du cookie « page où l'on se trouve », nettoyé : {slug: ["o", id] | ["m"]}."""
    out = {}
    if not isinstance(data, dict):
        return out
    for slug, entry in data.items():
        if not isinstance(slug, str) or not 0 < len(slug) <= 80 or not isinstance(entry, list) or not entry:
            continue
        if entry[0] == "m" and len(entry) == 1:
            out[slug] = ["m"]
        elif entry[0] == "o" and len(entry) == 2 and isinstance(entry[1], int) \
                and not isinstance(entry[1], bool) and entry[1] > 0:
            out[slug] = ["o", entry[1]]
    return out


def strip_param(query_string, name):
    """Query string sans un paramètre, tous les autres conservés tels quels (ordre et doublons compris)."""
    text = query_string.decode("utf-8", "replace") if isinstance(query_string, bytes) else str(query_string or "")
    pairs = [(k, v) for k, v in parse_qsl(text, keep_blank_values=True) if k != name]
    return urlencode(pairs)


def audit_allowed(counters, ip, now=None, window=AUDIT_WINDOW, limit=AUDIT_PER_WINDOW, max_ips=AUDIT_MAX_IPS):
    """Vrai pour les `limit` premières tentatives d'une adresse IP par `window` secondes (le journal reste lisible)."""
    now = time.monotonic() if now is None else now
    if len(counters) > max_ips:
        for key in [k for k, q in counters.items() if not q or q[-1] <= now - window]:
            del counters[key]
    q = counters.setdefault(ip, deque())
    while q and q[0] <= now - window:
        q.popleft()
    if len(q) >= limit:
        return False
    q.append(now)
    return True


# ==========================================================================
# Données
# ==========================================================================
def _restaurant(slug):
    if not slug:
        return None
    cache = g.__dict__.setdefault("_mf_oa_restaurants", {})
    if slug not in cache:
        cache[slug] = Restaurant.query.filter_by(slug=slug, is_active=True).first()
    return cache[slug]


def _order_exists(restaurant_id, order_id):
    return db.session.query(Order.id).filter_by(id=order_id, restaurant_id=restaurant_id).first() is not None


def _owned_ids():
    from order_refs import _owned_ids as owned
    return owned()


def _staff_can_see(restaurant):
    if not current_user.is_authenticated:
        return False
    if getattr(current_user, "is_super_admin", False) or current_user.restaurant_id == restaurant.id:
        return True
    return RestaurantAccess.query.filter_by(user_id=current_user.id, restaurant_id=restaurant.id).first() is not None


def _valid_link_token(order_id):
    token = request.args.get("k") or ""
    if not token or len(token) > 100:
        return False
    try:
        from takeaway import TakeawayOrder
        row = db.session.get(TakeawayOrder, order_id)
    except Exception:
        db.session.rollback()
        return False
    return bool(row and hmac.compare_digest(str(row.public_token), token))


def can_access(restaurant, order_id, read_only):
    """Cette personne a-t-elle le droit d'utiliser cette commande ? Une commande inexistante = non.

    TOUTES les vérifications sont faites dans le même ordre, que la commande existe ou non, puis combinées :
    sinon la réponse (en-têtes « Vary », nombre de requêtes à la base) laisserait deviner quels numéros existent.
    """
    exists = _order_exists(restaurant.id, order_id)
    owned = order_id in _owned_ids()
    staff = _staff_can_see(restaurant)                      # consulte toujours la session
    token = _valid_link_token(order_id)
    if not exists:
        return False
    if owned:
        return True
    if read_only and staff:
        return True
    if read_only and token:
        g._mf_owned_new, g._mf_owned = order_id, None        # le lien reçu par email vaut propriété (order_refs.py)
        return True
    return False


# ---------------------------------------------------------------- « page où l'on se trouve » (cookie signé)
def _read_here():
    cached = g.__dict__.get("_mf_here")
    if cached is not None:
        return cached
    out = {}
    raw = request.cookies.get(COOKIE, "")
    if raw and _state["serializer"] is not None:
        try:
            out = sanitize_here(_state["serializer"].loads(raw, max_age=COOKIE_TTL))
        except (BadSignature, SignatureExpired, TypeError, ValueError):
            out = {}
    g._mf_here = out
    return out


def _menu_url(restaurant):
    """Menu du restaurant, avec la table scannée par cet appareil s'il y en a une (qr_security.py)."""
    params = {"slug": restaurant.slug}
    try:
        from qr_security import bound_qr, display_slug
        table = bound_qr(restaurant.id)
        if table is not None:
            params["qr"] = display_slug(table.label)           # le NOM seulement, jamais le code
    except Exception:
        db.session.rollback()
    return url_for("public_menu", **params)


def fallback_url(restaurant):
    """Page où ramener le client : celle qu'il affichait en dernier, sinon le menu."""
    entry = _read_here().get(restaurant.slug)
    if entry and entry[0] == "o":
        order_id = entry[1]
        if order_id in _owned_ids() and _order_exists(restaurant.id, order_id):
            return url_for("order_status", slug=restaurant.slug, order_id=order_id)
    return _menu_url(restaurant)


# ==========================================================================
# Branchement
# ==========================================================================
def init_order_access(app, *, log_audit=None):
    if app.extensions.get("mf_order_access"):
        return                                                   # déjà branché
    app.extensions["mf_order_access"] = True
    _state["serializer"] = URLSafeTimedSerializer(app.config["SECRET_KEY"], salt="mf-here")
    _state["secure"] = bool(app.config.get("SESSION_COOKIE_SECURE", False))

    def wants_json():
        return (request.headers.get("X-Requested-With") == "fetch" or request.is_json
                or request.path.endswith(".json"))

    def audit(restaurant, what):
        if log_audit is None:
            return
        ip = request.remote_addr or "?"
        if not audit_allowed(_state["audits"], ip):
            return
        try:
            log_audit("order_access_denied", success=False, restaurant_id=restaurant.id, user_email=None,
                      details=f"{what} ip={ip}"[:300])
        except Exception:
            log.exception("Trace de sécurité impossible")

    def deny(restaurant, what):
        audit(restaurant, what)
        target = fallback_url(restaurant)
        if wants_json():
            response = jsonify({"ok": False, "redirect": target})      # aucun texte à afficher
            response.status_code = 404
        else:
            response = redirect(target, 302)
        response.headers["Cache-Control"] = "no-store"
        response.vary.add("Cookie")                              # la destination dépend de vos cookies, toujours
        return response

    # ------------------------------------------------------------------ avant chaque requête
    def _guard():
        endpoint = request.endpoint
        if endpoint in PROTECTED_ENDPOINTS:
            args = request.view_args or {}
            restaurant = _restaurant(args.get("slug"))
            if restaurant is None:
                return None                                      # restaurant inconnu : 404 d'origine
            order_id = args.get("order_id")
            try:
                if can_access(restaurant, order_id, read_only=endpoint in READ_ENDPOINTS):
                    return None
                return deny(restaurant, f"{endpoint} #{order_id}")
            except Exception:
                db.session.rollback()
                log.exception("Vérification de l'accès à la commande %s impossible", order_id)
                return deny(restaurant, f"{endpoint} (erreur)")

        if endpoint is None:                                     # adresse non reconnue ou mauvaise méthode
            match = ORDER_PATH.match(request.path)
            if match:
                restaurant = _restaurant(match.group(1))
                if restaurant is not None:
                    return deny(restaurant, "adresse de commande invalide")
            return None

        if endpoint == "public_menu" and request.method in ("GET", "HEAD") and "ajouter" in request.args:
            restaurant = _restaurant((request.view_args or {}).get("slug"))
            if restaurant is None:
                return None
            raw = (request.args.get("ajouter") or "").strip()
            mine = False
            if raw.isdigit() and len(raw) <= 9:
                try:
                    mine = can_access(restaurant, int(raw), read_only=False)
                except Exception:
                    db.session.rollback()
            if not mine:
                audit(restaurant, "ajouter invalide")
                query = strip_param(request.query_string, "ajouter")
                response = redirect(request.path + ("?" + query if query else ""), 302)
                response.headers["Cache-Control"] = "no-store"
                response.vary.add("Cookie")
                return response
        return None

    # Avant TOUS les autres gardes : ils ne voient jamais une commande qui n'est pas la sienne.
    app.before_request_funcs.setdefault(None, []).insert(0, _guard)

    # ------------------------------------------------------------------ après chaque réponse : retenir la page
    @app.after_request
    def _remember_page(response):
        if request.method != "GET" or response.status_code != 200:
            return response
        endpoint = request.endpoint
        entry = None
        try:
            if endpoint == "order_status":
                order_id = (request.view_args or {}).get("order_id")
                if order_id in _owned_ids():                     # jamais la page d'une commande qui n'est pas la sienne
                    entry = ["o", order_id]
            elif endpoint == "public_menu":
                entry = ["m"]
            if entry is None:
                return response
            slug = (request.view_args or {}).get("slug")
            here = dict(_read_here())
            if here.get(slug) == entry:
                return response                                  # inchangé : pas de cookie à renvoyer
            here.pop(slug, None)
            here[slug] = entry
            while len(here) > COOKIE_MAX_RESTAURANTS:
                here.pop(next(iter(here)))
            response.set_cookie(COOKIE, _state["serializer"].dumps(here), max_age=COOKIE_TTL,
                                httponly=True, samesite="Lax", secure=_state["secure"])
        except Exception:
            log.exception("Mémorisation de la page impossible")
        return response
