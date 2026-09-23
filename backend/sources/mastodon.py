"""
Fuente Mastodon -- complementa a Bluesky en el mismo panel "Redes en vivo"
con posts publicos que mencionan las palabras clave del proyecto (ver
keywords.py), en cualquier parte del mundo -- sin filtro geografico, a
pedido del usuario.

Mastodon es una red federada, no existe un "buscador global" unico. Se
consulta una sola instancia grande y bien conectada (mastodon.social).

Dos formas de traer posts, segun si hay credencial configurada:

- **Con MASTODON_ACCESS_TOKEN configurado** (recomendado, texto libre igual
  que Bluesky): se usa /api/v2/search?type=statuses, que busca en CUALQUIER
  post publico de la instancia, no solo los que usaron un hashtag. Se
  confirmo probandolo en vivo: una busqueda de "sismo" devolvio un post de
  una cuenta de noticias de Venezuela que nunca uso el hashtag #sismo, solo
  la palabra suelta en el texto -- antes de esto, un post asi quedaba
  afuera. Ese endpoint exige estar autenticado (sin token devuelve
  resultados vacios, silencioso, no un error) -- el token se genera en
  Preferencias > Desarrollo > (crear una aplicacion) > "Tu token de
  acceso", con permiso "read" alcanza, no necesita "write". Se guarda como
  secret de GitHub Actions (ver .github/workflows/collect.yml).
- **Sin token** (fallback, no requiere cuenta): se usa la timeline publica
  por hashtag (api/v1/timelines/tag/:hashtag), que si funciona sin
  autenticacion pero SOLO encuentra posts que usaron el hashtag real (ej.
  "#sismo") -- un post que menciona "sismo" como palabra suelta, sin el
  "#", no aparece por esta via. Se mantiene como respaldo para no romper
  el proyecto si alguien lo corre sin configurar el token.

Mastodon tiene su propio ecosistema de bots de monitoreo sismico mundial
(ej. "monitorsismico", "NCSeismicobserv") que, a diferencia de la familia
de bots de Bluesky, no comparten un formato de texto comun ni un llamado a
la accion estandarizado -- se detectan por cuenta conocida en vez de por
una frase, y se les aplica el mismo umbral de magnitud 5.0+ que a los bots
de Bluesky (ver sources/bluesky.py) para que los sismos grandes se sigan
viendo.
"""
import os
import re
from datetime import datetime, timezone
from html import unescape

import requests

import comuna_coords
import keywords

INSTANCE_DOMAIN = "mastodon.social"
HASHTAG_TIMELINE_URL = f"https://{INSTANCE_DOMAIN}/api/v1/timelines/tag/{{tag}}"
STATUS_SEARCH_URL = f"https://{INSTANCE_DOMAIN}/api/v2/search"
REQUEST_TIMEOUT = 20
POSTS_PER_KEYWORD = 20

# Cuentas conocidas que republican CUALQUIER sismo detectado en el mundo
# (formato libre, sin un patron de texto en comun) -- se dejan pasar solo
# si mencionan un sismo M5.0+, igual que los bots de Bluesky.
MIN_BOT_MAGNITUDE = 5.0
BOT_ACCOUNTS = {"monitorsismico", "ncseismicobserv", "earthquake_monitor", "earthquakebot", "nws_ntwc_bot"}
MAGNITUDE_PATTERNS = [
    re.compile(r"magnitud\s+(\d+(?:[.,]\d+)?)", re.IGNORECASE),
    re.compile(r"\b(?:mb|ml|mlv|mw|md)\s*(\d+(?:\.\d+)?)\b", re.IGNORECASE),
]


def fetch_mastodon_mentions():
    """Una consulta por cada palabra clave (busqueda de texto libre si hay
    token, o timeline de hashtag si no -- ver el docstring del modulo),
    dedupeadas por link."""
    access_token = os.environ.get("MASTODON_ACCESS_TOKEN")

    mentions = {}
    for keyword in keywords.KEYWORDS:
        try:
            posts = _search_statuses(keyword, access_token) if access_token else _fetch_hashtag_timeline(keyword)
        except Exception as exc:
            print(f"Aviso: no se pudo consultar Mastodon para '{keyword}' ({exc}).")
            continue
        for post in posts:
            mentions[post["link"]] = post
    return list(mentions.values())


def _search_statuses(keyword, access_token):
    """Busqueda de texto libre (api/v2/search) -- encuentra CUALQUIER post
    publico que mencione la palabra, con o sin hashtag. Exige autenticacion
    (sin ella, la instancia devuelve resultados vacios en silencio)."""
    headers = {"Authorization": f"Bearer {access_token}"}
    params = {"q": keyword, "type": "statuses", "limit": POSTS_PER_KEYWORD}
    response = requests.get(STATUS_SEARCH_URL, params=params, headers=headers, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    statuses = response.json().get("statuses", [])
    return [item for item in (_parse_status(s) for s in statuses) if item]


def _fetch_hashtag_timeline(tag):
    """Respaldo sin autenticacion -- solo encuentra posts que usaron el
    hashtag real (#tag), no la palabra suelta en el texto."""
    url = HASHTAG_TIMELINE_URL.format(tag=tag)
    response = requests.get(url, params={"limit": POSTS_PER_KEYWORD}, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    statuses = response.json()
    return [item for item in (_parse_status(s) for s in statuses) if item]


def _parse_status(status):
    """Misma forma de post (Status entity) en la busqueda y en la timeline
    de hashtag -- un solo parseo compartido por las dos vias."""
    text = _strip_html(status.get("content") or "")
    if not text or not keywords.is_relevant(text):
        return None

    account = status.get("account") or {}
    acct = account.get("acct")
    link = status.get("url")
    if not acct or not link:
        return None

    if _is_bot_account(acct) and (_extract_magnitude(text) or 0) < MIN_BOT_MAGNITUDE:
        return None

    place, coords = comuna_coords.find_known_place(text)

    return {
        "platform": "mastodon",
        "link": link,
        "text": text,
        "author_handle": _full_handle(acct),
        "author_name": account.get("display_name") or acct,
        "author_avatar": account.get("avatar"),
        "published": _parse_created_at(status.get("created_at")),
        "like_count": status.get("favourites_count") or 0,
        "keywords_matched": keywords.matched_keywords(text),
        "place": place,
        "lat": coords[0] if coords else None,
        "lon": coords[1] if coords else None,
    }


def _strip_html(html):
    """El campo "content" de Mastodon viene como HTML (parrafos, links de hashtag)."""
    text = re.sub(r"<[^>]+>", " ", html)
    text = unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _full_handle(acct):
    """Una cuenta LOCAL a la instancia consultada viene sin @instancia (ej.
    "monitorsismico") -- se le agrega la instancia para mostrar un handle
    completo y sin ambiguedad, igual que una cuenta remota ya lo trae."""
    return acct if "@" in acct else f"{acct}@{INSTANCE_DOMAIN}"


def _is_bot_account(acct):
    username = acct.split("@")[0].lower()
    return username in BOT_ACCOUNTS


def _extract_magnitude(text):
    for pattern in MAGNITUDE_PATTERNS:
        match = pattern.search(text)
        if match:
            try:
                return float(match.group(1).replace(",", "."))
            except ValueError:
                continue
    return None


def _parse_created_at(created_at):
    if not created_at:
        return None
    try:
        parsed = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc).isoformat()
