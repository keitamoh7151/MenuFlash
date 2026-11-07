"""
Branche la sécurité des QR de table (qr_security.py) dans votre projet.

Usage, dans le dossier du projet (là où se trouve app.py) :

    python ajouter_securite_qr.py

Il fait 5 retouches précises et ne touche à rien d'autre (il met aussi à jour la version précédente de ce
correctif, si vous l'aviez déjà installée) :
  1. app.py                       2 lignes de branchement, avant « from hardening import init_hardening »
  2. templates/admin_qrcodes.html bouton « Nouveau code » par table + une phrase d'explication
  3. templates/order_status.html  le bouton « Retour au menu » d'une commande de table utilise le NOM de la table
  4. order_edit.py                « Ajouter d'autres plats » (si installé) utilise le NOM de la table
  5. qr_compact.py + admin_qrcodes.html   (si installés) l'autocollant « Mini » passe de 32 x 42 à 34 x 44 mm :
                                  le lien d'une table est plus long (nom + code), donc son QR plus dense ; sans
                                  cela « Mini » serait agrandi en « Petit » à chaque impression

- Sans danger : relancé, il répond « déjà fait » et ne change rien.
- Garde le style de fin de ligne de chaque fichier (Windows ou Linux).
- Copie de sécurité de chaque fichier modifié : <fichier>.avant_qr_securite
- Si une retouche ne trouve pas son repère (fichier modifié à la main), il ne touche pas au fichier et vous
  affiche exactement quoi ajouter.
"""
import os
import re
import shutil
import sys

SUFFIX = ".avant_qr_securite"

APP_OLD_ANCHORS = ("from hardening import init_hardening", 'if __name__ == "__main__":')
APP_BLOCK = [
    "# --- QR codes de table sécurisés : l'adresse ne peut plus servir à changer de table (qr_security.py) ---",
    "from qr_security import init_qr_security",
    "init_qr_security(app, owner_required=owner_required, active_restaurant=active_restaurant,",
    "                 log_audit=log_audit)",
    "",
]

REGEN_FORM = """{% if mf_qr_security is defined %}
<form method="post" action="{{ url_for('admin_qrcode_regenerate', qr_id=qr.id) }}"
      class="mf-confirm d-inline" data-confirm-message="Générer un nouveau code pour « {{ qr.label }} » ? L'ancien autocollant ne fonctionnera plus : il faudra le réimprimer.">
  <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
  <button type="submit" class="btn btn-outline-secondary btn-sm" title="Nouveau code (si ce QR a été photographié ou partagé)" aria-label="Nouveau code pour {{ qr.label }}"><i class="bi bi-arrow-repeat"></i></button>
</form>
{% endif %}"""

INTRO_TEXT = ("Chaque QR contient un <strong>code secret</strong> propre à sa table, invisible pour le client : "
              "l'adresse affichée sur son téléphone ne contient que le <strong>nom</strong> de la table, et la modifier "
              "ne permet pas de changer de table. Si un QR a été photographié ou partagé, le bouton "
              "<i class=\"bi bi-arrow-repeat\"></i> génère un nouveau code (il faudra réimprimer l'autocollant). "
              "« Copier » donne le contenu complet du QR : ne le partagez pas aux clients.")
INTRO = """{% if mf_qr_security is defined %}
      <p class="small text-secondary">""" + INTRO_TEXT + """</p>
      {% endif %}"""
INTRO_V1_RE = re.compile(r'<p class="small text-secondary">Chaque QR contient un <strong>code secret</strong> propre à sa table : modifier.*?</p>', re.S)

ORDER_EDIT_IMPORT_OLD = "from models import MenuItem, Order, OrderItem, db"
ORDER_EDIT_IMPORT_NEW = "from models import MenuItem, Order, OrderItem, QRCode, db"
ORDER_EDIT_ORIGINAL = '''        label = None if is_takeaway(order.id) else (order.table_label or None)
        params = {"slug": restaurant.slug, "ajouter": order.id}
        if label:
            params["t"] = label
        return url_for("public_menu", **params)'''
