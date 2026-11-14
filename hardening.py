"""
MenuFlash Mini — Mise en production : sécurité et fiabilité avant les tests clients.

Branchement dans app.py, JUSTE APRÈS init_sa_controls(...) et AVANT
« if __name__ == "__main__": » :

    from hardening import init_hardening
    init_hardening(app, log_audit=log_audit)

1. COMMANDES PROTÉGÉES (faille corrigée)
   Avant : n'importe qui pouvait annuler, modifier, noter ou consulter la
   commande d'un autre client en changeant le numéro dans l'adresse
   (/m/<slug>/commande/12, 13, 14…).
   Maintenant : seules ces personnes y ont accès :
   - le téléphone qui a passé la commande (cookie signé « mf_my_orders ») ;
   - le lien reçu par email (commande à emporter), qui contient un jeton
     secret ; l'ouvrir ajoute la commande à ce téléphone ;
   - l'équipe connectée du restaurant (consultation seulement).
   Le panneau « Mes commandes » ne renvoie plus que les commandes du téléphone.

2. VRAIE ADRESSE IP DES VISITEURS (Render)
   Sur Render, l'application est derrière un proxy : sans correction, tous
   les visiteurs avaient la même adresse IP, et les limites anti-abus
   (connexion, inscription, commandes) s'appliquaient à tout le monde à la
   fois. Le client pouvait aussi falsifier son IP avec l'en-tête
   X-Forwarded-For. Corrigé avec ProxyFix (werkzeug) : seule l'adresse
   ajoutée par le proxy de Render est retenue.
   Variable facultative : PROXY_HOPS (nombre de proxys devant l'application,
   1 par défaut sur Render).

3. CLÉ SECRÈTE OBLIGATOIRE
   Sur Render, l'application refuse de démarrer si FLASK_SECRET_KEY est
   absente ou vaut la valeur de développement (sessions et liens falsifiables).

4. BASE DE DONNÉES À JOUR AU DÉMARRAGE (PostgreSQL et SQLite)
   L'ancienne mise à jour des colonnes (app.py) utilisait « PRAGMA », une
   commande qui n'existe qu'en SQLite : sur PostgreSQL elle échouait sans
   rien dire, et elle ne s'exécutait qu'avec « python app.py », jamais avec
   gunicorn. Maintenant, à chaque démarrage : tables manquantes créées,
   colonnes manquantes ajoutées, tampons de sécurité complétés.

5. PAGES LÉGALES + ACCEPTATION À L'INSCRIPTION
   /conditions-utilisation, /confidentialite, /mentions-legales.
   Case « J'accepte » obligatoire à l'inscription (tracée dans le journal).
   Variables à renseigner sur Render : LEGAL_OPERATOR (votre nom ou celui de
   votre entreprise), LEGAL_CONTACT_EMAIL, LEGAL_PHONE, LEGAL_ADDRESS,
   LEGAL_RCCM (si vous avez une entreprise immatriculée).

6. DONNÉES DES CLIENTS EFFACÉES APRÈS 90 JOURS
   Le téléphone et l'email des clients « à emporter » sont effacés 90 jours
   après le retrait (le prénom et la commande restent pour les statistiques).
   Variable facultative : CUSTOMER_DATA_DAYS.

Aucune nouvelle dépendance, aucune table existante modifiée.
"""
import hmac
import logging
import os
import time
from datetime import datetime, timedelta
from functools import wraps

import sqlalchemy as sa
from flask import (abort, flash, g, jsonify, redirect, render_template, request,
                   session, url_for)
from flask_login import current_user
from werkzeug.datastructures import ImmutableMultiDict
from werkzeug.middleware.proxy_fix import ProxyFix

from models import DEFAULT_PREP_MINUTES, DEFAULT_THEME, Order, RestaurantAccess, User, db

log = logging.getLogger("menuflash.hardening")

TERMS_VERSION = "2026-11"
DEV_SECRETS = {"", "dev-key-a-changer", "change-moi"}
PURGE_EVERY_SECONDS = 6 * 3600

# (table, colonne, type, défaut) — colonnes ajoutées au fil des versions.
_COLUMNS = [
    ("restaurants", "theme", "VARCHAR(20)", f"'{DEFAULT_THEME}'"),
    ("menu_categories", "is_paused", "BOOLEAN", "FALSE"),
    ("menu_items", "prep_minutes", "INTEGER", str(DEFAULT_PREP_MINUTES)),
    ("orders", "comment", "VARCHAR(500)", None),
    ("orders", "reviewed", "BOOLEAN", "FALSE"),
    ("orders", "cancelled_by", "VARCHAR(20)", None),
    ("order_items", "prep_minutes", "INTEGER", str(DEFAULT_PREP_MINUTES)),
    ("users", "failed_login_attempts", "INTEGER", "0"),
    ("users", "locked_until", "TIMESTAMP", None),
    ("users", "last_login_at", "TIMESTAMP", None),
    ("users", "security_stamp", "VARCHAR(32)", "''"),
    ("users", "email_verified", "BOOLEAN", "FALSE"),
    ("users", "email_verified_at", "TIMESTAMP", None),
]

