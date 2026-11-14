"""
MenuFlash — Correction MF-18 (fausses commandes) : anti-robot Cloudflare Turnstile.

Ajoute une vérification anti-robot UNIQUEMENT aux commandes passées sans QR code
de table (lien général / à emporter). Les commandes à table ne changent pas.
La plupart des clients ne voient rien (vérification invisible) ; une case
n'apparaît qu'en cas de doute.

Ce que fait le script :
  1. crée turnstile_guard.py (vérification du jeton auprès de Cloudflare) ;
  2. app.py : active la vérification ;
  3. CSP (csp_policy.py, ou app.py si MF-06 n'est pas installée) : autorise
     https://challenges.cloudflare.com (script + cadre) ;
  4. templates/public_menu.html : widget dans la fenêtre « À emporter » ;
  5. static/app.js : nouveau jeton après un échec (et version du fichier +1).

Sans TURNSTILE_SITE_KEY et TURNSTILE_SECRET_KEY dans Render, RIEN ne change.

UTILISATION (à la racine du projet) :  python corriger_mf18.py
Vérifie tout avant d'écrire ; copies : *.avant-mf18
"""
import os
import re
import shutil
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
GUARD = '"""\nAnti-robot Cloudflare Turnstile sur les commandes à distance (audit MF-18).\n\nOù ?  UNIQUEMENT sur les commandes passées SANS QR code de table (lien général /\n      commande à emporter). Les commandes à table (QR scanné) ne changent pas.\nComment ?\n  - Le navigateur affiche le widget Turnstile dans la fenêtre « À emporter ».\n    En mode « interaction-only », la plupart des clients ne voient RIEN : la\n    vérification se fait en arrière-plan. Une case n\'apparaît qu\'en cas de doute.\n  - Le serveur vérifie le jeton auprès de Cloudflare (Siteverify) AVANT de créer\n    la commande. Un robot qui envoie directement la requête est refusé.\nConfiguration (Render > Environment) :\n  TURNSTILE_SITE_KEY    clé publique du widget\n  TURNSTILE_SECRET_KEY  clé secrète (ne jamais la mettre dans le code)\n  Les deux absentes  -> protection désactivée (comportement d\'avant).\nSi Cloudflare ne répond pas (panne, délai de 5 s) : la commande est ACCEPTÉE et\nl\'incident est journalisé : une panne externe ne doit jamais bloquer un restaurant.\n"""\nimport json\nimport logging\nimport os\nimport urllib.parse\nimport urllib.request\n\nfrom flask import flash, jsonify, redirect, request, url_for\n\nlog = logging.getLogger("menuflash.turnstile")\n\nSITEVERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"\nTOKEN_FIELD = "cf-turnstile-response"\nTIMEOUT_SECONDS = 5\nPROTECTED_ENDPOINTS = {"place_order"}\n\nMSG_MISSING = ("Vérification anti-robot en cours : patientez quelques secondes puis "\n               "appuyez de nouveau sur « Confirmer la commande ».")\nMSG_FAILED = ("La vérification anti-robot a échoué. Rechargez la page puis réessayez. "\n              "Si le problème continue, commandez sur place ou appelez le restaurant.")\n\n\ndef site_key():\n    return (os.environ.get("TURNSTILE_SITE_KEY") or "").strip()\n\n\ndef secret_key():\n    return (os.environ.get("TURNSTILE_SECRET_KEY") or "").strip()\n\n\ndef enabled():\n    return bool(site_key() and secret_key())\n\n\ndef verify_token(token, remote_ip=None):\n    """Renvoie (ok, raison). Cloudflare injoignable -> (True, "indisponible")."""\n    token = (token or "").strip()\n    if not token:\n        return False, "jeton absent"\n    if len(token) > 2048:\n        return False, "jeton trop long"\n    data = {"secret": secret_key(), "response": token}\n    if remote_ip:\n        data["remoteip"] = remote_ip\n    req = urllib.request.Request(SITEVERIFY_URL, data=urllib.parse.urlencode(data).encode(),\n                                 method="POST",\n                                 headers={"Content-Type": "application/x-www-form-urlencoded"})\n    try:\n        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:\n            result = json.loads(resp.read().decode("utf-8") or "{}")\n    except Exception as exc:                     # panne réseau ou Cloudflare : on n\'empêche pas de commander\n        log.error("Turnstile injoignable (%s) : commande acceptée sans vérification.", type(exc).__name__)\n        return True, "indisponible"\n    if result.get("success"):\n        return True, "ok"\n    return False, ",".join(result.get("error-codes") or []) or "refusé"\n\n\ndef _is_table_order(slug):\n    """Vrai si la commande vient d\'un QR code de table valide (elle n\'est pas concernée)."""\n    try:\n        from models import Restaurant\n        from takeaway import resolve_table\n        restaurant = Restaurant.query.filter_by(slug=slug).first()\n        if restaurant is None:\n            return False\n        return bool(resolve_table(restaurant.id, request.form.get("qr_id", type=int),\n                                  request.form.get("table_label")))\n    except Exception:\n        log.exception("Turnstile : impossible de déterminer la table")\n        return False\n\n\ndef init(app, *, log_audit=None, client_ip=None):\n    if site_key() and not secret_key() or secret_key() and not site_key():\n        log.error("Turnstile : il faut TURNSTILE_SITE_KEY ET TURNSTILE_SECRET_KEY. Protection désactivée.")\n\n    @app.context_processor\n    def _inject_turnstile():\n        return {"turnstile_site_key": site_key() if enabled() else ""}\n\n    @app.before_request\n    def _turnstile_gate():\n        if request.method != "POST" or request.endpoint not in PROTECTED_ENDPOINTS or not enabled():\n            return None\n        slug = (request.view_args or {}).get("slug", "")\n        if _is_table_order(slug):\n            return None\n        ip = client_ip() if client_ip else request.remote_addr\n        ok, reason = verify_token(request.form.get(TOKEN_FIELD), ip)\n        if ok:\n            return None\n        if log_audit:\n            try:\n                log_audit("order_captcha_failed", success=False, user_email=None,\n                          details=f"{slug} : {reason}"[:300])\n            except Exception:\n                log.exception("Journal indisponible")\n        message = MSG_MISSING if reason == "jeton absent" else MSG_FAILED\n        if request.headers.get("X-Requested-With") == "fetch":\n            return jsonify(ok=False, error=message, captcha=True), 403\n        flash(message, "error")\n        return redirect(url_for("public_menu", slug=slug))\n'
CF = "https://challenges.cloudflare.com"

