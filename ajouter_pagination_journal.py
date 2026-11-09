"""
Pagination des journaux de sécurité (restaurant : /admin/journal-securite, super-admin : /super-admin/journal-securite).

Avant : les 200 (restaurant) ou 300 (super-admin) derniers événements, sans pouvoir remonter plus loin.
Après : 50 événements par page, tout l'historique accessible, avec « Précédent / 1 2 3 … / Suivant ».

Usage, dans le dossier du projet (là où se trouve app.py) :

    python ajouter_pagination_journal.py

Retouches précises :
  - app.py : une fonction paginate_audit() et les deux routes utilisent la pagination (plus de limite à 200 / 300) ;
  - templates/admin_security_log.html et templates/super_admin_security_log.html : compteur + barre de pages ;
  - templates/_pagination.html : NOUVEAU fichier (créé par ce script).

Sécurité et discrétion
  - Le numéro de page est lu avec un contrôle strict : « abc », « 0 », « -5 », « 99999 », « 1e9 » ne produisent ni erreur
    ni message ; la page est simplement ramenée dans les limites (1 à dernière page).
  - Les liens de la barre ne contiennent que ?page=<nombre>.
  - Le journal du restaurant reste filtré par restaurant_id (un gestionnaire ne voit jamais les événements d'un autre).
  - Sur téléphone, la barre est compacte : « Précédent · Page 3 / 12 · Suivant ».

- Sans danger : relancé, il répond « déjà fait ». Garde le style de fin de ligne. Copie : <fichier>.avant_pagination
- Repère introuvable (fichier modifié à la main) : il ne touche pas au fichier et vous dit quoi faire.
"""
import os
import re
import shutil
import sys

SUFFIX = ".avant_pagination"
PER_PAGE = 50

HELPER = '''AUDIT_PAGE_SIZE = 50


def paginate_audit(query, per_page=AUDIT_PAGE_SIZE):
    """Pagination du journal : renvoie (lignes, infos). Un numéro de page invalide est ramené dans les limites, sans erreur."""
    raw = (request.args.get("page") or "1").strip()
    page = int(raw) if (raw.isascii() and raw.isdigit() and len(raw) <= 7) else 1
    total = query.order_by(None).count()
    pages = max(1, -(-total // per_page))
    page = max(1, min(page, pages))
    rows = (query.order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
            .offset((page - 1) * per_page).limit(per_page).all())
    start = (page - 1) * per_page + 1 if total else 0
    return rows, {"page": page, "pages": pages, "total": total, "per_page": per_page,
                  "start": start, "end": start + len(rows) - 1 if rows else 0}


'''

OWNER_OLD = re.compile(
    r'    logs = \(AuditLog\.query\n\s+\.filter_by\(restaurant_id=restaurant\.id\)\n\s+\.order_by\(AuditLog\.created_at\.desc\(\)\)\n'
    r'\s+\.limit\(200\)\n\s+\.all\(\)\)\n'
    r'    return render_template\("admin_security_log\.html", restaurant=restaurant, logs=logs\)')
OWNER_NEW = ('    logs, pg = paginate_audit(AuditLog.query.filter_by(restaurant_id=restaurant.id))\n'
             '    return render_template("admin_security_log.html", restaurant=restaurant, logs=logs, pg=pg)')
SA_OLD = re.compile(
    r'    logs = AuditLog\.query\.order_by\(AuditLog\.created_at\.desc\(\)\)\.limit\(300\)\.all\(\)\n'
    r'    return render_template\("super_admin_security_log\.html", logs=logs\)')
SA_NEW = ('    logs, pg = paginate_audit(AuditLog.query)\n'
          '    return render_template("super_admin_security_log.html", logs=logs, pg=pg)')

