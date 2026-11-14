"""
verifier_lot3.py — rejoue les attaques de la deuxième passe d'audit sur TON code.
Base de données temporaire : ton menuflash.db et ta production ne sont jamais touchés.
Dure environ 1 minute.   Utilisation :  python verifier_lot3.py
"""
import os, sys, re, io, time, warnings, logging, contextlib, tempfile, pathlib, shutil, atexit, statistics
warnings.filterwarnings("ignore"); logging.disable(logging.CRITICAL)
try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass
_T = tempfile.mkdtemp(prefix="menuflash_lot3_"); atexit.register(lambda: shutil.rmtree(_T, ignore_errors=True))
for k in ("RENDER", "RATELIMIT_STORAGE_URI", "TURNSTILE_SITE_KEY", "TURNSTILE_SECRET_KEY", "BACKUP_ENCRYPTION_KEY", "CSP_MODE"): os.environ.pop(k, None)
os.environ["DATABASE_URL"] = "sqlite:///" + pathlib.Path(_T, "t.db").as_posix()
os.environ["FLASK_SECRET_KEY"] = "x" * 64; os.environ["BACKUP_AUTO"] = "false"
with contextlib.redirect_stdout(io.StringIO()):
    import seed
import app as appmod
from app import app, limiter
from models import db, Restaurant, User, MenuCategory, MenuItem, Order
from flask_login import login_user
from datetime import datetime, timedelta
import takeaway, two_factor
from takeaway import TakeawaySettings
limiter.enabled = False
app.config["PROPAGATE_EXCEPTIONS"] = False; app.config["TESTING"] = False

SENT = []
def fake_send(*a, **k):
    time.sleep(0.3); SENT.append(a[1] if len(a) > 1 else k.get("to")); return True
appmod.send_transactional_email = fake_send
takeaway.send_transactional_email = fake_send

@app.route("/__t/<int:uid>")
def __t(uid):
    u = db.session.get(User, uid); login_user(u)
    if u.is_super_admin: two_factor.mark_verified(u)
    return "ok"

with app.app_context():
    A = Restaurant.query.filter_by(slug="chez-awa").first(); A.subscription_plan = "pro"; A.subscription_active_until = datetime.utcnow() + timedelta(days=30)
    db.session.merge(TakeawaySettings(restaurant_id=A.id, enabled=True, allow_asap=True, allow_scheduled=True, prep_minutes=10, max_per_slot=500)); db.session.commit()
    owner = User.query.filter_by(email="awa@example.com").first(); sa = User.query.filter_by(is_super_admin=True).first()
    it1 = MenuItem.query.filter_by(restaurant_id=A.id).first()
    cat_x = MenuCategory(restaurant_id=A.id, name="Rush", position=99, is_paused=True); db.session.add(cat_x); db.session.commit()
    paused_item = MenuItem(restaurant_id=A.id, category_id=cat_x.id, name="Plat en pause", price=1234, position=1, is_available=True); db.session.add(paused_item); db.session.commit()
    IDS = dict(o=owner.id, sa=sa.id, it1=it1.id, paused=paused_item.id, rid=A.id)

RES = []
def check(label, ok, detail=""):
    RES.append(bool(ok)); print(("OK   " if ok else "FAIL ") + label + (f"  [{detail}]" if detail else ""))
def client(uid=None):
    c = app.test_client()
    if uid: c.get(f"/__t/{uid}")
    return c
def csrf(c, path="/admin/login"):
    h = c.get(path).get_data(as_text=True)
    m = re.search(r'name="csrf-token" content="([^"]+)"', h) or re.search(r'name="csrf_token" value="([^"]+)"', h)
    return m.group(1) if m else ""
def code(fn):
    try: return fn().status_code
    except Exception: return 599
BIG = "99999999999999999999999999"

print("\n=== LOT3-01  Numéros géants")
anon = client(); t = csrf(anon); own = client(IDS["o"]); to = csrf(own, "/admin")
for label, fn in [("anonyme  GET  /media/<géant>", lambda: anon.get(f"/media/{BIG}")),
                  ("anonyme  GET  /m/chez-awa/qrcodes/<géant>.png", lambda: anon.get(f"/m/chez-awa/qrcodes/{BIG}.png")),
                  ("anonyme  POST /super-admin/restaurants/<géant>/delete", lambda: anon.post(f"/super-admin/restaurants/{BIG}/delete", data={"csrf_token": t}, headers={"X-CSRFToken": t})),
                  ("gérant   POST /admin/items/<géant>/edit", lambda: own.post(f"/admin/items/{BIG}/edit", data={"csrf_token": to}, headers={"X-CSRFToken": to})),
                  ("gérant   POST /admin/commandes/<géant>/avancer", lambda: own.post(f"/admin/commandes/{BIG}/avancer", data={"csrf_token": to}, headers={"X-CSRFToken": to})),
                  ("gérant   POST /admin/items/add (category_id géant)", lambda: own.post("/admin/items/add", data={"csrf_token": to, "category_id": BIG, "name": "x", "price": "1"}))]:
    c_ = code(fn); check(f"{label:<58} HTTP {c_}", c_ < 500)

print("\n=== LOT3-02  Ajout d'un plat sans être connecté")
c_ = code(lambda: anon.post("/admin/items/add", data={"csrf_token": t, "name": "x", "price": "1"}, headers={"X-CSRFToken": t}))
check(f"anonyme POST /admin/items/add -> HTTP {c_} (pas d'erreur 500)", c_ < 500)

