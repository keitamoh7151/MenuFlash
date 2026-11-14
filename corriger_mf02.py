"""
MenuFlash — Correction de sécurité MF-02 (audit du 13/11/2026).

PROBLÈME
--------
Les flux temps réel (Server-Sent Events) gardaient un thread du serveur occupé
en permanence. Avec gunicorn « 1 worker × 4 threads », 4 connexions suffisaient
à bloquer TOUT le SaaS :
  - /m/<slug>/theme-stream : public, sans fin, sans authentification, et utilisé
    par AUCUNE page (route morte, mais ouverte à tous) ;
  - /admin/commandes/stream : 4 écrans « Commandes » ouverts en même temps, tous
    restaurants confondus, et plus personne ne pouvait commander.

CORRECTION
----------
1. realtime.py : plus de file d'attente bloquante. Les événements (nouvelle
   commande, commande modifiée/annulée, thème changé) sont gardés quelques
   minutes en mémoire, numérotés.
2. app.py :
   - suppression de /m/<slug>/theme-stream ;
   - /admin/commandes/stream remplacé par /admin/commandes/evenements.json :
     une requête courte (quelques millisecondes) qui renvoie les événements
     depuis le dernier numéro reçu.
3. static/app.js :
   - la page Commandes interroge ce point toutes les 4 s (10 s en arrière-plan) :
     mêmes sons, mêmes notifications, même indicateur « En direct » ;
   - showToast accepte un lien sûr (correction de la régression MF-01 : le
     lien « Voir » de la notification de nouvelle commande s'affichait en texte).
4. templates/base.html : nouvelle adresse ; base.html et signup.html chargent
   app.js?v=8 (les navigateurs prennent la nouvelle version).

UTILISATION (à la racine du projet, là où se trouve app.py)
-----------------------------------------------------------
    python corriger_mf02.py

Le script vérifie TOUT avant d'écrire quoi que ce soit. Si un repère n'est pas
trouvé, il s'arrête sans rien modifier et indique lequel.
Une copie de chaque fichier modifié est faite : <fichier>.avant-mf02
"""
import os
import re
import shutil
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))

# ==========================================================================
# 1. realtime.py (remplacé entièrement)
# ==========================================================================
REALTIME_PY = '''"""
Événements temps réel par restaurant (nouvelle commande, commande modifiée ou
annulée, thème changé).

Sécurité (audit MF-02) : plus aucun flux ouvert en permanence (Server-Sent
Events). Les événements sont gardés quelques minutes en mémoire, numérotés ;
l'écran Commandes les récupère par une requête courte (events_since). Un thread
du serveur n'est donc jamais bloqué, quel que soit le nombre d'écrans ouverts.

Limite connue (identique à l'ancienne version) : mémoire du processus. Avec
plusieurs workers gunicorn, il faudra un stockage partagé (Redis).
"""
import itertools
import threading
import time
from collections import deque

MAX_EVENTS_PER_RESTAURANT = 200      # au-delà, les plus anciens sont oubliés
MAX_EVENT_AGE_SECONDS = 600          # un événement de plus de 10 min n'est plus renvoyé
MAX_EVENTS_PER_RESPONSE = 50

_lock = threading.Lock()
_counter = itertools.count(1)
_last_seq = 0
_events = {}                         # restaurant_id -> deque[(seq, horodatage, type, data)]


def publish(restaurant_id, event_type, payload):
    """Enregistre un événement pour l'équipe du restaurant. Ne bloque jamais."""
    global _last_seq
    now = time.time()
    with _lock:
        seq = next(_counter)
        _last_seq = seq
        bucket = _events.get(restaurant_id)
        if bucket is None:
            bucket = _events[restaurant_id] = deque(maxlen=MAX_EVENTS_PER_RESTAURANT)
        bucket.append((seq, now, event_type, payload))
        # Ménage : restaurants sans activité récente.
        if len(_events) > 50 and seq % 100 == 0:
            for rid in [r for r, b in _events.items() if not b or now - b[-1][1] > MAX_EVENT_AGE_SECONDS]:
                _events.pop(rid, None)
    return seq


def events_since(restaurant_id, since):
    """Renvoie (événements, curseur).

    - since None (premier appel de la page) : aucun événement, seulement le
      curseur actuel (on ne rejoue pas l'historique : pas de son à l'ouverture).
    - since plus grand que le curseur (serveur redémarré) : aucun événement,
      le navigateur reprend au nouveau curseur.
    """
    now = time.time()
    with _lock:
        cursor = _last_seq
        if since is None or since >= cursor:
            return [], cursor
        bucket = _events.get(restaurant_id) or ()
        items = [{"type": t, "data": d} for (seq, ts, t, d) in bucket
                 if seq > since and now - ts <= MAX_EVENT_AGE_SECONDS]
    return items[-MAX_EVENTS_PER_RESPONSE:], cursor
'''