PARTIAL = '''{# Barre de pages (journaux). Reçoit « pg » : page, pages, total, per_page, start, end. #}
{% if pg and pg.pages > 1 %}
<style>
  .mf-pager { display: flex; flex-direction: column; align-items: center; gap: 10px; margin-top: 18px; }
  .mf-pager-count { font-size: .82rem; color: var(--mf-ink-soft); }
  .mf-pager-list { display: flex; flex-wrap: wrap; justify-content: center; align-items: center; gap: 6px; margin: 0; padding: 0; list-style: none; }
  .mf-pager-list a, .mf-pager-list span.mf-pager-item {
    display: inline-flex; align-items: center; justify-content: center; min-width: 40px; height: 40px; padding: 0 14px;
    border-radius: 999px; border: 1px solid var(--mf-border); background: #fff; color: var(--mf-ink);
    font-weight: 700; font-size: .9rem; text-decoration: none;
  }
  .mf-pager-list a:hover { background: var(--mf-accent-light); border-color: var(--mf-accent); color: var(--mf-ink); }
  .mf-pager-list span.mf-pager-item.is-current { background: var(--mf-accent); border-color: var(--mf-accent); color: #1E140C; }
  .mf-pager-list span.mf-pager-item.is-disabled { opacity: .4; pointer-events: none; }
  .mf-pager-list span.mf-pager-item.is-gap { border: 0; background: transparent; min-width: 22px; padding: 0; color: var(--mf-ink-soft); }
  .mf-pager-compact { font-weight: 700; font-size: .9rem; color: var(--mf-ink); padding: 0 6px; }
</style>
{% set p = pg.page %}{% set n = pg.pages %}
<nav class="mf-pager" aria-label="Pages du journal">
  <div class="mf-pager-count">{{ pg.start }}–{{ pg.end }} sur {{ pg.total }} événements</div>

  {# Téléphone : Précédent · Page x / n · Suivant #}
  <ul class="mf-pager-list d-flex d-sm-none">
    <li>{% if p > 1 %}<a href="{{ url_for(request.endpoint, page=p-1) }}" rel="prev" aria-label="Page précédente"><i class="bi bi-chevron-left"></i></a>
        {% else %}<span class="mf-pager-item is-disabled"><i class="bi bi-chevron-left"></i></span>{% endif %}</li>
    <li class="mf-pager-compact">Page {{ p }} / {{ n }}</li>
    <li>{% if p < n %}<a href="{{ url_for(request.endpoint, page=p+1) }}" rel="next" aria-label="Page suivante"><i class="bi bi-chevron-right"></i></a>
        {% else %}<span class="mf-pager-item is-disabled"><i class="bi bi-chevron-right"></i></span>{% endif %}</li>
  </ul>

  {# Ordinateur : Précédent · 1 … 4 5 6 … 20 · Suivant #}
  <ul class="mf-pager-list d-none d-sm-flex">
    <li>{% if p > 1 %}<a href="{{ url_for(request.endpoint, page=p-1) }}" rel="prev"><i class="bi bi-chevron-left me-1"></i>Précédent</a>
        {% else %}<span class="mf-pager-item is-disabled"><i class="bi bi-chevron-left me-1"></i>Précédent</span>{% endif %}</li>
    {% set shown = [] %}
    {% for i in range(1, n + 1) %}
      {% if i == 1 or i == n or (i >= p - 2 and i <= p + 2) %}
        {% if shown and i - shown[-1] > 1 %}<li><span class="mf-pager-item is-gap">…</span></li>{% endif %}
        {% set _ = shown.append(i) %}
        <li>{% if i == p %}<span class="mf-pager-item is-current" aria-current="page">{{ i }}</span>
            {% else %}<a href="{{ url_for(request.endpoint, page=i) }}">{{ i }}</a>{% endif %}</li>
      {% endif %}
    {% endfor %}
    <li>{% if p < n %}<a href="{{ url_for(request.endpoint, page=p+1) }}" rel="next">Suivant<i class="bi bi-chevron-right ms-1"></i></a>
        {% else %}<span class="mf-pager-item is-disabled">Suivant<i class="bi bi-chevron-right ms-1"></i></span>{% endif %}</li>
  </ul>
</nav>
{% elif pg and pg.total %}
<div class="mf-pager"><div class="mf-pager-count" style="font-size:.82rem;color:var(--mf-ink-soft);text-align:center;margin-top:14px">{{ pg.total }} événement{{ 's' if pg.total > 1 }}</div></div>
{% endif %}
'''
results = []


