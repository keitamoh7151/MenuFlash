"""Tests de url_shield.py sans base de données : python test_url_shield.py"""
import sys
import unittest

from flask import Flask, abort, flash, jsonify, redirect, url_for
from flask_login import LoginManager, UserMixin, current_user, login_required, login_user

from url_shield import (area_of, audit_allowed, init_url_shield, is_malformed, is_navigation,
                        safe_internal_path, short_path)

NAV = {"Accept": "text/html,application/xhtml+xml", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document"}
FETCH = {"Accept": "*/*", "Sec-Fetch-Mode": "cors", "Sec-Fetch-Dest": "empty", "X-Requested-With": "fetch"}
USERS = {}


class U(UserMixin):
    def __init__(self, uid, owner=True, sa=False):
        self.id, self.is_owner, self.is_super_admin, self.email = str(uid), owner, sa, f"u{uid}@x.ca"
        USERS[self.id] = self


def make_app(audits):
    app = Flask(__name__)
    app.config["SECRET_KEY"] = "test"
    lm = LoginManager(app)
    lm.login_view = "admin_login"
    lm.user_loader(lambda uid: USERS.get(uid))

    @app.route("/")
    def landing(): return "accueil"

    @app.route("/admin/login")
    def admin_login(): return "connexion"

    @app.route("/_login/<uid>")
    def _login(uid):
        login_user(USERS[uid]); return "ok"

    @app.route("/admin")
    @login_required
    def admin_dashboard(): return "<html>dashboard</html>"

    @app.route("/admin/commandes")
    @login_required
    def admin_orders(): return "<html>commandes</html>"

    @app.route("/admin/commandes/feed.json")
    @login_required
    def feed(): return jsonify(ok=True)

    @app.route("/admin/items/<int:item_id>/delete", methods=["POST"])
    @login_required
    def delete_item(item_id):
        if item_id != 1: abort(404)
        return "<html>supprime</html>"

    @app.route("/admin/equipe")
    @login_required
    def team():
        if not current_user.is_owner: abort(403)
        return "<html>equipe</html>"

    @app.route("/admin/formule-gestionnaire")
    @login_required
    def owner_only():
        if not current_user.is_owner:
            flash("Cette page est réservée au gestionnaire du restaurant.", "error")
            return redirect(url_for("admin_orders"))
        return "<html>g</html>"

    @app.route("/super-admin")
    @login_required
    def super_admin_dashboard():
        if not current_user.is_super_admin: abort(403)
        return "<html>sa</html>"

    @app.route("/m/<slug>")
    def public_menu(slug):
        if slug != "chez-awa": abort(404)
        return "<html>menu</html>"

    @app.route("/m/<slug>/commande/<int:order_id>")
    def order_status(slug, order_id): return "<html>suivi</html>"

    init_url_shield(app, log_audit=lambda *a, **k: audits.append((a, k)),
                    customer_target=lambda slug: "/m/chez-awa" if slug == "chez-awa" else None)
    return app


class Pure(unittest.TestCase):
    def test_navigation(self):
        self.assertTrue(is_navigation(NAV))
        self.assertFalse(is_navigation(FETCH))
        self.assertFalse(is_navigation({"Accept": "*/*"}))
        self.assertFalse(is_navigation({"Accept": "text/event-stream"}))
        self.assertTrue(is_navigation({"Accept": "text/html"}))                    # vieux navigateur
        self.assertFalse(is_navigation({"Sec-Fetch-Mode": "cors", "Accept": "text/html"}))
        self.assertFalse(is_navigation({"Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "iframe"}))

    def test_malformed(self):
        for p, q in [("/a/../b", ""), ("/a//b", ""), ("/a\\b", ""), ("/a" * 200, ""), ("/a", "x=" + "1" * 2000),
                     ("/a\x00", ""), ("/a", "x=%00"), ("/a", "x=%3Cscript%3E"), ("/a", "x=%0d%0a"), ("/a<b", "")]:
            self.assertTrue(is_malformed(p, q), (p, q))
        for p, q in [("/m/chez-awa", "qr=Table%201"), ("/admin/commandes", "filtre=today&page=2"),
                     ("/m/café", "qr=Terrasse+1&k=abcDEF-_123"), ("/", "")]:
            self.assertFalse(is_malformed(p, q), (p, q))

    def test_areas_and_paths(self):
        self.assertEqual(area_of("/admin/commande"), "admin")
        self.assertEqual(area_of("/super-admin/x"), "admin")
        self.assertEqual(area_of("/compte/appareils"), "admin")
        self.assertEqual(area_of("/administrator"), None)
        self.assertEqual(area_of("/m/chez-awa/zzz"), "public")
        self.assertIsNone(area_of("/foo"))
        for bad in ["//evil.com", "http://evil.com", "/a\\b", "", None, "/a\nb"]:
            self.assertFalse(safe_internal_path(bad), bad)
        self.assertTrue(safe_internal_path("/admin/commandes"))

    def test_audit_and_short_path(self):
        c = {}
        self.assertEqual([audit_allowed(c, "1.1.1.1", now=t) for t in (0, 1, 2, 3)], [True, True, True, False])
        self.assertTrue(audit_allowed(c, "1.1.1.1", now=700))
        self.assertEqual(short_path("/admin/reinitialiser/" + "x" * 60 + "/y"), "/admin/reinitialiser/" + "x" * 24 + "…")


class Flow(unittest.TestCase):
    def setUp(self):
        self.audits = []
        import url_shield
        url_shield._state["audits"].clear()
        self.app = make_app(self.audits)
        U(1, owner=True); U(2, owner=False); U(3, owner=True, sa=True)

    def client(self, uid=None):
        c = self.app.test_client()
        if uid:
            c.get(f"/_login/{uid}")
        return c

    def loc(self, r):
        return r.headers.get("Location")

    def test_exact_user_case(self):
        c = self.client(1)
        self.assertEqual(c.get("/admin/commandes", headers=NAV).status_code, 200)
        r = c.get("/admin/commande", headers=NAV)                     # un « s » en moins
        self.assertEqual((r.status_code, self.loc(r)), (302, "/admin/commandes"))
        self.assertNotIn(b"Not Found", r.data)
        self.assertEqual(c.get("/admin/commandes", headers=NAV).status_code, 200)

    def test_super_admin_keeps_real_errors(self):
        c = self.client(3)
        self.assertEqual(c.get("/admin/commande", headers=NAV).status_code, 404)
        self.assertEqual(c.get("/super-admin", headers=NAV).status_code, 200)

    def test_forbidden_and_method(self):
        c = self.client(2)                                            # employé (non gestionnaire)
        c.get("/admin/commandes", headers=NAV)
        for url in ("/super-admin", "/admin/equipe"):
            r = c.get(url, headers=NAV)
            self.assertEqual((r.status_code, self.loc(r)), (302, "/admin/commandes"), url)
        r = c.get("/admin/items/1/delete", headers=NAV)               # 405 : mauvaise méthode
        self.assertEqual((r.status_code, self.loc(r)), (302, "/admin/commandes"))

    def test_owner_only_flash_removed(self):
        c = self.client(2)
        r = c.get("/admin/formule-gestionnaire", headers=NAV)
        self.assertEqual(self.loc(r), "/admin/commandes")
        with c.session_transaction() as s:
            self.assertFalse(s.get("_flashes"))

    def test_staff_without_memory_goes_home(self):
        r = self.client(1).get("/admin/zzz", headers=NAV)
        self.assertEqual(self.loc(r), "/admin")
        r = self.client(2).get("/admin/zzz", headers=NAV)
        self.assertEqual(self.loc(r), "/admin/commandes")

    def test_anonymous_and_enumeration(self):
        c = self.app.test_client()
        r1 = c.get("/admin/zzz", headers=NAV)                         # n'existe pas
        r2 = self.app.test_client().get("/super-admin/zzz", headers=NAV)
        self.assertEqual((self.loc(r1), self.loc(r2)), ("/admin/login", "/admin/login"))
        r3 = self.app.test_client().get("/admin/commandes", headers=NAV)   # existe : Flask-Login
        self.assertEqual(r3.status_code, 302)
        self.assertIn("/admin/login", self.loc(r3))

    def test_customer(self):
        c = self.app.test_client()
        self.assertEqual(self.loc(c.get("/m/chez-awa/zzz", headers=NAV)), "/m/chez-awa")
        self.assertEqual(self.loc(c.get("/m/chez-awa/commandes", headers=NAV)), "/m/chez-awa")
        self.assertEqual(self.loc(c.get("/m/inconnu", headers=NAV)), "/")
        self.assertEqual(self.loc(c.get("/n-importe-quoi", headers=NAV)), "/")

    def test_scripts_files_and_bots_untouched(self):
        c = self.client(1)
        r = c.get("/admin/commande", headers=FETCH)
        self.assertEqual(r.status_code, 404)
        self.assertEqual(c.get("/admin/commandes/feed.jsn", headers={"Accept": "*/*"}).status_code, 404)
        self.assertEqual(c.get("/static/x.css", headers=NAV).status_code, 404)
        self.assertEqual(self.app.test_client().get("/wp-login.php").status_code, 404)

    def test_post_form_redirected_303(self):
        c = self.client(1)
        c.get("/admin/commandes", headers=NAV)
        r = c.post("/admin/items/99/delete", headers=NAV)             # id d'un autre restaurant
        self.assertEqual((r.status_code, self.loc(r)), (303, "/admin/commandes"))
        self.assertEqual(c.post("/admin/items/99/delete", headers=FETCH).status_code, 404)
        self.assertEqual(c.post("/admin/items/1/delete", headers=NAV).status_code, 200)

    def test_malformed_urls(self):
        c = self.client(1)
        c.get("/admin/commandes", headers=NAV)
        for url in ("/admin/commandes?x=%3Cscript%3E", "/admin/commandes?x=%00", "/admin/../super-admin",
                    "/admin/commandes?" + "a=1&" * 500):
            r = c.get(url, headers=NAV)
            self.assertEqual((r.status_code, self.loc(r)), (302, "/admin/commandes"), url)
        r = self.app.test_client().get("/m/chez-awa?qr=%3Cb%3E", headers=NAV)       # page publique, paramètre falsifié
        self.assertEqual(self.loc(r), "/m/chez-awa")
        self.assertEqual(c.get("/admin/commandes?x=%3C", headers=FETCH).status_code, 400)
        self.assertEqual(self.client(3).get("/admin/commandes?x=%3C", headers=NAV).status_code, 200)

    def test_never_remember_foreign_or_super_admin_page(self):
        c = self.client(1)
        c.get("/admin/commandes", headers=NAV)
        c.get("/super-admin", headers=NAV)                            # 403 → redirigé, non retenu
        r = c.get("/admin/zzz", headers=NAV)
        self.assertEqual(self.loc(r), "/admin/commandes")
        # cookie falsifié
        c.set_cookie("mf_nav", "falsifie")
        self.assertEqual(self.loc(c.get("/admin/zzz", headers=NAV)), "/admin")

    def test_no_infinite_loop(self):
        c = self.client(2)
        c.get("/admin/commandes", headers=NAV)
        # la page retenue devient interdite : l'employé ne doit pas tourner en rond
        self.app.view_functions["admin_orders"] = lambda: abort(403)
        hops, url = 0, "/admin/zzz"
        while hops < 8:
            r = c.get(url, headers=NAV)
            if r.status_code != 302:
                break
            url = self.loc(r); hops += 1
        self.assertEqual(url, "/")
        self.assertEqual(r.status_code, 200)
        self.assertLessEqual(hops, 4)

    def test_audit_throttled_and_clean(self):
        c = self.client(1)
        for _ in range(6):
            c.get("/admin/zzz?secret=1", headers=NAV)
        self.assertEqual(len(self.audits), 3)
        details = self.audits[0][1]["details"]
        self.assertIn("/admin/zzz", details)
        self.assertNotIn("secret", details)

    def test_idempotent(self):
        init_url_shield(self.app)
        self.assertEqual(self.client(1).get("/admin/zzz", headers=NAV).status_code, 302)


if __name__ == "__main__":
    unittest.main(verbosity=1)