# ==========================================================================
# 2. app.py
# ==========================================================================
APP_OLD_PUBLIC = '''@app.route("/m/<slug>/theme-stream")
def public_theme_stream(slug):
    restaurant = tenant_or_404(slug)
    q = realtime.subscribe(restaurant.id)

    def event_stream():
        try:
            yield "retry: 3000\\n\\n"
            while True:
                try:
                    message = q.get(timeout=15)
                    try:
                        parsed = json.loads(message)
                    except Exception:
                        continue
                    if parsed.get("type") == "theme_changed":
                        yield realtime.format_sse(message)
                except Exception:
                    yield ": ping\\n\\n"
        finally:
            realtime.unsubscribe(restaurant.id, q)

    return Response(event_stream(), mimetype="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    })


'''
APP_NEW_PUBLIC = '''# Audit MF-02 : l'ancien flux public /m/<slug>/theme-stream (sans fin, sans
# authentification, utilisé par aucune page) a été supprimé.


'''

APP_OLD_STREAM = '''@app.route("/admin/commandes/stream")
@login_required
def admin_orders_stream():
    restaurant_id = active_restaurant_id()
    q = realtime.subscribe(restaurant_id)

    def event_stream():
        import time
        started = time.time()
        try:
            yield "retry: 3000\\n\\n"
            # Ferme le flux après 5 minutes : le navigateur se reconnecte
            # automatiquement, ce qui libère les connexions abandonnées
            # (onglets fermés, téléphones en veille).
            while time.time() - started < 300:
                try:
                    message = q.get(timeout=15)
                    yield realtime.format_sse(message)
                except Exception:
                    yield ": ping\\n\\n"
        finally:
            realtime.unsubscribe(restaurant_id, q)

    return Response(event_stream(), mimetype="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    })
'''
APP_NEW_STREAM = '''@app.route("/admin/commandes/evenements.json")
@login_required
def admin_orders_events():
    """Événements de la cuisine depuis le curseur « depuis » (audit MF-02).

    Remplace l'ancien flux SSE /admin/commandes/stream : une requête courte au lieu
    d'un thread bloqué. Isolation : uniquement les événements du restaurant actif.
    """
    restaurant_id = active_restaurant_id()
    raw = (request.args.get("depuis") or "").strip()
    since = int(raw) if raw.isdigit() and len(raw) <= 18 else None
    events, cursor = realtime.events_since(restaurant_id, since)
    response = jsonify(cursor=cursor, events=events)
    response.headers["Cache-Control"] = "no-store"
    return response
'''

# ==========================================================================
# 3. static/app.js
# ==========================================================================
JS_OLD_TOAST_SIG = '''  function showToast(message, isError) {'''
JS_NEW_TOAST_SIG = '''  // link (facultatif) : { href: "/chemin/interne", label: "Voir" } — lien sûr, texte uniquement.
  function showToast(message, isError, link) {'''

JS_OLD_TOAST_BODY = '''    body.appendChild(iconEl);
    body.appendChild(textEl);
'''
JS_NEW_TOAST_BODY = '''    body.appendChild(iconEl);
    body.appendChild(textEl);
    if (link && typeof link.href === "string" && link.href.charAt(0) === "/" &&
        link.href.charAt(1) !== "/" && link.href.indexOf("\\\\") === -1) {
      var linkEl = document.createElement("a");
      linkEl.href = link.href;
      linkEl.className = "fw-bold ms-1";
      linkEl.textContent = link.label || "Voir";
      body.appendChild(linkEl);
    }
'''

