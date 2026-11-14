"""
MenuFlash — Correction de sécurité MF-15 (audit du 13/11/2026).

PROBLÈME
--------
Si l'envoi d'emails n'est pas configuré (BREVO_API_KEY / MAIL_SERVER absents ou
supprimés par erreur), mailer.py écrivait le CONTENU COMPLET de l'email dans les
logs : liens de réinitialisation du mot de passe (1 h), invitations d'équipe et
d'administrateurs (72 h), « Ce n'était pas moi »… Toute personne ayant accès aux
logs Render pouvait s'en servir pour prendre un compte. Les adresses des
destinataires apparaissaient aussi en clair dans les logs à chaque envoi.

CORRECTION (mailer.py uniquement)
---------------------------------
1. Sur Render (variable RENDER présente) : un email non envoyé n'écrit plus que
   l'objet et le destinataire masqué (a***@domaine.com). Jamais le contenu.
   En local (sans RENDER) : le contenu reste affiché, pour tes tests, comme avant.
2. Les adresses sont masquées dans tous les logs d'envoi (succès et erreurs).
3. Au démarrage sur Render, si les emails ne sont pas configurés : message
   d'ERREUR (et non plus simple avertissement), pour qu'il soit repéré.

UTILISATION (à la racine du projet) :  python corriger_mf15.py
Vérifie tout avant d'écrire ; copie : mailer.py.avant-mf15
"""
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(ROOT, "mailer.py")

R = [
('''BREVO_URL = "https://api.brevo.com/v3/smtp/email"''',
'''BREVO_URL = "https://api.brevo.com/v3/smtp/email"


def _mask(email):
    """a***@domaine.com : l'adresse reste reconnaissable sans être lisible (audit MF-15)."""
    email = str(email or "")
    local, _, domain = email.partition("@")
    return (local[:1] + "***@" + domain) if domain else "***"


def _on_render():
    return bool(os.environ.get("RENDER"))''', "BREVO_URL"),

('''    if mode == "log":
        logger.warning("Emails NON configurés : ils seront seulement écrits dans les logs. "
                       "Définissez BREVO_API_KEY et MAIL_DEFAULT_SENDER.")''',
'''    if mode == "log":
        if _on_render():
            logger.error("Emails NON configurés en production : AUCUN email ne part (réinitialisations, "
                         "invitations, alertes). Définissez BREVO_API_KEY et MAIL_DEFAULT_SENDER.")
        else:
            logger.warning("Emails NON configurés : ils seront seulement écrits dans les logs (local). "
                           "Définissez BREVO_API_KEY et MAIL_DEFAULT_SENDER.")''', "avertissement de démarrage"),

('''        logger.error("Email non envoyé : destinataire invalide (%r).", to)''',
'''        logger.error("Email non envoyé : destinataire invalide (%s).", _mask(to))''', "destinataire invalide"),

('''        logger.exception("Échec d'envoi de l'email « %s » à %s (mode %s).", subject, to, mode)''',
'''        logger.exception("Échec d'envoi de l'email « %s » à %s (mode %s).", subject, _mask(to), mode)''', "échec d'envoi"),

('''    logger.warning("EMAIL NON ENVOYÉ (aucun service configuré)\\nÀ : %s\\nObjet : %s\\n\\n%s",
                   to, subject, body_text)''',
'''    if _on_render():
        # Audit MF-15 : jamais le contenu en production (liens de réinitialisation, invitations…).
        logger.error("EMAIL NON ENVOYÉ (aucun service configuré) — À : %s — Objet : %s",
                     _mask(to), subject)
    else:
        logger.warning("EMAIL NON ENVOYÉ (aucun service configuré, mode local)\\nÀ : %s\\nObjet : %s\\n\\n%s",
                       to, subject, body_text)''', "email non envoyé"),

('''                logger.info("Email « %s » envoyé à %s (Brevo).", subject, to)''',
'''                logger.info("Email « %s » envoyé à %s (Brevo).", subject, _mask(to))''', "succès Brevo"),

('''        logger.error("Brevo a refusé l'email « %s » à %s : HTTP %s %s", subject, to, exc.code, detail)''',
'''        logger.error("Brevo a refusé l'email « %s » à %s : HTTP %s %s", subject, _mask(to), exc.code, detail)''', "refus Brevo"),

('''    logger.info("Email « %s » envoyé à %s (SMTP).", subject, to)''',
'''    logger.info("Email « %s » envoyé à %s (SMTP).", subject, _mask(to))''', "succès SMTP"),
]


def main():
    if not os.path.exists(PATH):
        sys.exit("ARRÊT : mailer.py introuvable (lancez le script à la racine du projet).")
    text = open(PATH, encoding="utf-8").read()
    if "def _mask(email)" in text:
        print("MF-15 est déjà appliquée : rien à faire.")
        return
    if "import os" not in text:
        sys.exit("ARRÊT : « import os » absent de mailer.py. Aucun fichier n'a été modifié.")
    for old, new, label in R:
        if text.count(old) != 1:
            sys.exit(f"ARRÊT : repère « {label} » introuvable dans mailer.py. Aucun fichier n'a été modifié.")
        text = text.replace(old, new)
    shutil.copy2(PATH, PATH + ".avant-mf15")
    with open(PATH, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    print("  modifié : mailer.py")
    print("\nMF-15 appliquée. Copie de sécurité : mailer.py.avant-mf15 (à ne pas pousser sur Git).")


if __name__ == "__main__":
    main()