def read(path):
    raw = open(path, "rb").read().decode("utf-8")
    return raw.replace("\r\n", "\n"), ("\r\n" if "\r\n" in raw else "\n")


def write(path, text, eol):
    if os.path.exists(path) and not os.path.exists(path + SUFFIX):
        shutil.copyfile(path, path + SUFFIX)
    open(path, "wb").write(text.replace("\n", eol).encode("utf-8"))


def report(path, status, help_text=""):
    results.append((path, status))
    print(f"{path} : {status}")
    if help_text:
        print("   " + help_text.replace("\n", "\n   "))


def patch_app():
    path = "app.py"
    text, eol = read(path)
    original, missing = text, []
    if "def paginate_audit" not in text:
        anchor = '@app.route("/admin/journal-securite")'
        if anchor in text:
            text = text.replace(anchor, HELPER + anchor, 1)
        else:
            missing.append("route /admin/journal-securite")
    if "paginate_audit(AuditLog.query.filter_by" not in text:
        new, n = OWNER_OLD.subn(lambda m: OWNER_NEW, text, count=1)
        if n:
            text = new
        else:
            missing.append("requête du journal restaurant (limit 200)")
    if "paginate_audit(AuditLog.query)" not in text:
        new, n = SA_OLD.subn(lambda m: SA_NEW, text, count=1)
        if n:
            text = new
        else:
            missing.append("requête du journal super-admin (limit 300)")
    if missing:
        return report(path, "REPÈRE INTROUVABLE (" + ", ".join(missing) + ") : fichier non modifié")
    if text == original:
        return report(path, "déjà fait")
    write(path, text, eol)
    report(path, "modifié")


def patch_template(name, old_phrase):
    path = os.path.join("templates", name)
    if not os.path.exists(path):
        return report(path, "INTROUVABLE")
    text, eol = read(path)
    original, missing = text, []
    if "_pagination.html" not in text:
        new, n = re.subn(r'(\n  </div>\n)(</div>\n\{% endblock %\})', lambda m: m.group(1) + '  {% include "_pagination.html" %}\n' + m.group(2),
                         text, count=1)
        if n:
            text = new
        else:
            missing.append("fin de page")
    if old_phrase in text:
        text = text.replace(old_phrase, "Affichage par pages de {{ pg.per_page }} événements, du plus récent au plus ancien.", 1)
    if missing:
        return report(path, "REPÈRE INTROUVABLE (" + ", ".join(missing) + ") : fichier non modifié",
                      'Ajoutez à la main, juste avant le dernier </div> de la page :  {% include "_pagination.html" %}')
    if text == original:
        return report(path, "déjà fait")
    write(path, text, eol)
    report(path, "modifié")


def create_partial():
    path = os.path.join("templates", "_pagination.html")
    existed = os.path.exists(path)
    if existed and read(path)[0] == PARTIAL:
        return report(path, "déjà fait")
    write(path, PARTIAL, "\n")
    report(path, "mis à jour" if existed else "créé")


def main():
    if not os.path.exists("app.py"):
        print("app.py introuvable : lancez le script dans le dossier du projet.")
        return 1
    create_partial()
    patch_app()
    patch_template("admin_security_log.html", "Les 200 derniers événements sont affichés.")
    patch_template("super_admin_security_log.html", "Les 300 derniers événements sont affichés.")
    bad = [p for p, s in results if "INTROUVABLE" in s]
    print("\nTerminé." if not bad else "\nÀ vérifier : " + ", ".join(bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
