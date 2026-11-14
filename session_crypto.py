"""
Cookie de session CHIFFRÉ (audit MF-23).

Le cookie de session de Flask est seulement SIGNÉ : son contenu (email et
téléphone pré-remplis, lien d'invitation d'un administrateur…) peut être lu en
le décodant (base64), même s'il ne peut pas être modifié.

Ici, le contenu est en plus CHIFFRÉ (Fernet, clé dérivée de FLASK_SECRET_KEY)
avant d'être signé : le cookie devient illisible.

Compatibilité : les anciens cookies (non chiffrés) restent acceptés jusqu'à leur
expiration ; personne n'est déconnecté au déploiement. Un cookie chiffré
illisible (clé changée, falsification) est simplement ignoré : session vide.
"""
import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from flask.sessions import SecureCookieSessionInterface
from flask.json.tag import TaggedJSONSerializer
from itsdangerous import BadPayload

_PREFIX = "~"


class _EncryptedJSONSerializer:
    def __init__(self, fernet):
        self._fernet = fernet
        self._inner = TaggedJSONSerializer()

    def dumps(self, value):
        plain = self._inner.dumps(value).encode("utf-8")
        return _PREFIX + self._fernet.encrypt(plain).decode("ascii")

    def loads(self, data):
        if isinstance(data, bytes):
            data = data.decode("utf-8")
        if data.startswith(_PREFIX):
            try:
                plain = self._fernet.decrypt(data[len(_PREFIX):].encode("ascii"))
            except (InvalidToken, ValueError):
                raise BadPayload("Cookie de session illisible")
            return self._inner.loads(plain.decode("utf-8"))
        return self._inner.loads(data)          # ancien cookie (avant chiffrement)


def _fernet_for(secret):
    raw = hashlib.sha256(b"menuflash-session-v1:" + str(secret).encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(raw))


def install(app):
    """À appeler une fois, après avoir défini SECRET_KEY."""
    interface = SecureCookieSessionInterface()
    interface.serializer = _EncryptedJSONSerializer(_fernet_for(app.config["SECRET_KEY"]))
    app.session_interface = interface
    return interface