APP_OLD = '''if __name__ == "__main__":'''
APP_NEW = '''import turnstile_guard                             # audit MF-18 : anti-robot sur les commandes à distance
turnstile_guard.init(app, log_audit=log_audit, client_ip=get_client_ip)


if __name__ == "__main__":'''

TPL_OLD = '''          </div>
          <div class="modal-footer border-0 flex-nowrap">'''
TPL_NEW = '''            {% if turnstile_site_key %}
            {# Audit MF-18 : anti-robot (invisible pour la plupart des clients) #}
            <div class="cf-turnstile mt-3" data-sitekey="{{ turnstile_site_key }}"
                 data-appearance="interaction-only" data-theme="light"></div>
            <script src="https://challenges.cloudflare.com/turnstile/v0/api.js" async defer
                    {% if csp_nonce is defined %}nonce="{{ csp_nonce() }}"{% endif %}></script>
            {% endif %}
          </div>
          <div class="modal-footer border-0 flex-nowrap">'''

JS = [
('''        .then(function (res) {
          var data = res.data || {};
''', '''        .then(function (res) {
          var data = res.data || {};
          if (!data.ok && window.turnstile) {            // audit MF-18 : nouveau jeton pour le prochain essai
            try { window.turnstile.reset(); } catch (e) {}
          }
'''),
]
CURRENT_SCRIPT_RE = re.compile(r'^(?P<ind>[ \t]*)"script-src \'self\' \'unsafe-inline\' https://cdn\.jsdelivr\.net; "\n', re.M)


