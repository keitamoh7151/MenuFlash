"""
verifier_idor.py — Test d'attaque IDOR / BOLA : deux restaurants (A = Chez Awa, B = Resto B), chacun avec un gérant,
un cuisinier, des commandes, des plats, des QR codes, une équipe. On essaie, depuis chaque rôle,
d'atteindre ou de modifier les objets de l'autre. Chaque attaque est jugée sur DEUX critères :
  1) la réponse ne révèle aucune donnée de la victime ;
  2) la base de données de la victime n'a pas changé.
"""
import os, sys, re, io, json, warnings, logging, contextlib, tempfile, pathlib, shutil, atexit
warnings.filterwarnings("ignore"); logging.disable(logging.CRITICAL)
try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass
# Base de données TEMPORAIRE : ton menuflash.db et ta production ne sont jamais touchés.
_TMP = tempfile.mkdtemp(prefix="menuflash_idor_")
atexit.register(lambda: shutil.rmtree(_TMP, ignore_errors=True))
for _k in ("TURNSTILE_SITE_KEY", "TURNSTILE_SECRET_KEY", "RATELIMIT_STORAGE_URI", "RENDER", "BACKUP_ENCRYPTION_KEY", "CSP_MODE"):
    os.environ.pop(_k, None)
os.environ["DATABASE_URL"] = "sqlite:///" + pathlib.Path(_TMP, "test_idor.db").as_posix()
os.environ["FLASK_SECRET_KEY"] = "x" * 64; os.environ["BACKUP_AUTO"] = "false"
with contextlib.redirect_stdout(io.StringIO()):
    import seed
import app as appmod
from app import app, limiter
from models import (db, Restaurant, User, MenuCategory, MenuItem, QRCode, Order, OrderItem,
                    RestaurantAccess, MenuItemSuggestion)
from flask_login import login_user
import order_refs
limiter.enabled = False
from datetime import datetime

SECRET_B = "PLAT-SECRET-RESTO-B"
NOTE_B = "NOTE-PRIVEE-CLIENT-B"
NOTE_A2 = "NOTE-PRIVEE-CLIENT-A2"
SECRET_A = "PLAT-SECRET-RESTO-A"

@app.route("/__t/<int:uid>")
def __t(uid):
    login_user(db.session.get(User, uid)); return "ok"

# ------------------------------------------------------------------ données
with app.app_context():
    A = Restaurant.query.filter_by(slug="chez-awa").first(); A.subscription_plan = "pro"
    B = Restaurant(name="Resto B", slug="resto-b", is_active=True, subscription_plan="pro",
                   subscription_active_until=datetime(2099, 1, 1)); db.session.add(B); db.session.commit()
    ownerA = User.query.filter_by(email="awa@example.com").first(); ownerA.set_password("MotDePasseTestA2026")
    staffA = User.query.filter_by(email="cuisine@example.com").first(); staffA.set_password("MotDePasseTestK2026"); db.session.commit()
    ownerB = User(restaurant_id=B.id, email="gerant-b@test.test", role="owner", email_verified=True); ownerB.set_password("MotDePasseB2026x")
    staffB = User(restaurant_id=B.id, email="cuisine-b@test.test", role="staff", email_verified=True); staffB.set_password("MotDePasseB2026y")
    db.session.add_all([ownerB, staffB]); db.session.commit()
    catB = MenuCategory(restaurant_id=B.id, name="Cat B", position=1); db.session.add(catB); db.session.commit()
    itemB = MenuItem(restaurant_id=B.id, category_id=catB.id, name=SECRET_B, price=1111, position=1, is_available=True)
    itemB2 = MenuItem(restaurant_id=B.id, category_id=catB.id, name="Autre plat B", price=2222, position=2, is_available=True)
    qrB = QRCode(restaurant_id=B.id, label="Table B1"); db.session.add_all([itemB, itemB2, qrB]); db.session.commit()
    catA = MenuCategory.query.filter_by(restaurant_id=A.id).first()
    itemA = MenuItem.query.filter_by(restaurant_id=A.id).first(); itemA.name = SECRET_A; db.session.commit()
    def mk(rid, label, note, qty_item):
        o = Order(restaurant_id=rid, table_label=label, status="nouvelle", notes=note, created_at=datetime.utcnow())
        db.session.add(o); db.session.commit()
        db.session.add(OrderItem(order_id=o.id, menu_item_id=qty_item.id, item_name=qty_item.name, item_price=qty_item.price, quantity=2))
        db.session.commit(); return o.id
    oB = mk(B.id, "Table B1", NOTE_B, itemB)
    oA1 = mk(A.id, "Table A1", "note A1", itemA)
    oA2 = mk(A.id, "Table A2", NOTE_A2, itemA)
    from service import ServiceCall
    callB = ServiceCall(restaurant_id=B.id, table_label="Table B1", kind="serveur"); db.session.add(callB); db.session.commit()
    IDS = dict(A=A.id, B=B.id, ownerA=ownerA.id, staffA=staffA.id, ownerB=ownerB.id, staffB=staffB.id, catB=catB.id, itemB=itemB.id,
               itemB2=itemB2.id, qrB=qrB.id, oB=oB, oA1=oA1, oA2=oA2, callB=callB.id, catA=catA.id, itemA=itemA.id)

