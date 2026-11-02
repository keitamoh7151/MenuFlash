"""
MenuFlash Mini — Numéros de téléphone validés selon le pays.

Basé sur « phonenumbers », le portage Python de la librairie libphonenumber de
Google (celle d'Android et de WhatsApp). Elle connaît le format exact de chaque
pays : longueur, préfixes mobiles et fixes, indicatif.

    pip install phonenumbers

Règles appliquées
-----------------
- Le numéro doit être VALIDE pour le pays sélectionné. Un numéro canadien est
  refusé si le pays choisi est le Mali, et inversement.
- Seuls les numéros fixes et mobiles sont acceptés : pas de numéros surtaxés,
  courts ou spéciaux.
- Le numéro est enregistré au format international lisible, par exemple
  « +223 76 12 34 56 ». Ce format s'affiche tel quel, fonctionne pour les liens
  tel: et pour WhatsApp, et il est identique quelle que soit la façon dont
  l'utilisateur l'a saisi (espaces, tirets, 00223…, +223…).
"""
import phonenumbers
from phonenumbers import NumberParseException, PhoneNumberFormat, PhoneNumberType

DEFAULT_COUNTRY = "ML"
MAX_RAW_LENGTH = 30

# Pays proposés dans le sélecteur, dans l'ordre d'affichage.
_COUNTRY_NAMES = [
    ("ML", "Mali"), ("SN", "Sénégal"), ("CI", "Côte d'Ivoire"), ("BF", "Burkina Faso"),
    ("GN", "Guinée"), ("NE", "Niger"), ("MR", "Mauritanie"), ("BJ", "Bénin"),
    ("TG", "Togo"), ("CM", "Cameroun"), ("GA", "Gabon"), ("CG", "Congo"),
    ("CD", "RD Congo"), ("TD", "Tchad"), ("GM", "Gambie"), ("GH", "Ghana"),
    ("NG", "Nigeria"), ("MA", "Maroc"), ("DZ", "Algérie"), ("TN", "Tunisie"),
    ("FR", "France"), ("BE", "Belgique"), ("CH", "Suisse"), ("ES", "Espagne"),
    ("IT", "Italie"), ("DE", "Allemagne"), ("GB", "Royaume-Uni"),
    ("CA", "Canada"), ("US", "États-Unis"),
]

_ALLOWED_TYPES = {
    PhoneNumberType.MOBILE,
    PhoneNumberType.FIXED_LINE,
    PhoneNumberType.FIXED_LINE_OR_MOBILE,
}


def _national_example(region):
    num = phonenumbers.example_number_for_type(region, PhoneNumberType.MOBILE) \
        or phonenumbers.example_number(region)
    return phonenumbers.format_number(num, PhoneNumberFormat.NATIONAL) if num else ""


# (code ISO, nom, indicatif « +223 », exemple national « 70 12 34 56 »)
COUNTRIES = [
    (code, name, f"+{phonenumbers.country_code_for_region(code)}", _national_example(code))
    for code, name in _COUNTRY_NAMES
]
_BY_CODE = {c[0]: c for c in COUNTRIES}
ALLOWED_REGIONS = set(_BY_CODE)


class PhoneValidationError(ValueError):
    pass


def check_phone(raw, country, label="Le numéro de téléphone", required=False):
    """
    Valide un numéro pour un pays. Renvoie (numéro_formaté_ou_vide, erreur_ou_None).
    Le numéro formaté est au format international : « +223 76 12 34 56 ».
    """
    raw = (raw or "").strip()
    country = (country or DEFAULT_COUNTRY).strip().upper()

    if not raw:
        return "", (f"{label} est obligatoire." if required else None)
    if country not in ALLOWED_REGIONS:
        return "", "Pays non pris en charge. Choisissez un pays dans la liste."
    if len(raw) > MAX_RAW_LENGTH or any(ch not in "0123456789+-(). " for ch in raw):
        return "", f"{label} ne doit contenir que des chiffres, espaces et le signe +."

    if raw.startswith("00"):
        raw = "+" + raw[2:]

    _, name, dial, example = _BY_CODE[country]
    try:
        num = phonenumbers.parse(raw, country)
    except NumberParseException:
        return "", f"{label} est illisible. Exemple pour {name} : {example}"

    if not phonenumbers.is_valid_number_for_region(num, country):
        other = phonenumbers.region_code_for_number(num)
        if phonenumbers.is_valid_number(num) and other and other != country:
            other_name = _BY_CODE[other][1] if other in _BY_CODE else other
            other_dial = f"+{num.country_code}"
            return "", (f"{label} est un numéro {other_dial} ({other_name}), pas {dial} ({name}). "
                        f"Choisissez le bon pays dans la liste.")
        return "", f"{label} n'est pas un numéro valide pour {name} ({dial}). Exemple : {example}"

    if phonenumbers.number_type(num) not in _ALLOWED_TYPES:
        return "", f"{label} doit être un numéro de mobile ou de fixe (pas un numéro spécial ou surtaxé)."

    return phonenumbers.format_number(num, PhoneNumberFormat.INTERNATIONAL), None


def normalize_phone(raw, country, *, required=True, label="Le numéro de téléphone"):
    """Comme check_phone, mais lève PhoneValidationError en cas d'erreur."""
    value, err = check_phone(raw, country, label=label, required=required)
    if err:
        raise PhoneValidationError(err)
    return value or None


def split_phone(stored):
    """
    « +223 76 12 34 56 » -> ("ML", "76 12 34 56"), pour pré-remplir un formulaire.
    Anciens numéros saisis sans indicatif : relus comme numéros du pays par défaut.
    """
    stored = (stored or "").strip()
    if not stored:
        return DEFAULT_COUNTRY, ""
    try:
        num = phonenumbers.parse(stored, None if stored.startswith("+") else DEFAULT_COUNTRY)
        region = phonenumbers.region_code_for_number(num)
        if region in ALLOWED_REGIONS and phonenumbers.is_valid_number(num):
            return region, phonenumbers.format_number(num, PhoneNumberFormat.NATIONAL)
    except NumberParseException:
        pass
    return DEFAULT_COUNTRY, stored


def register_phone_helpers(app):
    """Rend la liste des pays et split_phone disponibles dans tous les templates."""
    app.jinja_env.globals["PHONE_COUNTRIES"] = COUNTRIES
    app.jinja_env.globals["DEFAULT_PHONE_COUNTRY"] = DEFAULT_COUNTRY
    app.jinja_env.globals["split_phone"] = split_phone
