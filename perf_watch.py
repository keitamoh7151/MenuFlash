"""
MenuFlash Mini — Chronomètre des requêtes : où passe le temps quand une page est lente ?

Branchement dans app.py, JUSTE APRÈS « db.init_app(app) » (le script ajouter_perf_watch.py le fait) :

    from perf_watch import init_perf_watch
    init_perf_watch(app)

Ce que fait le module
---------------------
Pour chaque requête, il mesure :
  - la base de données : nombre de requêtes SQL, temps total, requête la plus lente ;
  - les validations (commits) : nombre et temps ;
  - le calcul du mot de passe (scrypt) : volontairement lent, c'est la sécurité ;
  - les appels à un service externe pendant la requête (Brevo pour les emails,
    Have I Been Pwned pour les mots de passe) : nom du serveur et temps ;
  - le reste (Python, page HTML).

1. JOURNAL RENDER (Logs). Une ligne d'avertissement par requête lente :

     LENTE 3.21 s · POST admin_login /admin/login → 302 · base 17 req 0.45 s ·
     commits 4 0.12 s · mot de passe 0.31 s · externe 1 (api.brevo.com) 2.20 s ·
     reste 0.13 s · plus lente 0.40 s : SELECT users.id …

   et une ligne « CONNEXION » à chaque connexion (même rapide), pour comprendre
   le délai que vous ressentez au bouton « Se connecter ».

2. EN-TÊTE « Server-Timing ». Dans Chrome : F12 > Network > cliquer la requête >
   Timing : une section « Server Timing » affiche base, commits, mot de passe,
   externe et total. Envoyée uniquement aux utilisateurs CONNECTÉS (jamais avant
   l'authentification : la durée du mot de passe ne doit pas révéler si un email
   existe).

Réglages (Render > Environment, tous facultatifs)
  SLOW_REQUEST_MS     seuil d'une requête « lente », défaut 800 (100 à 60000)
  PERF_LOG_LOGIN      « false » pour ne plus journaliser chaque connexion
  PERF_SERVER_TIMING  « false » pour ne plus envoyer l'en-tête Server-Timing
  PERF_WATCH          « false » pour désactiver tout le module

Confidentialité : aucun email, mot de passe, jeton ni valeur de requête n'est
journalisé. On note le nom de la route (« admin_login »), sa forme
(« /admin/reinitialiser/<token> », jamais l'adresse réelle), le code de réponse,
le nom du serveur externe (sans adresse complète) et le début de la requête SQL
SANS ses valeurs. Coût : quelques microsecondes par requête.
Les flux temps réel (/stream) et les fichiers statiques ne sont pas mesurés.
"""
import logging
import os
import re
import time
import urllib.parse
import urllib.request

from flask import current_app, g, has_request_context, request
from flask_login import current_user
from sqlalchemy import event
from sqlalchemy.orm import Session

from models import User, db

log = logging.getLogger("menuflash.perf")

SKIP_ENDPOINTS = {"static", "media", "admin_orders_stream", "public_theme_stream"}
LOGIN_ENDPOINTS = {"admin_login", "two_factor_verify"}
STATEMENT_CHARS = 110


def _env_int(name, default, low, high):
    try:
        return max(low, min(int(os.environ.get(name, default)), high))
    except (TypeError, ValueError):
        return default


def _env_flag(name, default=True):
    raw = os.environ.get(name)
    return default if raw is None else raw.strip().lower() not in ("0", "false", "non", "no", "off")


def _now():
    return time.perf_counter()


def _stats():
    """Compteurs de la requête en cours, ou None (démarrage, thread d'arrière-plan, flux ignoré)."""
    if not has_request_context():
        return None
    return getattr(g, "_mf_perf", None)


def _clean_statement(statement):
    """Début de la requête SQL, sans valeurs (elles sont passées à part) et sur une ligne."""
    return re.sub(r"\s+", " ", str(statement or "")).strip()[:STATEMENT_CHARS]


def _host_of(target):
    try:
        url = target.full_url if hasattr(target, "full_url") else str(target)
        return (urllib.parse.urlparse(url).hostname or "?")[:60]
    except Exception:
        return "?"


def format_seconds(ms):
    return f"{ms / 1000:.2f} s"


def build_line(label, total_ms, method, endpoint, rule, status, st):
    """Ligne de journal : durée totale, puis la ventilation. Fonction pure (testée)."""
    sql_ms, commit_ms, hash_ms, http_ms = st["sql_ms"], st["commit_ms"], st["hash_ms"], st["http_ms"]
    rest = max(0.0, total_ms - sql_ms - commit_ms - hash_ms - http_ms)
    parts = [f"{label} {format_seconds(total_ms)}",
             f"{method} {endpoint or '?'} {rule or ''} → {status}".replace("  ", " "),
             f"base {st['sql_n']} req {format_seconds(sql_ms)}"]
    if st["commit_n"]:
        parts.append(f"commits {st['commit_n']} {format_seconds(commit_ms)}")
    if hash_ms:
        parts.append(f"mot de passe {format_seconds(hash_ms)}")
    if st["http_n"]:
        parts.append(f"externe {st['http_n']} ({st['http_host']}) {format_seconds(http_ms)}")
    parts.append(f"reste {format_seconds(rest)}")
    if st["slow_ms"] >= 50:
        parts.append(f"plus lente {format_seconds(st['slow_ms'])} : {st['slow_sql']}")
    return " · ".join(parts)


