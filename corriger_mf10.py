"""
MenuFlash — Correction de sécurité MF-10 (audit du 13/11/2026).

PROBLÈMES
---------
1. Énumération : un compte verrouillé affichait « Compte temporairement
   verrouillé… », un email inconnu « Identifiants incorrects » : on pouvait savoir
   quelles adresses ont un compte MenuFlash (et la réponse « verrouillé » était
   aussi plus rapide, sans calcul du mot de passe).
2. Verrouillage à volonté : n'importe qui pouvait bloquer le compte d'un
   restaurateur avec 5 mauvais mots de passe. Pire : à l'expiration du verrou,
   le compteur restait à 5, donc UN seul échec reverrouillait 15 minutes.

CORRECTION
----------
models.py
  - Le compteur d'échecs repart de zéro quand le verrou a expiré.
security_plus.py
  - Expose « cet appareil est-il déjà connu pour ce compte ? » (cookie d'appareil
    signé, déjà utilisé pour les alertes de nouvelle connexion).
app.py (connexion)
  - Même message, même temps de réponse dans tous les cas : email inconnu,
    mauvais mot de passe, compte verrouillé.
  - Un compte verrouillé reste accessible avec le BON mot de passe depuis un
    appareil où son propriétaire s'est déjà connecté (tablette de cuisine,
    téléphone du gérant). Un attaquant ne peut donc plus bloquer le service.
    Depuis un appareil inconnu, le verrou s'applique normalement.
    (Les verrous posés par la 2FA ou par « Ce n'était pas moi » restent
    efficaces : la 2FA est redemandée et refuse un compte verrouillé ; « Ce
    n'était pas moi » oublie tous les appareils.)
  - Quand un compte se verrouille, son propriétaire reçoit un email
    (« si ce n'était pas vous… ») avec le lien « Mot de passe oublié ».
  - Réinitialiser son mot de passe par email débloque le compte.

Limite connue (documentée) : l'inscription indique toujours « Un compte existe
déjà avec cet email » (nécessaire car l'inscription connecte immédiatement).
Elle reste limitée à 8 essais par heure et par adresse IP.

UTILISATION (à la racine du projet) :  python corriger_mf10.py
Vérifie tout avant d'écrire ; copies : *.avant-mf10
"""
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))

GENERIC = "Identifiants incorrects."

