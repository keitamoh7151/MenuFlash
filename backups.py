"""
MenuFlash Mini — Sauvegardes automatiques, hors site et restaurations.

Branchement dans app.py, JUSTE AVANT l'appel à init_super_admin(...) :

    from backups import init_backups
    init_backups(app, log_audit=log_audit, super_admin_required=super_admin_required)

(L'ordre compte : la sauvegarde planifiée doit pouvoir passer même quand le
mode maintenance est actif.)

Principe
--------
Une sauvegarde est une copie COMPLÈTE de toutes les tables de l'application
(restaurants, comptes, menus, commandes, paiements, images, paramètres,
journaux…), au format JSON compressé (gzip). Ce format est indépendant du
moteur : une sauvegarde faite sur PostgreSQL (Render) peut être restaurée sur
SQLite (ton PC) et inversement.

Trois niveaux de protection, du plus rapide au plus sûr :

1. COPIE LOCALE (dans la base, table `backups`) : protège contre les erreurs
   de manipulation (suppression par erreur, mauvaise modification). Les
   BACKUP_KEEP_LOCAL dernières sont conservées.
2. COPIE HORS SITE, CHIFFRÉE (stockage compatible S3 : Cloudflare R2,
   Backblaze B2, AWS S3…) : protège contre la perte de la base elle-même
   (expiration de la base gratuite Render, suppression, panne). Les
   BACKUP_KEEP_OFFSITE dernières sont conservées.
3. TÉLÉCHARGEMENT sur ton PC, depuis le panel : une copie que tu gardes
   toi-même.

Déclenchement automatique :
- dans l'application : une sauvegarde est lancée en arrière-plan dès que la
  dernière date de plus de BACKUP_INTERVAL_HOURS heures ;
- depuis l'extérieur : GitHub Actions appelle chaque nuit
  GET /cron/sauvegarde avec l'en-tête X-Backup-Token (voir
  .github/workflows/sauvegarde-quotidienne.yml). Indispensable sur le plan
  gratuit de Render, où le service s'endort sans visite.

Restauration (panel super-admin > Système > Sauvegardes) :
- depuis une copie locale, une copie hors site ou un fichier téléversé ;
- confirmation par le mot « RESTAURER » ET le mot de passe du super-admin ;
- une sauvegarde de sécurité est faite automatiquement juste avant ;
- tout se fait dans une seule transaction : en cas d'erreur, rien n'est
  modifié ;
- l'application est bloquée pour les autres utilisateurs pendant l'opération ;
- un « test » (vérification sans rien écrire) est disponible pour chaque
  sauvegarde : une sauvegarde jamais testée n'est pas une sauvegarde.

Variables d'environnement (Render > Environment)
------------------------------------------------
BACKUP_ENCRYPTION_KEY   clé de chiffrement Fernet (obligatoire pour le hors
                        site). Générer avec :
                        python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
                        À CONSERVER PRÉCIEUSEMENT : sans elle, les copies
                        hors site sont illisibles.
BACKUP_S3_ENDPOINT      ex : https://<compte>.r2.cloudflarestorage.com
BACKUP_S3_BUCKET        nom du bucket
BACKUP_S3_ACCESS_KEY    clé d'accès
BACKUP_S3_SECRET_KEY    clé secrète
BACKUP_S3_REGION        défaut : auto
BACKUP_S3_PREFIX        défaut : menuflash/sauvegardes/
BACKUP_CRON_TOKEN       jeton secret (32+ caractères) pour /cron/sauvegarde
BACKUP_INTERVAL_HOURS   défaut : 24
BACKUP_KEEP_LOCAL       défaut : 7
BACKUP_KEEP_OFFSITE     défaut : 30
BACKUP_AUTO             "false" pour désactiver le déclenchement interne
"""
import base64
import gzip
import hashlib
import hmac
import json
import logging
import os
import threading
import time
from datetime import datetime, date, timedelta

