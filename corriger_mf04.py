"""
MenuFlash — Correction de sécurité MF-04 (audit du 13/11/2026).

PROBLÈME
--------
Le compteur de codes 2FA faux était stocké dans le cookie de session (côté
navigateur). En renvoyant toujours le cookie obtenu juste après le mot de passe,
un attaquant gardait le compteur à 0 : la limite de 5 essais n'était jamais
atteinte. De plus, le verrouillage posé en base ne fermait pas les sessions déjà
ouvertes. Seule restait une limite par adresse IP, contournable.

CORRECTION (two_factor.py uniquement)
-------------------------------------
1. Le compteur est stocké EN BASE, par compte (nouvelle table
   user_two_factor_failures, créée automatiquement) : impossible à remettre à
   zéro depuis le navigateur. Il repart de zéro après 15 minutes sans erreur,
   ou après un code correct.
2. Au 5e code faux : compte verrouillé 15 minutes ET tampon de sécurité changé.
   Toutes les sessions et tous les cookies existants deviennent invalides
   (y compris un cookie copié).
3. Un compte verrouillé ne peut plus valider de code, même avec une session
   ouverte : il est déconnecté.

Inchangé : codes TOTP, codes de secours, anti-rejeu, limite par IP existante.

UTILISATION (à la racine du projet, là où se trouve app.py)
-----------------------------------------------------------
    python corriger_mf04.py

Vérifie tout avant d'écrire ; copie de sécurité : two_factor.py.avant-mf04
"""
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(ROOT, "two_factor.py")

R = []  # (ancien, nouveau, libellé)

# 1. Nouveau modèle, juste avant la section TOTP.
R.append(('''# ==========================================================================
# TOTP (RFC 6238) — sans dépendance externe''', '''class TwoFactorFailure(db.Model):
    """Audit MF-04 : codes 2FA faux, comptés EN BASE par compte (et non dans le cookie)."""
    __tablename__ = "user_two_factor_failures"
    user_id = db.Column(db.Integer, primary_key=True)
    count = db.Column(db.Integer, nullable=False, default=0)
    window_start = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


FAILURE_WINDOW = timedelta(minutes=15)      # sans erreur pendant 15 min : le compteur repart à zéro


def register_failure(user_id, now=None):
    """Ajoute un échec et renvoie le total sur la fenêtre en cours (sans commit)."""
    now = now or datetime.utcnow()
    row = db.session.get(TwoFactorFailure, user_id)
    if row is None:
        row = TwoFactorFailure(user_id=user_id, count=0, window_start=now)
        db.session.add(row)
    elif now - row.window_start > FAILURE_WINDOW:
        row.count, row.window_start = 0, now
    row.count += 1
    return row.count


def clear_failures(user_id):
    """Remet le compteur à zéro (sans commit)."""
    row = db.session.get(TwoFactorFailure, user_id)
    if row is not None:
        db.session.delete(row)


# ==========================================================================
# TOTP (RFC 6238) — sans dépendance externe''', "section TOTP"))

# 2. Création de la table au démarrage.
R.append(('''            UserTwoFactor.__table__.create(db.engine, checkfirst=True)
''', '''            UserTwoFactor.__table__.create(db.engine, checkfirst=True)
            TwoFactorFailure.__table__.create(db.engine, checkfirst=True)
''', "création de la table"))

# 3. Un compte verrouillé ne valide plus aucun code (même avec une session ouverte).
R.append(('''        if is_verified():
            return _after_verified()

        if request.method == "POST":
            mode = request.form.get("mode", "code")''', '''        if is_verified():
            return _after_verified()

        if getattr(current_user, "is_locked", False):
            audit("2fa_blocked_locked", success=False, details="compte verrouillé : code refusé")
            minutes = current_user.lock_minutes_remaining
            logout_user()
            session.clear()
            flash(f"Compte verrouillé : réessayez dans {minutes} minute(s).", "error")
            return redirect(url_for("admin_login"))

        if request.method == "POST":
            mode = request.form.get("mode", "code")''', "début de la vérification"))

# 4. Succès : compteur remis à zéro.
R.append(('''            if ok:
                mark_verified(user)''', '''            if ok:
                clear_failures(user.id)
                db.session.commit()
                mark_verified(user)''', "succès de la vérification"))

# 5. Échec : compteur en base, verrouillage + fermeture de toutes les sessions.
R.append(('''            fails = session.get(FAIL_KEY, 0) + 1
            session[FAIL_KEY] = fails
            audit("2fa_failed", success=False, details=f"tentative {fails}/{MAX_FAILURES} ({mode})")
            if fails >= MAX_FAILURES:
                user.locked_until = datetime.utcnow() + timedelta(minutes=LOCKOUT_DURATION_MINUTES)
                db.session.commit()''', '''            # Audit MF-04 : compteur en base (le cookie ne peut plus le remettre à zéro).
            fails = register_failure(user.id)
            db.session.commit()
            audit("2fa_failed", success=False, details=f"tentative {fails}/{MAX_FAILURES} ({mode})")
            if fails >= MAX_FAILURES:
                user.locked_until = datetime.utcnow() + timedelta(minutes=LOCKOUT_DURATION_MINUTES)
                user.rotate_security_stamp()          # toutes les sessions et cookies existants tombent
                clear_failures(user.id)
                db.session.commit()''', "échec de la vérification"))


def main():
    if not os.path.exists(PATH):
        sys.exit("ARRÊT : two_factor.py introuvable (lancez le script à la racine du projet).")
    text = open(PATH, encoding="utf-8").read()
    if "class TwoFactorFailure" in text:
        print("MF-04 est déjà appliquée : rien à faire.")
        return
    for old, new, label in R:
        if text.count(old) != 1:
            sys.exit(f"ARRÊT : repère « {label} » introuvable ou en double. Aucun fichier n'a été modifié.")
        text = text.replace(old, new)
    shutil.copy2(PATH, PATH + ".avant-mf04")
    with open(PATH, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    print("  modifié : two_factor.py")
    print("\nMF-04 appliquée. Copie de sécurité : two_factor.py.avant-mf04 (à ne pas pousser sur Git).")


if __name__ == "__main__":
    main()