print("\n=== LOT3-03  Listes de réordonnancement malformées")
for path in ("/admin/categories/reorder", "/admin/items/reorder"):
    for body in ({"order": [{"x": 1}]}, {"order": [10 ** 30]}, [1, 2, 3], {"order": "abc"}):
        c_ = code(lambda: own.post(path, json=body, headers={"X-CSRFToken": to}))
        check(f"gérant POST {path} {str(body)[:30]:<30} HTTP {c_}", c_ < 500)

print("\n=== LOT3-04  Mode rush (catégorie en pause)")
def place(extra, c=None):
    c = c or app.test_client(); h = c.get("/m/chez-awa").get_data(as_text=True)
    tk = re.search(r'name="csrf_token" value="([^"]+)"', h).group(1)
    data = {"csrf_token": tk, "fulfillment": "takeaway", "customer_name": "Awa", "customer_phone": "76123456", "customer_phone_country": "ML", "pickup_type": "asap"}; data.update(extra)
    r = c.post("/m/chez-awa/commande", data=data, headers={"X-Requested-With": "fetch"}); return r.status_code, (r.get_json(silent=True) or {})
s, j = place({f"qty_{IDS['paused']}": "1"}); check(f"Plat d'une catégorie EN PAUSE : commande refusée (ok={j.get('ok')})", not j.get("ok"))
s, j = place({f"qty_{IDS['it1']}": "1"}); check(f"Contrôle : un plat normal reste commandable (ok={j.get('ok')})", j.get("ok") is True)

print("\n=== LOT3-05  Pages privées jamais mises en cache")
sac = client(IDS["sa"]); koa = client(IDS["o"])
for who, c, p in [("gérant", koa, "/admin"), ("gérant", koa, "/admin/journal-securite"), ("gérant", koa, "/admin/menu/import"), ("super-admin", sac, "/super-admin/sauvegardes"), ("super-admin", sac, "/super-admin/clients/export.csv")]:
    r = c.get(p); cc = r.headers.get("Cache-Control", "(absent)"); check(f"{who:<12} {p:<34} Cache-Control: {cc[:40]}", "no-store" in cc)
pub = app.test_client().get("/m/chez-awa"); check("Le menu public n'est pas affecté (pas de no-store imposé)", "no-store" not in pub.headers.get("Cache-Control", ""))

print("\n=== LOT3-06  « Mot de passe oublié » (patience : ~25 s)")
def forgot(c, email):
    h = c.get("/admin/mot-de-passe-oublie").get_data(as_text=True); f = {"email": email}
    for n in ("csrf_token", "form_ts"):
        m = re.search(rf'name="{n}" value="([^"]+)"', h); f[n] = m.group(1) if m else ""
    time.sleep(3.1); t0 = time.perf_counter(); r = c.post("/admin/mot-de-passe-oublie", data=f); return time.perf_counter() - t0
SENT.clear(); ex = [forgot(app.test_client(), "awa@example.com") for _ in range(3)]; un = [forgot(app.test_client(), f"inconnu{i}@nulle-part.test") for i in range(3)]
time.sleep(1.2)
check(f"Compte existant {statistics.mean(ex)*1000:.0f} ms / compte inconnu {statistics.mean(un)*1000:.0f} ms : indiscernables (écart < 100 ms)", abs(statistics.mean(ex) - statistics.mean(un)) < 0.1)
forgot(app.test_client(), "awa@example.com"); time.sleep(1.2)
n_mail = len([s for s in SENT if s == "awa@example.com"]); check(f"Après 4 demandes : {n_mail} email(s) reçu(s) par le compte (maximum 3)", n_mail <= 3)

print("\n=== LOT3-07  Commandes à emporter : emails vers une adresse choisie par le visiteur")
SENT.clear()
for i in range(8): place({f"qty_{IDS['it1']}": "1", "customer_email": "victime@exemple.test"})
time.sleep(0.5); n = len([s for s in SENT if s == "victime@exemple.test"])
check(f"8 commandes avec la même adresse : {n} email(s) de confirmation reçu(s) (maximum 6)", n <= 6)

print("\n=== LOT3-08  Redirections « retour » et paramètres 2FA")
c = client(IDS["o"]); tk = csrf(c, "/admin")
r = c.post("/m/chez-awa/commande", data={"csrf_token": csrf(c, "/m/chez-awa"), "fulfillment": "takeaway"}, headers={"Referer": "http://evil.test/piege"})
loc = r.headers.get("Location", ""); check(f"Échec d'une commande + Referer pirate : redirection vers « {loc[:30] or '(aucune)'} »", "evil.test" not in loc)
with app.app_context():
    u = db.session.get(User, IDS["o"]); u.email_verified = False; db.session.commit()
c2 = client(IDS["o"]); tk2 = csrf(c2, "/admin")
rules = [x.rule for x in app.url_map.iter_rules() if "renvoy" in x.rule or "resend" in x.endpoint]
if rules:
    r = c2.post(rules[0], data={"csrf_token": tk2}, headers={"Referer": "http://evil.test/piege"}); loc = r.headers.get("Location", "")
    check(f"« Renvoyer l'email » + Referer pirate : redirection vers « {loc[:30] or '(aucune)'} »", "evil.test" not in loc)
    r = c2.post(rules[0], data={"csrf_token": tk2}, headers={"Referer": "http://localhost/admin/equipe"}); loc = r.headers.get("Location", "")
    check(f"Contrôle : Referer de ce site conservé (« {loc[:34]} »)", "equipe" in loc or r.status_code in (302, 303, 429))
print(f"\nRÉSULTAT : {sum(RES)}/{len(RES)} contrôles réussis, {len(RES) - sum(RES)} échec(s)")
sys.exit(0 if all(RES) else 1)
