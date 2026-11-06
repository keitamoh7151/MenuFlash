"""
MenuFlash Mini — Demandes de données personnelles (accès, export, effacement).

Branché par sa_suite.py. Réservé au rôle Administrateur.

Pourquoi : la politique de confidentialité promet à un client de pouvoir demander
l'accès à ses données et leur suppression (loi malienne n° 2013-015, comme le RGPD).
Cette page permet de répondre à une telle demande en quelques minutes.

Fonctionnement
--------------
1. Recherche (POST, jamais dans l'adresse de la page : pas de donnée personnelle
   dans les journaux d'accès) par TÉLÉPHONE, EMAIL ou PRÉNOM.
   - téléphone : au moins 7 chiffres, espaces et « + » ignorés ;
   - email : correspondance exacte ;
   - prénom : contient (peut renvoyer plusieurs personnes : cochez les bonnes lignes).
2. Résultat : les commandes à emporter concernées (restaurant, date, référence,
   prénom, téléphone, email, articles) et, en lecture seule, les comptes
   (gestionnaires ou équipe) qui correspondent.
3. EXPORTER : fichier JSON des lignes cochées, à transmettre à la personne.
4. EFFACER : anonymise les lignes COCHÉES (jamais « tout ce qui correspond »).
   Prénom -> « Client supprimé », téléphone et email vidés, lien de suivi invalidé,
   nom affiché sur la commande, remarque et commentaire d'avis effacés. Le montant
   et les articles restent (statistiques du restaurant). Il faut taper EFFACER et
   saisir son mot de passe.
5. Chaque export / effacement est enregistré dans « Historique des demandes » :
   date, qui, nombre de lignes, et une EMPREINTE de la recherche (HMAC) : la preuve
   que la demande a été traitée, sans conserver la donnée personnelle elle-même.

Les comptes de restaurateurs ou d'employés ne sont pas supprimés ici : fermez le
compte depuis la fiche du client (archivage puis suppression).

Nouvelle table « privacy_requests » (créée automatiquement, incluse dans les
sauvegardes). L'effacement automatique après 90 jours (CUSTOMER_DATA_DAYS) reste actif.
"""
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
from datetime import datetime, timedelta

from flask import Response, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import func

from models import Order, Restaurant, User, db

log = logging.getLogger("menuflash.sa_privacy")

EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")
MAX_RESULTS = 200
ERASED_NAME = "Client supprimé"
ERASED_LABEL = "À emporter · (supprimé)"
KIND_LABELS = {"phone": "téléphone", "email": "email", "name": "prénom"}


class PrivacyRequest(db.Model):
    __tablename__ = "privacy_requests"
    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    handled_by = db.Column(db.String(120))
    kind = db.Column(db.String(10), nullable=False)               # export / erase
    subject_kind = db.Column(db.String(10), nullable=False)       # phone / email / name
    subject_hash = db.Column(db.String(64), nullable=False)       # HMAC de la recherche
    orders_count = db.Column(db.Integer, nullable=False, default=0)
    note = db.Column(db.String(200))


# ==========================================================================
# Fonctions pures (testées)
# ==========================================================================
def parse_subject(raw):
    """Renvoie ((genre, valeur_normalisée), None) ou (None, message d'erreur)."""
    raw = (raw or "").strip()[:120]
    if not raw:
        return None, "Saisissez un numéro de téléphone, un email ou un prénom."
    if "@" in raw:
        email = raw.lower()
        if not EMAIL_RE.match(email):
            return None, "Cette adresse email n'est pas valide."
        return ("email", email), None
    cleaned = re.sub(r"[\s().\-+]", "", raw)
    if cleaned.isdigit():
        digits = cleaned[2:] if cleaned.startswith("00") else cleaned
        if len(digits) < 7:
            return None, "Saisissez au moins 7 chiffres pour rechercher un numéro de téléphone."
        return ("phone", digits), None
    if len(raw) < 3:
        return None, "Saisissez au moins 3 lettres pour rechercher un prénom."
    return ("name", " ".join(raw.casefold().split())), None


def subject_hash(secret, kind, value):
    return hmac.new(secret.encode(), f"{kind}:{value}".encode(), hashlib.sha256).hexdigest()


def digits_only(text):
    return re.sub(r"\D", "", text or "")


