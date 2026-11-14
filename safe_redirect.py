"""
Retour à la page précédente sans redirection ouverte (audit lot 3).

Plusieurs écrans faisaient « redirect(request.referrer or ...) ». L'en-tête Referer vient du
navigateur : on ne redirige que vers une page de CE site, sinon vers la page par défaut.
"""
from urllib.parse import urlsplit

from flask import redirect, request


def safe_back(default):
    ref = request.referrer or ""
    try:
        parts = urlsplit(ref)
        if parts.scheme in ("http", "https") and parts.netloc == request.host:
            return redirect(ref)
    except ValueError:
        pass
    return redirect(default)
