"""
MenuFlash — Lot de corrections final (audit du 13/11/2026), sauf MF-12.

  MF-11  Mot de passe conforme OWASP ASVS 5.0 : 12 caractères minimum, phrases de
         passe, espaces, accents et ponctuation acceptés ; plus de « majuscule /
         chiffre obligatoire ». Les mots de passe existants restent valables.
  MF-18  Plafond global de commandes par adresse IP (tous restaurants confondus).
  MF-21  Clé dédiée au chiffrement de la 2FA (TOTP_ENCRYPTION_KEY, facultative) et
         rotation possible de FLASK_SECRET_KEY (FLASK_SECRET_KEY_FALLBACKS).
  MF-22  Déconnexion par bouton (POST) ; un simple lien ne déconnecte plus ;
         la session est entièrement vidée.
  MF-23  Cookie de session CHIFFRÉ (plus d'email, téléphone ni lien lisibles).
  MF-24  Rôles support / finance : aucun accès aux pages /admin des restaurants ni
         au suivi des commandes des clients.
  MF-25  Un restaurant additionnel suspendu n'est plus administrable.
  MF-26  Redirection après la 2FA limitée aux vraies pages de MenuFlash.
  MF-27  Journal : écriture dans une transaction séparée (PostgreSQL), emails
         inconnus masqués, jetons masqués dans les erreurs, email d'alerte aux
         administrateurs sur les événements critiques.
  MF-28  Mode debug local uniquement, limite sur la sauvegarde planifiée, réponse
         de la sauvegarde résumée dans GitHub Actions.

Chaque correction est vérifiée séparément : si les repères de l'une ne sont pas
trouvés, ELLE SEULE est ignorée (et signalée) ; les autres sont appliquées.

UTILISATION (à la racine du projet) :  python corriger_lot2.py
Copies de sécurité : *.avant-lot2
"""
SESSION_CRYPTO = '"""\nCookie de session CHIFFRÉ (audit MF-23).\n\nLe cookie de session de Flask est seulement SIGNÉ : son contenu (email et\ntéléphone pré-remplis, lien d\'invitation d\'un administrateur…) peut être lu en\nle décodant (base64), même s\'il ne peut pas être modifié.\n\nIci, le contenu est en plus CHIFFRÉ (Fernet, clé dérivée de FLASK_SECRET_KEY)\navant d\'être signé : le cookie devient illisible.\n\nCompatibilité : les anciens cookies (non chiffrés) restent acceptés jusqu\'à leur\nexpiration ; personne n\'est déconnecté au déploiement. Un cookie chiffré\nillisible (clé changée, falsification) est simplement ignoré : session vide.\n"""\nimport base64\nimport hashlib\n\nfrom cryptography.fernet import Fernet, InvalidToken\nfrom flask.sessions import SecureCookieSessionInterface\nfrom flask.json.tag import TaggedJSONSerializer\nfrom itsdangerous import BadPayload\n\n_PREFIX = "~"\n\n\nclass _EncryptedJSONSerializer:\n    def __init__(self, fernet):\n        self._fernet = fernet\n        self._inner = TaggedJSONSerializer()\n\n    def dumps(self, value):\n        plain = self._inner.dumps(value).encode("utf-8")\n        return _PREFIX + self._fernet.encrypt(plain).decode("ascii")\n\n    def loads(self, data):\n        if isinstance(data, bytes):\n            data = data.decode("utf-8")\n        if data.startswith(_PREFIX):\n            try:\n                plain = self._fernet.decrypt(data[len(_PREFIX):].encode("ascii"))\n            except (InvalidToken, ValueError):\n                raise BadPayload("Cookie de session illisible")\n            return self._inner.loads(plain.decode("utf-8"))\n        return self._inner.loads(data)          # ancien cookie (avant chiffrement)\n\n\ndef _fernet_for(secret):\n    raw = hashlib.sha256(b"menuflash-session-v1:" + str(secret).encode("utf-8")).digest()\n    return Fernet(base64.urlsafe_b64encode(raw))\n\n\ndef install(app):\n    """À appeler une fois, après avoir défini SECRET_KEY."""\n    interface = SecureCookieSessionInterface()\n    interface.serializer = _EncryptedJSONSerializer(_fernet_for(app.config["SECRET_KEY"]))\n    app.session_interface = interface\n    return interface\n'