import sqlalchemy as sa
from sqlalchemy.orm import deferred
from flask import (render_template, redirect, url_for, request, flash, abort,
                   Response, jsonify)
from flask_login import current_user, logout_user

from models import db

logger = logging.getLogger("menuflash.backups")

FORMAT_NAME = "menuflash-backup"
FORMAT_VERSION = 1
BACKUP_TABLE = "backups"
CRON_PATH = "/cron/sauvegarde"
UPLOAD_PATH = "/super-admin/sauvegardes/restaurer-fichier"
MAX_UPLOAD_MB = 100
GZIP_MAGIC = b"\x1f\x8b"
FERNET_MAGIC = b"gAAAAA"

KIND_LABELS = {
    "auto": "Automatique",
    "planifiee": "Planifiée (GitHub)",
    "manuelle": "Manuelle",
    "avant_restauration": "Sécurité (avant restauration)",
}
OFFSITE_LABELS = {
    "ok": ("Envoyée", "mf-sa-badge-ok"),
    "erreur": ("Échec", "mf-sa-badge-danger"),
    "cle_manquante": ("Clé de chiffrement manquante", "mf-sa-badge-warning"),
    "non_configure": ("Non configuré", "mf-sa-badge-muted"),
    "en_cours": ("En cours", "mf-sa-badge-info"),
}


def _env_int(name, default, lo=1, hi=100000):
    try:
        return max(lo, min(int(os.environ.get(name, default)), hi))
    except (TypeError, ValueError):
        return default


def interval_hours():
    return _env_int("BACKUP_INTERVAL_HOURS", 24, 1, 24 * 30)


def keep_local():
    return _env_int("BACKUP_KEEP_LOCAL", 7, 1, 60)


def keep_offsite():
    return _env_int("BACKUP_KEEP_OFFSITE", 30, 1, 3650)


class BackupError(Exception):
    """Erreur prévue, avec un message lisible par l'utilisateur."""


# ==========================================================================
# Modèle
# ==========================================================================
class Backup(db.Model):
    __tablename__ = BACKUP_TABLE
    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True, nullable=False)
    kind = db.Column(db.String(20), nullable=False, default="auto")
    created_by = db.Column(db.String(120))
    size_bytes = db.Column(db.Integer, nullable=False, default=0)
    tables_count = db.Column(db.Integer, nullable=False, default=0)
    rows_count = db.Column(db.Integer, nullable=False, default=0)
    checksum = db.Column(db.String(64), nullable=False)
    # deferred : la liste des sauvegardes ne charge pas les données elles-mêmes.
    data = deferred(db.Column(db.LargeBinary, nullable=False))
    offsite_status = db.Column(db.String(20), nullable=False, default="non_configure")
    offsite_key = db.Column(db.String(300))
    offsite_error = db.Column(db.String(300))


# ==========================================================================
# Chiffrement
# ==========================================================================
def encryption_configured():
    return bool(os.environ.get("BACKUP_ENCRYPTION_KEY", "").strip())


def _fernet():
    from cryptography.fernet import Fernet
    key = os.environ.get("BACKUP_ENCRYPTION_KEY", "").strip()
    if not key:
        raise BackupError("La clé de chiffrement BACKUP_ENCRYPTION_KEY n'est pas définie.")
    try:
        return Fernet(key.encode())
    except (ValueError, TypeError):
        raise BackupError("La clé BACKUP_ENCRYPTION_KEY est invalide (elle doit être générée avec Fernet).")


def encrypt(blob):
    return _fernet().encrypt(blob)


def to_gzip_bytes(raw):
    """Accepte une sauvegarde chiffrée ou non, et renvoie le gzip en clair."""
    if raw.startswith(GZIP_MAGIC):
        return raw
    if raw.startswith(FERNET_MAGIC):
        from cryptography.fernet import InvalidToken
        try:
            return _fernet().decrypt(raw)
        except InvalidToken:
            raise BackupError("Impossible de déchiffrer : ce fichier a été chiffré avec une autre clé, "
                              "ou il est endommagé.")
    raise BackupError("Ce fichier n'est pas une sauvegarde MenuFlash.")