ORDER_EDIT_V1 = '''        params = {"slug": restaurant.slug, "ajouter": order.id}
        if not is_takeaway(order.id):
            # Le lien d'une table porte son code secret (qr_security.py) : on donne le numéro du QR, que le
            # serveur convertit en « nom-code ». Sans numéro (ancienne commande) : menu général.
            qr_id = getattr(order, "qr_code_id", None)
            if not qr_id and order.table_label:
                row = QRCode.query.filter_by(restaurant_id=restaurant.id, label=order.table_label).first()
                qr_id = row.id if row else None
            if qr_id:
                params["qr"] = qr_id
        return url_for("public_menu", **params)'''
ORDER_EDIT_V2 = '''        params = {"slug": restaurant.slug, "ajouter": order.id}
        if not is_takeaway(order.id):
            # Lien destiné au CLIENT : la table n'y figure que par son NOM (qr_security.py), jamais par un code.
            # Le droit de commander à cette table vient de son QR scanné. Sans QR (ancienne commande) : menu général.
            qr_id = getattr(order, "qr_code_id", None)
            if not qr_id and order.table_label:
                row = QRCode.query.filter_by(restaurant_id=restaurant.id, label=order.table_label).first()
                qr_id = row.id if row else None
            if qr_id:
                try:
                    from qr_security import qr_name_for
                    params["qr"] = qr_name_for(qr_id)
                except Exception:
                    pass
        return url_for("public_menu", **params)'''

STATUS_ORIGINAL = "url_for('public_menu', slug=restaurant.slug, t=order.table_label) if (order.table_label and not tk)"
STATUS_V1 = "url_for('public_menu', slug=restaurant.slug, qr=order.qr_code_id) if (order.qr_code_id and not tk)"
STATUS_V2 = "url_for('public_menu', slug=restaurant.slug, qr=qr_slug(order.qr_code_id)) if (order.qr_code_id and not tk)"

MINI_OLD, MINI_NEW = '"mini": (32, 42, 24),', '"mini": (34, 44, 26),'
MINI_DOC_OLD = "mini       32 x 42 mm, QR 24 mm"
MINI_DOC_NEW = "mini       34 x 44 mm, QR 26 mm"
MINI_LABEL_OLD, MINI_LABEL_NEW = "Mini : 32 × 42 mm", "Mini : 34 × 44 mm"

results = []


def read(path):
    raw = open(path, "rb").read().decode("utf-8")
    eol = "\r\n" if "\r\n" in raw else "\n"
    return raw.replace("\r\n", "\n"), eol


def write(path, text, eol):
    if not os.path.exists(path + SUFFIX):
        shutil.copyfile(path, path + SUFFIX)
    open(path, "wb").write(text.replace("\n", eol).encode("utf-8"))


def report(name, status, manual=None):
    results.append((name, status))
    print(f"{name} : {status}")
    if manual:
        print(manual)


def patch_app():
    path = "app.py"
    if not os.path.exists(path):
        return report(path, "INTROUVABLE (lancez le script dans le dossier du projet)")
    text, eol = read(path)
    if "init_qr_security" in text:
        return report(path, "déjà fait")
    lines = text.split("\n")
    index = None
    for anchor in APP_OLD_ANCHORS:
        index = next((i for i, l in enumerate(lines) if l.strip() == anchor), None)
        if index is not None:
            break
    if index is None:
        return report(path, "REPÈRE INTROUVABLE", "Ajoutez à la main, en bas de app.py :\n" + "\n".join(APP_BLOCK))
    lines[index:index] = APP_BLOCK
    write(path, "\n".join(lines), eol)
    report(path, "branchement ajouté")