_ORDER_READ = ("order_status", "order_status_json")
_ORDER_WRITE = ("cancel_order_by_client", "add_to_order", "submit_review")
_PUBLIC_ENDPOINTS_WITH_LEGAL = ("public_menu", "order_status")


# ==========================================================================
# 2. Adresse IP réelle
# ==========================================================================
class _ForwardedForSanitizer:
    """Après ProxyFix, REMOTE_ADDR contient l'IP fiable. On remplace l'en-tête
    X-Forwarded-For par cette seule valeur : le code existant qui lit cet
    en-tête (journal d'audit, limites anti-abus) ne peut plus être trompé."""

    def __init__(self, wsgi_app):
        self.wsgi_app = wsgi_app

    def __call__(self, environ, start_response):
        environ["HTTP_X_FORWARDED_FOR"] = environ.get("REMOTE_ADDR") or ""
        return self.wsgi_app(environ, start_response)


class _CanonicalHost:
    """Audit MF-03 — adresse officielle du site.

    Si APP_BASE_URL est définie (ex. https://menuflash.app), chaque requête est
    traitée comme adressée à ce domaine : url_for(..., _external=True) produit
    donc TOUJOURS des liens vers le vrai site (emails, QR codes, redirections),
    même si un attaquant envoie un en-tête Host falsifié. L'adresse reçue est
    conservée dans environ["menuflash.received_host"] pour le diagnostic.
    Le protocole (http/https) n'est pas modifié : la redirection HTTPS continue
    de fonctionner normalement.
    """

    def __init__(self, wsgi_app, host):
        self.wsgi_app = wsgi_app
        self.host = host

    @staticmethod
    def parse(raw):
        """Renvoie le domaine (avec port éventuel) d'APP_BASE_URL, ou None si invalide."""
        from urllib.parse import urlsplit
        raw = (raw or "").strip().rstrip("/")
        if not raw:
            return None
        parts = urlsplit(raw)
        if (parts.scheme not in ("http", "https") or not parts.hostname or parts.path
                or parts.query or parts.fragment or parts.username or parts.password):
            return None
        if any(ch in parts.netloc for ch in " /\\@"):
            return None
        return parts.netloc.lower()

    @classmethod
    def wrap(cls, wsgi_app):
        raw = os.environ.get("APP_BASE_URL", "")
        host = cls.parse(raw)
        if host:
            log.info("Adresse officielle des liens : %s (APP_BASE_URL).", host)
            return cls(wsgi_app, host)
        if raw.strip():
            log.error("APP_BASE_URL invalide (%r) : attendu par ex. https://menuflash.app, "
                      "sans chemin ni / final. Elle est ignorée.", raw)
        elif os.environ.get("RENDER"):
            log.warning("APP_BASE_URL n'est pas définie : les liens envoyés par email "
                        "utilisent l'adresse reçue. Définissez-la dans Render > Environment.")
        return wsgi_app

    def __call__(self, environ, start_response):
        environ["menuflash.received_host"] = environ.get("HTTP_HOST", "")
        environ["HTTP_HOST"] = self.host
        name, _, port = self.host.partition(":")
        environ["SERVER_NAME"] = name
        if port:
            environ["SERVER_PORT"] = port
        return self.wsgi_app(environ, start_response)


def _install_proxy_fix(app):
    raw = os.environ.get("PROXY_HOPS")
    if raw is None:
        hops = 1 if os.environ.get("RENDER") else 0
    else:
        try:
            hops = max(0, min(int(raw), 5))
        except ValueError:
            hops = 1
    inner = _ForwardedForSanitizer(_CanonicalHost.wrap(app.wsgi_app))
    # Audit MF-03 : x_host=0 — l'en-tête X-Forwarded-Host (modifiable par le
    # visiteur) ne peut plus changer l'adresse utilisée dans les liens.
    app.wsgi_app = ProxyFix(inner, x_for=hops, x_proto=1, x_host=0) if hops else inner
    log.info("Adresse IP des visiteurs : %s proxy(s) de confiance.", hops)