# ==========================================================================
# Sérialisation
# ==========================================================================
def _app_tables():
    """Toutes les tables de l'application présentes en base, dans l'ordre des
    dépendances (parents avant enfants), sauf la table des sauvegardes."""
    existing = set(sa.inspect(db.engine).get_table_names())
    return [t for t in db.metadata.sorted_tables
            if t.name != BACKUP_TABLE and t.name in existing]


def _encode(value):
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"b64": base64.b64encode(bytes(value)).decode("ascii")}
    return value


def _decode(value, column):
    if value is None:
        return None
    if isinstance(value, dict) and "b64" in value:
        return base64.b64decode(value["b64"])
    t = column.type
    if isinstance(t, sa.DateTime):
        return datetime.fromisoformat(value)
    if isinstance(t, sa.Date):
        return date.fromisoformat(value)
    if isinstance(t, sa.Boolean):
        return bool(value)
    return value


def build_dump():
    """Renvoie (gzip_bytes, nombre_de_tables, nombre_de_lignes)."""
    tables, total = {}, 0
    for t in _app_tables():
        cols = [c.name for c in t.columns]
        order = list(t.primary_key.columns) or [t.columns[cols[0]]]
        rows = []
        for r in db.session.execute(t.select().order_by(*order)):
            m = r._mapping
            rows.append([_encode(m[c]) for c in cols])
        tables[t.name] = {"columns": cols, "rows": rows}
        total += len(rows)
    payload = {
        "format": FORMAT_NAME,
        "version": FORMAT_VERSION,
        "created_at": datetime.utcnow().isoformat(),
        "engine": db.engine.dialect.name,
        "commit": (os.environ.get("RENDER_GIT_COMMIT") or "")[:12],
        "tables": tables,
    }
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")
    return gzip.compress(raw, compresslevel=6), len(tables), total


def parse_backup(raw):
    """Lit et vérifie une sauvegarde (chiffrée ou non). Ne modifie rien."""
    try:
        data = json.loads(gzip.decompress(to_gzip_bytes(raw)).decode("utf-8"))
    except BackupError:
        raise
    except Exception:
        raise BackupError("Fichier illisible ou endommagé.")
    if not isinstance(data, dict) or data.get("format") != FORMAT_NAME:
        raise BackupError("Ce fichier n'est pas une sauvegarde MenuFlash.")
    if int(data.get("version", 0)) > FORMAT_VERSION:
        raise BackupError("Cette sauvegarde vient d'une version plus récente de l'application.")
    tables = data.get("tables")
    if not isinstance(tables, dict):
        raise BackupError("Sauvegarde incomplète (aucune table).")
    for name, block in tables.items():
        if (not isinstance(block, dict) or not isinstance(block.get("columns"), list)
                or not isinstance(block.get("rows"), list)):
            raise BackupError(f"Table « {name} » endommagée dans la sauvegarde.")
        width = len(block["columns"])
        if any(not isinstance(row, list) or len(row) != width for row in block["rows"]):
            raise BackupError(f"Lignes endommagées dans la table « {name} ».")
    return data


def summarize(data):
    known = {t.name for t in _app_tables()}
    tables = data["tables"]
    return {
        "created_at": data.get("created_at", "")[:16].replace("T", " "),
        "engine": data.get("engine", "?"),
        "tables": len(tables),
        "rows": sum(len(b["rows"]) for b in tables.values()),
        "restaurants": len(tables.get("restaurants", {}).get("rows", [])),
        "users": len(tables.get("users", {}).get("rows", [])),
        "orders": len(tables.get("orders", {}).get("rows", [])),
        "unknown": sorted(set(tables) - known),
    }


