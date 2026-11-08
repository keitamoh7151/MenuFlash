"""
MenuFlash Mini — Bouclier d'adresses : seul le super-admin peut « jouer » avec les adresses (URL).

Branchement dans app.py, JUSTE APRÈS « init_order_access(app, log_audit=log_audit) » et AVANT
« from hardening import init_hardening » (le script ajouter_url_shield.py le fait pour vous) :

    from url_shield import init_url_shield
    init_url_shield(app, log_audit=log_audit)

Le problème
-----------
Dans la barre d'adresse, /admin/commandes devenait /admin/commande (un « s » en moins) et Flask répondait la page
blanche « Not Found ». Même chose pour une adresse inconnue, une adresse réservée à un autre rôle (403 « Forbidden »),
ou une adresse tapée dans la mauvaise méthode (405 « Method Not Allowed »). Ces pages :
  - sont incompréhensibles pour un restaurateur ou un client ;
  - révèlent des informations (« cette adresse existe mais vous n'y avez pas droit » ≠ « elle n'existe pas »).

Le comportement
---------------
1. SUPER-ADMIN (connecté) : rien ne change. Il garde les vraies erreurs (404, 403, 405) : c'est lui qui a tous les droits.
2. TOUT LE MONDE (client, restaurateur, employé, visiteur) : lorsqu'une adresse tapée ou collée dans le navigateur
   n'aboutit pas (404, 403, 405) ou est manifestement falsifiée (caractères de contrôle, « .. », « // », « < » « > »,
   adresse démesurée), le serveur ramène la personne, SANS AUCUN MESSAGE ET SANS ERREUR, sur une page valide :
     - client dans /m/<restaurant>/… : sa page de commande ou le menu (order_access.py), sinon l'accueil ;
     - restaurateur / employé connecté : la dernière page d'administration valide qu'il a affichée, sinon son accueil ;
     - visiteur non connecté sur /admin… ou /super-admin… : la page de connexion (même réponse que pour une adresse
       qui existe : impossible de deviner quelles pages d'administration existent) ;
     - autre : l'accueil.
   « La dernière page valide » est retenue par le serveur dans un cookie signé « mf_nav » (12 h). Elle n'est JAMAIS
   lue dans l'adresse. Une adresse réservée au super-admin n'est jamais retenue pour un autre rôle.
   Le message « Cette page est réservée au gestionnaire du restaurant. » (employé qui tape une adresse de gestionnaire)
   est lui aussi retiré : l'employé arrive sur sa page de commandes, sans message.
3. Appels du navigateur (JSON, flux temps réel, formulaires envoyés par script) : non touchés, ils reçoivent l'erreur
   d'origine. Seule la navigation (barre d'adresse, lien, formulaire classique) est redirigée.
4. Les fichiers (/static, images, favicon) et les robots (qui n'envoient pas « text/html » en navigation) gardent
   leur 404 normal : aucune boucle, aucun faux « 200 ».
5. Anti-boucle : cookie signé « mf_bounce » (15 s). Si la page où l'on ramène la personne échoue elle-même, elle est
   envoyée à l'accueil « / » (jamais plus de 2 redirections de suite).
6. Tentatives tracées dans le journal de sécurité (« url_tampering »), 3 lignes au plus par IP et par 10 minutes ;
   seules les 3 premières parties de l'adresse sont écrites, sans paramètres ni jeton.

Ce que ce module ne fait PAS (et pourquoi)
------------------------------------------
Un restaurateur qui tape /admin/statistiques au lieu de cliquer sur « Statistiques » arrive sur la même page :
c'est une page qui lui est autorisée. Empêcher cela n'apporte aucune sécurité. La vraie protection est le contrôle des
droits sur le serveur (connexion, rôle, restaurant) : il est vérifié sur chaque route (audit joint dans le LISEZMOI).

Aucune table, aucune dépendance. Si ce module ne peut pas démarrer, l'application fonctionne comme avant.
"""
import logging
import re
import time
from collections import deque
from urllib.parse import parse_qsl