def patch_csp(texts):
    """Ajoute challenges.cloudflare.com à la CSP (fichier csp_policy.py ou app.py)."""
    target = "csp_policy.py" if texts.get("csp_policy.py") else "app.py"
    t = texts[target]
    m = CURRENT_SCRIPT_RE.search(t)
    if not m or len(CURRENT_SCRIPT_RE.findall(t)) != 1:
        return None, f"directive script-src introuvable dans {target}"
    ind = m.group("ind")
    t = (t[:m.start()] + f'{ind}"script-src \'self\' \'unsafe-inline\' https://cdn.jsdelivr.net {CF}; "\n'
         f'{ind}"frame-src \'self\' {CF}; "\n' + t[m.end():])
    if target == "csp_policy.py":
        if t.count("CDN_SCRIPTS = (\n") != 1:
            return None, "CDN_SCRIPTS introuvable dans csp_policy.py"
        t = t.replace("CDN_SCRIPTS = (\n", f'CDN_SCRIPTS = (\n    "{CF}",                 # audit MF-18 (Turnstile)\n')
        m2 = re.search(r'^(?P<ind>[ \t]*)"script-src-attr \'none\'; "\n', t, re.M)
        if not m2:
            return None, "politique stricte introuvable dans csp_policy.py"
        t = t[:m2.end()] + f'{m2.group("ind")}"frame-src \'self\' {CF}; "\n' + t[m2.end():]
    return (target, t), None


def main():
    names = ["app.py", "csp_policy.py", "static/app.js", "templates/public_menu.html",
             "templates/base.html", "templates/signup.html"]
    texts = {}
    for n in names:
        p = os.path.join(ROOT, n)
        texts[n] = open(p, encoding="utf-8").read() if os.path.exists(p) else None
    if texts["app.py"] is None:
        sys.exit("ARRÊT : lancez le script à la racine du projet (là où se trouve app.py).")
    if "import turnstile_guard" in texts["app.py"]:
        print("MF-18 (Turnstile) est déjà appliquée : rien à faire.")
        return
    new = dict(texts)

    if new["app.py"].count(APP_OLD) != 1:
        sys.exit("ARRÊT : « if __name__ == \"__main__\": » introuvable dans app.py. Rien n'a été modifié.")
    new["app.py"] = new["app.py"].replace(APP_OLD, APP_NEW)

    res, err = patch_csp(new)
    if err:
        sys.exit(f"ARRÊT : {err}. Rien n'a été modifié.")
    new[res[0]] = res[1]

    tpl = new["templates/public_menu.html"]
    if not tpl or tpl.count(TPL_OLD) != 1:
        sys.exit("ARRÊT : fenêtre « À emporter » introuvable dans public_menu.html. Rien n'a été modifié.")
    new["templates/public_menu.html"] = tpl.replace(TPL_OLD, TPL_NEW)

    js = new["static/app.js"]
    for old, rep in JS:
        if not js or js.count(old) != 1:
            sys.exit("ARRÊT : envoi de la commande introuvable dans static/app.js. Rien n'a été modifié.")
        js = js.replace(old, rep)
    new["static/app.js"] = js

    for n in ("templates/base.html", "templates/signup.html"):
        if new[n]:
            new[n], k = re.subn(r"(app\.js'\) }}\?v=)(\d+)", lambda m: m.group(1) + str(int(m.group(2)) + 1), new[n])

    for n in names:
        if new[n] is not None and new[n] != texts[n]:
            p = os.path.join(ROOT, n)
            shutil.copy2(p, p + ".avant-mf18")
            with open(p, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(new[n])
            print(f"  modifié : {n}")
    with open(os.path.join(ROOT, "turnstile_guard.py"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(GUARD)
    print("  créé    : turnstile_guard.py")
    print("\nMF-18 installée. Elle reste INACTIVE tant que TURNSTILE_SITE_KEY et "
          "TURNSTILE_SECRET_KEY ne sont pas définies. Copies : *.avant-mf18")


if __name__ == "__main__":
    main()