# ==========================================================================
# Restauration
# ==========================================================================
def _reset_sequences(tables):
    """PostgreSQL : remet les compteurs d'identifiants après les données restaurées."""
    if db.engine.dialect.name != "postgresql":
        return
    for t in tables:
        pk = list(t.primary_key.columns)
        if len(pk) != 1 or not isinstance(pk[0].type, sa.Integer):
            continue
        seq = db.session.execute(sa.text("SELECT pg_get_serial_sequence(:t, :c)"),
                                 {"t": t.name, "c": pk[0].name}).scalar()
        if seq:
            db.session.execute(sa.text(
                f'SELECT setval(:s, COALESCE((SELECT MAX("{pk[0].name}") FROM "{t.name}"), 0) + 1, false)'),
                {"s": seq})


def restore_data(data):
    """Remplace TOUTES les données (sauf les sauvegardes) en une transaction."""
    tables = _app_tables()
    db.session.commit()
    restored = 0
    try:
        for t in reversed(tables):
            db.session.execute(t.delete())
        for t in tables:
            block = data["tables"].get(t.name)
            if not block or not block["rows"]:
                continue
            columns = {c.name: c for c in t.columns}
            names = block["columns"]
            keep = [i for i, n in enumerate(names) if n in columns]
            batch = []
            for row in block["rows"]:
                batch.append({names[i]: _decode(row[i], columns[names[i]]) for i in keep})
                if len(batch) >= 500:
                    db.session.execute(t.insert(), batch)
                    batch = []
            if batch:
                db.session.execute(t.insert(), batch)
            restored += len(block["rows"])
        _reset_sequences(tables)
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        logger.exception("Restauration annulée")
        raise BackupError(f"La restauration a échoué et a été annulée, aucune donnée n'a été modifiée "
                          f"({type(exc).__name__}).")
    db.session.expunge_all()
    return restored


# ==========================================================================
# Stockage hors site (compatible S3)
# ==========================================================================
_S3_VARS = ("BACKUP_S3_ENDPOINT", "BACKUP_S3_BUCKET", "BACKUP_S3_ACCESS_KEY", "BACKUP_S3_SECRET_KEY")


def offsite_missing():
    return [v for v in _S3_VARS if not os.environ.get(v, "").strip()]


def offsite_configured():
    return not offsite_missing()


def _prefix():
    p = os.environ.get("BACKUP_S3_PREFIX", "menuflash/sauvegardes/").strip().strip("/")
    return (p + "/") if p else ""


def _bucket():
    return os.environ["BACKUP_S3_BUCKET"].strip()


def _s3_client():
    import boto3
    from botocore.config import Config
    return boto3.client(
        "s3",
        endpoint_url=os.environ["BACKUP_S3_ENDPOINT"].strip(),
        aws_access_key_id=os.environ["BACKUP_S3_ACCESS_KEY"].strip(),
        aws_secret_access_key=os.environ["BACKUP_S3_SECRET_KEY"].strip(),
        region_name=os.environ.get("BACKUP_S3_REGION", "auto").strip() or "auto",
        config=Config(signature_version="s3v4", connect_timeout=10, read_timeout=120,
                      retries={"max_attempts": 3, "mode": "standard"}),
    )


def _short_error(exc):
    return f"{type(exc).__name__}: {exc}"[:300]


def upload_offsite(backup):
    """Envoie une copie CHIFFRÉE hors site. Met à jour le statut sans lever d'erreur."""
    if not offsite_configured():
        backup.offsite_status, backup.offsite_error = "non_configure", None
        return False
    if not encryption_configured():
        backup.offsite_status = "cle_manquante"
        backup.offsite_error = "BACKUP_ENCRYPTION_KEY non définie : rien n'est envoyé hors site sans chiffrement."
        return False
    try:
        key = f"{_prefix()}menuflash_{backup.created_at:%Y%m%d_%H%M%S}_{backup.id}.mfbak"
        _s3_client().put_object(
            Bucket=_bucket(), Key=key, Body=encrypt(backup.data),
            ContentType="application/octet-stream",
            Metadata={"checksum": backup.checksum, "rows": str(backup.rows_count), "kind": backup.kind},
        )
        backup.offsite_status, backup.offsite_key, backup.offsite_error = "ok", key, None
        _prune_offsite()
        return True
    except BackupError as exc:
        backup.offsite_status, backup.offsite_error = "erreur", str(exc)[:300]
    except Exception as exc:
        logger.exception("Envoi hors site impossible")
        backup.offsite_status, backup.offsite_error = "erreur", _short_error(exc)
    return False


