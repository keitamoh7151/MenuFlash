"""
MenuFlash — Correction de sécurité MF-14 (audit du 13/11/2026).

PROBLÈME
--------
Les exports CSV contenaient tels quels des textes saisis par des clients ou des
restaurateurs. Une note de commande comme  =HYPERLINK("http://site-pirate";"Cliquez")
devenait une FORMULE à l'ouverture dans Excel : lien piégé, et selon la
configuration d'Excel, envoi du contenu d'autres cellules vers l'extérieur.
4 exports concernés : commandes (restaurateur), clients, paiements et santé des
clients (super-admin).

CORRECTION
----------
1. Nouveau fichier csv_safe.py : toute cellule de texte qui commence par
   = + - @ (ou tabulation / retour chariot, ou ces caractères précédés
   d'espaces) est précédée d'une apostrophe. Excel l'affiche alors comme du
   texte, sans l'exécuter (règle OWASP « CSV Injection »).
   Les nombres (montants, quantités) ne sont jamais modifiés.
2. super_admin.py : _csv_response (exports clients, paiements, santé) l'utilise ;
   les exports ne sont plus gardés en cache par le navigateur (emails, téléphones).
3. plans.py : l'export des commandes du restaurateur l'utilise.

UTILISATION (à la racine du projet) :  python corriger_mf14.py
Vérifie tout avant d'écrire ; copies : *.avant-mf14
"""
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))

CSV_SAFE = '''"""
Exports CSV sans risque d'injection de formules (audit MF-14, OWASP « CSV Injection »).

Une cellule de texte qui commence par = + - @ (ou tabulation / retour chariot,
éventuellement précédés d'espaces) serait interprétée comme une FORMULE par
Excel, LibreOffice ou Google Sheets. On la préfixe d'une apostrophe : le tableur
l'affiche comme du texte. Les nombres ne sont jamais modifiés.

Usage :
    from csv_safe import safe_writer
    writer = safe_writer(buf, delimiter=";")     # même interface que csv.writer
"""
import csv
import re

_PLAIN_NUMBER = re.compile(r"[+-]?[0-9]+(?:[.,][0-9]+)?")
_DANGEROUS_START = ("=", "+", "-", "@", "\\t", "\\r", "\\uff1d", "\\uff0b", "\\uff0d", "\\uff20")


def safe_cell(value):
    """Valeur prête pour un CSV. Seules les chaînes de caractères peuvent être modifiées."""
    if not isinstance(value, str) or not value:
        return value
    if value.lstrip(" ").startswith(_DANGEROUS_START):
        # Un simple nombre saisi comme texte (« -500 », « -12,5 ») reste tel quel :
        # aucune formule n'est possible avec uniquement des chiffres.
        if _PLAIN_NUMBER.fullmatch(value):
            return value
        return "'" + value
    return value


class _SafeWriter:
    def __init__(self, writer):
        self._writer = writer

    def writerow(self, row):
        return self._writer.writerow([safe_cell(v) for v in row])

    def writerows(self, rows):
        for row in rows:
            self.writerow(row)


def safe_writer(fileobj, **kwargs):
    """Comme csv.writer, mais chaque cellule passe par safe_cell()."""
    return _SafeWriter(csv.writer(fileobj, **kwargs))
'''

PATCHES = {
    "super_admin.py": [
        ('''    writer = csv.writer(buf, delimiter=";")
    writer.writerow(header)
    for row in rows:
        writer.writerow(row)
    return Response(buf.getvalue(), mimetype="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})''',
         '''    from csv_safe import safe_writer               # audit MF-14 : pas de formule exécutable
    writer = safe_writer(buf, delimiter=";")
    writer.writerow(header)
    for row in rows:
        writer.writerow(row)
    return Response(buf.getvalue(), mimetype="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"',
                             "Cache-Control": "no-store"})''', "_csv_response"),
    ],
    "plans.py": [
        ('''        writer = csv.writer(output, delimiter=";")
        writer.writerow(["Commande", "Date UTC",''',
         '''        from csv_safe import safe_writer           # audit MF-14 : pas de formule exécutable
        writer = safe_writer(output, delimiter=";")
        writer.writerow(["Commande", "Date UTC",''', "export des commandes"),
    ],
}


def main():
    target = os.path.join(ROOT, "csv_safe.py")
    texts = {}
    for name, repls in PATCHES.items():
        path = os.path.join(ROOT, name)
        if not os.path.exists(path):
            sys.exit(f"ARRÊT : {name} introuvable (lancez le script à la racine du projet).")
        text = open(path, encoding="utf-8").read()
        if "from csv_safe import safe_writer" in text:
            continue
        for old, new, label in repls:
            if text.count(old) != 1:
                sys.exit(f"ARRÊT : repère « {label} » introuvable dans {name}. Aucun fichier n'a été modifié.")
            text = text.replace(old, new)
        texts[name] = text
    if not texts and os.path.exists(target):
        print("MF-14 est déjà appliquée : rien à faire.")
        return

    with open(target, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(CSV_SAFE)
    print("  créé    : csv_safe.py")
    for name, text in texts.items():
        path = os.path.join(ROOT, name)
        shutil.copy2(path, path + ".avant-mf14")
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        print(f"  modifié : {name}")
    print("\nMF-14 appliquée. Copies de sécurité : *.avant-mf14 (à ne pas pousser sur Git).")


if __name__ == "__main__":
    main()
