"""
MenuFlash — Correction de sécurité MF-06 (audit du 13/11/2026) : politique CSP.

PROBLÈME
--------
La politique de sécurité des contenus (CSP) autorisait 'unsafe-inline' pour les
scripts et TOUT le CDN cdn.jsdelivr.net. Résultat : si un attaquant réussissait
à injecter du HTML (faille XSS), son script s'exécutait quand même, et il pouvait
charger n'importe quel paquet publié sur ce CDN. La CSP ne servait pas de
deuxième ligne de défense.

CORRECTION EN DEUX TEMPS (sans risque pour le service)
------------------------------------------------------
1. Nouveau fichier csp_policy.py :
   - chaque page reçoit un jeton aléatoire (« nonce ») ; seuls les scripts de
     MenuFlash, les 3 bibliothèques utilisées (versions exactes) et les scripts
     portant ce jeton sont autorisés ; onclick=… interdits ;
   - point de réception /csp-rapport : les navigateurs y signalent ce qui serait
     bloqué ; visible dans les logs Render (préfixe « CSP-RAPPORT »).
2. Mode « observation » par défaut : la politique stricte est seulement
   observée (rien n'est bloqué). Après quelques jours sans signalement, on
   l'active avec CSP_MODE=stricte (sans redéployer de code).
3. app.py : utilise csp_policy ; protection immédiate et sans risque ajoutée
   (object-src 'none', base-uri 'self', frame-ancestors ; le ticket imprimé
   garde le droit d'être affiché dans un cadre de MenuFlash).
4. 14 gabarits : les scripts intégrés reçoivent le jeton de la page.
5. two_factor_recovery_codes.html : bouton « Imprimer » sans onclick=…

UTILISATION (à la racine du projet) :  python corriger_mf06.py
Vérifie tout avant d'écrire ; copies : *.avant-mf06
"""
import os
import re
import shutil
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))