RESULTS = []
def check(label, ok, detail=""):
    RESULTS.append((label, ok, detail)); print(("OK   " if ok else "FAIL ") + label + (f"  [{detail}]" if detail else ""))

def csrf(c, path="/admin"):
    h = c.get(path).get_data(as_text=True)
    m = re.search(r'name="csrf-token" content="([^"]+)"', h) or re.search(r'name="csrf_token" value="([^"]+)"', h)
    return m.group(1) if m else ""

def login_as(uid):
    c = app.test_client(); c.get(f"/__t/{uid}"); return c

def owned_cookie(c, order_ids):
    with app.test_request_context():
        val = order_refs._state["serializer"].dumps(list(order_ids))
    c.set_cookie(order_refs.OWNED_COOKIE, val)

def snapshot_B():
    with app.app_context():
        return dict(
            cat=db.session.get(MenuCategory, IDS["catB"]).name, catpause=db.session.get(MenuCategory, IDS["catB"]).is_paused,
            item=(db.session.get(MenuItem, IDS["itemB"]).name, db.session.get(MenuItem, IDS["itemB"]).price, db.session.get(MenuItem, IDS["itemB"]).is_available) if db.session.get(MenuItem, IDS["itemB"]) else None,
            qr=db.session.get(QRCode, IDS["qrB"]).label if db.session.get(QRCode, IDS["qrB"]) else None,
            staff=(db.session.get(User, IDS["staffB"]).is_super_admin, db.session.get(User, IDS["staffB"]).security_stamp) if db.session.get(User, IDS["staffB"]) else None,
            order=(db.session.get(Order, IDS["oB"]).status, db.session.get(Order, IDS["oB"]).notes, len(db.session.get(Order, IDS["oB"]).items)) if db.session.get(Order, IDS["oB"]) else None,
            call=db.session.get(ServiceCall, IDS["callB"]).status,
            suggestions=MenuItemSuggestion.query.filter_by(restaurant_id=IDS["B"]).count(),
            items_in_catB=MenuItem.query.filter_by(category_id=IDS["catB"]).count(),
        )
def snapshot_A():
    with app.app_context():
        a2 = db.session.get(Order, IDS["oA2"]); a1 = db.session.get(Order, IDS["oA1"])
        return dict(a2=(a2.status, a2.notes, len(a2.items)), a1=(a1.status, a1.notes, len(a1.items)), item=db.session.get(MenuItem, IDS["itemA"]).name)

def leaked(resp, *needles):
    body = resp.get_data(as_text=True)
    return [n for n in needles if n in body]

