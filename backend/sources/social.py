"""
Fuente de redes sociales / RSS de medios -- Fase 2.

En vez de mantener el feed RSS propio de cada medio chileno, se usa Google
News RSS (no requiere API key, no tiene limite documentado para uso
razonable): agrega automaticamente Emol, La Tercera, BioBioChile, Infobae y
otros. Se investigo mantener feeds individuales primero (como decia el plan
original) pero el indice RSS de Emol ya no existe (redirige a otra parte del
sitio) y BioBioChile no tiene la ruta RSS tipica -- Google News RSS es mas
simple y mas robusto porque no depende de que cada medio mantenga su feed.

Cada mencion pasa por keywords.matched_keywords() antes de guardarse (la
query a Google News ya filtra, esto es una confirmacion). La ubicacion no
viene geolocalizada: se busca si el titulo/descripcion menciona el nombre de
alguna comuna/ciudad chilena conocida (ver comuna_coords.find_known_place)
y se geocodifica con lo mismo que ya usa la fuente CSN.

Importante: esta fuente es un proxy de "donde se habla del sismo", no un
dato de intensidad Mercalli verificada. En el frontend se muestra en una
capa separada y claramente rotulada como tal (ver js/social-layer.js).
"""
import xml.etree.ElementTree as ET
from datetime import timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

import requests

import comuna_coords
import keywords

GOOGLE_NEWS_RSS_URL = "https://news.google.com/rss/search"
REQUEST_TIMEOUT = 20

# Medios excluidos a pedido del usuario (no los considera fuente confiable).
# Se compara contra el nombre del medio Y el dominio del <source url=...>,
# porque Google News a veces trae el mismo medio con nombres distintos
# ("Infobae" e "infobae.com" aparecieron los dos).
BLOCKED_SOURCE_MARKERS = ("infobae",)

# Medios chilenos: cualquier dominio .cl, mas estos que usan .com. El titulo
# de una noticia suele no decir "Chile" ("Temblor sacude el norte del pais"),
# asi que el dominio del medio es la mejor senal de que es contenido chileno.
CHILEAN_NEWS_DOMAINS = ("latercera.com", "emol.com")


def fetch_rss_mentions():
    """Una consulta por cada palabra clave, dedupeadas por link."""
    mentions = {}
    for keyword in keywords.KEYWORDS:
        try:
            items = _fetch_google_news(keyword)
        except Exception as exc:
            print(f"Aviso: no se pudo consultar Google News para '{keyword}' ({exc}).")
            continue
        for item in items:
            mentions[item["link"]] = item
    return list(mentions.values())


def _fetch_google_news(keyword):
    params = {"q": f"{keyword} Chile", "hl": "es-419", "gl": "CL", "ceid": "CL:es"}
    response = requests.get(GOOGLE_NEWS_RSS_URL, params=params, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    root = ET.fromstring(response.text)

    items = []
    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        description = (item.findtext("description") or "").strip()
        if not title or not link:
            continue

        source_el = item.find("source")
        source_url = (source_el.get("url") if source_el is not None else "") or ""
        if is_blocked_source(_parse_source(item), source_url):
            continue
        from_chilean_outlet = _is_chilean_outlet(source_url)

        full_text = f"{title} {description}"
        if not keywords.is_relevant(full_text):
            continue

        place, coords = comuna_coords.find_known_place(full_text)

        items.append(
            {
                "title": title,
                "link": link,
                "source": _parse_source(item),
                "published": _parse_pub_date(item.findtext("pubDate")),
                "keywords_matched": keywords.matched_keywords(full_text),
                "chile": from_chilean_outlet or keywords.mentions_chile(full_text, place),
                "place": place,
                "lat": coords[0] if coords else None,
                "lon": coords[1] if coords else None,
            }
        )
    return items


def is_blocked_source(source_name, source_url=""):
    haystack = f"{source_name or ''} {source_url or ''}".lower()
    return any(marker in haystack for marker in BLOCKED_SOURCE_MARKERS)


# Respaldo por NOMBRE del medio, para noticias guardadas antes de que existiera
# el campo "chile" (no traen el dominio, solo el nombre que muestra Google News).
CHILEAN_OUTLET_NAMES = {
    "biobiochile", "meganoticias", "radio agricultura", "chilevisión", "chilevision",
    "teletrece", "t13", "vln radio", "24horas", "la tercera", "emol", "adn radio",
    "cooperativa", "radio pauta 100.5", "antofagasta tv", "epicentro chile", "tvn",
    "ex-ante", "publimetro", "la cuarta", "fm plus", "canal 9 bío bío televisión",
}


def is_chilean_outlet_name(name):
    lowered = (name or "").strip().lower()
    return ".cl" in lowered or lowered in CHILEAN_OUTLET_NAMES


def _is_chilean_outlet(source_url):
    host = (urlparse(source_url or "").hostname or "").lower()
    return host.endswith(".cl") or any(host == d or host.endswith("." + d) for d in CHILEAN_NEWS_DOMAINS)


def _parse_source(item):
    source_el = item.find("source")
    return source_el.text.strip() if source_el is not None and source_el.text else None


def _parse_pub_date(pub_date_text):
    if not pub_date_text:
        return None
    try:
        published = parsedate_to_datetime(pub_date_text)
    except (TypeError, ValueError):
        return None
    if published.tzinfo is None:
        published = published.replace(tzinfo=timezone.utc)
    else:
        published = published.astimezone(timezone.utc)
    return published.isoformat()