# ==========================================================================
# 3. Clé secrète
# ==========================================================================
def _check_secret(app):
    key = app.config.get("SECRET_KEY") or ""
    if key in DEV_SECRETS:
        if os.environ.get("RENDER"):
            raise RuntimeError(
                "FLASK_SECRET_KEY n'est pas définie sur Render : démarrage refusé. "
                "Ajoutez-la dans Render > Environment (64 caractères aléatoires).")
        log.warning("Clé secrète de développement utilisée (normal en local uniquement).")
    elif len(key) < 32:
        log.warning("FLASK_SECRET_KEY est courte (%s caractères) : 32 au minimum sont recommandés.", len(key))


# ==========================================================================
# 4. Schéma de la base
# ==========================================================================
def _ensure_schema(app):
    with app.app_context():
        try:
            db.create_all()
        except Exception:
            db.session.rollback()
            log.exception("Création des tables manquantes impossible")
        try:
            insp = sa.inspect(db.engine)
            tables = set(insp.get_table_names())
            pg = db.engine.dialect.name == "postgresql"
            for table, column, col_type, default in _COLUMNS:
                if table not in tables:
                    continue
                if column in {c["name"] for c in insp.get_columns(table)}:
                    continue
                if not pg:
                    col_type = col_type.replace("TIMESTAMP", "DATETIME")
                    if default in ("FALSE", "TRUE"):
                        default = "0" if default == "FALSE" else "1"
                ddl = f'ALTER TABLE "{table}" ADD COLUMN "{column}" {col_type}'
                if default is not None:
                    ddl += f" DEFAULT {default}"
                with db.engine.begin() as conn:
                    conn.execute(sa.text(ddl))
                log.warning("Colonne ajoutée : %s.%s", table, column)
        except Exception:
            log.exception("Mise à jour des colonnes impossible")
        try:
            missing = User.query.filter((User.security_stamp.is_(None)) | (User.security_stamp == "")).all()
            for u in missing:
                u.rotate_security_stamp()
            if missing:
                db.session.commit()
        except Exception:
            db.session.rollback()
            log.exception("Tampons de sécurité impossibles à compléter")


# ==========================================================================
# 1. Accès aux commandes
# ==========================================================================
def _owned_ids():
    from order_refs import _owned_ids as owned
    return owned()


def _remember_on_this_device(order_id):
    """Ajoute la commande au cookie signé du téléphone (écrit par order_refs.py)."""
    g._mf_owned_new = order_id
    g._mf_owned = None


def _staff_can_see(order):
    if not current_user.is_authenticated:
        return False
    if getattr(current_user, "is_super_admin", False) or current_user.restaurant_id == order.restaurant_id:
        return True
    return RestaurantAccess.query.filter_by(user_id=current_user.id,
                                           restaurant_id=order.restaurant_id).first() is not None


def _valid_link_token(order_id):
    token = request.args.get("k") or ""
    if not token or len(token) > 100:
        return False
    try:
        from takeaway import TakeawayOrder
        row = db.session.get(TakeawayOrder, order_id)
    except Exception:
        return False
    return bool(row and hmac.compare_digest(row.public_token, token))


def _order_access(order_id, read_only):
    if order_id in _owned_ids():
        return True
    order = db.session.get(Order, order_id)
    if order is None:
        return True                     # la route d'origine répondra 404
    if read_only and _staff_can_see(order):
        return True
    if read_only and _valid_link_token(order_id):
        _remember_on_this_device(order_id)
        return True
    return False


def _deny(endpoint):
    if endpoint == "order_status":
        abort(404)
    return jsonify(ok=False, error="Cette commande n'a pas été passée depuis cet appareil."), 404


def _protect_orders(app):
    for endpoint in _ORDER_READ + _ORDER_WRITE:
        original = app.view_functions.get(endpoint)
        if original is None:
            log.error("Route %s introuvable : protection non appliquée.", endpoint)
            continue

        def make(original, endpoint):
            read_only = endpoint in _ORDER_READ

            @wraps(original)
            def guarded(*args, **kwargs):
                order_id = kwargs.get("order_id")
                if order_id is not None and not _order_access(order_id, read_only):
                    return _deny(endpoint)
                return original(*args, **kwargs)
            return guarded
        app.view_functions[endpoint] = make(original, endpoint)

    original_bulk = app.view_functions.get("orders_bulk_status")
    if original_bulk is not None:
        @wraps(original_bulk)
        def orders_bulk_status(*args, **kwargs):
            owned = _owned_ids()
            asked = [int(p) for p in (request.args.get("ids") or "").split(",") if p.strip().isdigit()][-50:]
            keep = [i for i in asked if i in owned]
            others = [i for i in asked if i not in owned]
            request.args = ImmutableMultiDict({"ids": ",".join(str(i) for i in keep)})
            resp = original_bulk(*args, **kwargs)
            data = resp.get_json(silent=True) if hasattr(resp, "get_json") else None
            if not isinstance(data, dict):
                return resp
            data["expired_ids"] = list(data.get("expired_ids") or []) + others
            return jsonify(data)
        app.view_functions["orders_bulk_status"] = orders_bulk_status

    # Le lien « Suivre ma commande » (email à emporter, redirection après la
    # commande) contient le jeton secret : il fonctionne aussi sur un autre appareil.
    @app.url_defaults
    def _order_link_token(endpoint, values):
        if endpoint != "order_status" or "k" in values or "order_id" not in values:
            return
        try:
            from takeaway import TakeawayOrder
            row = db.session.get(TakeawayOrder, values["order_id"])
            if row:
                values["k"] = row.public_token
        except Exception:
            pass