def list_offsite():
    client, items, token = _s3_client(), [], None
    while True:
        kwargs = {"Bucket": _bucket(), "Prefix": _prefix()}
        if token:
            kwargs["ContinuationToken"] = token
        resp = client.list_objects_v2(**kwargs)
        for obj in resp.get("Contents", []):
            if obj["Key"].endswith(".mfbak"):
                items.append({"key": obj["Key"], "name": obj["Key"][len(_prefix()):],
                              "size": obj.get("Size", 0), "date": obj.get("LastModified")})
        if not resp.get("IsTruncated"):
            break
        token = resp.get("NextContinuationToken")
    items.sort(key=lambda x: x["date"] or datetime.min, reverse=True)
    return items


def _prune_offsite():
    try:
        items = list_offsite()
        client = _s3_client()
        for old in items[keep_offsite():]:
            client.delete_object(Bucket=_bucket(), Key=old["key"])
    except Exception:
        logger.exception("Nettoyage hors site impossible")


def download_offsite(key):
    if not key.startswith(_prefix()) or not key.endswith(".mfbak") or ".." in key:
        raise BackupError("Fichier hors site invalide.")
    try:
        return _s3_client().get_object(Bucket=_bucket(), Key=key)["Body"].read()
    except Exception as exc:
        raise BackupError(f"Téléchargement hors site impossible ({type(exc).__name__}).")


# ==========================================================================
# Création
# ==========================================================================
_lock = threading.Lock()
_restore_state = {"on": False}
_schedule_state = {"checked_at": 0.0}


def _prune_local():
    ids = [b.id for b in Backup.query.order_by(Backup.created_at.desc()).offset(keep_local()).all()]
    if ids:
        Backup.query.filter(Backup.id.in_(ids)).delete(synchronize_session=False)
        db.session.commit()


def _do_backup(kind, actor, offsite=True):
    blob, ntables, nrows = build_dump()
    backup = Backup(kind=kind, created_by=(actor or "système")[:120], size_bytes=len(blob),
                    tables_count=ntables, rows_count=nrows,
                    checksum=hashlib.sha256(blob).hexdigest(), data=blob,
                    offsite_status="en_cours" if offsite and offsite_configured() else "non_configure")
    db.session.add(backup)
    db.session.commit()
    if offsite:
        upload_offsite(backup)
        db.session.commit()
    _prune_local()
    return backup


def create_backup(kind="manuelle", actor=None, offsite=True):
    if _restore_state["on"]:
        raise BackupError("Une restauration est en cours.")
    if not _lock.acquire(blocking=False):
        raise BackupError("Une sauvegarde est déjà en cours, réessayez dans un instant.")
    try:
        return _do_backup(kind, actor, offsite)
    finally:
        _lock.release()


def last_backup_at():
    try:
        return db.session.query(sa.func.max(Backup.created_at)).scalar()
    except Exception:
        db.session.rollback()
        return None


def is_stale():
    last = last_backup_at()
    return last is None or datetime.utcnow() - last > timedelta(hours=interval_hours() + 2)