from flask import redirect, request, session, url_for
from flask_login import current_user
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from werkzeug.exceptions import HTTPException

log = logging.getLogger("menuflash.url_shield")

NAV_COOKIE = "mf_nav"
NAV_TTL = 12 * 3600
BOUNCE_COOKIE = "mf_bounce"
BOUNCE_TTL = 15

MAX_PATH = 300
MAX_QUERY = 1500

AUDIT_WINDOW = 600
AUDIT_PER_WINDOW = 3
AUDIT_MAX_IPS = 2000

HANDLED_CODES = (403, 404, 405)

PUBLIC_PATH = re.compile(r"^/m/([^/]+)(?:/|$)")
ADMIN_PREFIXES = ("/admin", "/super-admin", "/compte")
NEVER_REMEMBER = {"admin_login", "admin_logout", "forgot_password", "reset_password", "verify_email"}
FORBIDDEN_CHARS = set("<>")
# Messages d'accès refusé affichés par l'application quand on tape une adresse réservée à un autre rôle :
# retirés (l'employé arrive simplement sur sa page, sans savoir que l'adresse existe).
SILENT_FLASHES = ("Cette page est réservée au gestionnaire du restaurant.",)

_state = {"serializer": None, "secure": False, "audits": {}}


# ==========================================================================
# Fonctions pures (testées)
# ==========================================================================
def is_navigation(headers, method="GET"):
    """Vrai si la requête vient d'une navigation du navigateur (barre d'adresse, lien, formulaire classique).

    Les appels de script (fetch, EventSource, images…) ne sont jamais des navigations : on ne les redirige pas.
    """
    if headers.get("X-Requested-With"):
        return False
    mode = (headers.get("Sec-Fetch-Mode") or "").lower()
    dest = (headers.get("Sec-Fetch-Dest") or "").lower()
    if mode or dest:
        return mode == "navigate" and dest in ("document", "")
    accept = (headers.get("Accept") or "").lower()
    return "text/html" in accept


def is_malformed(path, query_string):
    """Adresse manifestement falsifiée : aucun usage légitime n'en produit."""
    text = query_string.decode("utf-8", "replace") if isinstance(query_string, bytes) else str(query_string or "")
    if len(path) > MAX_PATH or len(text) > MAX_QUERY:
        return True
    if "\\" in path or "//" in path:
        return True
    if any(part == ".." for part in path.split("/")):
        return True
    decoded_pairs = parse_qsl(text, keep_blank_values=True)
    for chunk in [path, text] + [k + v for k, v in decoded_pairs]:
        for ch in chunk:
            if ord(ch) < 32 or ord(ch) == 127 or ch in FORBIDDEN_CHARS:
                return True
    return False


def area_of(path):
    """« public » (/m/<slug>…), « admin » (/admin, /super-admin, /compte), ou None."""
    if path.startswith(ADMIN_PREFIXES) and (path.split("/")[1] in ("admin", "super-admin", "compte")):
        return "admin"
    if PUBLIC_PATH.match(path):
        return "public"
    return None


def is_super_admin_path(path):
    return path == "/super-admin" or path.startswith("/super-admin/")


def safe_internal_path(path):
    """Un chemin interne et rien d'autre (jamais d'adresse externe, jamais de « // »)."""
    return (isinstance(path, str) and 0 < len(path) <= MAX_PATH and path.startswith("/")
            and not path.startswith("//") and "\\" not in path
            and not any(ord(c) < 32 or ord(c) == 127 for c in path))


def audit_allowed(counters, ip, now=None, window=AUDIT_WINDOW, limit=AUDIT_PER_WINDOW, max_ips=AUDIT_MAX_IPS):
    """Vrai pour les `limit` premières tentatives d'une adresse IP par `window` secondes."""
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