# =====================================================================================
print("\n=== 1. GÉRANT DU RESTAURANT A attaque les objets du RESTAURANT B (accès horizontal)")
before = snapshot_B()
cA = login_as(IDS["ownerA"]); tok = csrf(cA)
H = {"X-CSRFToken": tok}
attacks = [
 ("POST", f"/admin/categories/{IDS['catB']}/edit", {"name": "PIRATE"}),
 ("POST", f"/admin/categories/{IDS['catB']}/toggle-pause", {}),
 ("POST", f"/admin/items/{IDS['itemB']}/edit", {"name": "PIRATE", "price": "1"}),
 ("POST", f"/admin/items/{IDS['itemB']}/toggle", {}),
 ("POST", f"/admin/items/{IDS['itemB']}/delete", {}),
 ("POST", f"/admin/items/{IDS['itemB']}/composition", {"ingredients": "PIRATE"}),
 ("POST", f"/admin/qrcodes/{IDS['qrB']}/delete", {}),
 ("POST", f"/admin/qrcodes/{IDS['qrB']}/regenerer", {}),
 ("POST", f"/admin/equipe/{IDS['staffB']}/delete", {"confirm_password": "MotDePasseTestA2026"}),
 ("POST", f"/admin/equipe/{IDS['staffB']}/inviter", {"confirm_password": "MotDePasseTestA2026"}),
 ("POST", f"/admin/equipe/{IDS['staffB']}/desactiver", {"confirm_password": "MotDePasseTestA2026"}),
 ("POST", f"/admin/equipe/{IDS['staffB']}/reactiver", {"confirm_password": "MotDePasseTestA2026"}),
 ("POST", f"/admin/commandes/{IDS['oB']}/avancer", {}),
 ("POST", f"/admin/commandes/{IDS['oB']}/annuler", {}),
 ("POST", f"/admin/commandes/{IDS['oB']}/remettre-prete", {}),
 ("POST", f"/admin/appels/{IDS['callB']}/traiter", {}),
]
for method, path, data in attacks:
    data = dict(data, csrf_token=tok)
    r = cA.post(path, data=data, headers={"X-Requested-With": "fetch"})
    ok = r.status_code in (403, 404) or (r.status_code in (302, 303) and "/admin/login" not in r.headers.get("Location", "") and True)
    check(f"{method} {path.split('/admin')[1][:48]:<48} -> HTTP {r.status_code}", r.status_code != 200, "")
r = cA.get(f"/admin/commandes/{IDS['oB']}/ticket")
check(f"GET  ticket de la commande de B -> HTTP {r.status_code}, contenu de B absent", r.status_code in (403, 404) and not leaked(r, SECRET_B, NOTE_B))
# Réordonnancement : ids de B passés dans une requête de A
cA.post("/admin/categories/reorder", json={"order": [IDS["catB"]]}, headers=H)
cA.post("/admin/items/reorder", json={"order": [IDS["itemB"], IDS["itemB2"]]}, headers=H)
# Ajout d'un plat DANS la catégorie de B
r = cA.post("/admin/items/add", data={"csrf_token": tok, "category_id": IDS["catB"], "name": "INJECTE", "price": "5"})
# Suggestions croisées : le plat de A suggère un plat de B
r = cA.post(f"/admin/items/{IDS['itemA']}/edit", data={"csrf_token": tok, "name": SECRET_A, "price": "100", "suggestions": [IDS["itemB"], IDS["itemB2"]]})
with app.app_context():
    cross = MenuItemSuggestion.query.filter_by(menu_item_id=IDS["itemA"]).filter(MenuItemSuggestion.suggested_item_id.in_([IDS["itemB"], IDS["itemB2"]])).count()
check("Suggestion de vente : un plat de A ne peut pas pointer vers un plat de B", cross == 0, f"{cross} lien(s) croisé(s)")
# Bascule vers le restaurant de B
r = cA.post("/admin/restaurants/switch", data={"csrf_token": tok, "restaurant_id": IDS["B"]})
with cA.session_transaction() as s: sw = s.get("active_restaurant_id")
check("Bascule vers le restaurant de B refusée (pas d'accès accordé)", sw != IDS["B"], f"restaurant actif = {sw}")
# Après la tentative de bascule, les pages de A n'affichent rien de B
pg = cA.get("/admin"); check("Tableau de bord de A : aucune donnée de B", not leaked(pg, SECRET_B, "Resto B"))
for path in ("/admin/commandes/tableau.json", "/admin/commandes/historique.json?filtre=tout", "/admin/commandes/compteur.json", "/admin/historique"):
    pg = cA.get(path); check(f"GET  {path.split('?')[0]:<38} aucune donnée de B", not leaked(pg, SECRET_B, NOTE_B, "Table B1"), f"HTTP {pg.status_code}")
after = snapshot_B()
diff = {k: (before[k], after[k]) for k in before if before[k] != after[k]}
check("BASE DE DONNÉES de B inchangée après toutes les attaques du gérant A", not diff, str(diff) if diff else "identique")

# =====================================================================================
print("\n=== 2. CUISINIER DU RESTAURANT A (rôle limité) — accès horizontal ET vertical")
cK = login_as(IDS["staffA"]); tokK = csrf(cK, "/admin/commandes")
before = snapshot_B(); beforeA = snapshot_A()
for method, path, data in [
    ("POST", f"/admin/commandes/{IDS['oB']}/avancer", {}), ("POST", f"/admin/commandes/{IDS['oB']}/annuler", {}),
    ("POST", f"/admin/commandes/{IDS['oB']}/remettre-prete", {}), ("POST", f"/admin/appels/{IDS['callB']}/traiter", {}),
]:
    r = cK.post(path, data=dict(data, csrf_token=tokK), headers={"X-Requested-With": "fetch"})
    check(f"Cuisinier A -> {path.split('/admin')[1][:44]:<44} HTTP {r.status_code}", r.status_code != 200)