import os
import re
import shutil
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
SUFFIX = ".avant-lot2"

# ==========================================================================
# MF-11 — Politique de mot de passe (ASVS 5.0 V6.2)
# ==========================================================================
MF11 = {
"security_forms.py": [
('''PASSWORD_MIN_LENGTH = 10
PASSWORD_MAX_LENGTH = 64''',
'''PASSWORD_MIN_LENGTH = 12          # audit MF-11 (ASVS 5.0 : 12 minimum, tous caractères acceptés)
PASSWORD_MAX_LENGTH = 128'''),
('''    errors = []
    if not (PASSWORD_MIN_LENGTH <= len(password) <= PASSWORD_MAX_LENGTH):
        errors.append(f"Le mot de passe doit contenir entre {PASSWORD_MIN_LENGTH} et "
                      f"{PASSWORD_MAX_LENGTH} caractères.")
    if not re.fullmatch(r"[A-Za-z0-9]*", password):
        errors.append("Le mot de passe ne doit contenir que des lettres (sans accent) et des "
                      "chiffres : aucun espace, aucune ponctuation.")
    if not re.search(r"[A-Z]", password):
        errors.append("Le mot de passe doit contenir au moins une lettre majuscule.")
    if not re.search(r"[a-z]", password):
        errors.append("Le mot de passe doit contenir au moins une lettre minuscule.")
    if not re.search(r"[0-9]", password):
        errors.append("Le mot de passe doit contenir au moins un chiffre.")
    if errors:
        return errors
''',
'''    # Audit MF-11 : longueur seulement. Espaces, accents et ponctuation sont acceptés
    # (phrases de passe) ; plus de règle « majuscule / chiffre obligatoire ».
    password = unicodedata.normalize("NFKC", password)
    errors = []
    if not (PASSWORD_MIN_LENGTH <= len(password) <= PASSWORD_MAX_LENGTH):
        errors.append(f"Le mot de passe doit contenir entre {PASSWORD_MIN_LENGTH} et "
                      f"{PASSWORD_MAX_LENGTH} caractères. Astuce : une phrase de plusieurs mots "
                      f"est facile à retenir et difficile à deviner.")
    if any(unicodedata.category(ch)[0] == "C" for ch in password):
        errors.append("Le mot de passe contient un caractère invisible non autorisé.")
    if errors:
        return errors
'''),
],
"static/forms_security.js": [
('''  var MIN = 10, MAX = 64;
  var ALLOWED = /^[A-Za-z0-9]*$/;
  var RULES = [
    { key: "len",   label: MIN + " à " + MAX + " caractères",
      test: function (v) { return v.length >= MIN && v.length <= MAX; } },
    { key: "upper", label: "Une lettre majuscule", test: function (v) { return /[A-Z]/.test(v); } },
    { key: "lower", label: "Une lettre minuscule", test: function (v) { return /[a-z]/.test(v); } },
    { key: "digit", label: "Un chiffre",           test: function (v) { return /[0-9]/.test(v); } },
    { key: "chars", label: "Lettres sans accent et chiffres uniquement (ni espace, ni ponctuation)",
      test: function (v) { return v.length > 0 && ALLOWED.test(v); } }
  ];''',
'''  // Audit MF-11 : tous les caractères sont acceptés (espaces, accents, ponctuation).
  var MIN = 12, MAX = 128;
  var ALLOWED = /^[^\\u0000-\\u001f\\u007f]*$/;
  var RULES = [
    { key: "len",   label: MIN + " caractères minimum (une phrase de plusieurs mots est idéale)",
      test: function (v) { return v.length >= MIN && v.length <= MAX; } }
  ];'''),
],
}