CSP_POLICY = '"""\nPolitique de sécurité des contenus (CSP) de MenuFlash — audit MF-06.\n\nDeux politiques\n---------------\n- « actuelle » : celle d\'avant, renforcée sans risque (object-src \'none\',\n  base-uri \'self\', frame-ancestors).\n- « stricte » : scripts autorisés UNIQUEMENT s\'ils viennent de MenuFlash, des\n  3 bibliothèques utilisées (versions exactes sur cdn.jsdelivr.net) ou s\'ils\n  portent le jeton aléatoire (« nonce ») de la page. Plus de \'unsafe-inline\'\n  pour les scripts : un script injecté (XSS) ne s\'exécute plus. Gestionnaires\n  onclick=… interdits. Formulaires envoyés uniquement vers MenuFlash.\n\nVariable CSP_MODE (Render > Environment)\n----------------------------------------\n- absente / « observation » (défaut) : la politique actuelle est appliquée ; la\n  stricte est seulement OBSERVÉE (en-tête Report-Only) : le navigateur signale\n  ce qu\'elle bloquerait, sans rien bloquer. Les signalements apparaissent dans\n  les logs Render avec le préfixe « CSP-RAPPORT ».\n- « stricte » : la politique stricte est appliquée (et continue de signaler).\nRetour arrière immédiat : remettre CSP_MODE=observation (aucun code à changer).\n"""\nimport json\nimport logging\nimport os\nimport secrets\nimport threading\nimport time\n\nfrom flask import g, request\n\nlog = logging.getLogger("menuflash.csp")\n\nREPORT_PATH = "/csp-rapport"\nTICKET_ENDPOINT = "order_ticket"          # ticket imprimé dans un cadre (tickets.py)\n\n# Versions EXACTES des bibliothèques chargées depuis le CDN (un autre paquet du\n# même CDN ne peut plus être chargé).\nCDN_SCRIPTS = (\n    "https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/",\n    "https://cdn.jsdelivr.net/npm/chart.js@4.4.4/",\n    "https://cdn.jsdelivr.net/npm/sortablejs@1.15.2/",\n)\nCDN_STYLES = (\n    "https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/",\n    "https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/",\n    "https://fonts.googleapis.com",\n)\nCDN_FONTS = ("https://fonts.gstatic.com", "https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/")\n\n\ndef mode():\n    return "stricte" if (os.environ.get("CSP_MODE") or "").strip().lower() in ("stricte", "strict") else "observation"\n\n\ndef csp_nonce():\n    """Jeton aléatoire de la page (nouveau à chaque requête)."""\n    nonce = getattr(g, "_mf_csp_nonce", None)\n    if nonce is None:\n        nonce = g._mf_csp_nonce = secrets.token_urlsafe(18)\n    return nonce\n\n\ndef _frame_ancestors():\n    return "\'self\'" if request.endpoint == TICKET_ENDPOINT else "\'none\'"\n\n\ndef current_policy():\n    return ("default-src \'self\'; "\n            "script-src \'self\' \'unsafe-inline\' https://cdn.jsdelivr.net; "\n            "style-src \'self\' \'unsafe-inline\' https://cdn.jsdelivr.net https://fonts.googleapis.com; "\n            "font-src \'self\' https://fonts.gstatic.com https://cdn.jsdelivr.net; "\n            "img-src \'self\' data: blob:; "\n            "connect-src \'self\'; "\n            "object-src \'none\'; base-uri \'self\'; "\n            f"frame-ancestors {_frame_ancestors()};")\n\n\ndef strict_policy():\n    return ("default-src \'self\'; "\n            f"script-src \'self\' \'nonce-{csp_nonce()}\' {\' \'.join(CDN_SCRIPTS)}; "\n            "script-src-attr \'none\'; "\n            f"style-src \'self\' \'unsafe-inline\' {\' \'.join(CDN_STYLES)}; "\n            f"font-src \'self\' {\' \'.join(CDN_FONTS)}; "\n            "img-src \'self\' data: blob:; "\n            "connect-src \'self\'; "\n            "object-src \'none\'; base-uri \'self\'; form-action \'self\'; "\n            f"frame-ancestors {_frame_ancestors()}; "\n            f"report-uri {REPORT_PATH};")\n\n\ndef apply_headers(response):\n    """Appelée par set_security_headers (app.py) pour chaque réponse."""\n    response.headers.pop("Content-Security-Policy-Report-Only", None)\n    if mode() == "stricte":\n        response.headers["Content-Security-Policy"] = strict_policy()\n    else:\n        response.headers["Content-Security-Policy"] = current_policy()\n        response.headers["Content-Security-Policy-Report-Only"] = strict_policy()\n    return response\n\n\n# ---------------------------------------------------------------- signalements\n_seen = {}\n_seen_lock = threading.Lock()\n_DEDUP_SECONDS = 3600\n_MAX_KEYS = 500\n\n\ndef _clean(value, limit=160):\n    text = str(value or "")\n    for sep in ("?", "#"):\n        text = text.split(sep)[0]\n    return "".join(ch for ch in text if ch.isprintable())[:limit]\n\n\ndef _summarize(payload):\n    """Extrait (directive, ressource bloquée, page, ligne) d\'un signalement CSP."""\n    items = []\n    if isinstance(payload, dict) and isinstance(payload.get("csp-report"), dict):\n        items.append(payload["csp-report"])\n    elif isinstance(payload, list):                                  # format « Reporting API »\n        items.extend(r.get("body") for r in payload[:20] if isinstance(r, dict) and isinstance(r.get("body"), dict))\n    out = []\n    for r in items:\n        directive = r.get("violated-directive") or r.get("effective-directive") or r.get("effectiveDirective")\n        blocked = r.get("blocked-uri") or r.get("blockedURL") or ""\n        page = r.get("document-uri") or r.get("documentURL") or ""\n        line = r.get("line-number") or r.get("lineNumber") or ""\n        source = r.get("source-file") or r.get("sourceFile") or ""\n        out.append((_clean(directive, 60), _clean(blocked), _clean(page), _clean(source), str(line)[:8]))\n    return out\n\n\ndef init(app, *, csrf=None, limiter=None):\n    app.jinja_env.globals["csp_nonce"] = csp_nonce\n\n    def csp_report():\n        raw = request.get_data(cache=False, as_text=True)[:20000]\n        try:\n            payload = json.loads(raw or "null")\n        except ValueError:\n            return ("", 204)\n        now = time.monotonic()\n        for directive, blocked, page, source, line in _summarize(payload):\n            key = (directive, blocked, page)\n            with _seen_lock:\n                if now - _seen.get(key, -_DEDUP_SECONDS) < _DEDUP_SECONDS:\n                    continue\n                if len(_seen) > _MAX_KEYS:\n                    _seen.clear()\n                _seen[key] = now\n            log.warning("CSP-RAPPORT [%s] directive=%s bloqué=%s page=%s fichier=%s ligne=%s",\n                        mode(), directive, blocked or "(inline)", page, source or "-", line or "-")\n        return ("", 204)\n\n    view = csp_report\n    if limiter is not None:\n        view = limiter.limit("60 per minute")(view)\n    if csrf is not None:\n        view = csrf.exempt(view)          # le navigateur envoie ces rapports sans jeton CSRF\n    app.add_url_rule(REPORT_PATH, "csp_report", view, methods=["POST"])\n    if os.environ.get("RENDER"):\n        log.warning("CSP en mode « %s » (variable CSP_MODE).", mode())\n'