r = cK.get(f"/admin/commandes/{IDS['oB']}/ticket"); check("Cuisinier A -> ticket de la commande de B", r.status_code in (403, 404) and not leaked(r, SECRET_B, NOTE_B), f"HTTP {r.status_code}")
r = cK.get("/admin/commandes/tableau.json"); check("Cuisinier A -> écran cuisine : aucune commande de B", not leaked(r, SECRET_B, NOTE_B, "Table B1"))
# vertical : le cuisinier tente des actions réservées au gérant, sur SON restaurant
vertical = [
 (f"/admin/items/{IDS['itemA']}/delete", {}), (f"/admin/items/{IDS['itemA']}/edit", {"name": "HACK", "price": "1"}),
 ("/admin/categories/add", {"name": "HACK"}), (f"/admin/categories/{IDS['catA']}/edit", {"name": "HACK"}),
 ("/admin/qrcodes/add", {"label": "HACK"}), ("/admin/equipe/add", {"email": "x@y.zz", "password": "MotDePasse2026ab"}),
 (f"/admin/equipe/{IDS['ownerA']}/delete", {}), ("/admin/restaurants/new", {"name": "Hack"}),
]
for path, data in vertical:
    r = cK.post(path, data=dict(data, csrf_token=tokK), headers={"X-Requested-With": "fetch"})
    check(f"Cuisinier -> action de gérant {path.split('/admin')[1][:40]:<40} HTTP {r.status_code}", r.status_code in (302, 303, 403, 404) and r.status_code != 200)
afterA = snapshot_A()
with app.app_context():
    names = {i.name for i in MenuItem.query.filter_by(restaurant_id=IDS["A"]).all()}
    cat_hack = MenuCategory.query.filter_by(name="HACK").count(); qr_hack = QRCode.query.filter_by(label="HACK").count()
with app.app_context(): owner_still = db.session.get(User, IDS["ownerA"]) is not None
check("Le compte du gérant de A existe toujours (le cuisinier n'a pu supprimer personne)", owner_still)
check("Aucune modification de A par le cuisinier (plats, catégories, QR)", "HACK" not in names and cat_hack == 0 and qr_hack == 0 and afterA["item"] == SECRET_A)
check("BASE DE DONNÉES de B inchangée après les attaques du cuisinier A", before == snapshot_B())

# =====================================================================================
print("\n=== 3. VISITEUR ANONYME — routes publiques de commande du restaurant B")
anon = app.test_client()
pub = [
 ("GET", f"/m/resto-b/commande/{IDS['oB']}", None), ("GET", f"/m/resto-b/commande/{IDS['oB']}/etat.json", None),
 ("GET", f"/m/resto-b/commande/{IDS['oB']}/lignes.json", None),
 ("POST", f"/m/resto-b/commande/{IDS['oB']}/annuler-client", {}), ("POST", f"/m/resto-b/commande/{IDS['oB']}/ajouter", {}),
 ("POST", f"/m/resto-b/commande/{IDS['oB']}/avis", {"rating_1": "1"}), ("POST", f"/m/resto-b/commande/{IDS['oB']}/modifier", {"item_id": IDS["itemB"], "delta": "5"}),
 ("GET", f"/m/resto-a/commande/{IDS['oB']}", None),  # mauvais slug
 ("GET", f"/m/resto-b?ajouter={IDS['oB']}", None),
]
before = snapshot_B()
for method, path, data in pub:
    c = app.test_client(); t = csrf(c, "/m/resto-b")
    r = c.get(path, headers={"X-Requested-With": "fetch"}) if method == "GET" else c.post(path, data=dict(data, csrf_token=t), headers={"X-Requested-With": "fetch"})
    body_leak = leaked(r, SECRET_B, NOTE_B, "Table B1")
    check(f"Anonyme {method:<4} {path[:52]:<52} HTTP {r.status_code}", not body_leak and not (r.status_code == 200 and "commande" in path and "etat" in path), "FUITE: " + ",".join(body_leak) if body_leak else "")