def patch_admin_qrcodes():
    path = os.path.join("templates", "admin_qrcodes.html")
    if not os.path.exists(path):
        return report(path, "INTROUVABLE")
    text, eol = read(path)
    if "admin_qrcode_regenerate" in text:
        if INTRO_TEXT in text:
            return report(path, "déjà fait")
        new_text, n = INTRO_V1_RE.subn('<p class="small text-secondary">' + INTRO_TEXT + "</p>", text, count=1)
        if n:
            write(path, new_text, eol)
            return report(path, "explication mise à jour (version précédente)")
        return report(path, "bouton déjà présent (explication modifiée à la main : laissée telle quelle)")
    lines = text.split("\n")
    form_idx = next((i for i, l in enumerate(lines) if "url_for('admin_qrcodes_delete'" in l and "<form" in l), None)
    head_idx = next((i for i, l in enumerate(lines) if "Codes QR par table</h2>" in l), None)
    if form_idx is None:
        return report(path, "REPÈRE INTROUVABLE (formulaire de suppression)",
                      "Le bouton « Nouveau code » n'est pas affiché. La sécurité fonctionne quand même. Pour l'ajouter,\n"
                      "demandez-moi le bloc à insérer avant le formulaire de suppression d'un QR.")
    indent = lines[form_idx][:len(lines[form_idx]) - len(lines[form_idx].lstrip())]
    block = [indent + l if l else l for l in REGEN_FORM.split("\n")]
    lines[form_idx:form_idx] = block                                   # d'abord la ligne la plus basse
    if head_idx is not None and head_idx < form_idx:
        hindent = lines[head_idx][:len(lines[head_idx]) - len(lines[head_idx].lstrip())]
        lines[head_idx + 1:head_idx + 1] = [hindent + l if l else l for l in INTRO.split("\n")]
    write(path, "\n".join(lines), eol)
    report(path, "bouton « Nouveau code » ajouté")


def patch_order_status():
    path = os.path.join("templates", "order_status.html")
    if not os.path.exists(path):
        return report(path, "INTROUVABLE")
    text, eol = read(path)
    if STATUS_V2 in text:
        return report(path, "déjà fait")
    for old, label in ((STATUS_V1, "mis à jour (version précédente)"), (STATUS_ORIGINAL, "lien « Retour au menu » mis à jour")):
        if old in text:
            write(path, text.replace(old, STATUS_V2, 1), eol)
            return report(path, label)
    report(path, "REPÈRE INTROUVABLE",
           "Dans order_status.html, le lien « Retour au menu » doit utiliser le NOM de la table :\n  " + STATUS_V2)


def patch_order_edit():
    path = "order_edit.py"
    if not os.path.exists(path):
        return report(path, "absent (« Modifier la commande » non installé) : rien à faire")
    text, eol = read(path)
    if "qr_name_for" in text:
        return report(path, "déjà fait")
    for old, label in ((ORDER_EDIT_V1, "mis à jour (version précédente)"),
                       (ORDER_EDIT_ORIGINAL, "« Ajouter d'autres plats » utilise le nom de la table")):
        if old in text:
            text = text.replace(old, ORDER_EDIT_V2, 1)
            if ORDER_EDIT_IMPORT_OLD in text:
                text = text.replace(ORDER_EDIT_IMPORT_OLD, ORDER_EDIT_IMPORT_NEW, 1)
            write(path, text, eol)
            return report(path, label)
    report(path, "REPÈRE INTROUVABLE",
           "Fichier modifié à la main : « Ajouter d'autres plats » ouvrira le menu général pour une commande de table.\n"
           "Dites-le-moi et j'adapte la retouche à votre version.")


def patch_mini():
    path = "qr_compact.py"
    if not os.path.exists(path):
        return report(path, "absent (autocollants non installés) : rien à faire")
    text, eol = read(path)
    if MINI_NEW in text:
        status = "déjà fait"
    elif MINI_OLD in text:
        write(path, text.replace(MINI_OLD, MINI_NEW, 1).replace(MINI_DOC_OLD, MINI_DOC_NEW, 1), eol)
        status = "format « Mini » : 34 x 44 mm"
    else:
        return report(path, "REPÈRE INTROUVABLE (taille « mini » modifiée à la main : sans gravité, « Mini » sera "
                            "simplement agrandi en « Petit » quand le lien est long)")
    tpl = os.path.join("templates", "admin_qrcodes.html")
    if os.path.exists(tpl):
        ttext, teol = read(tpl)
        if MINI_LABEL_OLD in ttext:
            write(tpl, ttext.replace(MINI_LABEL_OLD, MINI_LABEL_NEW), teol)
    report(path, status)


if __name__ == "__main__":
    for step in (patch_app, patch_admin_qrcodes, patch_order_status, patch_order_edit, patch_mini):
        step()
    failed = [n for n, s in results if "INTROUVABLE" in s and "absent" not in s and "qr_compact" not in n]
    sys.exit(1 if failed else 0)