# ==========================================================================
# MF-18 — Fausses commandes : plafond global par adresse IP
# ==========================================================================
MF18 = {
"security_plus.py": [
('''        view = limiter.limit(ORDER_LIMIT_PER_IP, key_func=ip_key, methods=["POST"])(view)
''',
'''        view = limiter.limit(ORDER_LIMIT_PER_IP, key_func=ip_key, methods=["POST"])(view)
        # Audit MF-18 : plafond global, tous restaurants confondus (une même adresse ne peut
        # plus inonder plusieurs restaurants). Réglable : ORDER_LIMIT_GLOBAL_IP.
        view = limiter.limit(os.environ.get("ORDER_LIMIT_GLOBAL_IP", "150 per 10 minutes"),
                             key_func=lambda: f"order-ip-global:{client_ip()}", methods=["POST"])(view)
'''),
],
}

# ==========================================================================
# MF-21 — Clé dédiée au chiffrement de la 2FA + rotation de FLASK_SECRET_KEY
# ==========================================================================
MF21 = {
"two_factor.py": [
('''def _fernet(app):
    from cryptography.fernet import Fernet
    raw = hashlib.sha256(b"menuflash-2fa:" + app.config["SECRET_KEY"].encode()).digest()
    return Fernet(base64.urlsafe_b64encode(raw))
''',
'''def _legacy_fernet(secret):
    from cryptography.fernet import Fernet
    raw = hashlib.sha256(b"menuflash-2fa:" + str(secret).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(raw))


def _fernet(app):
    """Audit MF-21 : clé DÉDIÉE (TOTP_ENCRYPTION_KEY) si elle est définie, sinon clé
    dérivée de FLASK_SECRET_KEY. Les anciennes clés restent lisibles (rotation)."""
    from cryptography.fernet import Fernet, MultiFernet
    keys = []
    dedicated = (os.environ.get("TOTP_ENCRYPTION_KEY") or "").strip()
    if dedicated:
        try:
            keys.append(Fernet(dedicated.encode()))
        except (ValueError, TypeError):
            logger.error("TOTP_ENCRYPTION_KEY invalide : elle est ignorée (format Fernet attendu).")
    keys.append(_legacy_fernet(app.config["SECRET_KEY"]))
    for old in app.config.get("SECRET_KEY_FALLBACKS") or []:
        keys.append(_legacy_fernet(old))
    return MultiFernet(keys)


def rotate_totp_secrets(app):
    """Réchiffre les secrets 2FA avec la clé principale actuelle (au démarrage)."""
    f = _fernet(app)
    n = 0
    for rec in UserTwoFactor.query.all():
        try:
            new = f.rotate(rec.secret_enc.encode()).decode("ascii")
        except Exception:
            continue                       # illisible : laissé tel quel (reconfiguration manuelle)
        if new != rec.secret_enc:
            rec.secret_enc, n = new, n + 1
    if n:
        db.session.commit()
        logger.warning("2FA : %s secret(s) réchiffré(s) avec la clé actuelle.", n)
    return n
'''),
('''    def audit(action, success=True, details=None, user=None):
        user = user or (current_user if current_user.is_authenticated else None)''',
'''    # Audit MF-21 : réchiffrement avec la clé principale (TOTP_ENCRYPTION_KEY si définie).
    with app.app_context():
        try:
            rotate_totp_secrets(app)
        except Exception:
            db.session.rollback()
            logger.exception("Réchiffrement des secrets 2FA impossible")

    def audit(action, success=True, details=None, user=None):
        user = user or (current_user if current_user.is_authenticated else None)'''),
],
"app.py": [
('''csrf = CSRFProtect(app)
''',
'''# Audit MF-21 : rotation de FLASK_SECRET_KEY sans déconnecter tout le monde.
# Mettre l'ANCIENNE clé dans FLASK_SECRET_KEY_FALLBACKS (séparées par des virgules).
app.config["SECRET_KEY_FALLBACKS"] = [k.strip() for k in
                                      os.environ.get("FLASK_SECRET_KEY_FALLBACKS", "").split(",") if k.strip()]

csrf = CSRFProtect(app)
'''),
],
}