# Énumération de masse
ids = ",".join(str(i) for i in range(1, 400))
r = anon.get(f"/m/resto-b/commandes/etats.json?ids={ids}")
j = r.get_json(silent=True) or {}
check(f"Énumération de masse : /commandes/etats.json?ids=1..399 ne renvoie AUCUNE commande ({len(j.get('orders', []))})", not j.get("orders") and not leaked(r, SECRET_B, NOTE_B), f"HTTP {r.status_code}")
# Existence : commande inexistante et commande d'un autre -> réponses indiscernables ?
def sig(path):
    c = app.test_client(); r = c.get(path, headers={"X-Requested-With": "fetch"})
    return (r.status_code, r.headers.get("Location"), r.get_data(as_text=True)[:200])
s1 = sig(f"/m/resto-b/commande/{IDS['oB']}/etat.json"); s2 = sig("/m/resto-b/commande/999999/etat.json")
check("Un numéro existant et un numéro inexistant donnent la même réponse (pas d'oracle d'existence)", s1 == s2, f"{s1[0]} vs {s2[0]}")
check("BASE DE DONNÉES de B inchangée après les attaques anonymes", before == snapshot_B())
r = anon.get("/commande-emporter/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.json"); check("Jeton de suivi à emporter deviné -> 404", r.status_code == 404)

# =====================================================================================
print("\n=== 4. CLIENT DU RESTAURANT A (propriétaire de sa commande a1) -> commande a2 d'un AUTRE client du MÊME restaurant")
c1 = app.test_client(); owned_cookie(c1, [IDS["oA1"]]); t1 = csrf(c1, "/m/chez-awa")
r = c1.get(f"/m/chez-awa/commande/{IDS['oA1']}", headers={"X-Requested-With": "fetch"})
check("Contrôle : le client voit bien SA commande a1", r.status_code == 200 and "Table A1" in r.get_data(as_text=True) or r.status_code == 200, f"HTTP {r.status_code}")
beforeA = snapshot_A()
for method, path, data in [
 ("GET", f"/m/chez-awa/commande/{IDS['oA2']}", None), ("GET", f"/m/chez-awa/commande/{IDS['oA2']}/etat.json", None),
 ("GET", f"/m/chez-awa/commande/{IDS['oA2']}/lignes.json", None),
 ("POST", f"/m/chez-awa/commande/{IDS['oA2']}/annuler-client", {}), ("POST", f"/m/chez-awa/commande/{IDS['oA2']}/ajouter", {}),
 ("POST", f"/m/chez-awa/commande/{IDS['oA2']}/avis", {}), ("POST", f"/m/chez-awa/commande/{IDS['oA2']}/modifier", {"item_id": IDS["itemA"], "delta": "3"}),
 ("GET", f"/m/chez-awa?ajouter={IDS['oA2']}", None),
]:
    r = c1.get(path, headers={"X-Requested-With": "fetch"}) if method == "GET" else c1.post(path, data=dict(data, csrf_token=t1), headers={"X-Requested-With": "fetch"})
    lk = leaked(r, NOTE_A2, "Table A2")
    check(f"Client a1 {method:<4} {path.split('chez-awa')[1][:44]:<44} HTTP {r.status_code}", not lk, ("FUITE: " + ",".join(lk)) if lk else "")
j = c1.get(f"/m/chez-awa/commandes/etats.json?ids={IDS['oA1']},{IDS['oA2']}").get_json(silent=True) or {}
got = [o["id"] for o in j.get("orders", [])]
check("« Mes commandes » : le client reçoit a1 mais JAMAIS a2", IDS["oA2"] not in got, f"commandes reçues : {got}")
check("BASE DE DONNÉES : la commande a2 n'a pas bougé", beforeA["a2"] == snapshot_A()["a2"])


# =====================================================================================
print("\n=== 5b. FONCTIONS RÉSERVÉES AU SUPER-ADMIN (gérant, cuisinier, anonyme)")
sa_paths = [f"/super-admin/restaurants/{IDS['B']}", f"/super-admin/restaurants/{IDS['B']}/export.json", "/super-admin/clients/export.csv",
            "/super-admin/sauvegardes", "/super-admin/journal-securite", "/super-admin/finances/export.csv", "/super-admin/administrateurs"]
for label, uid in (("Anonyme", None), ("Gérant de A", IDS["ownerA"]), ("Cuisinier de A", IDS["staffA"])):
    c = login_as(uid) if uid else app.test_client()
    bad = []
    for p in sa_paths:
        r = c.get(p)
        if r.status_code == 200 or leaked(r, "Resto B", SECRET_B): bad.append(f"{p} -> {r.status_code}")
    check(f"{label:<15} : aucune des {len(sa_paths)} pages super-admin n'est accessible", not bad, "; ".join(bad))
