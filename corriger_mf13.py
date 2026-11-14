"""
MenuFlash — Correction de sécurité MF-13 (audit du 13/11/2026).

PROBLÈME
--------
Tant que la variable TWO_FACTOR_EMERGENCY_RESET existait sur Render, la 2FA du
compte indiqué était SUPPRIMÉE À CHAQUE REQUÊTE. Si la variable était oubliée
après usage, la 2FA de ce super-admin restait désactivée en permanence, sans
alerte visible : il suffisait alors du mot de passe pour entrer, et l'attaquant
pouvait même enregistrer son propre téléphone.

CORRECTION (two_factor.py uniquement)
-------------------------------------
1. Usage UNIQUE : la réinitialisation n'a lieu qu'une seule fois. C'est
   enregistré en base (nouvelle table créée automatiquement). La variable
   oubliée n'a plus aucun effet ensuite.
2. Pour la réutiliser plus tard : supprimer la variable (Render redémarre et
   efface la trace), puis la remettre.
3. Tant que la variable est présente : ERREUR dans les logs à chaque démarrage,
   et message rouge sur le panel super-admin (une fois par session).
4. Chaque réinitialisation reste tracée dans le journal de sécurité.

UTILISATION (à la racine du projet) :  python corriger_mf13.py
Vérifie tout avant d'écrire ; copie : two_factor.py.avant-mf13
"""
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(ROOT, "two_factor.py")

R = [
('''def init_two_factor(app, *, log_audit, limiter=None):
''',
'''class TwoFactorEmergencyUse(db.Model):
    """Audit MF-13 : une réinitialisation d'urgence ne sert qu'UNE fois."""
    __tablename__ = "user_two_factor_emergency"
    user_id = db.Column(db.Integer, primary_key=True)
    used_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


EMERGENCY_VAR = "TWO_FACTOR_EMERGENCY_RESET"
EMERGENCY_NOTICE_KEY = "mf_2fa_emergency_notice"


def init_two_factor(app, *, log_audit, limiter=None):
''', "début de init_two_factor"),

('''    def audit(action, success=True, details=None, user=None):
        user = user or (current_user if current_user.is_authenticated else None)''',
'''    # Audit MF-13 : table « usage unique » + nettoyage quand la variable est absente.
    with app.app_context():
        try:
            TwoFactorEmergencyUse.__table__.create(db.engine, checkfirst=True)
            if os.environ.get(EMERGENCY_VAR, "").strip():
                logger.error("%s est définie : réinitialisation d'urgence de la 2FA ARMÉE (usage unique). "
                             "Supprimez la variable dès que possible.", EMERGENCY_VAR)
            elif TwoFactorEmergencyUse.query.count():
                # Variable retirée -> une prochaine urgence pourra de nouveau être traitée.
                TwoFactorEmergencyUse.query.delete()
                db.session.commit()
        except Exception:
            db.session.rollback()
            logger.exception("Table user_two_factor_emergency indisponible")

    def audit(action, success=True, details=None, user=None):
        user = user or (current_user if current_user.is_authenticated else None)''', "fonction audit"),

('''        emergency = os.environ.get("TWO_FACTOR_EMERGENCY_RESET", "").strip().lower()
        if emergency and emergency == current_user.email.lower():
            rec = _record(current_user)
            if rec:
                db.session.delete(rec)
                db.session.commit()
                audit("2fa_emergency_reset", details="2FA supprimée via TWO_FACTOR_EMERGENCY_RESET")
                logger.warning("2FA réinitialisée en urgence pour %s : retirez la variable "
                               "TWO_FACTOR_EMERGENCY_RESET de Render.", current_user.email)
            session.pop(SESSION_KEY, None)
''',
'''        emergency = os.environ.get(EMERGENCY_VAR, "").strip().lower()
        if emergency:
            if not session.get(EMERGENCY_NOTICE_KEY):
                session[EMERGENCY_NOTICE_KEY] = True
                flash(f"Sécurité : la variable {EMERGENCY_VAR} est encore définie sur Render. "
                      "Supprimez-la dès que possible.", "error")
            # Audit MF-13 : usage UNIQUE (enregistré en base), plus jamais à chaque requête.
            if emergency == current_user.email.lower() and \\
                    db.session.get(TwoFactorEmergencyUse, current_user.id) is None:
                rec = _record(current_user)
                if rec:
                    db.session.delete(rec)
                db.session.add(TwoFactorEmergencyUse(user_id=current_user.id))
                db.session.commit()
                audit("2fa_emergency_reset", success=False,
                      details="2FA supprimée via TWO_FACTOR_EMERGENCY_RESET (usage unique)")
                logger.error("2FA réinitialisée en urgence pour %s : retirez la variable "
                             "%s de Render.", current_user.email, EMERGENCY_VAR)
                session.pop(SESSION_KEY, None)
''', "réinitialisation d'urgence"),
]


def main():
    if not os.path.exists(PATH):
        sys.exit("ARRÊT : two_factor.py introuvable (lancez le script à la racine du projet).")
    text = open(PATH, encoding="utf-8").read()
    if "class TwoFactorEmergencyUse" in text:
        print("MF-13 est déjà appliquée : rien à faire.")
        return
    for needed in ("flash", "from datetime import datetime"):
        if needed not in text:
            sys.exit(f"ARRÊT : « {needed} » absent de two_factor.py. Aucun fichier n'a été modifié.")
    for old, new, label in R:
        if text.count(old) != 1:
            sys.exit(f"ARRÊT : repère « {label} » introuvable dans two_factor.py. Aucun fichier n'a été modifié.")
        text = text.replace(old, new)
    shutil.copy2(PATH, PATH + ".avant-mf13")
    with open(PATH, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    print("  modifié : two_factor.py")
    print("\nMF-13 appliquée. Copie de sécurité : two_factor.py.avant-mf13 (à ne pas pousser sur Git).")


if __name__ == "__main__":
    main()