# ==========================================================================
# 6. Effacement des données des clients
# ==========================================================================
_purge_state = {"at": 0.0}


def _purge_customer_data():
    try:
        days = max(7, min(int(os.environ.get("CUSTOMER_DATA_DAYS", "90")), 3650))
    except ValueError:
        days = 90
    from takeaway import TakeawayOrder
    limit = datetime.utcnow() - timedelta(days=days)
    n = (TakeawayOrder.query
         .filter(TakeawayOrder.pickup_at < limit,
                 (TakeawayOrder.customer_phone != "") | (TakeawayOrder.customer_email.isnot(None)))
         .update({"customer_phone": "", "customer_email": None}, synchronize_session=False))
    db.session.commit()
    if n:
        log.info("Données de %s client(s) à emporter effacées (plus de %s jours).", n, days)


# ==========================================================================
# Branchement
# ==========================================================================
def init_hardening(app, *, log_audit):
    _check_secret(app)
    _install_proxy_fix(app)
    _ensure_schema(app)
    _protect_orders(app)

    # ---------------- Effacement périodique des données clients ----------------
    @app.before_request
    def _maybe_purge():
        now = time.time()
        if now - _purge_state["at"] < PURGE_EVERY_SECONDS:
            return None
        _purge_state["at"] = now
        try:
            _purge_customer_data()
        except Exception:
            db.session.rollback()
            log.exception("Effacement des données clients impossible")
        return None

    # ---------------- Pages légales ----------------
    def legal_info():
        return {
            "operator": os.environ.get("LEGAL_OPERATOR", "").strip() or "[Nom de l'exploitant à compléter]",
            "email": (os.environ.get("LEGAL_CONTACT_EMAIL", "").strip()
                      or app.config.get("MAIL_DEFAULT_SENDER") or "[email à compléter]"),
            "phone": os.environ.get("LEGAL_PHONE", "").strip(),
            "address": os.environ.get("LEGAL_ADDRESS", "").strip() or "[Adresse à compléter]",
            "rccm": os.environ.get("LEGAL_RCCM", "").strip(),
            "days": os.environ.get("CUSTOMER_DATA_DAYS", "90"),
            "version": TERMS_VERSION,
        }

    def _legal(page):
        return render_template("legal.html", page=page, legal=legal_info())

    app.add_url_rule("/conditions-utilisation", "legal_terms", lambda: _legal("terms"))
    app.add_url_rule("/confidentialite", "legal_privacy", lambda: _legal("privacy"))
    app.add_url_rule("/mentions-legales", "legal_notice", lambda: _legal("notice"))

    @app.context_processor
    def _inject_legal_links():
        return {"mf_show_legal_footer": request.endpoint in _PUBLIC_ENDPOINTS_WITH_LEGAL}

    # ---------------- Acceptation des conditions à l'inscription ----------------
    @app.before_request
    def _require_terms():
        if request.endpoint != "signup" or request.method != "POST":
            return None
        if request.form.get("accept_terms") == "1":
            g._mf_terms_ok = True
            return None
        session["mf_prefill"] = {k: (request.form.get(k) or "")[:120] for k in (
            "restaurant_name", "slug", "email", "phone", "phone_country", "first_name", "last_name", "city")}
        flash("Vous devez accepter les conditions d'utilisation et la politique de confidentialité.", "error")
        return redirect(url_for("signup"))

    @app.after_request
    def _record_terms(response):
        if getattr(g, "_mf_terms_ok", False) and current_user.is_authenticated:
            try:
                log_audit("terms_accepted", restaurant_id=current_user.restaurant_id,
                          user_email=current_user.email,
                          details=f"conditions et confidentialité, version {TERMS_VERSION}")
            except Exception:
                log.exception("Trace de l'acceptation des conditions impossible")
            g._mf_terms_ok = False
        return response