c = app.test_client()
bad = [p for p in ("/admin", "/admin/commandes/tableau.json", "/admin/equipe", "/admin/qrcodes") if c.get(p).status_code == 200]
check("Anonyme : aucune page /admin n'est accessible", not bad, ",".join(bad))

# =====================================================================================
print("\n=== 6. SECRET DES QR CODES DE TABLE (un visiteur anonyme peut-il commander pour une table sans l'avoir scannée ?)")
import qrcode
captured = []; _orig_add = qrcode.QRCode.add_data
def _spy(self, data, *a, **k):
    captured.append(data); return _orig_add(self, data, *a, **k)
qrcode.QRCode.add_data = _spy
ctl = app.test_client(); h = ctl.get("/m/resto-b").get_data(as_text=True)
r0 = ctl.post("/m/resto-b/commande", data={"csrf_token": re.search(r'name="csrf_token" value="([^"]+)"', h).group(1), f"qty_{IDS['itemB']}": "1",
                                              "table_label": "Table B1", "qr_id": str(IDS["qrB"])}, headers={"X-Requested-With": "fetch"})
check(f"Contrôle : commande pour une table SANS le secret -> refusée (HTTP {r0.status_code})", r0.status_code == 403)
thief = app.test_client()
rimg = thief.get(f"/m/resto-b/qrcodes/{IDS['qrB']}.png"); rpdf = thief.get(f"/m/resto-b/qrcodes/{IDS['qrB']}.pdf")
secret_url = captured[-1] if captured else None
check(f"Anonyme : l'image du QR d'une table ne doit pas être téléchargeable (PNG HTTP {rimg.status_code}, PDF HTTP {rpdf.status_code})",
      rimg.status_code in (403, 404) and rpdf.status_code in (403, 404) and not secret_url)
with app.app_context(): n_before = Order.query.filter_by(restaurant_id=IDS["B"]).count()
if secret_url:
    v = app.test_client(); page = v.get("/" + secret_url.split("://", 1)[1].split("/", 1)[1], follow_redirects=True).get_data(as_text=True)
    lab = re.search(r'name="table_label" value="([^"]*)"', page); qid = re.search(r'name="qr_id" value="([^"]*)"', page)
    tk = re.search(r'name="csrf_token" value="([^"]+)"', page)
    v.post("/m/resto-b/commande", data={"csrf_token": tk.group(1) if tk else "", f"qty_{IDS['itemB']}": "2",
           "table_label": lab.group(1) if lab else "", "qr_id": qid.group(1) if qid else ""}, headers={"X-Requested-With": "fetch"})
with app.app_context(): n_after = Order.query.filter_by(restaurant_id=IDS["B"]).count()
check(f"Anonyme : AUCUNE fausse commande pour la table de B n'a pu être créée ({n_before} -> {n_after})", n_after == n_before)
qrcode.QRCode.add_data = _orig_add
c = login_as(IDS["ownerB"]); rb = c.get(f"/m/resto-b/qrcodes/{IDS['qrB']}.png")
check(f"Contrôle : le gérant de B peut toujours télécharger le QR de SA table (HTTP {rb.status_code})", rb.status_code == 200)

# =====================================================================================
print("\n=== 7. ENUMÉRATION d'IDENTIFIANTS (informatif)")
ids_seq = sorted([IDS["oA1"], IDS["oA2"], IDS["oB"]])
print("  Les numéros de commande sont séquentiels sur toute la plateforme :", ids_seq, "(a1, a2 et b se suivent d'un restaurant à l'autre).")
from media_store import StoredImage
with app.app_context():
    img = StoredImage(restaurant_id=IDS["B"], kind="photo", mimetype="image/webp", data=b"RIFFxxxxWEBP"); db.session.add(img); db.session.commit(); iid = img.id
r = app.test_client().get(f"/media/{iid}")
print(f"  /media/<id> : HTTP {r.status_code} pour une photo de B, sans compte. Public par conception (photos de menus et logos), numéros devinables.")
fails = [r for r in RESULTS if not r[1]]
print(f"\nRÉSULTAT : {len(RESULTS) - len(fails)}/{len(RESULTS)} contrôles réussis, {len(fails)} échec(s)")
for l, _, d in fails: print("  ÉCHEC :", l, d)
sys.exit(1 if fails else 0)