def init_sa_privacy(app, *, super_admin_required, log_audit):
    from takeaway import TakeawayOrder

    with app.app_context():
        try:
            PrivacyRequest.__table__.create(db.engine, checkfirst=True)
        except Exception:
            log.exception("Création de la table privacy_requests impossible")

    secret = app.config["SECRET_KEY"]

    def audit(action, details=None, success=True):
        log_audit(action[:40], success=success, restaurant_id=None,
                  user_email=current_user.email if current_user.is_authenticated else None,
                  details=(details or "")[:300])

    # ------------------------------------------------------------------ recherche
    def search(kind, value):
        t = TakeawayOrder
        q = (db.session.query(t, Order, Restaurant)
             .join(Order, Order.id == t.order_id).join(Restaurant, Restaurant.id == t.restaurant_id))
        if kind == "email":
            q = q.filter(func.lower(t.customer_email) == value)
        elif kind == "phone":
            stripped = t.customer_phone
            for ch in (" ", "+", "-", "(", ")", "."):
                stripped = func.replace(stripped, ch, "")
            q = q.filter(stripped.contains(value, autoescape=True))
        else:
            q = q.filter(func.lower(t.customer_name).contains(value, autoescape=True))
        return q.order_by(t.order_id.desc()).limit(MAX_RESULTS).all()

    def search_accounts(kind, value):
        out = []
        try:
            from profiles import UserProfile
            q = db.session.query(User, UserProfile).outerjoin(UserProfile, UserProfile.user_id == User.id)
            if kind == "email":
                q = q.filter(func.lower(User.email) == value)
            elif kind == "phone":
                stripped = UserProfile.phone
                for ch in (" ", "+", "-", "(", ")", "."):
                    stripped = func.replace(stripped, ch, "")
                q = q.filter(stripped.contains(value, autoescape=True))
            else:
                q = q.filter(func.lower(UserProfile.first_name).contains(value, autoescape=True))
            for u, p in q.filter(User.is_super_admin.is_(False)).limit(50).all():
                resto = db.session.get(Restaurant, u.restaurant_id)
                out.append({"user": u, "profile": p, "restaurant": resto})
        except Exception:
            db.session.rollback()
            log.exception("Recherche de comptes impossible")
        return out

    def refs_for(ids):
        try:
            from order_refs import references_for
            return references_for(ids)
        except Exception:
            db.session.rollback()
            return {}

    def result_rows(found):
        refs = refs_for([o.id for _, o, _ in found])
        rows = []
        for t, o, r in found:
            rows.append({"id": o.id, "restaurant": r.name, "restaurant_id": r.id, "created": o.created_at,
                         "ref": refs.get(o.id), "name": t.customer_name, "phone": t.customer_phone or "",
                         "email": t.customer_email or "", "status": o.status, "total": o.total,
                         "currency": r.currency, "pickup": t.pickup_at, "notes": o.notes or "",
                         "articles": [{"nom": it.item_name, "quantite": it.quantity, "prix_unitaire": it.item_price}
                                      for it in o.items],
                         "erased": t.customer_name == ERASED_NAME})
        return rows

    def retention():
        try:
            days = max(7, min(int(os.environ.get("CUSTOMER_DATA_DAYS", "90")), 3650))
        except ValueError:
            days = 90
        limit = datetime.utcnow() - timedelta(days=days)
        overdue = TakeawayOrder.query.filter(
            TakeawayOrder.pickup_at < limit,
            (TakeawayOrder.customer_phone != "") | (TakeawayOrder.customer_email.isnot(None))).count()
        return {"days": days, "overdue": overdue}

    def history():
        return PrivacyRequest.query.order_by(PrivacyRequest.created_at.desc()).limit(50).all()

    def page(**extra):
        return render_template("sa_privacy.html", history=history(), retention=retention(),
                               kind_labels=KIND_LABELS, **extra)

    def selected_ids():
        ids = set()
        for raw in request.form.getlist("ids")[:MAX_RESULTS]:
            if raw.isdigit():
                ids.add(int(raw))
        return ids

    # ------------------------------------------------------------------ pages
    @app.route("/super-admin/donnees-personnelles", methods=["GET", "POST"])
    @super_admin_required
    def super_admin_privacy():
        if request.method == "GET":
            return page(searched=False)
        raw = request.form.get("q", "")
        subject, err = parse_subject(raw)
        if err:
            flash(err, "error")
            return page(searched=False)
        kind, value = subject
        found = search(kind, value)
        rows = result_rows(found)
        accounts = search_accounts(kind, value)
        audit("sa_privacy_search", f"recherche par {KIND_LABELS[kind]} : {len(rows)} commande(s), "
                                   f"{len(accounts)} compte(s)")
        return page(searched=True, q=raw.strip()[:120], kind=kind, rows=rows, accounts=accounts,
                    max_results=MAX_RESULTS)

    def selected_matches(kind, value):
        """Lignes cochées ∩ résultats de la recherche (on n'agit jamais hors de la recherche)."""
        wanted = selected_ids()
        found = search(kind, value)
        if wanted:
            found = [row for row in found if row[1].id in wanted]
        return found

    @app.route("/super-admin/donnees-personnelles/export", methods=["POST"])
    @super_admin_required
    def super_admin_privacy_export():
        subject, err = parse_subject(request.form.get("q", ""))
        if err:
            flash(err, "error")
            return redirect(url_for("super_admin_privacy"))
        kind, value = subject
        found = selected_matches(kind, value)
        rows = result_rows(found)
        accounts = search_accounts(kind, value)
        payload = {
            "genere_le": datetime.utcnow().isoformat() + "Z",
            "demande": {"type": KIND_LABELS[kind], "valeur_recherchee": request.form.get("q", "").strip()[:120]},
            "commandes_a_emporter": [{
                "commande": r["id"], "reference": r["ref"], "restaurant": r["restaurant"],
                "date": r["created"].isoformat() if r["created"] else None,
                "retrait": r["pickup"].isoformat() if r["pickup"] else None,
                "statut": r["status"], "prenom": r["name"], "telephone": r["phone"] or None,
                "email": r["email"] or None, "total": r["total"], "devise": r["currency"],
                "remarque": r["notes"] or None, "articles": r["articles"]} for r in rows],
            "comptes": [{"email": a["user"].email, "role": a["user"].role,
                         "restaurant": a["restaurant"].name if a["restaurant"] else None,
                         "prenom": a["profile"].first_name if a["profile"] else None,
                         "nom": a["profile"].last_name if a["profile"] else None,
                         "telephone": a["profile"].phone if a["profile"] else None} for a in accounts],
        }
        db.session.add(PrivacyRequest(handled_by=current_user.email, kind="export", subject_kind=kind,
                                      subject_hash=subject_hash(secret, kind, value), orders_count=len(rows),
                                      note=" ".join((request.form.get("note") or "").split())[:200] or None))
        db.session.commit()
        audit("sa_privacy_export", f"export : {len(rows)} commande(s), {len(accounts)} compte(s)")
        body = json.dumps(payload, ensure_ascii=False, indent=2)
        return Response(body, mimetype="application/json", headers={
            "Content-Disposition": f'attachment; filename="donnees_personnelles_{datetime.utcnow():%Y%m%d_%H%M}.json"',
            "Cache-Control": "no-store"})

    @app.route("/super-admin/donnees-personnelles/effacer", methods=["POST"])
    @super_admin_required
    def super_admin_privacy_erase():
        subject, err = parse_subject(request.form.get("q", ""))
        if err:
            flash(err, "error")
            return redirect(url_for("super_admin_privacy"))
        kind, value = subject
        if not selected_ids():
            flash("Cochez au moins une commande à effacer.", "error")
            return redirect(url_for("super_admin_privacy"))
        if (request.form.get("confirm") or "").strip().upper() != "EFFACER":
            flash("Confirmation incorrecte : tapez EFFACER en majuscules.", "error")
            return redirect(url_for("super_admin_privacy"))
        pwd = request.form.get("confirm_password", "")
        if not pwd or len(pwd) > 256 or not current_user.check_password(pwd):
            audit("sa_privacy_refused", "mot de passe incorrect", success=False)
            flash("Mot de passe incorrect.", "error")
            return redirect(url_for("super_admin_privacy"))
        found = selected_matches(kind, value)
        done = 0
        try:
            for t, o, r in found:
                if t.customer_name == ERASED_NAME and not t.customer_phone and not t.customer_email:
                    continue
                t.customer_name, t.customer_phone, t.customer_email = ERASED_NAME, "", None
                t.public_token = secrets.token_urlsafe(32)               # le lien de suivi ne marche plus
                o.table_label, o.notes, o.comment = ERASED_LABEL, None, None
                done += 1
            db.session.add(PrivacyRequest(
                handled_by=current_user.email, kind="erase", subject_kind=kind,
                subject_hash=subject_hash(secret, kind, value), orders_count=done,
                note=" ".join((request.form.get("note") or "").split())[:200] or None))
            db.session.commit()
        except Exception:
            db.session.rollback()
            log.exception("Effacement impossible")
            flash("L'effacement a échoué : aucune donnée n'a été modifiée.", "error")
            return redirect(url_for("super_admin_privacy"))
        audit("sa_privacy_erase", f"{done} commande(s) anonymisée(s) (recherche par {KIND_LABELS[kind]})")
        flash(f"{done} commande(s) anonymisée(s). La demande est enregistrée dans l'historique.", "success")
        return redirect(url_for("super_admin_privacy"))
