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


# Consultas para Bluesky y Mastodon (busqueda de texto libre). Las palabras
# sueltas ("sismo", "temblor") devuelven solo las ultimas ~20 publicaciones DEL
# MUNDO, y las de cuentas chilenas (BioBioChile, Emol, La Tercera, Cooperativa
# en mastodon.cl, gente comentando un temblor) quedan tapadas por el ruido
# global. Auditoria 24-09-2026: sumar estas consultas con "Chile" subio, en una
# ventana de 7 dias, de 2 a 14 las publicaciones sobre Chile capturadas (Mastodon). El texto de cada
# resultado igual tiene que contener una palabra clave (is_relevant).
SEARCH_QUERIES = KEYWORDS + [f"{k} Chile" for k in ("sismo", "temblor", "terremoto", "tsunami", "maremoto")]


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


# Nombres de lugar del set base que tambien son otra cosa (Los Angeles de EE.UU.,
# La Florida, un apellido, un grado militar...) -- por si solos no prueban que
# el texto hable de Chile (auditoria 24-09-2026: un sismo en Los Angeles, EE.UU.
# salio como "sobre Chile"). "santiago" se deja: "temblor en Santiago" es la
# forma normal de decirlo y casi siempre es la capital.
AMBIGUOUS_PLACES = {"los angeles", "la florida", "castro", "concepcion", "san antonio", "san fernando", "los andes", "coronel"}


def mentions_chile(text, place=None, handle=None):
    """True si el texto nombra Chile, un lugar chileno conocido (`place`), o
    viene de una cuenta chilena (handle que contiene "chile" o termina en
    ".cl", ej. chile-sismos.bsky.social o alguien@mastodon.cl)."""
    if place and place not in AMBIGUOUS_PLACES:
        return True
    if handle:
        h = handle.lower()
        if "chile" in h or h.endswith(".cl"):
            return True
    return bool(_CHILE_PATTERN.search(normalize(text)))


_PLACE_PATTERN = re.compile(r"\bal\s+[nsoe]{1,3}\s+de\s+(.+?)(?:,|$)", re.IGNORECASE)


def place_search_term(place):
    """Nombre del lugar de referencia de un sismo, para buscar noticias/posts
    por el ("20 km al S de La Higuera, Chile" -> "La Higuera"). None si el
    texto no tiene esa forma (ej. USGS: "3 km WSW of Fuig")."""
    match = _PLACE_PATTERN.search(place or "")
    return match.group(1).strip() if match else None


# "terremoto" es tambien un trago de las Fiestas Patrias ("el terremoto de
# Chile", con piscola y pipeno), y "tsunami"/"maremoto"/"temblando" se usan
# como metafora ("tsunami de emociones", "me quede temblando"). Para la
# trazabilidad de un sismo REAL esas menciones son ruido: si el texto solo trae
# palabras de ese tipo, tiene que traer ademas vocabulario de sismo.
_STRONG_KEYWORDS = {"sismo", "temblor", "temblo", "megaterremoto"}
_QUAKE_CONTEXT = re.compile(
    r"\b(magnitud\w*|epicentro|richter|profundidad\w*|replicas?|percib\w*|sacud\w*|remece\w*|"
    r"sismic\w*|evacua\w*|alerta|csn|senapred|onemi|shoa|grados|km|quil\w*metros)\b"
    r"|\bmag\.?\s*\d"                       # "mag. 4.6" (agregadores de sismos)
    r"|\b(terremoto|sismo|temblor)\s+de\s+\d[.,]\d"   # "terremoto de 4,7" (no "de 1960")
    r"|\bterremoto\s+en\s+(chile|santiago)\b"           # "terremoto en Chile" (no "el terremoto de Chile", el trago)
)


def is_quake_report(text):
    """True si el texto habla de un sismo de verdad, no de un trago ni de una
    metafora. Tambien descarta las listas automaticas de "tendencias"."""
    normalized = normalize(text)
    if normalized.startswith("items de los ultimos"):
        return False
    if _STRONG_KEYWORDS.intersection(matched_keywords(text)):
        return True
    return bool(_QUAKE_CONTEXT.search(normalized))


def is_chile_quake_post(text, place=None, handle=None, chilean_source=False):
    """Publicacion o noticia sobre un sismo Y sobre Chile (el campo `chile` del
    archivo -- ver storage.archive_mentions)."""
    if not is_quake_report(text):
        return False
    return chilean_source or mentions_chile(text, place, handle)
