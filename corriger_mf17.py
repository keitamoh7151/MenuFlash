"""
MenuFlash — Correction de sécurité MF-17 (audit du 13/11/2026).

PROBLÈME
--------
Une « bombe de décompression » (ex. PNG uni de 20 000 x 20 000 pixels, moins de
1 Mo sur le disque) passait la limite de 5 Mo, puis était entièrement décodée
en mémoire (plus d'1 Go). Sur Render, le processus unique peut être tué :
tous les restaurants sont coupés. Tout restaurateur inscrit pouvait le faire
(photo de plat ou logo). De plus, l'erreur DecompressionBombError de Pillow
n'était pas interceptée (erreur 500).

CORRECTION (app.py uniquement)
------------------------------
1. Les dimensions sont lues dans l'en-tête AVANT tout décodage : au-delà de
   40 mégapixels, l'image est refusée sans être chargée en mémoire.
2. Photos JPEG (cas des téléphones) : décodage directement en taille réduite
   (mode « draft » de Pillow), donc mémoire minimale même pour une photo de 40 Mpx.
   PNG / WEBP : 25 mégapixels maximum.
3. Les erreurs « bombe » de Pillow sont interceptées : refus propre, jamais d'erreur 500.
4. Message clair au restaurateur quand une photo de plat est refusée (avant,
   le plat était enregistré sans photo, sans explication).

UTILISATION (à la racine du projet, là où se trouve app.py)
-----------------------------------------------------------
    python corriger_mf17.py

Vérifie tout avant d'écrire ; copie de sécurité : app.py.avant-mf17
"""
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(ROOT, "app.py")

R = []

R.append(('''def save_compressed_image(file_storage, restaurant_slug, restaurant_id=None):''',
'''# --------------------------------------------------------------------------
# Audit MF-17 : ouverture sûre des images (anti « bombe de décompression »)
# --------------------------------------------------------------------------
MAX_UPLOAD_PIXELS = 40_000_000      # au-delà : refus, sans rien décoder
MAX_DECODE_PIXELS = 25_000_000      # PNG / WEBP : décodés en taille réelle
Image.MAX_IMAGE_PIXELS = MAX_UPLOAD_PIXELS
IMAGE_REJECTED_MESSAGE = ("Image refusée : format non reconnu ou image trop grande "
                          "(JPG, PNG ou WEBP, 40 mégapixels maximum).")


def open_image_safely(stream, target_side):
    """Ouvre une image téléversée sans risque pour la mémoire du serveur.

    Renvoie l'image chargée, ou None si le fichier est invalide ou trop grand.
    """
    try:
        probe = Image.open(stream)                    # lit seulement l'en-tête
        width, height = probe.size
        if width <= 0 or height <= 0 or width * height > MAX_UPLOAD_PIXELS:
            return None
        probe.verify()
        stream.seek(0)
        img = Image.open(stream)
        if img.format == "JPEG":
            img.draft("RGB", (target_side, target_side))   # décodage directement réduit (jamais sous la taille finale)
        elif width * height > MAX_DECODE_PIXELS:
            return None
        img.load()
        return img
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError,
            Image.DecompressionBombError, Image.DecompressionBombWarning):
        return None


def save_compressed_image(file_storage, restaurant_slug, restaurant_id=None):''', "save_compressed_image"))

R.append(('''    try:
        img = Image.open(file_storage.stream)
        img.verify()
        file_storage.stream.seek(0)
        img = ImageOps.exif_transpose(Image.open(file_storage.stream)).convert("RGB")
        img.info = {}   # retire les informations cachées (position GPS, téléphone, date)
    except (UnidentifiedImageError, OSError, ValueError):
        return None
''', '''    img = open_image_safely(file_storage.stream, MAX_IMAGE_SIDE)   # audit MF-17
    if img is None:
        return None
    try:
        img = ImageOps.exif_transpose(img).convert("RGB")
        img.info = {}   # retire les informations cachées (position GPS, téléphone, date)
    except (OSError, ValueError):
        return None
''', "décodage de la photo"))

R.append(('''    try:
        img = Image.open(file_storage.stream)
        img.verify()
        file_storage.stream.seek(0)
        img = ImageOps.exif_transpose(Image.open(file_storage.stream))
        has_alpha''', '''    img = open_image_safely(file_storage.stream, MAX_LOGO_SIDE)    # audit MF-17
    if img is None:
        return None
    try:
        img = ImageOps.exif_transpose(img)
        has_alpha''', "décodage du logo"))

R.append(('''    if file and file.filename:
        photo_url = save_compressed_image(file, restaurant.slug, restaurant.id)

    last = db.session.query''', '''    if file and file.filename:
        photo_url = save_compressed_image(file, restaurant.slug, restaurant.id)
        if not photo_url:
            flash(IMAGE_REJECTED_MESSAGE + " Le plat est ajouté sans photo.", "error")

    last = db.session.query''', "ajout de plat"))

R.append(('''        photo_url = save_compressed_image(file, restaurant.slug, restaurant.id)
        if photo_url:
            delete_stored_image(item.photo_url)
            item.photo_url = photo_url
            changes.append("photo mise à jour")
''', '''        photo_url = save_compressed_image(file, restaurant.slug, restaurant.id)
        if photo_url:
            delete_stored_image(item.photo_url)
            item.photo_url = photo_url
            changes.append("photo mise à jour")
        else:
            flash(IMAGE_REJECTED_MESSAGE + " L'ancienne photo est conservée.", "error")
''', "modification de plat"))

R.append(('''        flash("Format d'image non reconnu. Utilisez un fichier JPG, PNG ou WEBP.", "error")
        return redirect(url_for("admin_appearance"))''', '''        flash(IMAGE_REJECTED_MESSAGE, "error")
        return redirect(url_for("admin_appearance"))''', "message du logo"))


def main():
    if not os.path.exists(PATH):
        sys.exit("ARRÊT : app.py introuvable (lancez le script à la racine du projet).")
    text = open(PATH, encoding="utf-8").read()
    if "def open_image_safely" in text:
        print("MF-17 est déjà appliquée : rien à faire.")
        return
    for old, new, label in R:
        if text.count(old) != 1:
            sys.exit(f"ARRÊT : repère « {label} » introuvable ou en double. Aucun fichier n'a été modifié.")
        text = text.replace(old, new)
    shutil.copy2(PATH, PATH + ".avant-mf17")
    with open(PATH, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    print("  modifié : app.py")
    print("\nMF-17 appliquée. Copie de sécurité : app.py.avant-mf17 (à ne pas pousser sur Git).")


if __name__ == "__main__":
    main()