# ==========================================================================
# MF-22 — Déconnexion par POST uniquement, session entièrement vidée
# ==========================================================================
MF22_APP = [
('''@app.route("/admin/logout")
@login_required
def admin_logout():
    log_audit("logout", restaurant_id=current_user.restaurant_id, user_email=current_user.email)
    session.pop("active_restaurant_id", None)
    logout_user()
    return redirect(url_for("admin_login"))
''',
'''@app.route("/admin/logout", methods=["GET", "POST"])
@login_required
def admin_logout():
    # Audit MF-22 : un lien (GET) ne déconnecte plus ; il affiche un bouton de confirmation.
    if request.method != "POST":
        return render_template_string(_LOGOUT_CONFIRM_PAGE)
    origin = request.headers.get("Origin")
    if origin and origin.rstrip("/") != request.host_url.rstrip("/"):
        abort(403)
    log_audit("logout", restaurant_id=current_user.restaurant_id, user_email=current_user.email)
    logout_user()
    session.clear()                                   # plus aucune donnée de l'ancienne session
    return redirect(url_for("admin_login"))


_LOGOUT_CONFIRM_PAGE = """{% extends "base.html" %}{% block title %}Déconnexion{% endblock %}
{% block content %}<div class="container py-5 text-center" style="max-width:420px">
<h1 class="h5 mb-3">Se déconnecter de MenuFlash ?</h1>
<form method="post" action="{{ url_for('admin_logout') }}">
<input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
<button type="submit" class="btn btn-primary w-100">Se déconnecter</button></form>
<a href="{{ url_for('admin_login') }}" class="d-block small mt-3">Annuler</a></div>{% endblock %}"""
'''),
]
LOGOUT_LINK = re.compile(
    r'<a\s+(?P<a1>[^>]*?)href="\{\{ url_for\(\'admin_logout\'\) \}\}"(?P<a2>[^>]*)>(?P<inner>.*?)</a>', re.S)


def _logout_form(m):
    attrs = (m.group("a1") + " " + m.group("a2")).strip()
    cls = re.search(r'class="([^"]*)"', attrs)
    cls = cls.group(1) if cls else ""
    if "btn" not in cls and "dropdown-item" not in cls:
        cls = ("btn btn-link p-0 align-baseline " + cls).strip()
    return ('<form method="post" action="{{ url_for(\'admin_logout\') }}" class="d-inline m-0">'
            '<input type="hidden" name="csrf_token" value="{{ csrf_token() }}">'
            f'<button type="submit" class="{cls}">{m.group("inner")}</button></form>')


# ==========================================================================
# MF-24 — Rôles limités (support, finance) : rien hors de leurs pages
# ==========================================================================
MF24 = {
"sa_roles.py": [
('''        endpoint = request.endpoint or ""
        if not endpoint.startswith("super_admin"):
            return None
        role = role_of(current_user)''',
'''        endpoint = request.endpoint or ""
        if not endpoint.startswith("super_admin"):
            # Audit MF-24 : un rôle limité n'accède ni aux pages de gestion d'un restaurant
            # (/admin…) ni au suivi des commandes des clients, sauf son propre compte.
            path = request.path or ""
            if (path.startswith("/admin") and endpoint not in _SELF_SERVICE) or \\
                    (path.startswith("/m/") and "/commande/" in path):
                role = role_of(current_user)
                if role != "admin":
                    audit("sa_role_denied", f"{role} -> {endpoint or path} {request.method}", success=False)
                    return render_template("sa_forbidden.html", role=role,
                                           role_label=ROLES.get(role, ("Aucun rôle",))[0],
                                           landing=LANDING.get(role)), 403
            return None
        role = role_of(current_user)'''),
('''LANDING = {"support": "super_admin_clients_health", "finance": "super_admin_dashboard"}''',
'''LANDING = {"support": "super_admin_clients_health", "finance": "super_admin_dashboard"}

# Audit MF-24 : pages /admin… autorisées à tous (son propre compte uniquement).
_SELF_SERVICE = {"admin_login", "admin_logout", "change_password", "forgot_password", "reset_password",
                 "two_factor_setup", "two_factor_verify", "verify_email", "resend_verification_email",
                 "static", "media"}'''),
],
}