# L'émoji de ce message peut être écrit tel quel ou en \\uXXXX : on cherche par motif.
JS_OLD_WATCHER_RE = re.compile(
    r'showToast\(\s*"[^"\n]*Nouvelle commande[^"\n]*"\s*\+\s*escapeHtml\(data\.latest_table\)\s*\+\s*'
    r"'[^'\n]*<a href=\"/admin/commandes\" class=\"fw-bold\">Voir</a>',\s*false\s*\);")
JS_NEW_WATCHER = '''showToast(
              "\\uD83C\\uDD95 Nouvelle commande · " + (data.latest_table || "") + " —",
              false,
              { href: "/admin/commandes", label: "Voir" }
            );'''

JS_LIVE_START = '''  function initThemeLiveSync() {'''
JS_NEW_LIVE = r'''  function initThemeLiveSync() {
    /* Temps réel de la page Commandes (audit MF-02) : requêtes courtes toutes les
       4 s (10 s en arrière-plan) au lieu d'un flux SSE qui bloquait un thread du
       serveur. Mêmes sons, mêmes notifications, même indicateur « En direct ». */
    var el = document.getElementById("theme-sync-root");
    if (!el || !el.dataset.eventsUrl) return;

    var eventsUrl = el.dataset.eventsUrl;
    var liveIndicator = document.getElementById("live-indicator");
    var cursor = null;
    var busy = false;
    var timer = null;
    var failures = 0;

    function setLiveStatus(isLive) {
      if (!liveIndicator) return;
      liveIndicator.classList.toggle("mf-live-off", !isLive);
      liveIndicator.textContent = "";
      var dot = document.createElement("span");
      dot.className = "mf-live-dot";
      liveIndicator.appendChild(dot);
      liveIndicator.appendChild(document.createTextNode(isLive ? " En direct" : " Reconnexion…"));
    }

    function handle(payload) {
      if (!payload || typeof payload.type !== "string") return;
      if (payload.type === "theme_changed") {
        applyThemeVars(payload.data);
        showToast("\uD83C\uDFA8 Le thème a été mis à jour.", false);
        return;
      }
      var tableInfo = (payload.data && payload.data.table_label) ? " · " + payload.data.table_label : "";
      if (payload.type === "new_order") {
        playNotificationSound();
        showToast("\uD83C\uDD95 Nouvelle commande reçue" + tableInfo + " !", false);
        if (kanbanRefreshFn) kanbanRefreshFn();
      } else if (payload.type === "order_updated") {
        playNotificationSound("updated");
        showToast("✏️ Une commande a été mise à jour" + tableInfo + ".", false);
        if (kanbanRefreshFn) kanbanRefreshFn();
      } else if (payload.type === "order_cancelled") {
        playNotificationSound("cancelled");
        showToast("\uD83D\uDEAB Commande annulée par le client" + tableInfo + ".", false);
        var orderId = payload.data && payload.data.id;
        if (orderId && window.MFKanbanRefreshWithCancelAnimation) {
          window.MFKanbanRefreshWithCancelAnimation(orderId);
        } else if (kanbanRefreshFn) {
          kanbanRefreshFn();
        }
      } else if (kanbanRefreshFn) {
        kanbanRefreshFn();
      }
    }

    function poll() {
      if (busy) return;
      busy = true;
      var url = eventsUrl + (cursor === null ? "" : "?depuis=" + encodeURIComponent(cursor));
      fetch(url, { cache: "no-store", headers: { "X-Requested-With": "fetch" } })
        .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
        .then(function (data) {
          failures = 0;
          setLiveStatus(true);
          var firstCall = cursor === null;
          if (!firstCall && Array.isArray(data.events)) data.events.forEach(handle);
          if (typeof data.cursor === "number") cursor = data.cursor;
        })
        .catch(function () {
          failures += 1;
          if (failures >= 2) setLiveStatus(false);
        })
        .then(function () { busy = false; });
    }

    function schedule() {
      clearInterval(timer);
      timer = setInterval(poll, document.hidden ? 10000 : 4000);
    }

    document.addEventListener("visibilitychange", function () {
      if (!document.hidden) poll();
      schedule();
    });

    poll();
    schedule();
  }
'''

