"""
MenuFlash — Retour à TA politique de mot de passe d'origine.

Annule uniquement la partie MF-11 de corriger_lot2.py :
  - 10 à 64 caractères ;
  - UNIQUEMENT des lettres sans accent et des chiffres (aucun espace, aucune
    ponctuation, aucun accent) ;
  - au moins une majuscule, une minuscule et un chiffre ;
  - (inchangé) pas de mot de passe courant, pas de suite évidente, pas l'email
    ni le nom du restaurant, vérification Have I Been Pwned.
Côté navigateur, la liste des règles affichées et le blocage à la frappe des
caractères interdits sont rétablis.

Fonctionne que corriger_lot2.py ait été lancé ou non :
  - déjà lancé  -> les règles d'origine sont remises ;
  - pas encore  -> ce script retire MF-11 de corriger_lot2.py (s'il est dans le même
                   dossier) : le lot appliquera tout le reste sans toucher aux mots de passe.

UTILISATION (à la racine du projet) :  python garder_mon_mot_de_passe.py
Copies : *.avant-mdp
"""
import os
import re
import shutil
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
PAIRS = [('security_forms.py', 'PASSWORD_MIN_LENGTH = 12          # audit MF-11 (ASVS 5.0 : 12 minimum, tous caractères acceptés)\nPASSWORD_MAX_LENGTH = 128', 'PASSWORD_MIN_LENGTH = 10\nPASSWORD_MAX_LENGTH = 64'), ('security_forms.py', '    # Audit MF-11 : longueur seulement. Espaces, accents et ponctuation sont acceptés\n    # (phrases de passe) ; plus de règle « majuscule / chiffre obligatoire ».\n    password = unicodedata.normalize("NFKC", password)\n    errors = []\n    if not (PASSWORD_MIN_LENGTH <= len(password) <= PASSWORD_MAX_LENGTH):\n        errors.append(f"Le mot de passe doit contenir entre {PASSWORD_MIN_LENGTH} et "\n                      f"{PASSWORD_MAX_LENGTH} caractères. Astuce : une phrase de plusieurs mots "\n                      f"est facile à retenir et difficile à deviner.")\n    if any(unicodedata.category(ch)[0] == "C" for ch in password):\n        errors.append("Le mot de passe contient un caractère invisible non autorisé.")\n    if errors:\n        return errors\n', '    errors = []\n    if not (PASSWORD_MIN_LENGTH <= len(password) <= PASSWORD_MAX_LENGTH):\n        errors.append(f"Le mot de passe doit contenir entre {PASSWORD_MIN_LENGTH} et "\n                      f"{PASSWORD_MAX_LENGTH} caractères.")\n    if not re.fullmatch(r"[A-Za-z0-9]*", password):\n        errors.append("Le mot de passe ne doit contenir que des lettres (sans accent) et des "\n                      "chiffres : aucun espace, aucune ponctuation.")\n    if not re.search(r"[A-Z]", password):\n        errors.append("Le mot de passe doit contenir au moins une lettre majuscule.")\n    if not re.search(r"[a-z]", password):\n        errors.append("Le mot de passe doit contenir au moins une lettre minuscule.")\n    if not re.search(r"[0-9]", password):\n        errors.append("Le mot de passe doit contenir au moins un chiffre.")\n    if errors:\n        return errors\n'), ('static/forms_security.js', '  // Audit MF-11 : tous les caractères sont acceptés (espaces, accents, ponctuation).\n  var MIN = 12, MAX = 128;\n  var ALLOWED = /^[^\\u0000-\\u001f\\u007f]*$/;\n  var RULES = [\n    { key: "len",   label: MIN + " caractères minimum (une phrase de plusieurs mots est idéale)",\n      test: function (v) { return v.length >= MIN && v.length <= MAX; } }\n  ];', '  var MIN = 10, MAX = 64;\n  var ALLOWED = /^[A-Za-z0-9]*$/;\n  var RULES = [\n    { key: "len",   label: MIN + " à " + MAX + " caractères",\n      test: function (v) { return v.length >= MIN && v.length <= MAX; } },\n    { key: "upper", label: "Une lettre majuscule", test: function (v) { return /[A-Z]/.test(v); } },\n    { key: "lower", label: "Une lettre minuscule", test: function (v) { return /[a-z]/.test(v); } },\n    { key: "digit", label: "Un chiffre",           test: function (v) { return /[0-9]/.test(v); } },\n    { key: "chars", label: "Lettres sans accent et chiffres uniquement (ni espace, ni ponctuation)",\n      test: function (v) { return v.length > 0 && ALLOWED.test(v); } }\n  ];')]


def patch_lot2_script():
    """Si corriger_lot2.py est présent : il n'appliquera plus jamais MF-11."""
    path = os.path.join(ROOT, "corriger_lot2.py")
    if not os.path.exists(path):
        return
    text = open(path, encoding="utf-8").read()
    old = '    ("MF-11", "Mot de passe conforme ASVS 5.0 (12+ caractères, tous caractères)", MF11),\n'
    if old in text:
        open(path, "w", encoding="utf-8", newline="\n").write(text.replace(old, ""))
        print("  corriger_lot2.py : MF-11 retirée (le lot ne touchera plus aux mots de passe)")


def main():
    patch_lot2_script()
    texts, changed = {}, []
    for rel, lot2_version, original in PAIRS:
        path = os.path.join(ROOT, rel)
        if not os.path.exists(path):
            sys.exit(f"ARRÊT : {rel} introuvable (lancez le script à la racine du projet).")
        text = texts.get(rel) or open(path, encoding="utf-8").read()
        if lot2_version in text:
            text = text.replace(lot2_version, original)
            changed.append(rel)
        elif original not in text:
            sys.exit(f"ARRÊT : {rel} ne contient ni l'ancienne ni la nouvelle règle (modifié à la main ?). "
                     "Aucun fichier n'a été modifié.")
        texts[rel] = text
    if not changed:
        print("Tes règles d'origine sont déjà en place : rien à faire.")
        return
    # Les navigateurs doivent recharger forms_security.js.
    for rel in ("templates/base.html", "templates/signup.html"):
        p = os.path.join(ROOT, rel)
        if os.path.exists(p):
            t = open(p, encoding="utf-8").read()
            texts[rel] = re.sub(r"(forms_security\.js'\) }}\?v=)(\d+)",
                                lambda m: m.group(1) + str(int(m.group(2)) + 1), t)
    for rel, text in texts.items():
        path = os.path.join(ROOT, rel)
        if text != open(path, encoding="utf-8").read():
            shutil.copy2(path, path + ".avant-mdp")
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)
            print(f"  modifié : {rel}")
    print("\nRègles d'origine rétablies. Copies : *.avant-mdp (à ne pas pousser sur Git).")


if __name__ == "__main__":
    main()