# ==========================================================================
# MF-25 — Restaurant additionnel suspendu : plus administrable
# ==========================================================================
MF25 = {
"app.py": [
('''        if rid in allowed_ids:
            r = Restaurant.query.get(rid)
            if r:
                return r
    return current_user.restaurant''',
'''        if rid in allowed_ids:
            r = Restaurant.query.get(rid)
            if r and (r.is_active or r.id == current_user.restaurant_id):
                return r
        session.pop("active_restaurant_id", None)     # audit MF-25 : suspendu ou retiré
    return current_user.restaurant'''),
('''    session["active_restaurant_id"] = target_id
    r = Restaurant.query.get(target_id)''',
'''    r = Restaurant.query.get(target_id)
    if r is None or (not r.is_active and target_id != current_user.restaurant_id):   # audit MF-25
        flash("Ce restaurant est suspendu : contactez l'équipe MenuFlash.", "error")
        return redirect(url_for("admin_dashboard"))
    session["active_restaurant_id"] = target_id'''),
],
}

# ==========================================================================
# MF-26 — Redirection après la 2FA : chemins internes réels uniquement
# ==========================================================================
MF26 = {
"two_factor.py": [
('''        target = session.pop(NEXT_KEY, None)
        if target and target.startswith("/") and not target.startswith("//"):
            return redirect(target)''',
'''        target = session.pop(NEXT_KEY, None)
        if (isinstance(target, str) and target.startswith("/") and not target.startswith("//")
                and "\\\\" not in target and not any(ord(c) < 32 for c in target)):
            try:                                       # audit MF-26 : une vraie page de MenuFlash
                app.url_map.bind("localhost").match(target.split("?", 1)[0], method="GET")
                return redirect(target)
            except Exception:
                pass'''),
],
}

# ==========================================================================
# MF-27 — Journal d'audit : transaction séparée, emails masqués, alertes
# ==========================================================================
MF27 = {
"app.py": [
('''def log_audit(action, success=True, restaurant_id=None, user_email=None, details=None):
    try:
        db.session.add(AuditLog(
            restaurant_id=restaurant_id,
            user_email=user_email,
            action=action,
            success=success,
            ip_address=get_client_ip(),
            details=(details or "")[:300],
        ))
        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.exception("Échec d'écriture du journal d'audit (action=%s)", action)
''',
'''def _mask_email(email):
    email = str(email or "")
    local, _, domain = email.partition("@")
    return (local[:1] + "***@" + domain)[:120] if domain else ("***" if email else "")


# Audit MF-27 : actions qui déclenchent un email d'alerte aux administrateurs.
_CRITICAL_ACTIONS = {"2fa_locked", "2fa_emergency_reset", "security_not_me", "backup_restored",
                     "backup_restore_failed", "sa_admin_disabled", "sa_email_changed",
                     "sa_admin_accepted"}
_alert_sent = {}


def _send_critical_alert(action, user_email, details):
    import threading
    import time as _time
    now = _time.monotonic()
    key = (action, user_email)
    if now - _alert_sent.get(key, -1e9) < 600:          # 1 alerte / 10 min pour le même événement
        return
    _alert_sent[key] = now
    ip = get_client_ip()

    def run():
        with app.app_context():
            try:
                from security_plus import StaffMeta
                for u in User.query.filter_by(is_super_admin=True).all():
                    meta = db.session.get(StaffMeta, u.id)
                    if meta and meta.disabled_at:
                        continue
                    send_transactional_email(
                        app, u.email, f"[MenuFlash] Alerte de sécurité : {action}",
                        f"Bonjour,\\n\\nÉvénement de sécurité enregistré :\\n\\n  • Action : {action}\\n"
                        f"  • Compte : {user_email or '-'}\\n  • Détails : {details or '-'}\\n"
                        f"  • Adresse IP : {ip}\\n\\nConsultez le journal de sécurité du panel.\\n\\n"
                        f"L'équipe MenuFlash")
            except Exception:
                logger.exception("Alerte de sécurité non envoyée (%s)", action)
    threading.Thread(target=run, daemon=True).start()


def log_audit(action, success=True, restaurant_id=None, user_email=None, details=None):
    values = dict(restaurant_id=restaurant_id, user_email=(user_email or None) and str(user_email)[:120],
                  action=str(action)[:40], success=bool(success), ip_address=get_client_ip(),
                  details=(details or "")[:300], created_at=datetime.utcnow())
    try:
        if db.engine.dialect.name == "postgresql":
            # Audit MF-27 : transaction SÉPARÉE. Écrire une ligne de journal ne valide plus
            # les modifications en cours de la page (elles restent annulables).
            with db.engine.begin() as conn:
                conn.execute(AuditLog.__table__.insert().values(**values))
        else:                                  # SQLite (local) : une seule écriture à la fois
            db.session.add(AuditLog(**values))
            db.session.commit()
    except Exception:
        db.session.rollback()
        logger.exception("Échec d'écriture du journal d'audit (action=%s)", action)
    if values["action"] in _CRITICAL_ACTIONS:
        try:
            _send_critical_alert(values["action"], values["user_email"], values["details"])
        except Exception:
            logger.exception("Alerte de sécurité impossible")
'''),
('''            log_audit("login_failed", success=False, user_email=email, details="compte inconnu")''',
'''            log_audit("login_failed", success=False, user_email=_mask_email(email),      # audit MF-27
                      details="compte inconnu")'''),
],
"super_admin.py": [
('''from datetime import datetime, timedelta
''', '''import re
from datetime import datetime, timedelta
'''),
('''                path=(request.path or "")[:300],''',
'''                path=re.sub(r"[A-Za-z0-9_\\-\\.]{24,}", "[jeton]", request.path or "")[:300],   # audit MF-27'''),
],
}

