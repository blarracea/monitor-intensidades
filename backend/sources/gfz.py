"""
Fuente GFZ (GEOFON, Centro Alemán de Investigación en Geociencias de Potsdam).

GFZ publica un catalogo mundial abierto con el estandar FDSN (el mismo formato que USGS), sin API
key: https://geofon.gfz-potsdam.de/fdsnws/event/1/ . Sirve como fuente adicional: cuando GFZ
tambien registro un sismo que ya tenemos (del CSN o de USGS), se agrega el enlace a su pagina de
evento en "Fuente" -- no cambia la magnitud, la referencia ni la intensidad del sismo.

El cruce con nuestros eventos es por cercania de hora y de epicentro (ver enrich_with_gfz en
collect.py): los catalogos calculan cada sismo por su cuenta, asi que la hora difiere por segundos
y el epicentro por algunos kilometros.
"""
import time
from datetime import datetime, timezone

import requests

FDSN_URL = "https://geofon.gfz-potsdam.de/fdsnws/event/1/query"
EVENT_PAGE_URL = "https://geofon.gfz.de/eqinfo/event.php?id={}"
REQUEST_TIMEOUT = 20
MAX_ATTEMPTS = 3
RETRY_PAUSE_SECONDS = (3, 8)


def _get(url, **kwargs):
    """requests.get con reintentos para errores transitorios (timeout, conexion, 5xx)."""
    last_exc = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            response = requests.get(url, timeout=REQUEST_TIMEOUT, **kwargs)
            if response.status_code < 500:
                return response
            last_exc = requests.HTTPError(f"{response.status_code} Server Error for url: {url}", response=response)
        except (requests.Timeout, requests.ConnectionError) as exc:
            last_exc = exc
        if attempt < MAX_ATTEMPTS - 1:
            time.sleep(RETRY_PAUSE_SECONDS[min(attempt, len(RETRY_PAUSE_SECONDS) - 1)])
    raise last_exc


def parse_text_catalog(text):
    """Lee la respuesta format=text de FDSN (columnas separadas por '|', encabezado con '#')."""
    events = []
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        cols = line.split("|")
        if len(cols) < 13:
            continue
        try:
            when = datetime.fromisoformat(cols[1]).replace(tzinfo=timezone.utc)
            events.append(
                {
                    "id": cols[0],
                    "time": when,
                    "lat": float(cols[2]),
                    "lon": float(cols[3]),
                    "magnitude": float(cols[10]) if cols[10] else None,
                    "place": cols[12],
                    "url": EVENT_PAGE_URL.format(cols[0]),
                }
            )
        except ValueError:
            continue  # linea con formato inesperado: se ignora, no se tumba la corrida
    return events


def fetch_events(start_time, end_time, min_magnitude):
    """Sismos de GFZ entre dos fechas (UTC), en todo el mundo."""
    params = {
        "starttime": start_time.strftime("%Y-%m-%dT%H:%M:%S"),
        "endtime": end_time.strftime("%Y-%m-%dT%H:%M:%S"),
        "minmagnitude": min_magnitude,
        "format": "text",
        "orderby": "time",
    }
    response = _get(FDSN_URL, params=params)
    if response.status_code == 204:  # FDSN: sin resultados
        return []
    response.raise_for_status()
    return parse_text_catalog(response.text)
