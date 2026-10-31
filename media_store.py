"""
MenuFlash Mini — stockage permanent des images (logos et photos de plats).

POURQUOI : sur Render (et la plupart des hébergeurs), le disque du serveur
n'est PAS permanent. Tout fichier écrit dans static/uploads disparaît à
chaque redéploiement, redémarrage ou mise en veille du service. C'est
pour cela que les logos "disparaissaient après quelque temps".

SOLUTION : les images (déjà recompressées et redimensionnées, donc
légères : quelques dizaines de Ko) sont enregistrées directement dans la
base de données PostgreSQL, qui, elle, est permanente. Elles sont servies
par la route /media/<id>, avec un cache navigateur long (une image ne
change jamais : un nouveau logo crée une nouvelle image avec un nouvel id).

La table `stored_images` est créée automatiquement au démarrage si elle
n'existe pas encore (aucune action manuelle nécessaire sur Render).
"""
import logging
from datetime import datetime

from models import db

logger = logging.getLogger("menuflash.media")

MEDIA_URL_PREFIX = "/media/"


class StoredImage(db.Model):
    __tablename__ = "stored_images"

    id = db.Column(db.Integer, primary_key=True)
    restaurant_id = db.Column(db.Integer, nullable=True, index=True)
    kind = db.Column(db.String(20), nullable=False, default="photo")   # "logo" ou "photo"
    mimetype = db.Column(db.String(40), nullable=False)
    data = db.Column(db.LargeBinary, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


def ensure_media_table(app):
    """Crée la table stored_images si elle n'existe pas (sans toucher aux autres)."""
    try:
        with app.app_context():
            StoredImage.__table__.create(db.engine, checkfirst=True)
    except Exception:
        logger.exception("Impossible de créer la table stored_images")


def store_image(data, mimetype, kind="photo", restaurant_id=None):
    """Enregistre les octets d'une image et renvoie son URL publique (/media/<id>)."""
    image = StoredImage(restaurant_id=restaurant_id, kind=kind, mimetype=mimetype, data=data)
    db.session.add(image)
    db.session.flush()  # attribue l'id sans valider la transaction de l'appelant
    return f"{MEDIA_URL_PREFIX}{image.id}"


def delete_stored_image(url):
    """Supprime une image stockée en base si l'URL pointe vers /media/<id>. Sans effet sinon."""
    if not url or not url.startswith(MEDIA_URL_PREFIX):
        return
    try:
        image_id = int(url[len(MEDIA_URL_PREFIX):])
    except ValueError:
        return
    StoredImage.query.filter_by(id=image_id).delete()