# ==========================================================================
# Branchement
# ==========================================================================
def init_backups(app, *, log_audit, super_admin_required):

    with app.app_context():
        try:
            Backup.__table__.create(db.engine, checkfirst=True)
        except Exception:
            logger.exception("Création de la table des sauvegardes impossible")

    # --- Téléversement d'une sauvegarde : limite de taille plus haute
    #     uniquement sur cette adresse (5 Mo partout ailleurs).
    base_request = app.request_class

    class _BackupAwareRequest(base_request):
        @property
        def max_content_length(self):
            if self.path == UPLOAD_PATH:
                return MAX_UPLOAD_MB * 1024 * 1024
            return super().max_content_length

    app.request_class = _BackupAwareRequest

    def audit(action, details=None, success=True, email=None):
        if email is None and current_user.is_authenticated:
            email = current_user.email
        log_audit(action, success=success, user_email=email, details=(details or "")[:300])

    # ----------------------------------------------------------------------
    # Déclenchement automatique interne (arrière-plan)
    # ----------------------------------------------------------------------
    def _background_backup():
        with app.app_context():
            try:
                b = create_backup("auto", "système")
                log_audit("backup_auto", user_email="système",
                          details=f"n°{b.id}, {b.rows_count} lignes, hors site : {b.offsite_status}")
            except BackupError:
                pass
            except Exception:
                logger.exception("Sauvegarde automatique impossible")
            finally:
                db.session.remove()

    def _maybe_schedule():
        if os.environ.get("BACKUP_AUTO", "true").lower() == "false" or app.config.get("TESTING"):
            return
        now = time.time()
        if now - _schedule_state["checked_at"] < 900:
            return
        _schedule_state["checked_at"] = now
        if _lock.locked() or _restore_state["on"] or not is_stale_for_schedule():
            return
        threading.Thread(target=_background_backup, name="menuflash-backup", daemon=True).start()

    def is_stale_for_schedule():
        last = last_backup_at()
        return last is None or datetime.utcnow() - last >= timedelta(hours=interval_hours())

    # ----------------------------------------------------------------------
    # Déclenchement externe (GitHub Actions)
    # ----------------------------------------------------------------------
    def _cron():
        expected = os.environ.get("BACKUP_CRON_TOKEN", "")
        given = request.headers.get("X-Backup-Token", "")
        if len(expected) < 24 or not hmac.compare_digest(expected.encode(), given.encode()):
            log_audit("backup_cron_refused", success=False, user_email=None,
                      details="jeton absent ou invalide")
            return jsonify(ok=False, error="unauthorized"), 403
        try:
            b = create_backup("planifiee", "GitHub Actions")
        except BackupError as exc:
            return jsonify(ok=False, error=str(exc)), 409
        except Exception as exc:
            logger.exception("Sauvegarde planifiée impossible")
            return jsonify(ok=False, error=type(exc).__name__), 500
        log_audit("backup_cron", user_email="GitHub Actions",
                  details=f"n°{b.id}, {b.rows_count} lignes, hors site : {b.offsite_status}")
        return jsonify(ok=True, id=b.id, size=b.size_bytes, rows=b.rows_count,
                       offsite=b.offsite_status, offsite_error=b.offsite_error)

    @app.route(CRON_PATH)
    def backup_cron():
        return _cron()

    @app.before_request
    def _backup_gate():
        # Traité ici (et pas seulement par la route) pour passer AVANT le mode
        # maintenance : une sauvegarde doit pouvoir se faire à tout moment.
        if request.path == CRON_PATH:
            return _cron()
        if request.endpoint in ("static", "media"):
            return None
        is_super = current_user.is_authenticated and getattr(current_user, "is_super_admin", False)
        if _restore_state["on"] and not is_super:
            return Response("Restauration des données en cours. Réessayez dans une minute.",
                            503, {"Retry-After": "60", "Content-Type": "text/plain; charset=utf-8"})
        try:
            _maybe_schedule()
        except Exception:
            db.session.rollback()
        return None

    @app.context_processor
    def _inject_backup_status():
        try:
            if current_user.is_authenticated and current_user.is_super_admin:
                return {"backup_stale": is_stale()}
        except Exception:
            db.session.rollback()
        return {}

    # ----------------------------------------------------------------------
    # Pages du panel
    # ----------------------------------------------------------------------
    def _back():
        return redirect(url_for("super_admin_backups"))

    @app.route("/super-admin/sauvegardes")
    @super_admin_required
    def super_admin_backups():
        backups = Backup.query.order_by(Backup.created_at.desc()).all()
        offsite_items, offsite_error = [], None
        if offsite_configured():
            try:
                offsite_items = list_offsite()
            except Exception as exc:
                offsite_error = _short_error(exc)
        last = last_backup_at()
        return render_template(
            "super_admin_backups.html",
            backups=backups, offsite_items=offsite_items, offsite_error=offsite_error,
            offsite_ok=offsite_configured(), offsite_missing=offsite_missing(),
            encryption_ok=encryption_configured(),
            cron_ok=len(os.environ.get("BACKUP_CRON_TOKEN", "")) >= 24,
            auto_on=os.environ.get("BACKUP_AUTO", "true").lower() != "false",
            last=last, stale=is_stale(), now=datetime.utcnow(),
            interval=interval_hours(), keep_local=keep_local(), keep_offsite=keep_offsite(),
            kind_labels=KIND_LABELS, offsite_labels=OFFSITE_LABELS, max_upload_mb=MAX_UPLOAD_MB,
        )

    @app.route("/super-admin/sauvegardes/creer", methods=["POST"])
    @super_admin_required
    def super_admin_backup_create():
        try:
            b = create_backup("manuelle", current_user.email)
        except BackupError as exc:
            flash(str(exc), "error")
            return _back()
        audit("backup_manual", f"n°{b.id}, {b.rows_count} lignes, hors site : {b.offsite_status}")
        msg = f"Sauvegarde n°{b.id} créée ({b.rows_count} lignes)."
        if b.offsite_status == "ok":
            msg += " Copie chiffrée envoyée hors site."
        elif b.offsite_status in ("erreur", "cle_manquante"):
            msg += f" Attention, copie hors site non envoyée : {b.offsite_error}"
        flash(msg, "success" if b.offsite_status in ("ok", "non_configure") else "error")
        return _back()

    @app.route("/super-admin/sauvegardes/<int:backup_id>/telecharger")
    @super_admin_required
    def super_admin_backup_download(backup_id):
        b = Backup.query.get_or_404(backup_id)
        stamp = f"{b.created_at:%Y%m%d_%H%M}"
        if encryption_configured():
            try:
                body, name = encrypt(b.data), f"menuflash_{stamp}_{b.id}.mfbak"
            except BackupError as exc:
                flash(str(exc), "error")
                return _back()
        else:
            body, name = b.data, f"menuflash_{stamp}_{b.id}.json.gz"
        audit("backup_downloaded", f"n°{b.id} ({'chiffrée' if name.endswith('.mfbak') else 'NON chiffrée'})")
        return Response(body, mimetype="application/octet-stream",
                        headers={"Content-Disposition": f'attachment; filename="{name}"',
                                 "Cache-Control": "no-store"})

    @app.route("/super-admin/sauvegardes/<int:backup_id>/hors-site", methods=["POST"])
    @super_admin_required
    def super_admin_backup_offsite(backup_id):
        b = Backup.query.get_or_404(backup_id)
        ok = upload_offsite(b)
        db.session.commit()
        audit("backup_offsite_retry", f"n°{b.id} : {b.offsite_status}", success=ok)
        flash("Copie hors site envoyée." if ok else f"Envoi hors site impossible : {b.offsite_error}",
              "success" if ok else "error")
        return _back()

    @app.route("/super-admin/sauvegardes/<int:backup_id>/supprimer", methods=["POST"])
    @super_admin_required
    def super_admin_backup_delete(backup_id):
        b = Backup.query.get_or_404(backup_id)
        if Backup.query.count() <= 1:
            flash("Impossible de supprimer la dernière sauvegarde restante.", "error")
            return _back()
        db.session.delete(b)
        db.session.commit()
        audit("backup_deleted", f"n°{backup_id} (copie locale)")
        flash(f"Sauvegarde n°{backup_id} supprimée (la copie hors site éventuelle est conservée).", "success")
        return _back()

    # ---------------- Test et restauration ----------------
    def _load_source():
        """Renvoie (octets, libellé) selon la source demandée dans le formulaire."""
        source = request.form.get("source")
        if source == "local":
            b = Backup.query.get_or_404(int(request.form.get("backup_id", 0) or 0))
            if hashlib.sha256(b.data).hexdigest() != b.checksum:
                raise BackupError(f"La sauvegarde n°{b.id} est endommagée (empreinte différente).")
            return b.data, f"copie locale n°{b.id} du {b.created_at:%d/%m/%Y %H:%M}"
        if source == "offsite":
            if not offsite_configured():
                raise BackupError("Le stockage hors site n'est pas configuré.")
            key = request.form.get("key", "")
            return download_offsite(key), f"copie hors site {key[len(_prefix()):]}"
        if source == "file":
            f = request.files.get("file")
            if not f or not f.filename:
                raise BackupError("Choisissez un fichier de sauvegarde.")
            return f.read(), f"fichier {os.path.basename(f.filename)[:80]}"
        raise BackupError("Source de sauvegarde inconnue.")

    def _check_confirmation():
        if (request.form.get("confirm") or "").strip().upper() != "RESTAURER":
            raise BackupError("Confirmation incorrecte : tapez RESTAURER en majuscules.")
        if not current_user.check_password(request.form.get("password", "")):
            audit("backup_restore_refused", "mot de passe incorrect", success=False)
            raise BackupError("Mot de passe incorrect.")

    def _handle(mode):
        try:
            raw, label = _load_source()
            data = parse_backup(raw)
            info = summarize(data)
        except BackupError as exc:
            flash(str(exc), "error")
            return _back()

        if mode == "test":
            msg = (f"Sauvegarde valide ({label}) : faite le {info['created_at']} sur {info['engine']}, "
                   f"{info['tables']} tables, {info['rows']} lignes — {info['restaurants']} restaurant(s), "
                   f"{info['users']} compte(s), {info['orders']} commande(s).")
            if info["unknown"]:
                msg += f" Tables ignorées à la restauration : {', '.join(info['unknown'])}."
            audit("backup_tested", label)
            flash(msg, "success")
            return _back()

        try:
            _check_confirmation()
        except BackupError as exc:
            flash(str(exc), "error")
            return _back()

        email = current_user.email
        if not _lock.acquire(blocking=False):
            flash("Une sauvegarde ou une restauration est déjà en cours.", "error")
            return _back()
        _restore_state["on"] = True
        try:
            safety_id = _do_backup("avant_restauration", email, offsite=offsite_configured()).id
            restored = restore_data(data)
        except BackupError as exc:
            log_audit("backup_restore_failed", success=False, user_email=email, details=f"{label} : {exc}")
            flash(str(exc), "error")
            return _back()
        finally:
            _restore_state["on"] = False
            _lock.release()

        log_audit("backup_restored", user_email=email,
                  details=f"{label} — {restored} lignes ; sécurité : n°{safety_id}")
        logout_user()
        flash(f"Restauration terminée depuis la {label} ({restored} lignes). Reconnectez-vous. "
              f"Une sauvegarde de sécurité (n°{safety_id}) a été faite juste avant : vous pouvez "
              f"revenir en arrière à tout moment.", "success")
        return redirect(url_for("admin_login"))

    @app.route("/super-admin/sauvegardes/tester", methods=["POST"])
    @super_admin_required
    def super_admin_backup_test():
        return _handle("test")

    @app.route("/super-admin/sauvegardes/restaurer", methods=["POST"])
    @super_admin_required
    def super_admin_backup_restore():
        return _handle("restore")

    @app.route(UPLOAD_PATH, methods=["POST"])
    @super_admin_required
    def super_admin_backup_restore_file():
        return _handle("test" if request.form.get("test_only") else "restore")