PATCHES = {
"models.py": [(
'''    def register_failed_login(self):
        self.failed_login_attempts = (self.failed_login_attempts or 0) + 1''',
'''    def register_failed_login(self):
        # Audit MF-10 : verrou expiré -> le compteur repart de zéro (sinon un seul
        # échec suffisait à reverrouiller le compte 15 minutes).
        if self.locked_until and self.locked_until <= datetime.utcnow():
            self.failed_login_attempts = 0
            self.locked_until = None
        self.failed_login_attempts = (self.failed_login_attempts or 0) + 1''',
"register_failed_login")],

"security_plus.py": [(
'''    @app.after_request
    def _set_device_cookie(resp):''',
'''    def is_known_device(user):
        """Audit MF-10 : le propriétaire de ce compte s'est-il déjà connecté depuis ce navigateur ?
        Ne crée jamais de cookie (lecture seule)."""
        raw = request.cookies.get(DEVICE_COOKIE, "")
        if not user or len(raw) != 32 or not all(c in "0123456789abcdef" for c in raw):
            return False
        try:
            return UserDevice.query.filter_by(user_id=user.id, device_hash=device_hash(raw)).first() is not None
        except Exception:
            db.session.rollback()
            return False

    app.extensions["mf_is_known_device"] = is_known_device

    @app.after_request
    def _set_device_cookie(resp):''',
"cookie d'appareil")],

"app.py": [(
'''        if user and user.is_locked:
            minutes = user.lock_minutes_remaining
            flash(f"Compte temporairement verrouillé suite à plusieurs échecs de connexion. "
                  f"Réessayez dans environ {minutes} minute(s).", "error")
            log_audit("login_blocked_locked", success=False, restaurant_id=user.restaurant_id,
                       user_email=email, details=f"verrouille encore {minutes} min")
            return render_template("admin_login.html")

        if user and user.check_password(password):''',
'''        # Audit MF-10 : réponse identique (message ET temps de calcul) que le compte
        # existe ou non, soit verrouillé ou non. Un compte verrouillé reste accessible
        # depuis un appareil déjà connu de son propriétaire.
        password_ok = bool(user) and user.check_password(password)
        known_device = False
        if user and user.is_locked:
            checker = app.extensions.get("mf_is_known_device")
            known_device = bool(checker and checker(user))
            if not (password_ok and known_device):
                minutes = user.lock_minutes_remaining
                log_audit("login_blocked_locked", success=False, restaurant_id=user.restaurant_id,
                           user_email=email, details=f"verrouille encore {minutes} min"
                           + (" (appareil connu, mot de passe faux)" if known_device else ""))
                flash("Identifiants incorrects.", "error")
                return render_template("admin_login.html")
            log_audit("login_lock_bypassed_known_device", restaurant_id=user.restaurant_id,
                       user_email=email, details="compte verrouillé, appareil déjà connu, bon mot de passe")

        if user and password_ok:''',
"compte verrouillé"),
(
'''            if just_locked:
                flash(f"Trop d'échecs : compte verrouillé pendant {LOCKOUT_DURATION_MINUTES} minutes.", "error")
            else:
                flash("Identifiants incorrects.", "error")''',
'''            if just_locked:
                _send_lock_notice(user)
            flash("Identifiants incorrects.", "error")      # audit MF-10 : même message partout''',
"message de verrouillage"),
(
'''@app.route("/admin/login", methods=["GET", "POST"])''',
'''def _send_lock_notice(user):
    """Audit MF-10 : prévient le propriétaire que son compte vient d'être verrouillé."""
    try:
        link = url_for("forgot_password", _external=True)
        send_transactional_email(
            app, user.email,
            subject="Connexion bloquée temporairement — MenuFlash",
            body_text=(
                f"Bonjour,\\n\\nPlusieurs mots de passe incorrects ont été saisis pour votre compte "
                f"MenuFlash. Par sécurité, la connexion depuis un NOUVEL appareil est bloquée "
                f"pendant {LOCKOUT_DURATION_MINUTES} minutes.\\n\\n"
                f"Vos appareils habituels (tablette de cuisine, téléphone) continuent de "
                f"fonctionner normalement.\\n\\n"
                f"Si ce n'était pas vous, choisissez dès maintenant un nouveau mot de passe :\\n{link}\\n\\n"
                f"L'équipe MenuFlash"))
    except Exception:
        logger.exception("Email de verrouillage non envoyé")


@app.route("/admin/login", methods=["GET", "POST"])''',
"route de connexion"),
(
'''    user.set_password(new_password)
    db.session.commit()
    log_audit("password_reset_completed", restaurant_id=user.restaurant_id, user_email=user.email)''',
'''    user.set_password(new_password)
    user.failed_login_attempts = 0          # audit MF-10 : la preuve par email débloque le compte
    user.locked_until = None
    db.session.commit()
    log_audit("password_reset_completed", restaurant_id=user.restaurant_id, user_email=user.email)''',
"réinitialisation"),
],
}


def main():
    texts = {}
    for name, repls in PATCHES.items():
        path = os.path.join(ROOT, name)
        if not os.path.exists(path):
            sys.exit(f"ARRÊT : {name} introuvable (lancez le script à la racine du projet).")
        texts[name] = open(path, encoding="utf-8").read()
    if "Audit MF-10" in texts["app.py"]:
        print("MF-10 est déjà appliquée : rien à faire.")
        return
    if "logger" not in texts["app.py"]:
        sys.exit("ARRÊT : « logger » absent de app.py. Aucun fichier n'a été modifié.")
    for name, repls in PATCHES.items():
        for old, new, label in repls:
            if texts[name].count(old) != 1:
                sys.exit(f"ARRÊT : repère « {label} » introuvable dans {name}. Aucun fichier n'a été modifié.")
            texts[name] = texts[name].replace(old, new)
    for name, text in texts.items():
        path = os.path.join(ROOT, name)
        shutil.copy2(path, path + ".avant-mf10")
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        print(f"  modifié : {name}")
    print("\nMF-10 appliquée. Copies de sécurité : *.avant-mf10 (à ne pas pousser sur Git).")


if __name__ == "__main__":
    main()
