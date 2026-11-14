"""
Anti-robot Cloudflare Turnstile sur les commandes à distance (audit MF-18).

Où ?  UNIQUEMENT sur les commandes passées SANS QR code de table (lien général /
      commande à emporter). Les commandes à table (QR scanné) ne changent pas.
Comment ?
  - Le navigateur affiche le widget Turnstile dans la fenêtre « À emporter ».
    En mode « interaction-only », la plupart des clients ne voient RIEN : la
    vérification se fait en arrière-plan. Une case n'apparaît qu'en cas de doute.
  - Le serveur vérifie le jeton auprès de Cloudflare (Siteverify) AVANT de créer
    la commande. Un robot qui envoie directement la requête est refusé.
Configuration (Render > Environment) :
  TURNSTILE_SITE_KEY    clé publique du widget
  TURNSTILE_SECRET_KEY  clé secrète (ne jamais la mettre dans le code)
  Les deux absentes  -> protection désactivée (comportement d'avant).
Si Cloudflare ne répond pas (panne, délai de 5 s) : la commande est ACCEPTÉE et
l'incident est journalisé : une panne externe ne doit jamais bloquer un restaurant.
"""
import json
import logging
import os
import urllib.parse
import urllib.request

from flask import flash, jsonify, redirect, request, url_for

log = logging.getLogger("menuflash.turnstile")

SITEVERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
TOKEN_FIELD = "cf-turnstile-response"
TIMEOUT_SECONDS = 5
PROTECTED_ENDPOINTS = {"place_order"}

MSG_MISSING = ("Vérification anti-robot en cours : patientez quelques secondes puis "
               "appuyez de nouveau sur « Confirmer la commande ».")
MSG_FAILED = ("La vérification anti-robot a échoué. Rechargez la page puis réessayez. "
              "Si le problème continue, commandez sur place ou appelez le restaurant.")


def site_key():
    return (os.environ.get("TURNSTILE_SITE_KEY") or "").strip()


def secret_key():
    return (os.environ.get("TURNSTILE_SECRET_KEY") or "").strip()


def enabled():
    return bool(site_key() and secret_key())


def verify_token(token, remote_ip=None):
    """Renvoie (ok, raison). Cloudflare injoignable -> (True, "indisponible")."""
    token = (token or "").strip()
    if not token:
        return False, "jeton absent"
    if len(token) > 2048:
        return False, "jeton trop long"
    data = {"secret": secret_key(), "response": token}
    if remote_ip:
        data["remoteip"] = remote_ip
    req = urllib.request.Request(SITEVERIFY_URL, data=urllib.parse.urlencode(data).encode(),
                                 method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            result = json.loads(resp.read().decode("utf-8") or "{}")
    except Exception as exc:                     # panne réseau ou Cloudflare : on n'empêche pas de commander
        log.error("Turnstile injoignable (%s) : commande acceptée sans vérification.", type(exc).__name__)
        return True, "indisponible"
    if result.get("success"):
        return True, "ok"
    return False, ",".join(result.get("error-codes") or []) or "refusé"


def _is_table_order(slug):
    """Vrai si la commande vient d'un QR code de table valide (elle n'est pas concernée)."""
    try:
        from models import Restaurant
        from takeaway import resolve_table
        restaurant = Restaurant.query.filter_by(slug=slug).first()
        if restaurant is None:
            return False
        return bool(resolve_table(restaurant.id, request.form.get("qr_id", type=int),
                                  request.form.get("table_label")))
    except Exception:
        log.exception("Turnstile : impossible de déterminer la table")
        return False


def init(app, *, log_audit=None, client_ip=None):
    if site_key() and not secret_key() or secret_key() and not site_key():
        log.error("Turnstile : il faut TURNSTILE_SITE_KEY ET TURNSTILE_SECRET_KEY. Protection désactivée.")

    @app.context_processor
    def _inject_turnstile():
        return {"turnstile_site_key": site_key() if enabled() else ""}

    @app.before_request
    def _turnstile_gate():
        if request.method != "POST" or request.endpoint not in PROTECTED_ENDPOINTS or not enabled():
            return None
        slug = (request.view_args or {}).get("slug", "")
        if _is_table_order(slug):
            return None
        ip = client_ip() if client_ip else request.remote_addr
        ok, reason = verify_token(request.form.get(TOKEN_FIELD), ip)
        if ok:
            return None
        if log_audit:
            try:
                log_audit("order_captcha_failed", success=False, user_email=None,
                          details=f"{slug} : {reason}"[:300])
            except Exception:
                log.exception("Journal indisponible")
        message = MSG_MISSING if reason == "jeton absent" else MSG_FAILED
        if request.headers.get("X-Requested-With") == "fetch":
            return jsonify(ok=False, error=message, captcha=True), 403
        flash(message, "error")
        return redirect(url_for("public_menu", slug=slug))
