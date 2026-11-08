"""
MenuFlash — QR codes de table SÉCURISÉS : l'adresse ne contient que le NOM de la table, et on ne
peut plus changer de table en la modifiant.

Branchement dans app.py, JUSTE AVANT « from hardening import init_hardening »
(le script ajouter_securite_qr.py le fait pour vous) :

    from qr_security import init_qr_security
    init_qr_security(app, owner_required=owner_required,
                     active_restaurant=active_restaurant, log_audit=log_audit)

Le problème
-----------
Le lien d'un QR de table était /m/chez-awa?qr=1. Le « 1 » est un numéro qui se devine : en le changeant
en « 2 », un client assis à la table 1 commandait pour la table 2. Le même défaut existait dans trois
endroits qui lisaient la table dans la requête sans la vérifier :
  - le menu public (adresse ?qr=…, et ?t=Table 2) ;
  - l'envoi d'une commande (champs cachés « qr_id » et « table_label » du formulaire) ;
  - « Appeler le serveur » / « L'addition ».
Ces champs peuvent aussi fausser la limite « commandes en attente par table ».

La solution : l'adresse AFFICHÉE ne contient que le nom
-------------------------------------------------------
1. Dans l'adresse du téléphone, la table n'est donnée QUE par son nom : /m/chez-awa?qr=terrasse.
   Aucun caractère tiré au hasard n'y apparaît jamais.
2. Ce nom ne sert qu'à l'affichage, il n'a AUCUN pouvoir : on peut le modifier comme on veut, rien ne
   change. Écrire un autre nom (qr=table-2), un numéro (qr=2) ou n'importe quoi ramène le client sur SA
   table, avec un message. Une adresse copiée sur le téléphone d'un autre (sans QR scanné) ouvre le menu
   général : le nom seul ne donne accès à aucune table.
3. Le droit de commander à une table vient du SCAN du QR code. L'image du QR contient, en plus du nom, un
   code secret propre à la table (6 caractères au hasard). À l'ouverture, le serveur le vérifie, mémorise
   la table dans un cookie signé (« mf_table », 6 h, impossible à falsifier), puis REDIRIGE aussitôt vers
   l'adresse « nom seul » : le code n'est ni affiché, ni gardé dans l'historique du navigateur.
4. Une commande ou un appel de serveur dont la table ne correspond pas à celle scannée est REFUSÉ, même si
   le formulaire est modifié (outils de développement). Un formulaire sans table = commande du lien
   général (à emporter) : inchangé. La limite « commandes en attente par table » utilise la table vérifiée.
5. Scanner VRAIMENT le QR d'une autre table permet de changer de table (déménagement).
6. ANTI-DEVINETTE : 15 tentatives invalides par 10 minutes et par adresse IP, puis blocage de 10 minutes ;
   tentatives tracées (journal de sécurité, 3 lignes par IP au plus).
7. BOUTON « NOUVEAU CODE » (page Codes QR) : si un QR a été photographié ou partagé, le restaurateur génère
   un nouveau code ; l'ancien QR cesse de fonctionner aussitôt (y compris pour les clients déjà connectés) ;
   il faut réimprimer l'autocollant de cette table.

Pourquoi le code existe malgré tout : si le QR ne contenait que le nom, quelqu'un pourrait taper le nom d'une
autre table et commander à sa place. Le secret vit donc UNIQUEMENT dans l'image du QR (et dans le bouton
« Copier » du restaurateur, qui donne le contenu du QR) ; les clients ne le voient jamais.

Convention des liens : url_for('public_menu', qr=<numéro>) donne le lien COMPLET d'un QR (avec code) : c'est
ce qu'utilisent les pages du restaurateur et les PDF/PNG. Pour un lien destiné au CLIENT (retour au menu,
« Ajouter d'autres plats »), on passe le NOM : qr=qr_slug(<numéro>).

Limite assumée : personne ne peut empêcher quelqu'un d'ASSIS à une table de scanner le QR de la table voisine,
ni de le photographier. Le code secret arrête ceux qui devinent des adresses ; en cas d'abus, « Nouveau code ».

Nouvelle table « qr_access » (qr_id, restaurant_id, code), créée au démarrage, incluse dans les sauvegardes,
supprimée avec le restaurant. Aucune dépendance nouvelle.
"""
import logging
import re
import secrets
import time
import unicodedata
from collections import deque
from datetime import datetime

import sqlalchemy as sa
from flask import Response, flash, g, jsonify, redirect, request, url_for
from flask_login import current_user
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import event
from werkzeug.datastructures import ImmutableMultiDict

