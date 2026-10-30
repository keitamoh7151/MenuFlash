"""
Envoi d'emails transactionnels (vérification d'adresse, réinitialisation
de mot de passe) via Flask-Mail, avec repli automatique en l'absence de
configuration SMTP (le lien est alors uniquement journalisé, utile pour
tester en local mais à configurer avant tout déploiement réel).
"""
import logging
from flask_mail import Mail, Message

logger = logging.getLogger("menuflash.mailer")
mail = Mail()


def init_mailer(app):
    app.config.setdefault("MAIL_SERVER", "")
    app.config.setdefault("MAIL_PORT", 587)
    app.config.setdefault("MAIL_USE_TLS", True)
    app.config.setdefault("MAIL_USERNAME", "")
    app.config.setdefault("MAIL_PASSWORD", "")
    app.config.setdefault("MAIL_DEFAULT_SENDER", "no-reply@menuflash.local")
    mail.init_app(app)


def is_mail_configured(app):
    return bool(app.config.get("MAIL_SERVER"))


def send_transactional_email(app, to, subject, body_text):
    if is_mail_configured(app):
        try:
            msg = Message(subject=subject, recipients=[to], body=body_text)
            mail.send(msg)
            return True
        except Exception:
            logger.exception("Échec de l'envoi d'email à %s (sujet: %s)", to, subject)
            return False
    else:
        logger.warning(
            "SMTP non configuré — email NON envoyé réellement. "
            "Destinataire: %s | Sujet: %s\n%s",
            to, subject, body_text,
        )
        return False
