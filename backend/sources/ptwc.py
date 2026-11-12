"""
Fuente PTWC (Pacific Tsunami Warning Center, NOAA, tsunami.gov).

El PTWC (Honolulu) y el NTWC (Palmer, Alaska) emiten boletines de tsunami para los sismos
importantes (informacion, aviso, alerta...) -- no para cada sismo: solo los grandes o los que
pueden afectar costas. Cada centro publica un feed Atom que solo trae el ULTIMO boletin, asi que
no hay historial que consultar: la recoleccion (cada 5 min) guarda cada boletin nuevo en
data/ptwc_bulletins.json y desde ahi se cruzan con los sismos (ver enrich_with_ptwc en collect.py).

Cada boletin enlaza a su texto (".txt"); de ahi sale la hora de origen exacta (el feed solo trae la
hora de emision del boletin). Si un sismo tiene varios boletines (informacion, suplementos), se
guarda el de numero mas alto -- el mas reciente.
"""
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import requests

FEED_URLS = [
    "https://www.tsunami.gov/events/xml/PHEBAtom.xml",  # PTWC Honolulu (Pacifico, Caribe, Indico)
    "https://www.tsunami.gov/events/xml/PAAQAtom.xml",  # NTWC Palmer (Alaska, Canada, EE.UU.)
]
REQUEST_TIMEOUT = 20
MAX_ATTEMPTS = 2
RETRY_PAUSE_SECONDS = 3

ATOM = "{http://www.w3.org/2005/Atom}"
GEO = "{http://www.w3.org/2003/01/geo/wgs84_pos#}"

_MONTHS = {m: i for i, m in enumerate(["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], 1)}
# "0146  UTC Nov 11 2026" (hora de origen, minuto exacto) en el texto del boletin.
_ORIGIN_UTC = re.compile(r"(\d{2})(\d{2})\s+UTC\s+([A-Za-z]{3})\s+(\d{1,2})\s+(\d{4})")
# Boletines del Pacifico: "ORIGIN TIME - 1046 UTC NOV 11 2026".
_ORIGIN_UTC_UPPER = re.compile(r"ORIGIN TIME\s*-?\s*(\d{2})(\d{2})\s+UTC\s+([A-Za-z]{3})\s+(\d{1,2})\s+(\d{4})")
_BULLETIN_PATH = re.compile(r"/events/([A-Z]+/\d{4}/\d{2}/\d{2}/[^/]+)/(\d+)/")


def _get(url):
    last_exc = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            response = requests.get(url, timeout=REQUEST_TIMEOUT)
            if response.status_code < 500:
                return response
            last_exc = requests.HTTPError(f"{response.status_code} Server Error for url: {url}", response=response)
        except (requests.Timeout, requests.ConnectionError) as exc:
            last_exc = exc
        if attempt < MAX_ATTEMPTS - 1:
            time.sleep(RETRY_PAUSE_SECONDS)
    raise last_exc


def parse_origin_time(bulletin_text):
    """Hora de origen (UTC) que declara el boletin, o None si no se encuentra."""
    match = _ORIGIN_UTC.search(bulletin_text) or _ORIGIN_UTC_UPPER.search(bulletin_text)
    if not match:
        return None
    hour, minute, month, day, year = match.groups()
    month_number = _MONTHS.get(month.upper())
    if not month_number:
        return None
    try:
        return datetime(int(year), month_number, int(day), int(hour), int(minute), tzinfo=timezone.utc)
    except ValueError:
        return None


def parse_feed(xml_text):
    """Entradas del feed Atom: [{url, event_key, number, lat, lon, magnitude, region, category}]."""
    entries = []
    root = ET.fromstring(xml_text)
    for entry in root.iter(f"{ATOM}entry"):
        bulletin_url = None
        for link in entry.findall(f"{ATOM}link"):
            if link.get("title") == "Bulletin":
                bulletin_url = (link.get("href") or "").strip()
        path = _BULLETIN_PATH.search(bulletin_url or "")
        if not path:
            continue
        summary_node = entry.find(f"{ATOM}summary")
        # Texto plano del resumen XHTML: "Category: Information Bulletin Issue Time: ... Preliminary Magnitude: 4.6(Ml)Lat/Lon: ..."
        summary = "".join(summary_node.itertext()) if summary_node is not None else ""
        magnitude = re.search(r"Preliminary Magnitude:\s*([\d.]+)", summary)
        category = re.search(r"Category:\s*(.+?)\s*Bulletin Issue", summary)
        try:
            lat = float(entry.findtext(f"{GEO}lat"))
            lon = float(entry.findtext(f"{GEO}long"))
        except (TypeError, ValueError):
            continue
        entries.append(
            {
                "url": bulletin_url,
                "event_key": path.group(1),
                "number": int(path.group(2)),
                "lat": lat,
                "lon": lon,
                "magnitude": float(magnitude.group(1)) if magnitude else None,
                "issue_time": (entry.findtext(f"{ATOM}updated") or "").strip(),
                "region": (entry.findtext(f"{ATOM}title") or "").strip(),
                "category": category.group(1).strip() if category else None,
            }
        )
    return entries


def fetch_new_bulletins(known_keys_numbers):
    """
    Boletines de los feeds que aun no estan guardados (o con numero mayor al guardado), con su hora
    de origen. known_keys_numbers: {event_key: numero_mas_alto_ya_guardado}. Un feed caido se
    omite sin tumbar a los demas.
    """
    bulletins = []
    for feed_url in FEED_URLS:
        try:
            response = _get(feed_url)
            response.raise_for_status()
            entries = parse_feed(response.content)
        except Exception as exc:
            print(f"Aviso: no se pudo leer el feed de PTWC {feed_url} ({exc}).")
            continue
        for entry in entries:
            if entry["number"] <= known_keys_numbers.get(entry["event_key"], 0):
                continue
            try:
                text_response = _get(entry["url"])
                text_response.raise_for_status()
                origin = parse_origin_time(text_response.text)
            except Exception as exc:
                print(f"Aviso: no se pudo leer el boletin de PTWC {entry['url']} ({exc}).")
                continue
            # Algunos boletines (p. ej. del Caribe) traen la hora de origen solo en hora local: sin
            # origin_time se cruza despues por la hora de emision (ver enrich_with_ptwc).
            bulletins.append({**entry, "origin_time": origin.isoformat() if origin else None})
    return bulletins