from models import QRCode, Restaurant, db

log = logging.getLogger("menuflash.qr_security")

ALPHABET = "23456789acdefghjkmnprtuvwxy"          # sans 0 1 i l o q b s z : pas de confusion
CODE_LENGTH = 6
SLUG_MAX = 16                                      # partie « nom » du lien contenu dans le QR (QR peu dense)
DISPLAY_MAX = 40                                   # nom affiché dans l'adresse du téléphone
CODE_RE = re.compile(r"^[a-z0-9]{%d}$" % CODE_LENGTH)

COOKIE = "mf_table"
BIND_TTL = 6 * 3600
BIND_MAX_RESTAURANTS = 8

INVALID_LIMIT = 15                                 # codes invalides tolérés…
INVALID_WINDOW = 600                               # …par tranche de 10 minutes et par adresse IP
AUDIT_PER_WINDOW = 3                               # lignes de journal par IP et par tranche

MSG_BOUND = ("Code QR non reconnu : vous restez à « {label} ». Pour changer de table, scannez le QR code "
             "de la nouvelle table. / Unrecognized QR code: you stay at “{label}”. To change table, "
             "scan the new table's QR code.")
MSG_FREE = ("Pour commander à table, scannez le QR code de votre table (les cookies doivent être autorisés). / "
            "To order at your table, scan your table's QR code (cookies must be enabled).")
MSG_MISMATCH = ("Cette demande ne correspond pas à votre table. Scannez le QR code de votre table, puis "
                "réessayez. / This request does not match your table. Scan your table's QR code and try again.")
MSG_TOO_MANY = ("Trop de codes invalides. Réessayez dans quelques minutes. / "
                "Too many invalid codes. Please try again in a few minutes.")

_state = {"ready": False, "serializer": None, "secure": False}


