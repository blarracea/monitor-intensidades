"""
Utilidades para detectar palabras clave relevantes en texto libre.

Se usa hoy para marcar si el campo "place" de un evento de USGS menciona
alguna palabra clave, y queda lista para filtrar texto de redes sociales /
RSS cuando se implemente la Fase 2 (ver sources/social.py).
"""
import re
import unicodedata

KEYWORDS = [
    "sismo",
    "terremoto",
    "tsunami",
    "temblor",
    # "temblo" (sin la "r") atrapa "tembló"/"temblo" -- conjugacion de
    # "temblar" que normalize() deja asi al sacarle la tilde a la "o". No es
    # prefijo de "temblor" (le falta la "r" final), asi que sin esta entrada
    # separada esos posts/titulares quedaban afuera aunque mencionen un
    # sismo. normalize() ya baja todo a minuscula, asi que no hace falta
    # una entrada separada para mayusculas/minusculas.
    "temblo",
    # "temblando" (gerundio, ej. "esta temblando") tampoco es atrapado por
    # "temblor" ni "temblo" -- diverge en la 6ta letra ("tembl-a-ndo" vs
    # "tembl-o-r"/"tembl-o"). Es una forma muy comun en redes en vivo
    # durante un sismo, asi que necesita su propia entrada.
    "temblando",
    "megaterremoto",
    "maremoto",
]


def normalize(text):
    """Pasa a minusculas y quita tildes, para que la busqueda no dependa de acentos."""
    if not text:
        return ""
    text = text.lower()
    text = unicodedata.normalize("NFKD", text)
    return "".join(c for c in text if not unicodedata.combining(c))


def matched_keywords(text):
    normalized = normalize(text)
    return [kw for kw in KEYWORDS if re.search(r"\b" + kw + r"\w*", normalized)]


def is_relevant(text):
    return len(matched_keywords(text)) > 0


# Marcas de que un texto habla de Chile (el "solo Chile" de la trazabilidad
# por sismo, ver storage.archive_mentions). Sin "santiago" suelto: tambien es
# una ciudad de Espana/Rep. Dominicana -- las ciudades chilenas ya las cubre
# el parametro `place` (comuna_coords.find_known_place).
_CHILE_PATTERN = re.compile(
    r"\b(chile|chilen[oa]s?|senapred|onemi|shoa|centro sismologico nacional)\b"
)


def mentions_chile(text, place=None, handle=None):
    """True si el texto nombra Chile, un lugar chileno conocido (`place`), o
    viene de una cuenta chilena (handle que contiene "chile" o termina en
    ".cl", ej. chile-sismos.bsky.social o alguien@mastodon.cl)."""
    if place:
        return True
    if handle:
        h = handle.lower()
        if "chile" in h or h.endswith(".cl"):
            return True
    return bool(_CHILE_PATTERN.search(normalize(text)))