def server_timing(total_ms, st):
    """Valeur de l'en-tête Server-Timing. Fonction pure (testée)."""
    items = [f"total;dur={total_ms:.1f}",
             f'db;dur={st["sql_ms"]:.1f};desc="{st["sql_n"]} req"']
    if st["commit_n"]:
        items.append(f'commit;dur={st["commit_ms"]:.1f};desc="{st["commit_n"]}"')
    if st["hash_ms"]:
        items.append(f"password;dur={st['hash_ms']:.1f}")
    if st["http_n"]:
        items.append(f'external;dur={st["http_ms"]:.1f};desc="{st["http_n"]}"')
    return ", ".join(items)


def init_perf_watch(app):
    if not _env_flag("PERF_WATCH", True):
        return
    if app.extensions.get("mf_perf"):
        return                                    # déjà branché : on ne double pas les mesures
    app.extensions["mf_perf"] = True

    # ------------------------------------------------------------------ base de données
    def before_execute(conn, cursor, statement, parameters, context, executemany):
        conn.info.setdefault("mf_t", []).append(_now())

    def after_execute(conn, cursor, statement, parameters, context, executemany):
        started = conn.info.get("mf_t")
        if not started:
            return
        elapsed = (_now() - started.pop()) * 1000
        st = _stats()
        if st is None:
            return
        st["sql_n"] += 1
        st["sql_ms"] += elapsed
        if elapsed > st["slow_ms"]:
            st["slow_ms"], st["slow_sql"] = elapsed, _clean_statement(statement)

    def before_commit(session):
        st = _stats()
        if st is not None:
            st["c0"], st["c0_sql"] = _now(), st["sql_ms"]

    def after_commit(session):
        st = _stats()
        if st is None or st.get("c0") is None:
            return
        span = (_now() - st["c0"]) * 1000
        # Le « commit » de SQLAlchemy envoie d'abord les INSERT/UPDATE en attente (déjà comptés
        # dans « base ») : on ne garde ici que le temps du COMMIT lui-même.
        st["commit_ms"] += max(0.0, span - (st["sql_ms"] - st["c0_sql"]))
        st["commit_n"] += 1
        st["c0"] = None

    with app.app_context():
        engine = db.engine
        event.listen(engine, "before_cursor_execute", before_execute)
        event.listen(engine, "after_cursor_execute", after_execute)
    event.listen(Session, "before_commit", before_commit)
    event.listen(Session, "after_commit", after_commit)

    # ------------------------------------------------------------------ mot de passe
    if not getattr(User.check_password, "_mf_timed", False):
        original_check, original_set = User.check_password, User.set_password

        def timed_check(self, password):
            t0 = _now()
            try:
                return original_check(self, password)
            finally:
                st = _stats()
                if st is not None:
                    st["hash_ms"] += (_now() - t0) * 1000

        def timed_set(self, password):
            t0 = _now()
            try:
                return original_set(self, password)
            finally:
                st = _stats()
                if st is not None:
                    st["hash_ms"] += (_now() - t0) * 1000

        timed_check._mf_timed = timed_set._mf_timed = True
        User.check_password, User.set_password = timed_check, timed_set

    # ------------------------------------------------------------------ appels externes (Brevo, HIBP)
    real_urlopen = urllib.request.urlopen

    def timed_urlopen(target, *args, **kwargs):
        t0 = _now()
        try:
            return real_urlopen(target, *args, **kwargs)
        finally:
            st = _stats()
            if st is not None:                 # hors requête (thread d'arrière-plan) : rien n'attend
                st["http_ms"] += (_now() - t0) * 1000
                st["http_n"] += 1
                st["http_host"] = _host_of(target)

    if not getattr(urllib.request.urlopen, "_mf_timed", False):
        timed_urlopen._mf_timed = True
        urllib.request.urlopen = timed_urlopen

    # ------------------------------------------------------------------ cycle d'une requête
    @app.before_request
    def _perf_start():
        if (request.endpoint or "") in SKIP_ENDPOINTS or request.path.endswith("/stream"):
            return None
        g._mf_perf = {"t0": _now(), "sql_n": 0, "sql_ms": 0.0, "slow_ms": 0.0, "slow_sql": "",
                      "commit_n": 0, "commit_ms": 0.0, "c0": None, "c0_sql": 0.0,
                      "hash_ms": 0.0, "http_n": 0, "http_ms": 0.0, "http_host": ""}
        return None

    @app.after_request
    def _perf_finish(response):
        st = getattr(g, "_mf_perf", None)
        if st is None:
            return response
        try:
            total_ms = (_now() - st["t0"]) * 1000
            endpoint = request.endpoint or ""
            rule = request.url_rule.rule if request.url_rule is not None else ""
            is_login = request.method == "POST" and endpoint in LOGIN_ENDPOINTS
            # Réglages relus à chaque requête (quelques microsecondes) : modifiables sans toucher au code.
            slow_ms = _env_int("SLOW_REQUEST_MS", 800, 100, 60000)
            log_logins = _env_flag("PERF_LOG_LOGIN", True)
            send_header = _env_flag("PERF_SERVER_TIMING", True)
            if total_ms >= slow_ms:
                log.warning(build_line("LENTE", total_ms, request.method, endpoint, rule,
                                       response.status_code, st))
            elif is_login and log_logins:
                log.warning(build_line("CONNEXION", total_ms, request.method, endpoint, rule,
                                       response.status_code, st))
            if send_header and current_user.is_authenticated:
                response.headers["Server-Timing"] = server_timing(total_ms, st)
        except Exception:
            log.exception("Chronomètre : mesure impossible")
        return response
