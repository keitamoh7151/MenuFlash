"""
MenuFlash — Affichage du lien « Voir » dans la notification « Nouvelle commande ».

Problème : le texte et le lien partageaient la même ligne flexible ; sur une
notification étroite, « Voir » était écrasé et coupé (« Voi / r »).
Correction : le texte prend la place disponible et passe à la ligne si besoin ;
« Voir » devient un petit bouton qui ne se coupe jamais. Le tiret « — » est retiré.

Utilisation (à la racine du projet) :  python corriger_toast_voir.py
Vérifie tout avant d'écrire ; copie de sécurité : *.avant-voir
"""
import os, re, shutil, sys

ROOT = os.path.dirname(os.path.abspath(__file__))
JS = os.path.join(ROOT, "static", "app.js")
TEMPLATES = [os.path.join(ROOT, "templates", n) for n in ("base.html", "signup.html")]

OLD_TEXT = '''    textEl.textContent = message == null ? "" : String(message);
'''
NEW_TEXT = '''    textEl.textContent = message == null ? "" : String(message);
    textEl.style.flex = "1 1 auto";
    textEl.style.minWidth = "0";
'''
OLD_LINK = '''      linkEl.className = "fw-bold ms-1";
'''
NEW_LINK = '''      linkEl.className = "btn btn-sm btn-outline-dark fw-bold py-0 px-2 flex-shrink-0 text-nowrap";
'''
OLD_MSG = '''(data.latest_table || "") + " —",'''
NEW_MSG = '''(data.latest_table || ""),'''
VERSION_RE = re.compile(r"(filename='app\.js'\) }}\?v=)(\d+)")


def main():
    js = open(JS, encoding="utf-8").read()
    if NEW_LINK in js:
        print("Déjà appliqué : rien à faire.")
        return
    for old, label in ((OLD_TEXT, "texte du message"), (OLD_LINK, "lien Voir"), (OLD_MSG, "message Nouvelle commande")):
        if js.count(old) != 1:
            sys.exit(f"ARRÊT : repère « {label} » introuvable (MF-01 et MF-02 doivent être installées). Rien n'a été modifié.")
    js = js.replace(OLD_TEXT, NEW_TEXT).replace(OLD_LINK, NEW_LINK).replace(OLD_MSG, NEW_MSG)

    out = {JS: js}
    for path in TEMPLATES:
        text = open(path, encoding="utf-8").read()
        text, n = VERSION_RE.subn(lambda m: m.group(1) + str(int(m.group(2)) + 1), text)
        if n != 1:
            sys.exit(f"ARRÊT : version de app.js introuvable dans {os.path.basename(path)}. Rien n'a été modifié.")
        out[path] = text

    for path, text in out.items():
        shutil.copy2(path, path + ".avant-voir")
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        print("  modifié :", os.path.relpath(path, ROOT))
    print("\nCorrection appliquée.")


if __name__ == "__main__":
    main()