BASE_OLD_ROOT = '''data-stream-url="{{ url_for('admin_orders_stream') }}"'''
BASE_NEW_ROOT = '''data-events-url="{{ url_for('admin_orders_events') }}"'''
APPJS_VERSION_RE = re.compile(r"(filename='app\.js'\) }}\?v=)\d+")


# ==========================================================================
# Outils
# ==========================================================================
def read(rel):
    path = os.path.join(ROOT, rel)
    if not os.path.exists(path):
        sys.exit(f"ARRÊT : fichier introuvable : {rel} (lancez le script à la racine du projet).")
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def once(text, old, new, label):
    n = text.count(old)
    if n != 1:
        sys.exit(f"ARRÊT : repère « {label} » trouvé {n} fois (1 attendu). Aucun fichier n'a été modifié.")
    return text.replace(old, new)


def replace_function(text, start_marker, new_block, label):
    """Remplace une fonction JS complète (de « function x() { » jusqu'à son « } » fermant)."""
    if text.count(start_marker) != 1:
        sys.exit(f"ARRÊT : repère « {label} » introuvable ou en double. Aucun fichier n'a été modifié.")
    start = text.index(start_marker)
    i = text.index("{", start)
    depth, in_str, esc = 0, None, False
    while i < len(text):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == in_str:
                in_str = None
        elif ch in "\"'":
            in_str = ch
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                if text[end:end + 1] == "\n":
                    end += 1
                return text[:start] + new_block + text[end:]
        i += 1
    sys.exit(f"ARRÊT : fin de « {label} » introuvable. Aucun fichier n'a été modifié.")


def main():
    if "def admin_orders_events" in read("app.py"):
        print("MF-02 est déjà appliquée : rien à faire.")
        return

    out = {}

    app_py = read("app.py")
    app_py = once(app_py, APP_OLD_PUBLIC, APP_NEW_PUBLIC, "app.py : flux public theme-stream")
    app_py = once(app_py, APP_OLD_STREAM, APP_NEW_STREAM, "app.py : flux /admin/commandes/stream")
    out["app.py"] = app_py

    out["realtime.py"] = REALTIME_PY

    js = read(os.path.join("static", "app.js"))
    if "textEl.textContent = message" not in js:
        sys.exit("ARRÊT : static/app.js ne contient pas la correction MF-01. Installez-la d'abord. "
                 "Aucun fichier n'a été modifié.")
    js = once(js, JS_OLD_TOAST_SIG, JS_NEW_TOAST_SIG, "app.js : showToast")
    js = once(js, JS_OLD_TOAST_BODY, JS_NEW_TOAST_BODY, "app.js : corps de showToast")
    js, n = JS_OLD_WATCHER_RE.subn(lambda m: JS_NEW_WATCHER, js)
    if n != 1:
        sys.exit(f"ARRÊT : repère « app.js : notification « Voir » » trouvé {n} fois (1 attendu). "
                 "Aucun fichier n'a été modifié.")
    js = replace_function(js, JS_LIVE_START, JS_NEW_LIVE, "app.js : initThemeLiveSync")
    out[os.path.join("static", "app.js")] = js

    base = read(os.path.join("templates", "base.html"))
    base = once(base, BASE_OLD_ROOT, BASE_NEW_ROOT, "base.html : adresse du flux")
    for rel in (os.path.join("templates", "base.html"), os.path.join("templates", "signup.html")):
        text = base if rel.endswith("base.html") else read(rel)
        text, n = APPJS_VERSION_RE.subn(r"\g<1>8", text)
        if n != 1:
            sys.exit(f"ARRÊT : version de app.js introuvable dans {rel}. Aucun fichier n'a été modifié.")
        out[rel] = text

    # Tout est vérifié : sauvegarde puis écriture.
    for rel, text in out.items():
        path = os.path.join(ROOT, rel)
        if os.path.exists(path):
            shutil.copy2(path, path + ".avant-mf02")
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        print(f"  modifié : {rel}")
    print("\nMF-02 appliquée. Copies de sécurité : *.avant-mf02 (à ne pas pousser sur Git).")


if __name__ == "__main__":
    main()
