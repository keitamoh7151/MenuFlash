"""
MenuFlash Mini — Vue support d'un restaurant, EN LECTURE SEULE (super-admin).

Branché par sa_suite.py.

Objectif : comprendre et résoudre le problème d'un restaurateur en voyant ce qu'il
voit, SANS se connecter à son compte et SANS rien pouvoir modifier.

Adresse : /super-admin/restaurants/<id>/support?onglet=resume|menu|commandes|equipe|reglages

Onglets
-------
  Résumé     diagnostic automatique des problèmes probables, indicateurs, formule et
             limites, contrôles du super-admin, horaires, « À emporter »
  Menu       catégories et plats (prix, disponibilité, photo, description)
  Commandes  les 40 dernières commandes (filtrables par statut), avec le prénom des
             clients « à emporter » ; leur téléphone et email sont MASQUÉS
  Équipe     comptes, dernière activité, verrouillages, invitations en attente
             (jamais de mot de passe ni d'empreinte)
  Réglages   profil, apparence, horaires, « À emporter », limites anti-abus, QR codes

Sécurité
--------
- Aucune route d'écriture : cette page ne contient aucun formulaire.
- Réservée aux rôles Administrateur et Support (voir sa_roles.py).
- Chaque consultation est tracée dans le journal de sécurité (qui, quel restaurant,
  quel onglet).
- Téléphone et email des clients finaux masqués ; mots de passe jamais affichés.
"""
import re
from datetime import datetime

from flask import abort, render_template, request
from flask_login import current_user

from models import (MenuCategory, Order, QRCode, Restaurant, User, db)
from super_admin import PLATFORM_SLUG, PLAN_LABELS, STATUS_LABELS, _meta

TABS = [("resume", "Résumé", "bi-activity"), ("menu", "Menu", "bi-journal-richtext"),
        ("commandes", "Commandes", "bi-receipt-cutoff"), ("equipe", "Équipe", "bi-people"),
        ("reglages", "Réglages", "bi-sliders")]
ORDER_STATUSES = [("", "Toutes"), ("nouvelle", "Nouvelles"), ("en_preparation", "En préparation"),
                  ("prete", "Prêtes"), ("servie", "Servies"), ("annulee", "Annulées")]
ORDERS_LIMIT = 40


def mask_phone(phone):
    """« +223 76 12 34 56 » -> « +223 •• •• •• 56 »."""
    if not phone:
        return "—"
    digits = re.sub(r"\D", "", phone)
    if len(digits) < 4:
        return "••••"
    prefix = phone.split(" ")[0] + " " if phone.startswith("+") and " " in phone else ""
    return f"{prefix}•• •• •• {digits[-2:]}"


def mask_email(email):
    if not email or "@" not in email:
        return "—"
    local, domain = email.split("@", 1)
    return f"{local[:1]}•••@{domain}"


