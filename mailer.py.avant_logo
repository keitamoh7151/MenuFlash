"""
MenuFlash Mini — Envoi des emails transactionnels.

Pourquoi ce fichier
-------------------
Sur Render, les services GRATUITS bloquent les ports SMTP (25, 465, 587)
depuis le 26 septembre 2025 : un envoi SMTP (Gmail, Outlook…) n'arrive jamais.
Ce module envoie donc les emails par l'API HTTPS de Brevo (port 443, jamais
bloqué). Offre gratuite Brevo : 300 emails par jour.

Ordre de priorité (le premier configuré est utilisé)
----------------------------------------------------
1. BREVO_API_KEY           -> API HTTPS Brevo   (recommandé, fonctionne sur Render gratuit)
2. MAIL_SERVER (+ MAIL_*)  -> SMTP classique    (local, ou Render payant)
3. Rien                    -> le contenu de l'email (avec le lien) est écrit
                              dans les logs : utile pour tester, mais rien n'est envoyé.

Variables d'environnement (Render > Environment)
------------------------------------------------
    BREVO_API_KEY        = xkeysib-....
    MAIL_DEFAULT_SENDER  = adresse VÉRIFIÉE dans Brevo (ex. menuflash.mini@gmail.com)
    MAIL_SENDER_NAME     = MenuFlash Mini            (optionnel)

Interface (inchangée pour le reste de l'application)
----------------------------------------------------
    init_mailer(app)
    send_transactional_email(app, to, subject, body_text) -> True / False
    is_mail_configured(app) -> True / False
"""
import json
import logging
import os
import smtplib
import ssl
import urllib.error
import urllib.request
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

logger = logging.getLogger("menuflash.mailer")

BREVO_URL = "https://api.brevo.com/v3/smtp/email"
HTTP_TIMEOUT = 10
SMTP_TIMEOUT = 10


def init_mailer(app):
    app.config.setdefault("BREVO_API_KEY", os.environ.get("BREVO_API_KEY", "").strip())
    app.config.setdefault("MAIL_SENDER_NAME", os.environ.get("MAIL_SENDER_NAME", "MenuFlash Mini").strip())
    mode = _mode(app)
    sender = app.config.get("MAIL_DEFAULT_SENDER", "")
    if mode == "log":
        logger.warning("Emails NON configurés : ils seront seulement écrits dans les logs. "
                       "Définissez BREVO_API_KEY et MAIL_DEFAULT_SENDER.")
    elif sender.endswith(".local"):
        logger.warning("MAIL_DEFAULT_SENDER (%s) n'est pas une vraie adresse : les emails seront refusés. "
                       "Utilisez l'adresse vérifiée dans Brevo.", sender)
    else:
        logger.info("Envoi des emails actif (mode %s, expéditeur %s).", mode, sender)


def _mode(app):
    if app.config.get("BREVO_API_KEY"):
        return "brevo"
    if app.config.get("MAIL_SERVER"):
        return "smtp"
    return "log"


def is_mail_configured(app):
    return _mode(app) != "log"


def send_transactional_email(app, to, subject, body_text):
    """Envoie un email texte. Ne lève jamais d'exception : renvoie True / False."""
    to = (to or "").strip()
    if not to or "@" not in to or "\n" in to or "\r" in to:
        logger.error("Email non envoyé : destinataire invalide (%r).", to)
        return False
    subject = " ".join((subject or "").split())[:200]
    mode = _mode(app)
    try:
        if mode == "brevo":
            return _send_brevo(app, to, subject, body_text)
        if mode == "smtp":
            return _send_smtp(app, to, subject, body_text)
    except Exception:
        logger.exception("Échec d'envoi de l'email « %s » à %s (mode %s).", subject, to, mode)
        return False
    logger.warning("EMAIL NON ENVOYÉ (aucun service configuré)\nÀ : %s\nObjet : %s\n\n%s",
                   to, subject, body_text)
    return False


def _sender(app):
    return (app.config.get("MAIL_DEFAULT_SENDER") or "").strip(), \
           (app.config.get("MAIL_SENDER_NAME") or "MenuFlash Mini").strip()


def _send_brevo(app, to, subject, body_text):
    email, name = _sender(app)
    payload = {
        "sender": {"email": email, "name": name},
        "to": [{"email": to}],
        "subject": subject,
        "textContent": body_text,
        "tags": ["menuflash-transactional"],
    }
    req = urllib.request.Request(
        BREVO_URL, data=json.dumps(payload).encode("utf-8"), method="POST",
        headers={"api-key": app.config["BREVO_API_KEY"], "Content-Type": "application/json",
                 "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            ok = 200 <= resp.status < 300
            if ok:
                logger.info("Email « %s » envoyé à %s (Brevo).", subject, to)
            return ok
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "ignore")[:500]
        logger.error("Brevo a refusé l'email « %s » à %s : HTTP %s %s", subject, to, exc.code, detail)
        return False


def _send_smtp(app, to, subject, body_text):
    email, name = _sender(app)
    msg = EmailMessage()
    msg["From"] = formataddr((name, email))
    msg["To"] = to
    msg["Subject"] = subject
    msg["Message-ID"] = make_msgid(domain=email.split("@")[-1] if "@" in email else None)
    msg.set_content(body_text)
    host, port = app.config["MAIL_SERVER"], int(app.config.get("MAIL_PORT", 587))
    user, pwd = app.config.get("MAIL_USERNAME"), app.config.get("MAIL_PASSWORD")
    ctx = ssl.create_default_context()
    if port == 465:
        server = smtplib.SMTP_SSL(host, port, timeout=SMTP_TIMEOUT, context=ctx)
    else:
        server = smtplib.SMTP(host, port, timeout=SMTP_TIMEOUT)
    with server:
        if port != 465 and app.config.get("MAIL_USE_TLS", True):
            server.starttls(context=ctx)
        if user:
            server.login(user, pwd)
        server.send_message(msg)
    logger.info("Email « %s » envoyé à %s (SMTP).", subject, to)
    return True