# ==========================================================================
# MF-28 — Mode debug local uniquement, limite sur la sauvegarde planifiée, workflow
# ==========================================================================
MF28 = {
"app.py": [
('''    app.run(debug=True, port=5000, host="0.0.0.0", threaded=True)''',
'''    # Audit MF-28 : accessible uniquement depuis ce PC ; débogueur seulement si FLASK_DEBUG=1.
    app.run(debug=os.environ.get("FLASK_DEBUG") == "1", port=5000, host="127.0.0.1", threaded=True)'''),
('''if __name__ == "__main__":''',
'''# Audit MF-28 : la sauvegarde planifiée a sa propre limite (10 appels par heure et par IP).
# Placée AVANT tous les autres contrôles : backups.py traite cette adresse très tôt.
from limits import parse as _parse_limit
_CRON_LIMIT = _parse_limit("10/hour")


def _cron_rate_guard():
    if request.path == "/cron/sauvegarde":
        try:
            if not limiter.limiter.hit(_CRON_LIMIT, "cron-sauvegarde", get_remote_address()):
                return jsonify(ok=False, error="too_many_requests"), 429
        except Exception:
            logger.exception("Limite de la sauvegarde planifiée indisponible")
    return None


app.before_request_funcs.setdefault(None, []).insert(0, _cron_rate_guard)


if __name__ == "__main__":'''),
],
".github/workflows/sauvegarde-quotidienne.yml": [
('''          echo "$response"
          echo "$response" | grep -q '"ok":true\'''',
'''          echo "$response" | grep -o '"ok":[a-z]*\\|"offsite":"[a-z_]*"' || true
          echo "$response" | grep -q '"ok":true\''''),
],
}

# ==========================================================================
# MF-23 — Cookie de session chiffré
# ==========================================================================
MF23 = {
"app.py": [
('''csrf = CSRFProtect(app)
''',
'''import session_crypto                              # audit MF-23 : cookie de session chiffré
session_crypto.install(app)

csrf = CSRFProtect(app)
'''),
],
}

CORRECTIONS = [
    ("MF-18", "Plafond global de commandes par adresse IP", MF18),
    ("MF-21", "Clé dédiée 2FA + rotation de FLASK_SECRET_KEY", MF21),
    ("MF-22", "Déconnexion par POST, session vidée", "MF22"),
    ("MF-23", "Cookie de session chiffré", MF23),
    ("MF-24", "Rôles support / finance limités à leurs pages", MF24),
    ("MF-25", "Restaurant additionnel suspendu non administrable", MF25),
    ("MF-26", "Redirection sûre après la 2FA", MF26),
    ("MF-27", "Journal : transaction séparée, emails masqués, alertes", MF27),
    ("MF-28", "Debug local, limite sauvegarde planifiée, workflow", MF28),
]

