"""
Accès aux images et PDF des QR codes de TABLE (audit IDOR-01).

PROBLÈME : /m/<slug>/qrcodes/<numéro>.png et .pdf étaient publics. Or l'image d'un QR de table
contient le code secret de la table (qr_security.py). N'importe qui, sans compte, pouvait donc
télécharger le QR de n'importe quelle table de n'importe quel restaurant (adresse du restaurant
publique, numéros 1, 2, 3…), lire le secret et commander pour cette table depuis ailleurs.

CORRECTION : ces deux adresses sont réservées au GÉRANT du restaurant concerné (compte « owner »
du restaurant, ou restaurant additionnel auquel il a accès). Tout autre visiteur (anonyme,
cuisinier, gérant d'un autre restaurant, super-admin) reçoit une page « introuvable » identique,
que le QR existe ou non. Les réponses ne sont jamais mises en cache.
Les QR généraux du menu (/m/<slug>/qrcode.png|pdf), qui ne contiennent aucun secret, restent publics.
"""
import logging
import time

from flask import abort, request
from flask_login import current_user

from models import Restaurant, RestaurantAccess

log = logging.getLogger("menuflash.qr_access")
PROTECTED_ENDPOINTS = {"table_qrcode_png", "table_qrcode_pdf"}
_audits = {}


def _may_audit(ip, window=600, limit=3):
    now = time.monotonic()
    if len(_audits) > 2000:
        for k in [k for k, v in _audits.items() if now - v[0] > window]:
            _audits.pop(k, None)
    start, n = _audits.get(ip, (now, 0))
    if now - start > window:
        start, n = now, 0
    if n >= limit:
        return False
    _audits[ip] = (start, n + 1)
    return True


def is_allowed(restaurant):
    """Gérant (rôle owner, pas super-admin) du restaurant, ou d'un restaurant additionnel."""
    if restaurant is None or not current_user.is_authenticated:
        return False
    if getattr(current_user, "is_super_admin", False) or not getattr(current_user, "is_owner", False):
        return False
    if current_user.restaurant_id == restaurant.id:
        return True
    return RestaurantAccess.query.filter_by(user_id=current_user.id, restaurant_id=restaurant.id).first() is not None


def init(app, *, log_audit=None):
    @app.before_request
    def _qr_image_guard():
        if request.endpoint not in PROTECTED_ENDPOINTS:
            return None
        slug = (request.view_args or {}).get("slug")
        restaurant = Restaurant.query.filter_by(slug=slug).first() if slug else None
        if is_allowed(restaurant):
            return None
        if log_audit is not None and restaurant is not None and _may_audit(request.remote_addr or "?"):
            try:
                log_audit("qr_image_denied", success=False, restaurant_id=restaurant.id,
                          user_email=getattr(current_user, "email", None) if current_user.is_authenticated else None,
                          details=f"{request.endpoint} #{(request.view_args or {}).get('qr_id')} ip={request.remote_addr}"[:300])
            except Exception:
                log.exception("Journal indisponible")
        abort(404)                      # identique que le QR existe ou non

    @app.after_request
    def _qr_image_no_store(response):
        if request.endpoint in PROTECTED_ENDPOINTS:
            response.headers["Cache-Control"] = "private, no-store"
        return response
