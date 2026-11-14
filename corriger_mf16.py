"""
MenuFlash — Correction de sécurité MF-16 (audit du 13/11/2026).

PROBLÈMES
---------
1. Les copies locales (table « backups » de la base) étaient stockées EN CLAIR :
   chaque copie dupliquait les hachages de mots de passe, les emails et les
   téléphones des clients, lisibles par quiconque accède à la base.
2. Sans BACKUP_ENCRYPTION_KEY, le panel permettait de télécharger une copie
   complète NON chiffrée de toute la plateforme.
3. La lecture d'une sauvegarde décompressait le fichier sans limite : un fichier
   piégé (bombe gzip) pouvait saturer la mémoire du serveur pendant un test ou
   une restauration.

CORRECTION (backups.py uniquement)
----------------------------------
1. Les NOUVELLES copies locales sont chiffrées (Fernet) dès leur création quand
   BACKUP_ENCRYPTION_KEY est définie. Les anciennes copies en clair restent
   lisibles et disparaissent d'elles-mêmes (7 copies conservées).
2. Envoi hors site et téléchargement : une copie déjà chiffrée n'est pas
   chiffrée une deuxième fois (fichier .mfbak, comme avant).
3. Sur Render, le téléchargement d'une copie NON chiffrée est refusé (message
   clair). En local, il reste possible.
4. Décompression limitée à 500 Mo (réglable : BACKUP_MAX_UNCOMPRESSED_MB).
5. Au démarrage sur Render : erreur dans les logs si BACKUP_ENCRYPTION_KEY manque.

Rien ne change dans le panel : tests, restaurations et copies hors site
fonctionnent comme avant, y compris avec les anciennes sauvegardes.

UTILISATION (à la racine du projet) :  python corriger_mf16.py
Vérifie tout avant d'écrire ; copie : backups.py.avant-mf16
"""
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(ROOT, "backups.py")

R = [
('''def encrypt(blob):
    return _fernet().encrypt(blob)
''',
'''def encrypt(blob):
    return _fernet().encrypt(blob)


def is_encrypted(blob):
    """Audit MF-16 : la copie est-elle déjà chiffrée (Fernet) ?"""
    return bytes(blob[:6]) == FERNET_MAGIC


def encrypted_copy(blob):
    """Copie chiffrée, sans jamais chiffrer deux fois."""
    return bytes(blob) if is_encrypted(blob) else encrypt(blob)


# Audit MF-16 : décompression bornée (anti « bombe gzip »).
MAX_UNCOMPRESSED_BYTES = _env_int("BACKUP_MAX_UNCOMPRESSED_MB", 500, 10, 4000) * 1024 * 1024


def _bounded_gunzip(blob):
    import io as _io
    with gzip.GzipFile(fileobj=_io.BytesIO(blob)) as fh:
        out = fh.read(MAX_UNCOMPRESSED_BYTES + 1)
    if len(out) > MAX_UNCOMPRESSED_BYTES:
        raise BackupError(f"Sauvegarde refusée : plus de {MAX_UNCOMPRESSED_BYTES // (1024 * 1024)} Mo "
                          "une fois décompressée (fichier piégé ou trop volumineux).")
    return out
''', "fonction encrypt"),

('''        data = json.loads(gzip.decompress(to_gzip_bytes(raw)).decode("utf-8"))''',
'''        data = json.loads(_bounded_gunzip(to_gzip_bytes(raw)).decode("utf-8"))''', "décompression"),

('''    blob, ntables, nrows = build_dump()
    backup = Backup(kind=kind,''',
'''    blob, ntables, nrows = build_dump()
    if encryption_configured():
        blob = encrypt(blob)          # audit MF-16 : la copie locale n'est jamais stockée en clair
    backup = Backup(kind=kind,''', "création de la copie"),

('''            Bucket=_bucket(), Key=key, Body=encrypt(backup.data),''',
'''            Bucket=_bucket(), Key=key, Body=encrypted_copy(backup.data),''', "envoi hors site"),

('''        if encryption_configured():
            try:
                body, name = encrypt(b.data), f"menuflash_{stamp}_{b.id}.mfbak"
            except BackupError as exc:
                flash(str(exc), "error")
                return _back()
        else:''',
'''        if is_encrypted(b.data) or encryption_configured():
            try:
                body, name = encrypted_copy(b.data), f"menuflash_{stamp}_{b.id}.mfbak"
            except BackupError as exc:
                flash(str(exc), "error")
                return _back()
        elif os.environ.get("RENDER"):
            # Audit MF-16 : jamais de copie complète en clair depuis la production.
            audit("backup_download_refused", f"n°{b.id} : chiffrement non configuré", success=False)
            flash("Téléchargement refusé : définissez d'abord BACKUP_ENCRYPTION_KEY dans "
                  "Render > Environment (la sauvegarde contient les données de tous les clients).", "error")
            return _back()
        else:''', "téléchargement"),

('''    with app.app_context():
        try:
            Backup.__table__.create(db.engine, checkfirst=True)''',
'''    if os.environ.get("RENDER") and not encryption_configured():
        logger.error("BACKUP_ENCRYPTION_KEY n'est pas définie : copies locales EN CLAIR, aucune copie "
                     "hors site, téléchargement refusé. Ajoutez-la dans Render > Environment.")

    with app.app_context():
        try:
            Backup.__table__.create(db.engine, checkfirst=True)''', "démarrage"),
]


def main():
    if not os.path.exists(PATH):
        sys.exit("ARRÊT : backups.py introuvable (lancez le script à la racine du projet).")
    text = open(PATH, encoding="utf-8").read()
    if "def is_encrypted(blob)" in text:
        print("MF-16 est déjà appliquée : rien à faire.")
        return
    for old, new, label in R:
        if text.count(old) != 1:
            sys.exit(f"ARRÊT : repère « {label} » introuvable dans backups.py. Aucun fichier n'a été modifié.")
        text = text.replace(old, new)
    shutil.copy2(PATH, PATH + ".avant-mf16")
    with open(PATH, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    print("  modifié : backups.py")
    print("\nMF-16 appliquée. Copie de sécurité : backups.py.avant-mf16 (à ne pas pousser sur Git).")


if __name__ == "__main__":
    main()