APP_OLD_CSP = """    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com https://cdn.jsdelivr.net; "
        "img-src 'self' data: blob:; "
        "connect-src 'self';"
    )
"""
APP_NEW_CSP = """    csp_policy.apply_headers(response)              # audit MF-06 (voir csp_policy.py)
"""
APP_OLD_INIT = """limiter.exempt(app.view_functions["static"])
"""
APP_NEW_INIT = """limiter.exempt(app.view_functions["static"])

import csp_policy                                  # audit MF-06 : CSP avec jeton par page
csp_policy.init(app, csrf=csrf, limiter=limiter)
"""

PRINT_OLD = """<button type="button" class="btn btn-outline-secondary btn-sm flex-grow-1" onclick="window.print()">"""
PRINT_NEW = """<button type="button" class="btn btn-outline-secondary btn-sm flex-grow-1" id="mf-print-codes">"""
PRINT_SCRIPT = """<script nonce="{{ csp_nonce() }}">
  document.getElementById("mf-print-codes").addEventListener("click", function () { window.print(); });
</script>
{% endblock %}"""

BARE_SCRIPT = re.compile(r"<script>")


def main():
    app_path = os.path.join(ROOT, "app.py")
    tpl_dir = os.path.join(ROOT, "templates")
    if not os.path.exists(app_path) or not os.path.isdir(tpl_dir):
        sys.exit("ARRÊT : app.py ou templates/ introuvable (lancez le script à la racine du projet).")
    app_t = open(app_path, encoding="utf-8").read()
    if "import csp_policy" in app_t:
        print("MF-06 est déjà appliquée : rien à faire.")
        return
    for old, label in ((APP_OLD_CSP, "en-tête Content-Security-Policy"), (APP_OLD_INIT, "limiter.exempt")):
        if app_t.count(old) != 1:
            sys.exit(f"ARRÊT : repère « {label} » introuvable dans app.py. Aucun fichier n'a été modifié.")
    if "csrf = CSRFProtect(app)" not in app_t or app_t.index("csrf = CSRFProtect(app)") > app_t.index(APP_OLD_INIT):
        sys.exit("ARRÊT : « csrf = CSRFProtect(app) » introuvable avant le limiteur. Aucun fichier n'a été modifié.")
    app_t = app_t.replace(APP_OLD_CSP, APP_NEW_CSP).replace(APP_OLD_INIT, APP_NEW_INIT)

    tpl_changes = {}
    for name in sorted(os.listdir(tpl_dir)):
        if not name.endswith(".html"):
            continue
        path = os.path.join(tpl_dir, name)
        text = open(path, encoding="utf-8").read()
        new = BARE_SCRIPT.sub('<script nonce="{{ csp_nonce() }}">', text)
        if name == "two_factor_recovery_codes.html":
            if new.count(PRINT_OLD) != 1 or not new.rstrip().endswith("{% endblock %}"):
                sys.exit("ARRÊT : bouton « Imprimer » introuvable dans two_factor_recovery_codes.html. "
                         "Aucun fichier n'a été modifié.")
            new = new.replace(PRINT_OLD, PRINT_NEW)
            head, _, _ = new.rstrip().rpartition("{% endblock %}")
            new = head + PRINT_SCRIPT + "\n"
        if new != text:
            tpl_changes[path] = new
    left = [n for n in os.listdir(tpl_dir) if n.endswith(".html") and
            re.search(r"\son(click|change|submit|load|error|input)\s*=",
                      tpl_changes.get(os.path.join(tpl_dir, n)) or open(os.path.join(tpl_dir, n), encoding="utf-8").read())]
    if left:
        print("  ATTENTION : gestionnaires on…= restants (à signaler) :", ", ".join(left))

    shutil.copy2(app_path, app_path + ".avant-mf06")
    with open(app_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(app_t)
    print("  modifié : app.py")
    with open(os.path.join(ROOT, "csp_policy.py"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(CSP_POLICY)
    print("  créé    : csp_policy.py")
    for path, text in tpl_changes.items():
        shutil.copy2(path, path + ".avant-mf06")
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
    print(f"  modifié : {len(tpl_changes)} gabarit(s) dans templates/")
    print("\nMF-06 appliquée en mode OBSERVATION (rien n'est bloqué). Copies : *.avant-mf06")


if __name__ == "__main__":
    main()