def short_path(path):
    """Version de l'adresse écrite dans le journal : 3 premiers segments, sans jeton ni paramètre."""
    parts = [p for p in path.split("/") if p][:3]
    cleaned = [p[:24] + "…" if len(p) > 24 else p for p in parts]
    return ("/" + "/".join(cleaned))[:80]


# ==========================================================================
# Cookies signés
# ==========================================================================
def _load(cookie_name, max_age):
    raw = request.cookies.get(cookie_name, "")
    if not raw or _state["serializer"] is None:
        return None
    try:
        return _state["serializer"].loads(raw, max_age=max_age, salt=cookie_name)
    except (BadSignature, SignatureExpired, TypeError, ValueError):
        return None


def _dump(cookie_name, value):
    return _state["serializer"].dumps(value, salt=cookie_name)


def _set_cookie(response, name, value, max_age):
    response.set_cookie(name, _dump(name, value), max_age=max_age, httponly=True,
                        samesite="Lax", secure=_state["secure"])


# ==========================================================================
# Données
# ==========================================================================
def _is_super_admin():
    return bool(current_user.is_authenticated and getattr(current_user, "is_super_admin", False))


def _valid_get_page(app, path):
    """Ce chemin est-il une vraie page (GET) de l'application ? Jamais d'erreur ni de redirection de Flask."""
    if not safe_internal_path(path):
        return None
    try:
        endpoint, _args = app.url_map.bind("localhost").match(path, method="GET")
    except HTTPException:
        return None
    except Exception:
        return None                                              # RequestRedirect et autres
    return endpoint


def _remembered_admin_page(app):
    data = _load(NAV_COOKIE, NAV_TTL)
    path = data.get("a") if isinstance(data, dict) else None
    if not path or is_super_admin_path(path) or area_of(path) != "admin":
        return None
    endpoint = _valid_get_page(app, path)
    if endpoint is None or endpoint in NEVER_REMEMBER or endpoint == "static":
        return None
    return path


def _staff_home():
    try:
        if getattr(current_user, "is_owner", False):
            return url_for("admin_dashboard")
        return url_for("admin_orders")
    except Exception:
        return "/"


def _login_url():
    try:
        return url_for("admin_login")
    except Exception:
        return "/"


def default_customer_target(slug):
    """Où ramener un client : sa page de commande ou le menu (order_access.py) ; None si le restaurant n'existe pas."""
    from models import Restaurant, db
    restaurant = Restaurant.query.filter_by(slug=slug, is_active=True).first()
    if restaurant is None:
        return None
    try:
        from order_access import fallback_url
        return fallback_url(restaurant)
    except Exception:
        db.session.rollback()
        return url_for("public_menu", slug=restaurant.slug)


