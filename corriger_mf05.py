"""
MenuFlash — Correction de sécurité MF-05 (audit du 13/11/2026).

PROBLÈME
--------
Un compte désactivé (administrateur MenuFlash ou membre d'équipe) pouvait
revenir par « Mot de passe oublié » : le lien était envoyé, la réinitialisation
ouvrait une session, et la déconnexion automatique des comptes désactivés
ignorait les comptes de type « owner » (dont font partie les super-admins).

CORRECTION (security_plus.py uniquement)
----------------------------------------
1. « Mot de passe oublié » : aucun lien n'est envoyé à un compte désactivé.
   Le message affiché est identique (on ne révèle pas l'état du compte) et la
   tentative est tracée dans le journal de sécurité.
2. Lien de réinitialisation déjà envoyé avant la désactivation : refusé, avec le
   message habituel « lien invalide ou expiré ». Le mot de passe n'est pas changé
   et aucune session n'est ouverte.
3. Toute session d'un compte désactivé est fermée à la requête suivante, quel que
   soit le rôle (super-admin compris). Ce contrôle passe désormais en premier.

Inchangé : la connexion par mot de passe d'un compte désactivé était déjà
refusée ; les invitations (équipe, administrateurs) vérifiaient déjà l'état.

UTILISATION (à la racine du projet, là où se trouve app.py)
-----------------------------------------------------------
    python corriger_mf05.py

Vérifie tout avant d'écrire ; copie de sécurité : security_plus.py.avant-mf05
"""
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(ROOT, "security_plus.py")

OLD = '''    @app.before_request
    def _logout_disabled():
        if current_user.is_authenticated and not current_user.is_owner \\
                and (request.endpoint or "") not in _SESSION_SKIP and is_disabled(current_user):
            return end_session("compte désactivé", "Ce compte a été désactivé par le gestionnaire.")
        return None
'''

NEW = '''    # ---- Audit MF-05 : un compte désactivé ne peut plus revenir, par aucun chemin.
    def _logout_disabled():
        """Ferme la session d'un compte désactivé, QUEL QUE SOIT son rôle (super-admin compris)."""
        if current_user.is_authenticated and (request.endpoint or "") not in _SESSION_SKIP \\
                and is_disabled(current_user):
            message = ("Ce compte a été désactivé." if current_user.is_super_admin
                       else "Ce compte a été désactivé par le gestionnaire.")
            return end_session("compte désactivé", message)
        return None

    # Exécuté AVANT les autres contrôles (2FA, maintenance…) : rien ne doit
    # pouvoir servir une page à une session désactivée.
    app.before_request_funcs.setdefault(None, []).insert(0, _logout_disabled)

    @app.before_request
    def _block_disabled_password_reset():
        endpoint = request.endpoint or ""

        # 1) Demande de lien : rien n'est envoyé à un compte désactivé (même message affiché).
        if endpoint == "forgot_password" and request.method == "POST":
            email = sf.normalize_email(request.form.get("email"))
            user = User.query.filter_by(email=email).first() if email else None
            if user and is_disabled(user):
                log_audit("password_reset_blocked_disabled", success=False,
                          restaurant_id=user.restaurant_id, user_email=email,
                          details="compte désactivé : aucun lien envoyé")
                flash("Si un compte existe avec cet email, un lien de réinitialisation "
                      "vient de lui être envoyé.", "success")
                return redirect(url_for("admin_login"))
            return None

        # 2) Lien reçu avant la désactivation : refusé, sans changer le mot de passe.
        if endpoint == "reset_password":
            token = (request.view_args or {}).get("token") or ""
            try:
                data = serializer.loads(token, salt="pwd-reset", max_age=7 * 24 * 3600)
            except (BadSignature, SignatureExpired):
                return None                      # la route affiche déjà « lien invalide »
            user = db.session.get(User, data.get("uid")) if isinstance(data, dict) else None
            if user and is_disabled(user):
                log_audit("password_reset_blocked_disabled", success=False,
                          restaurant_id=user.restaurant_id, user_email=user.email,
                          details="lien de réinitialisation refusé : compte désactivé")
                flash("Ce lien de réinitialisation est invalide, a expiré, ou a déjà été utilisé.", "error")
                return redirect(url_for("forgot_password"))
        return None
'''


def main():
    if not os.path.exists(PATH):
        sys.exit("ARRÊT : security_plus.py introuvable (lancez le script à la racine du projet).")
    text = open(PATH, encoding="utf-8").read()
    if "_block_disabled_password_reset" in text:
        print("MF-05 est déjà appliquée : rien à faire.")
        return
    if text.count(OLD) != 1:
        sys.exit("ARRÊT : bloc « _logout_disabled » introuvable ou modifié. Aucun fichier n'a été modifié.")
    for needed in ("serializer = URLSafeTimedSerializer(", "def is_disabled(user):", "def end_session(",
                   "_SESSION_SKIP = ", "import security_forms as sf"):
        if needed not in text:
            sys.exit(f"ARRÊT : élément attendu absent de security_plus.py ({needed}). Aucun fichier n'a été modifié.")
    text = text.replace(OLD, NEW)
    shutil.copy2(PATH, PATH + ".avant-mf05")
    with open(PATH, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    print("  modifié : security_plus.py")
    print("\nMF-05 appliquée. Copie de sécurité : security_plus.py.avant-mf05 (à ne pas pousser sur Git).")


if __name__ == "__main__":
    main()