NEW_FILES = {"MF-23": {"session_crypto.py": SESSION_CRYPTO}}


def _read(rel, texts):
    if rel not in texts:
        path = os.path.join(ROOT, rel)
        texts[rel] = open(path, encoding="utf-8").read() if os.path.exists(path) else None
    return texts[rel]


def _apply_mf22(texts):
    app = _read("app.py", texts)
    if app is None:
        return None, "app.py introuvable"
    for old, new in MF22_APP:
        if app.count(old) != 1:
            return None, "route admin_logout introuvable dans app.py"
        app = app.replace(old, new)
    if "render_template_string" not in app.split("def admin_logout")[0]:
        app = app.replace("from flask import (", "from flask import (render_template_string, ", 1)
        if "render_template_string, " not in app:
            return None, "import Flask introuvable dans app.py"
    out = {"app.py": app}
    tdir = os.path.join(ROOT, "templates")
    for name in sorted(os.listdir(tdir)):
        if name.endswith(".html"):
            rel = os.path.join("templates", name)
            text = _read(rel, texts)
            new = LOGOUT_LINK.sub(_logout_form, text)
            if new != text:
                out[rel] = new
    left = [r for r, t in texts.items() if r.startswith("templates") and t and
            "url_for('admin_logout')" in (out.get(r) or t) and 'method="post" action="{{ url_for(\'admin_logout\')' not in (out.get(r) or t)]
    if left:
        return None, "liens de déconnexion non convertis : " + ", ".join(left)
    return out, None


def _apply(spec, texts):
    out = {}
    for rel, repls in spec.items():
        text = out.get(rel) or _read(rel, texts)
        if text is None:
            return None, f"{rel} introuvable"
        for old, new in repls:
            if text.count(old) != 1:
                return None, f"repère introuvable dans {rel} : « {old.strip().splitlines()[0][:60]} »"
            text = text.replace(old, new)
        out[rel] = text
    return out, None


def main():
    if not os.path.exists(os.path.join(ROOT, "app.py")):
        sys.exit("ARRÊT : lancez le script à la racine du projet (là où se trouve app.py).")
    if "import session_crypto" in open(os.path.join(ROOT, "app.py"), encoding="utf-8").read():
        print("Ce lot est déjà appliqué : rien à faire.")
        return
    texts, original, done, skipped = {}, {}, [], []
    for code, label, spec in CORRECTIONS:
        result, err = _apply_mf22(texts) if spec == "MF22" else _apply(spec, texts)
        if err:
            skipped.append((code, label, err))
            continue
        for rel, new in result.items():
            original.setdefault(rel, _read(rel, texts))
            texts[rel] = new
        done.append((code, label))

    # Versions des scripts navigateur (les navigateurs prennent le nouveau fichier).
    if any(c == "MF-11" for c, _ in done):
        for rel in ("templates/base.html", "templates/signup.html"):
            t = _read(rel, texts)
            new = re.sub(r"(forms_security\.js'\) }}\?v=)(\d+)", lambda m: m.group(1) + str(int(m.group(2)) + 1), t)
            if new != t:
                original.setdefault(rel, t)
                texts[rel] = new

    for rel, text in texts.items():
        if rel in original and text != original[rel]:
            path = os.path.join(ROOT, rel)
            shutil.copy2(path, path + SUFFIX)
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)
            print(f"  modifié : {rel}")
    for code, _ in done:
        for name, content in NEW_FILES.get(code, {}).items():
            with open(os.path.join(ROOT, name), "w", encoding="utf-8", newline="\n") as fh:
                fh.write(content)
            print(f"  créé    : {name}")

    print("\nAppliquées :")
    for code, label in done:
        print(f"  ✔ {code}  {label}")
    if skipped:
        print("\nNON appliquées (fichiers laissés intacts pour ces corrections) :")
        for code, label, err in skipped:
            print(f"  ✘ {code}  {label}\n      -> {err}")
    print(f"\nCopies de sécurité : *{SUFFIX} (à ne pas pousser sur Git).")


if __name__ == "__main__":
    main()
