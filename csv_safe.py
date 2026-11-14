"""
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
_DANGEROUS_START = ("=", "+", "-", "@", "\t", "\r", "\uff1d", "\uff0b", "\uff0d", "\uff20")


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