def init_sa_support(app, *, super_admin_required, log_audit):
    from sa_health import collect, diagnose, local_dt, risk_days

    def audit(restaurant_id, tab):
        log_audit("sa_support_view", success=True, restaurant_id=restaurant_id,
                  user_email=current_user.email if current_user.is_authenticated else None,
                  details=f"onglet {tab}"[:300])

    # ------------------------------------------------------------------ données par onglet
    def tab_resume(r, h, now):
        data = {"issues": diagnose(r, h, now), "meta": _meta(r.id)}
        features = []
        try:
            from plans import FEATURES, effective_plan, entitlement
            usage = {"max_menu_items": h["dishes"], "max_staff": h["staff_count"]}
            for key, spec in FEATURES.items():
                value = entitlement(r, key)
                features.append({"label": spec["label"], "kind": spec["kind"], "value": value,
                                 "usage": usage.get(key)})
            data["effective_plan"] = effective_plan(r)
        except Exception:
            db.session.rollback()
        data["features"] = features
        try:
            from sa_controls import control_for
            data["control"] = control_for(r.id)
        except Exception:
            db.session.rollback()
            data["control"] = None
        try:
            from service import opening_status
            data["opening"] = opening_status(r.id)
        except Exception:
            db.session.rollback()
            data["opening"] = None
        return data

    def tab_menu(r):
        cats = (MenuCategory.query.filter_by(restaurant_id=r.id).order_by(MenuCategory.position).all())
        return {"categories": cats}

    def tab_commandes(r, status):
        q = Order.query.filter_by(restaurant_id=r.id)
        if status in dict(ORDER_STATUSES) and status:
            q = q.filter(Order.status == status)
        orders = q.order_by(Order.created_at.desc()).limit(ORDERS_LIMIT).all()
        ids = [o.id for o in orders]
        refs, takeaway = {}, {}
        try:
            from order_refs import references_for
            refs = references_for(ids)
        except Exception:
            db.session.rollback()
        try:
            from takeaway import takeaway_info_map
            takeaway = takeaway_info_map(ids)
        except Exception:
            db.session.rollback()
        rows = []
        for o in orders:
            t = takeaway.get(o.id)
            rows.append({
                "order": o, "ref": refs.get(o.id), "created": local_dt(o.created_at),
                "summary": ", ".join(f"{it.quantity}× {it.item_name}" for it in o.items),
                "takeaway": ({"name": t["name"], "phone": mask_phone(t["phone"]),
                              "pickup": local_dt(t["pickup"]), "asap": t["pickup_type"] == "asap"} if t else None),
            })
        return {"orders": rows, "status": status}

    def tab_equipe(r):
        users = User.query.filter_by(restaurant_id=r.id, is_super_admin=False).order_by(User.role, User.created_at).all()
        sessions, metas, profiles = {}, {}, {}
        try:
            from security_plus import StaffMeta, UserSession
            from sqlalchemy import func
            ids = [u.id for u in users] or [0]
            sessions = dict(db.session.query(UserSession.user_id, func.max(UserSession.last_seen_at))
                            .filter(UserSession.user_id.in_(ids)).group_by(UserSession.user_id).all())
            metas = {m.user_id: m for m in StaffMeta.query.filter(StaffMeta.user_id.in_(ids)).all()}
        except Exception:
            db.session.rollback()
        try:
            from profiles import UserProfile
            profiles = {p.user_id: p for p in UserProfile.query.filter(
                UserProfile.user_id.in_([u.id for u in users] or [0])).all()}
        except Exception:
            db.session.rollback()
        rows = []
        for u in users:
            m, p = metas.get(u.id), profiles.get(u.id)
            if m and m.disabled_at:
                state = "Désactivé"
            elif m and m.invited_at and not m.accepted_at and not u.last_login_at:
                state = "Invitation en attente"
            else:
                state = "Actif"
            rows.append({"user": u, "name": (p.full_name if p and p.full_name else (m.display_name if m else None)),
                         "job": p.job if p else None, "state": state, "seen": sessions.get(u.id),
                         "locked": u.is_locked})
        return {"team": rows}

    def tab_reglages(r):
        data = {}
        try:
            from profiles import RestaurantProfile
            data["profile"] = db.session.get(RestaurantProfile, r.id)
        except Exception:
            db.session.rollback()
        try:
            from service import DAYS_FR, RestaurantHours
            rec = db.session.get(RestaurantHours, r.id)
            data["hours"] = ({"enabled": rec.enabled, "slots": rec.slots(), "days": DAYS_FR,
                              "suspended": rec.suspended, "message": rec.suspended_message} if rec else None)
        except Exception:
            db.session.rollback()
        try:
            from takeaway import TakeawaySettings
            data["takeaway"] = db.session.get(TakeawaySettings, r.id)
        except Exception:
            db.session.rollback()
        try:
            from table_limits import get_limit
            data["limits"] = {"table": get_limit(r.id, "table"), "takeaway": get_limit(r.id, "takeaway")}
        except Exception:
            db.session.rollback()
            data["limits"] = None
        data["qrcodes"] = QRCode.query.filter_by(restaurant_id=r.id).order_by(QRCode.created_at).all()
        return data

    # ------------------------------------------------------------------ route
    @app.route("/super-admin/restaurants/<int:restaurant_id>/support")
    @super_admin_required
    def super_admin_support_view(restaurant_id):
        r = db.session.get(Restaurant, restaurant_id)
        if r is None or r.slug == PLATFORM_SLUG:
            abort(404)
        tab = request.args.get("onglet", "resume")
        if tab not in {t[0] for t in TABS}:
            tab = "resume"
        now = datetime.utcnow()
        h = collect(now, [r.id]).get(r.id)
        ctx = {"restaurant": r, "h": h, "tab": tab, "tabs": TABS, "now": now, "risk": risk_days(),
               "status_label": STATUS_LABELS[h["status"]], "plan_label": PLAN_LABELS.get(r.subscription_plan),
               "order_statuses": ORDER_STATUSES}
        if tab == "resume":
            ctx.update(tab_resume(r, h, now))
        elif tab == "menu":
            ctx.update(tab_menu(r))
        elif tab == "commandes":
            ctx.update(tab_commandes(r, request.args.get("statut", "")))
        elif tab == "equipe":
            ctx.update(tab_equipe(r))
        else:
            ctx.update(tab_reglages(r))
        audit(r.id, tab)
        return render_template("sa_support.html", **ctx)
