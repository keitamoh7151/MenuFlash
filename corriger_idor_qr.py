"""
MenuFlash — Correction IDOR-01 : les QR codes de table (qui contiennent le code secret de la table)
ne sont plus téléchargeables par n'importe qui.

Ce que fait le script :
  1. crée qr_access_guard.py ;
  2. app.py : branche la protection (2 lignes avant « if __name__ == "__main__": »).

UTILISATION (à la racine du projet) :  python corriger_idor_qr.py
Contrôle :  python corriger_idor_qr.py --verifier
Vérifie tout avant d'écrire ; copie : app.py.avant-idor
"""
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
GUARD = '"""\nAccès aux images et PDF des QR codes de TABLE (audit IDOR-01).\n\nPROBLÈME : /m/<slug>/qrcodes/<numéro>.png et .pdf étaient publics. Or l\'image d\'un QR de table\ncontient le code secret de la table (qr_security.py). N\'importe qui, sans compte, pouvait donc\ntélécharger le QR de n\'importe quelle table de n\'importe quel restaurant (adresse du restaurant\npublique, numéros 1, 2, 3…), lire le secret et commander pour cette table depuis ailleurs.\n\nCORRECTION : ces deux adresses sont réservées au GÉRANT du restaurant concerné (compte « owner »\ndu restaurant, ou restaurant additionnel auquel il a accès). Tout autre visiteur (anonyme,\ncuisinier, gérant d\'un autre restaurant, super-admin) reçoit une page « introuvable » identique,\nque le QR existe ou non. Les réponses ne sont jamais mises en cache.\nLes QR généraux du menu (/m/<slug>/qrcode.png|pdf), qui ne contiennent aucun secret, restent publics.\n"""\nimport logging\nimport time\n\nfrom flask import abort, request\nfrom flask_login import current_user\n\nfrom models import Restaurant, RestaurantAccess\n\nlog = logging.getLogger("menuflash.qr_access")\nPROTECTED_ENDPOINTS = {"table_qrcode_png", "table_qrcode_pdf"}\n_audits = {}\n\n\ndef _may_audit(ip, window=600, limit=3):\n    now = time.monotonic()\n    if len(_audits) > 2000:\n        for k in [k for k, v in _audits.items() if now - v[0] > window]:\n            _audits.pop(k, None)\n    start, n = _audits.get(ip, (now, 0))\n    if now - start > window:\n        start, n = now, 0\n    if n >= limit:\n        return False\n    _audits[ip] = (start, n + 1)\n    return True\n\n\ndef is_allowed(restaurant):\n    """Gérant (rôle owner, pas super-admin) du restaurant, ou d\'un restaurant additionnel."""\n    if restaurant is None or not current_user.is_authenticated:\n        return False\n    if getattr(current_user, "is_super_admin", False) or not getattr(current_user, "is_owner", False):\n        return False\n    if current_user.restaurant_id == restaurant.id:\n        return True\n    return RestaurantAccess.query.filter_by(user_id=current_user.id, restaurant_id=restaurant.id).first() is not None\n\n\ndef init(app, *, log_audit=None):\n    @app.before_request\n    def _qr_image_guard():\n        if request.endpoint not in PROTECTED_ENDPOINTS:\n            return None\n        slug = (request.view_args or {}).get("slug")\n        restaurant = Restaurant.query.filter_by(slug=slug).first() if slug else None\n        if is_allowed(restaurant):\n            return None\n        if log_audit is not None and restaurant is not None and _may_audit(request.remote_addr or "?"):\n            try:\n                log_audit("qr_image_denied", success=False, restaurant_id=restaurant.id,\n                          user_email=getattr(current_user, "email", None) if current_user.is_authenticated else None,\n                          details=f"{request.endpoint} #{(request.view_args or {}).get(\'qr_id\')} ip={request.remote_addr}"[:300])\n            except Exception:\n                log.exception("Journal indisponible")\n        abort(404)                      # identique que le QR existe ou non\n\n    @app.after_request\n    def _qr_image_no_store(response):\n        if request.endpoint in PROTECTED_ENDPOINTS:\n            response.headers["Cache-Control"] = "private, no-store"\n        return response\n'
OLD = 'if __name__ == "__main__":'
NEW = ('import qr_access_guard                           # audit IDOR-01 : QR de table réservés au gérant\n'
       'qr_access_guard.init(app, log_audit=log_audit)\n\n\n'
       'if __name__ == "__main__":')


def main():
    app_path = os.path.join(ROOT, "app.py")
    if not os.path.exists(app_path):
        sys.exit("ARRÊT : lancez le script à la racine du projet (là où se trouve app.py).")
    text = open(app_path, encoding="utf-8").read()
    if "--verifier" in sys.argv:
        checks = [("qr_access_guard.py présent", os.path.exists(os.path.join(ROOT, "qr_access_guard.py"))),
                  ("app.py : qr_access_guard branché", "qr_access_guard.init(" in text)]
        for label, ok in checks:
            print(("  OK     " if ok else "  MANQUE ") + label)
        sys.exit(0 if all(ok for _, ok in checks) else 1)
    if "qr_access_guard.init(" in text:
        print("IDOR-01 est déjà corrigée : rien à faire.")
        return
    if text.count(OLD) != 1:
        sys.exit('ARRÊT : « if __name__ == "__main__": » introuvable dans app.py. Rien n\'a été modifié.')
    shutil.copy2(app_path, app_path + ".avant-idor")
    with open(app_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text.replace(OLD, NEW))
    with open(os.path.join(ROOT, "qr_access_guard.py"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(GUARD)
    print("  modifié : app.py")
    print("  créé    : qr_access_guard.py")
    print("\nIDOR-01 corrigée. Contrôle :  python corriger_idor_qr.py --verifier")


if __name__ == "__main__":
    main()
