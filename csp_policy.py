"""
Politique de sécurité des contenus (CSP) de MenuFlash — audit MF-06.

Deux politiques
---------------
- « actuelle » : celle d'avant, renforcée sans risque (object-src 'none',
  base-uri 'self', frame-ancestors).
- « stricte » : scripts autorisés UNIQUEMENT s'ils viennent de MenuFlash, des
  3 bibliothèques utilisées (versions exactes sur cdn.jsdelivr.net) ou s'ils
  portent le jeton aléatoire (« nonce ») de la page. Plus de 'unsafe-inline'
  pour les scripts : un script injecté (XSS) ne s'exécute plus. Gestionnaires
  onclick=… interdits. Formulaires envoyés uniquement vers MenuFlash.

Variable CSP_MODE (Render > Environment)
----------------------------------------
- absente / « observation » (défaut) : la politique actuelle est appliquée ; la
  stricte est seulement OBSERVÉE (en-tête Report-Only) : le navigateur signale
  ce qu'elle bloquerait, sans rien bloquer. Les signalements apparaissent dans
  les logs Render avec le préfixe « CSP-RAPPORT ».
- « stricte » : la politique stricte est appliquée (et continue de signaler).
Retour arrière immédiat : remettre CSP_MODE=observation (aucun code à changer).
"""
import json
import logging
import os
import secrets
import threading
import time

from flask import g, request

log = logging.getLogger("menuflash.csp")

REPORT_PATH = "/csp-rapport"
TICKET_ENDPOINT = "order_ticket"          # ticket imprimé dans un cadre (tickets.py)

# Versions EXACTES des bibliothèques chargées depuis le CDN (un autre paquet du
# même CDN ne peut plus être chargé).
CDN_SCRIPTS = (
    "https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/",
    "https://cdn.jsdelivr.net/npm/chart.js@4.4.4/",
    "https://cdn.jsdelivr.net/npm/sortablejs@1.15.2/",
)
CDN_STYLES = (
    "https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/",
    "https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/",
    "https://fonts.googleapis.com",
)
CDN_FONTS = ("https://fonts.gstatic.com", "https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/")


def mode():
    return "stricte" if (os.environ.get("CSP_MODE") or "").strip().lower() in ("stricte", "strict") else "observation"


def csp_nonce():
    """Jeton aléatoire de la page (nouveau à chaque requête)."""
    nonce = getattr(g, "_mf_csp_nonce", None)
    if nonce is None:
        nonce = g._mf_csp_nonce = secrets.token_urlsafe(18)
    return nonce


def _frame_ancestors():
    return "'self'" if request.endpoint == TICKET_ENDPOINT else "'none'"


def current_policy():
    return ("default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com https://cdn.jsdelivr.net; "
            "img-src 'self' data: blob:; "
            "connect-src 'self'; "
            "object-src 'none'; base-uri 'self'; "
            f"frame-ancestors {_frame_ancestors()};")


def strict_policy():
    return ("default-src 'self'; "
            f"script-src 'self' 'nonce-{csp_nonce()}' {' '.join(CDN_SCRIPTS)}; "
            "script-src-attr 'none'; "
            f"style-src 'self' 'unsafe-inline' {' '.join(CDN_STYLES)}; "
            f"font-src 'self' {' '.join(CDN_FONTS)}; "
            "img-src 'self' data: blob:; "
            "connect-src 'self'; "
            "object-src 'none'; base-uri 'self'; form-action 'self'; "
            f"frame-ancestors {_frame_ancestors()}; "
            f"report-uri {REPORT_PATH};")


def apply_headers(response):
    """Appelée par set_security_headers (app.py) pour chaque réponse."""
    response.headers.pop("Content-Security-Policy-Report-Only", None)
    if mode() == "stricte":
        response.headers["Content-Security-Policy"] = strict_policy()
    else:
        response.headers["Content-Security-Policy"] = current_policy()
        response.headers["Content-Security-Policy-Report-Only"] = strict_policy()
    return response


# ---------------------------------------------------------------- signalements
_seen = {}
_seen_lock = threading.Lock()
_DEDUP_SECONDS = 3600
_MAX_KEYS = 500


def _clean(value, limit=160):
    text = str(value or "")
    for sep in ("?", "#"):
        text = text.split(sep)[0]
    return "".join(ch for ch in text if ch.isprintable())[:limit]


def _summarize(payload):
    """Extrait (directive, ressource bloquée, page, ligne) d'un signalement CSP."""
    items = []
    if isinstance(payload, dict) and isinstance(payload.get("csp-report"), dict):
        items.append(payload["csp-report"])
    elif isinstance(payload, list):                                  # format « Reporting API »
        items.extend(r.get("body") for r in payload[:20] if isinstance(r, dict) and isinstance(r.get("body"), dict))
    out = []
    for r in items:
        directive = r.get("violated-directive") or r.get("effective-directive") or r.get("effectiveDirective")
        blocked = r.get("blocked-uri") or r.get("blockedURL") or ""
        page = r.get("document-uri") or r.get("documentURL") or ""
        line = r.get("line-number") or r.get("lineNumber") or ""
        source = r.get("source-file") or r.get("sourceFile") or ""
        out.append((_clean(directive, 60), _clean(blocked), _clean(page), _clean(source), str(line)[:8]))
    return out


def init(app, *, csrf=None, limiter=None):
    app.jinja_env.globals["csp_nonce"] = csp_nonce

    def csp_report():
        raw = request.get_data(cache=False, as_text=True)[:20000]
        try:
            payload = json.loads(raw or "null")
        except ValueError:
            return ("", 204)
        now = time.monotonic()
        for directive, blocked, page, source, line in _summarize(payload):
            key = (directive, blocked, page)
            with _seen_lock:
                if now - _seen.get(key, -_DEDUP_SECONDS) < _DEDUP_SECONDS:
                    continue
                if len(_seen) > _MAX_KEYS:
                    _seen.clear()
                _seen[key] = now
            log.warning("CSP-RAPPORT [%s] directive=%s bloqué=%s page=%s fichier=%s ligne=%s",
                        mode(), directive, blocked or "(inline)", page, source or "-", line or "-")
        return ("", 204)

    view = csp_report
    if limiter is not None:
        view = limiter.limit("60 per minute")(view)
    if csrf is not None:
        view = csrf.exempt(view)          # le navigateur envoie ces rapports sans jeton CSRF
    app.add_url_rule(REPORT_PATH, "csp_report", view, methods=["POST"])
    if os.environ.get("RENDER"):
        log.warning("CSP en mode « %s » (variable CSP_MODE).", mode())