class QRAccess(db.Model):
    __tablename__ = "qr_access"
    qr_id = db.Column(db.Integer, primary_key=True)          # volontairement sans clé étrangère
    restaurant_id = db.Column(db.Integer, nullable=False, index=True)
    code = db.Column(db.String(12), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    rotated_at = db.Column(db.DateTime)
    __table_args__ = (db.UniqueConstraint("restaurant_id", "code", name="uq_qr_access_restaurant_code"),)


# ==========================================================================
# Fonctions pures (testées)
# ==========================================================================
def new_code():
    return "".join(secrets.choice(ALPHABET) for _ in range(CODE_LENGTH))


def slugify_label(label, limit=SLUG_MAX):
    """« Terrasse 2, côté jardin » -> « terrasse-2-cote » (lisible, sans accent, `limit` caractères au plus)."""
    text = unicodedata.normalize("NFKD", str(label or "")).encode("ascii", "ignore").decode("ascii").lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")[:limit].strip("-")
    return text or "table"


def display_slug(label):
    """Nom de la table tel qu'il apparaît dans l'adresse du téléphone (jamais de code)."""
    return slugify_label(label, DISPLAY_MAX)


def make_proof(label, code):
    return f"{slugify_label(label)}-{code}"


def parse_proof(value):
    """Code secret contenu dans la valeur du lien, ou None. Seul le code compte, jamais le nom."""
    value = str(value or "").strip().lower()[:80]
    if not value:
        return None
    code = value.rsplit("-", 1)[-1]
    return code if CODE_RE.match(code) else None


class SlidingWindow:
    """Compteur d'événements récents par clé (adresse IP). En mémoire : un seul processus, comme le
    reste de l'application (temps réel, limiteur)."""

    def __init__(self, seconds, max_keys=5000):
        self.seconds, self.max_keys, self.hits = seconds, max_keys, {}

    def _trim(self, key, now):
        q = self.hits.get(key)
        while q and q[0] <= now - self.seconds:
            q.popleft()
        return q

    def hit(self, key, now=None):
        now = time.monotonic() if now is None else now
        if len(self.hits) > self.max_keys:                       # évite une croissance sans fin
            for k in [k for k in self.hits if not self._trim(k, now)]:
                del self.hits[k]
        q = self._trim(key, now)
        if q is None:
            q = self.hits[key] = deque()
        q.append(now)
        return len(q)

    def count(self, key, now=None):
        q = self._trim(key, time.monotonic() if now is None else now)
        return len(q) if q else 0


_invalid_hits = SlidingWindow(INVALID_WINDOW)


def claim_matches(claim_id, claim_label, bound_id, bound_label):
    """La table annoncée par la page correspond-elle à la table scannée ? Les deux annonces présentes
    doivent concorder ; une annonce absente est ignorée ; aucune annonce = lien général (traité ailleurs)."""
    if bound_id is None:
        return False
    if claim_id is not None and claim_id != bound_id:
        return False
    if claim_label and claim_label != bound_label:
        return False
    return True


# ==========================================================================
# Accès aux données
# ==========================================================================
def _restaurant_by_slug(slug):
    if not slug:
        return None
    cache = g.__dict__.setdefault("_mf_qr_restaurants", {})
    if slug not in cache:
        cache[slug] = Restaurant.query.filter_by(slug=slug, is_active=True).first()
    return cache[slug]


def _unique_code(connection=None, restaurant_id=None, taken=()):
    for _ in range(20):
        code = new_code()
        if code in taken:
            continue
        if connection is not None:
            tbl = QRAccess.__table__
            hit = connection.execute(sa.select(tbl.c.qr_id).where(
                tbl.c.restaurant_id == restaurant_id, tbl.c.code == code)).first()
        else:
            hit = QRAccess.query.filter_by(restaurant_id=restaurant_id, code=code).first()
        if not hit:
            return code
    raise RuntimeError("Impossible de générer un code QR unique")


def access_for(qr):
    """Ligne d'accès d'un QR (créée au besoin : filet de sécurité, les événements la créent d'habitude)."""
    row = db.session.get(QRAccess, qr.id)
    if row is None:
        row = QRAccess(qr_id=qr.id, restaurant_id=qr.restaurant_id, code=_unique_code(None, qr.restaurant_id))
        db.session.add(row)
        db.session.commit()
    return row


def proof_for_qr(qr_id, restaurant_id=None):
    """Valeur « nom-code » du lien d'un QR de table, ou None (QR inconnu / d'un autre restaurant)."""
    cache = g.__dict__.setdefault("_mf_qr_proofs", {}) if g else {}
    key = (qr_id, restaurant_id)
    if key in cache:
        return cache[key]
    qr = db.session.get(QRCode, qr_id)
    proof = None
    if qr is not None and (restaurant_id is None or qr.restaurant_id == restaurant_id):
        try:
            proof = make_proof(qr.label, access_for(qr).code)
        except Exception:
            db.session.rollback()
            log.exception("Code du QR %s impossible à lire", qr_id)
    cache[key] = proof
    return proof


def qr_name_for(qr_id):
    """Nom (sans code) d'un QR de table pour un lien destiné au CLIENT, ou None si le QR n'existe plus.
    Disponible dans les gabarits sous le nom « qr_slug »."""
    try:
        qr = db.session.get(QRCode, int(qr_id)) if qr_id else None
    except (TypeError, ValueError):
        return None
    return display_slug(qr.label) if qr is not None else None


def verify_proof(restaurant, raw):
    """(QRCode, code) si la valeur du lien désigne une table de CE restaurant, sinon None."""
    code = parse_proof(raw)
    if code is None:
        return None
    row = QRAccess.query.filter_by(restaurant_id=restaurant.id, code=code).first()
    if row is None:
        return None
    qr = db.session.get(QRCode, row.qr_id)
    if qr is None or qr.restaurant_id != restaurant.id:
        return None
    return qr, row.code


# ---------------------------------------------------------------- table mémorisée au scan (cookie signé)
def _read_bindings():
    cached = g.__dict__.get("_mf_bindings")
    if cached is not None:
        return cached
    out = {}
    raw = request.cookies.get(COOKIE, "")
    if raw and _state["serializer"] is not None:
        try:
            data = _state["serializer"].loads(raw, max_age=BIND_TTL)
            for rid, entry in (data or {}).items():
                if str(rid).isdigit() and isinstance(entry, list) and len(entry) == 2 \
                        and isinstance(entry[0], int) and isinstance(entry[1], str):
                    out[str(rid)] = entry
        except (BadSignature, SignatureExpired, TypeError, ValueError):
            out = {}
    g._mf_bindings = out
    return out


def bound_qr(restaurant_id):
    """QR scanné par cet appareil pour ce restaurant, s'il existe encore ET si son code n'a pas changé."""
    entry = _read_bindings().get(str(restaurant_id))
    if not entry:
        return None
    qr = db.session.get(QRCode, entry[0])
    row = db.session.get(QRAccess, entry[0]) if qr is not None else None
    if qr is None or row is None or qr.restaurant_id != restaurant_id or row.code != entry[1]:
        return None
    return qr


# ==========================================================================
# Branchement
# ==========================================================================
def init_qr_security(app, *, owner_required, active_restaurant, log_audit):
    if app.extensions.get("mf_qr_security"):
        return                                                   # déjà branché
    app.extensions["mf_qr_security"] = True
    _state["serializer"] = URLSafeTimedSerializer(app.config["SECRET_KEY"], salt="mf-table-bind")
    _state["secure"] = bool(app.config.get("SESSION_COOKIE_SECURE", False))

    # ------------------------------------------------------------------ table + codes existants
    with app.app_context():
        QRAccess.__table__.create(db.engine, checkfirst=True)
        _state["ready"] = True
        try:
            # Base toute neuve (premier démarrage) : « qr_codes » n'existe pas encore, il n'y a rien à rattraper.
            has_qr_table = sa.inspect(db.engine).has_table(QRCode.__tablename__)
            missing = [] if not has_qr_table else (db.session.query(QRCode).outerjoin(QRAccess, QRAccess.qr_id == QRCode.id)
                       .filter(QRAccess.qr_id.is_(None)).all())
            taken = {}
            for qr in missing:
                bucket = taken.setdefault(qr.restaurant_id, set())
                code = _unique_code(None, qr.restaurant_id, bucket)
                bucket.add(code)
                db.session.add(QRAccess(qr_id=qr.id, restaurant_id=qr.restaurant_id, code=code))
            if missing:
                db.session.commit()
                log.warning("Codes secrets créés pour %s QR de table existants.", len(missing))
        except Exception:
            db.session.rollback()
            log.exception("Création des codes des QR existants impossible")

    @event.listens_for(QRCode, "after_insert")
    def _code_on_create(mapper, connection, target):
        if not _state["ready"]:
            return
        code = _unique_code(connection, target.restaurant_id)
        connection.execute(QRAccess.__table__.insert().values(
            qr_id=target.id, restaurant_id=target.restaurant_id, code=code, created_at=datetime.utcnow()))

    @event.listens_for(QRCode, "after_delete")
    def _code_on_delete(mapper, connection, target):
        if _state["ready"]:
            connection.execute(QRAccess.__table__.delete().where(QRAccess.__table__.c.qr_id == target.id))

    # ------------------------------------------------------------------ liens et PDF avec le code
    @app.url_defaults
    def _qr_links_carry_the_code(endpoint, values):
        """url_for('public_menu', qr=<numéro ENTIER>) devient le lien COMPLET du QR (nom-code) : c'est ce que
        veulent le bouton « Copier » et les PDF/PNG du restaurateur, sans autre modification.
        Les liens destinés au CLIENT passent le NOM (qr=qr_slug(<numéro>)), toujours une CHAÎNE, jamais
        converti : un nom de table fait de chiffres (« 1 », « 2 ») resterait sinon pris pour un numéro et
        donnerait le lien (et le code secret) d'une AUTRE table. Seuls les entiers venant du serveur
        (base de données) sont convertis, jamais une saisie du client."""
        if endpoint != "public_menu" or "qr" not in values:
            return
        value = values["qr"]
        if isinstance(value, bool) or value is None:
            values.pop("qr", None)
            return
        if isinstance(value, int):
            restaurant = _restaurant_by_slug(values.get("slug") or (request.view_args or {}).get("slug")) \
                if g else None
            proof = proof_for_qr(int(value), restaurant.id) if restaurant is not None else None
            if proof:
                values["qr"] = proof
            else:
                values.pop("qr", None)                        # jamais de lien à numéro : mieux vaut le lien général

    view = app.view_functions.get("table_qrcode_png")
    scope = getattr(view, "__globals__", None)
    if scope is not None and "_build_qr_target_url" in scope:
        def _secure_target_url(restaurant, qr=None, table_label=None):
            """Adresse encodée dans un QR : la table n'est plus donnée par son numéro ni par son nom seul."""
            base = url_for("public_menu", slug=restaurant.slug, _external=True)
            if qr is not None:
                proof = proof_for_qr(qr.id, restaurant.id)
                if proof:
                    return f"{base}?qr={proof}"
            return base
        scope["_build_qr_target_url"] = _secure_target_url
    else:
        log.error("qr_security : _build_qr_target_url introuvable : les PNG/PDF d'origine garderaient l'ancien lien.")

    # ------------------------------------------------------------------ audit (plafonné)
    def audit(action, restaurant, details):
        ip = request.remote_addr or "?"
        if _invalid_hits.count(ip) > AUDIT_PER_WINDOW + INVALID_LIMIT:      # tempête : on ne remplit pas le journal
            return
        try:
            log_audit(action, success=False, restaurant_id=restaurant.id, user_email=None,
                      details=f"{details} ip={ip}"[:300])
        except Exception:
            log.exception("Trace de sécurité impossible")

    def clean(raw):
        return re.sub(r"[^a-z0-9\-]", "", str(raw or "").lower())[:30]

    # ------------------------------------------------------------------ garde des 3 adresses sensibles
    def refuse(message, restaurant, status=403):
        if request.headers.get("X-Requested-With") == "fetch" or request.is_json \
                or request.endpoint == "service_call":
            return jsonify({"ok": False, "error": message, "table_mismatch": True}), status
        flash(message, "error")
        return redirect(url_for("public_menu", slug=restaurant.slug))

    def set_request_table(args=None, form=None):
        """Remplace ce que la page prétend (adresse, formulaire) par ce que le serveur a vérifié."""
        if args is not None:
            request.args = ImmutableMultiDict(args)
        if form is not None:
            request.form = ImmutableMultiDict(form)
        request.__dict__.pop("values", None)                     # « request.values » est recalculé à la demande

    def guard_menu(restaurant):
        slug = restaurant.slug
        others = [(k, v) for k, v in request.args.items(multi=True) if k not in ("qr", "t")]
        params = dict(others)
        params.pop("slug", None)
        raw = (request.args.get("qr") or "").strip()
        if not raw:
            set_request_table(args=others)                       # « ?t=Table 2 » ne désigne plus aucune table
            return None

        # 1) Le QR a été SCANNÉ : le code secret est valide.
        found = verify_proof(restaurant, raw)
        if found is not None:
            qr, code = found
            g._mf_bind_new = (restaurant.id, qr.id, code)
            shown = display_slug(qr.label)
            if display_slug(raw) == shown:                        # déjà l'adresse « nom seul » (cas limite) : pas de boucle
                set_request_table(args=others + [("qr", str(qr.id))])
                return None
            resp = redirect(url_for("public_menu", slug=slug, **dict(params, qr=shown)))
            resp.headers["Cache-Control"] = "no-store"            # le secret ne reste ni dans l'adresse, ni en cache
            return resp

        # 2) Pas de code valide : seul le NOM est donné. Il ne sert qu'à l'affichage.
        bound = bound_qr(restaurant.id)
        if bound is not None and display_slug(raw) == display_slug(bound.label):
            g._mf_bind_refresh = True                             # rechargement de SA page : on reste, délai prolongé
            set_request_table(args=others + [("qr", str(bound.id))])
            return None

        # 3) Adresse modifiée (autre nom, numéro, texte…) : on ne change PAS de table.
        ip = request.remote_addr or "?"
        n = _invalid_hits.hit(ip)
        if n > INVALID_LIMIT:
            return Response(MSG_TOO_MANY, 429, {"Retry-After": str(INVALID_WINDOW),
                                                "Content-Type": "text/plain; charset=utf-8"})
        if n <= AUDIT_PER_WINDOW:
            audit("qr_code_invalid", restaurant, f"qr={clean(raw)!r}")
        if bound is not None:
            flash(MSG_BOUND.format(label=bound.label), "error")
            return redirect(url_for("public_menu", slug=slug, **dict(params, qr=display_slug(bound.label))))
        flash(MSG_FREE, "error")
        return redirect(url_for("public_menu", slug=slug, **params))

    def guard_table_claim(restaurant, claim_id, claim_label):
        """Commande / appel de serveur : la table annoncée doit être celle scannée par cet appareil."""
        if claim_id is None and not claim_label:
            return "general"
        bound = bound_qr(restaurant.id)
        if bound is None or not claim_matches(claim_id, claim_label, bound.id, bound.label):
            audit("qr_table_mismatch", restaurant,
                  f"annoncé id={claim_id} nom={clean(claim_label)!r} ; scanné {'id=%s' % bound.id if bound else 'aucun'}")
            return "refused"
        return bound

    def guard_order(restaurant):
        form = request.form
        claim_id = form.get("qr_id", type=int)
        claim_label = (form.get("table_label") or "").strip()
        cleaned = [(k, v) for k, v in form.items(multi=True) if k not in ("qr_id", "table_label")]
        result = guard_table_claim(restaurant, claim_id, claim_label)
        if result == "general":
            set_request_table(form=cleaned)                      # lien général : aucune table, cookie ignoré
            return None
        if result == "refused":
            return refuse(MSG_MISMATCH, restaurant)
        set_request_table(form=cleaned + [("qr_id", str(result.id)), ("table_label", result.label)])
        g._mf_bind_refresh = True
        return None

    def guard_call(restaurant):
        args, form = request.args, request.form
        claim_id = (form.get("qr", type=int) or form.get("qr_id", type=int)
                    or args.get("qr", type=int) or args.get("qr_id", type=int))
        claim_label = (form.get("t") or form.get("table_label") or args.get("t") or args.get("table_label") or "").strip()
        drop = ("qr", "qr_id", "t", "table_label")
        clean_args = [(k, v) for k, v in args.items(multi=True) if k not in drop]
        clean_form = [(k, v) for k, v in form.items(multi=True) if k not in drop]
        result = guard_table_claim(restaurant, claim_id, claim_label)
        if result == "general":
            set_request_table(args=clean_args, form=clean_form)   # le code d'origine répond « Table inconnue »
            return None
        if result == "refused":
            return refuse(MSG_MISMATCH, restaurant)
        set_request_table(args=clean_args, form=clean_form + [("qr", str(result.id))])
        g._mf_bind_refresh = True
        return None

    def _guard():
        endpoint = request.endpoint
        if endpoint not in ("public_menu", "place_order", "service_call"):
            return None
        method = request.method
        if (endpoint == "public_menu") != (method in ("GET", "HEAD")):
            return None
        restaurant = _restaurant_by_slug((request.view_args or {}).get("slug"))
        if restaurant is None:
            return None                                          # la route d'origine répond 404
        try:
            if endpoint == "public_menu":
                return guard_menu(restaurant)
            if endpoint == "place_order":
                return guard_order(restaurant)
            return guard_call(restaurant)
        except Exception:
            db.session.rollback()
            log.exception("Vérification de la table impossible (%s)", endpoint)
            return refuse(MSG_MISMATCH, restaurant, 400) if endpoint != "public_menu" else None

    # Avant TOUS les autres gardes (limite par table, anti-abus, à emporter…) : ils lisent ensuite la table vérifiée.
    app.before_request_funcs.setdefault(None, []).insert(0, _guard)

    @app.after_request
    def _remember_table(response):
        new = g.__dict__.get("_mf_bind_new")
        refresh = g.__dict__.get("_mf_bind_refresh") and response.status_code < 400
        if new is None and not refresh:
            return response
        try:
            bindings = dict(_read_bindings())
            if new is not None:
                rid, qid, code = new
                bindings.pop(str(rid), None)
                bindings[str(rid)] = [qid, code]
            while len(bindings) > BIND_MAX_RESTAURANTS:
                bindings.pop(next(iter(bindings)))
            response.set_cookie(COOKIE, _state["serializer"].dumps(bindings), max_age=BIND_TTL,
                                httponly=True, samesite="Lax", secure=_state["secure"])
        except Exception:
            log.exception("Mémorisation de la table impossible")
        return response

    # ------------------------------------------------------------------ « Nouveau code » (restaurateur)
    def admin_qrcode_regenerate(qr_id):
        restaurant = active_restaurant()
        qr = QRCode.query.filter_by(id=qr_id, restaurant_id=restaurant.id).first_or_404()
        row = access_for(qr)
        row.code = _unique_code(None, restaurant.id, {row.code})
        row.rotated_at = datetime.utcnow()
        db.session.commit()
        try:
            log_audit("qr_code_regenerated", success=True, restaurant_id=restaurant.id,
                      user_email=current_user.email, details=f"« {qr.label} » : nouveau code")
        except Exception:
            log.exception("Trace du nouveau code impossible")
        flash(f"Nouveau code créé pour « {qr.label} ». L'ancien autocollant ne fonctionne plus : "
              f"réimprimez-le (boutons « QR petit » ou « Planche »).", "success")
        return redirect(url_for("admin_qrcodes"))

    app.add_url_rule("/admin/qrcodes/<int:qr_id>/regenerer", "admin_qrcode_regenerate",
                     owner_required(admin_qrcode_regenerate), methods=["POST"])
    app.jinja_env.globals["mf_qr_security"] = True               # les gabarits affichent le bouton seulement si actif
    app.jinja_env.globals["qr_slug"] = qr_name_for               # lien client : url_for('public_menu', qr=qr_slug(<numéro>))
