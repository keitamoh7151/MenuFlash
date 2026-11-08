"""
MenuFlash Mini — Outils d'exploitation du super-admin (point d'entrée unique).

Branchement dans app.py (voir ajouter_branchement.py), APRÈS init_sa_controls(...) :

    from sa_suite import init_sa_suite
    init_sa_suite(app, super_admin_required=super_admin_required, log_audit=log_audit, limiter=limiter)

Ce que cela active
------------------
  sa_roles.py    Plusieurs comptes super-admin avec rôles (Administrateur, Support,
                 Finances en lecture seule), par invitation, avec double authentification.
  sa_health.py   Santé d'activité de chaque restaurant, diagnostic automatique, type de
                 compte (Client / Pilote offert / Démo), seuil « sans commande » réglable.
  sa_support.py  Vue support d'un restaurant, en lecture seule, sans se connecter à son compte.
  sa_privacy.py  Demandes de données personnelles : recherche, export, effacement tracé.

Les nouvelles pages sont dans le menu « Clients » et « Système » du panel.
"""
from sa_health import init_sa_health
from sa_privacy import init_sa_privacy
from sa_roles import init_sa_roles
from sa_support import init_sa_support


def init_sa_suite(app, *, super_admin_required, log_audit, limiter=None):
    # Les rôles d'abord : la garde par rôle doit passer avant les routes du panel.
    init_sa_roles(app, super_admin_required=super_admin_required, log_audit=log_audit, limiter=limiter)
    init_sa_health(app, super_admin_required=super_admin_required, log_audit=log_audit)
    init_sa_support(app, super_admin_required=super_admin_required, log_audit=log_audit)
    init_sa_privacy(app, super_admin_required=super_admin_required, log_audit=log_audit)