# ==========================================================================
# Branchement
# ==========================================================================
def init_url_shield(app, *, log_audit=None, customer_target=None):
    if app.extensions.get("mf_url_shield"):
        return                                                   # déjà branché
    app.extensions["mf_url_shield"] = True
    _state["serializer"] = URLSafeTimedSerializer(app.config["SECRET_KEY"], salt="mf-url-shield")
    _state["secure"] = bool(app.config.get("SESSION_COOKIE_SECURE", False))
    customer_target = customer_target or default_customer_target

    def audit(what, path):
        if log_audit is None:
            return
        ip = request.remote_addr or "?"
        if not audit_allowed(_state["audits"], ip):
            return
        try:
            email = current_user.email if current_user.is_authenticated else None
            log_audit("url_tampering", success=False, restaurant_id=None, user_email=email,
                      details=f"{what} {request.method} {short_path(path)} ip={ip}"[:300])
        except Exception:
            log.exception("Trace de sécurité impossible")

    def target_for(path):
        """Page valide où ramener cette personne (jamais lue dans l'adresse)."""
        area = area_of(path)
        if area == "public":
            slug = PUBLIC_PATH.match(path).group(1)
            try:
                target = customer_target(slug)
            except Exception:
                log.exception("Destination client introuvable")
                target = None
            return target or "/"
        if not current_user.is_authenticated:
            return _login_url() if area == "admin" else "/"
        return _remembered_admin_page(app) or _staff_home()

    def bounce(what):
        """Redirection silencieuse, ou None s'il faut laisser l'erreur d'origine."""
        path = request.path
        audit(what, path)
        data = _load(BOUNCE_COOKIE, BOUNCE_TTL)
        failed_before = isinstance(data, dict) and data.get("t") == path      # la page où je l'ai envoyé échoue aussi
        target = "/" if failed_before else target_for(path)
        if not safe_internal_path(target):
            target = "/"
        if target == path:
            if path == "/":
                return None
            target = "/"
        response = redirect(target, 302 if request.method in ("GET", "HEAD") else 303)
        response.headers["Cache-Control"] = "no-store"
        response.vary.add("Cookie")
        _set_cookie(response, BOUNCE_COOKIE, {"t": target}, BOUNCE_TTL)
        return response

    # ------------------------------------------------------------------ erreurs d'adresse (404, 403, 405)
    def handle_error(error):
        try:
            if (_is_super_admin() or request.path.startswith("/static/")
                    or not is_navigation(request.headers, request.method)):
                return error
            response = bounce(f"{error.code}")
            return response if response is not None else error
        except Exception:
            log.exception("Bouclier d'adresses : erreur inattendue")
            return error

    for code in HANDLED_CODES:
        app.register_error_handler(code, handle_error)

    # ------------------------------------------------------------------ adresses falsifiées, avant tout le reste
    def _guard():
        if request.endpoint == "static":
            return None
        if not is_malformed(request.path, request.query_string):
            return None
        try:
            if _is_super_admin():
                return None
            if is_navigation(request.headers, request.method):
                if (request.method in ("GET", "HEAD") and not is_malformed(request.path, b"")
                        and _valid_get_page(app, request.path)):
                    audit("falsifiee", request.path)             # page valide, paramètres falsifiés : on les retire
                    response = redirect(request.path, 302)
                    response.headers["Cache-Control"] = "no-store"
                    return response
                response = bounce("falsifiee")
                if response is not None:
                    return response
            return ("", 400)
        except Exception:
            log.exception("Bouclier d'adresses : erreur inattendue")
            return ("", 400)

    app.before_request_funcs.setdefault(None, []).insert(0, _guard)

    # ------------------------------------------------------------------ après chaque réponse : retenir la page valide
    @app.after_request
    def _remember_page(response):
        try:
            if response.status_code in (301, 302, 303) and "_flashes" in session:
                kept = [f for f in session["_flashes"] if not (len(f) == 2 and f[1] in SILENT_FLASHES)]
                if len(kept) != len(session["_flashes"]):
                    if kept:
                        session["_flashes"] = kept
                    else:
                        session.pop("_flashes", None)
            if request.method != "GET" or response.status_code != 200:
                return response
            if not is_navigation(request.headers) or not (response.mimetype or "").startswith("text/html"):
                return response
            if BOUNCE_COOKIE in request.cookies:
                response.delete_cookie(BOUNCE_COOKIE)             # la page s'affiche : plus de redirection en cours
            path = request.path
            if (area_of(path) != "admin" or is_super_admin_path(path) or request.endpoint in NEVER_REMEMBER
                    or not current_user.is_authenticated or _is_super_admin() or not safe_internal_path(path)):
                return response
            data = _load(NAV_COOKIE, NAV_TTL)
            if isinstance(data, dict) and data.get("a") == path:
                return response                                   # inchangé : pas de cookie à renvoyer
            _set_cookie(response, NAV_COOKIE, {"a": path}, NAV_TTL)
            response.vary.add("Cookie")
        except Exception:
            log.exception("Mémorisation de la page impossible")
        return response
