"""
Cliente para la API publica de USGS (Servicio Geologico de EE.UU.).

Trae el catalogo de sismos en un rango de fechas/zona geografica y, para los
eventos con reportes ciudadanos ("Did You Feel It?" / DYFI), trae tambien la
grilla de intensidad Mercalli percibida por zona -- ese es el dato real que
alimenta el heatmap del dashboard (no la magnitud Richter del epicentro).

No requiere API key. Documentacion: https://earthquake.usgs.gov/fdsnws/event/1/
"""
import time

import requests

FEED_URL = "https://earthquake.usgs.gov/fdsnws/event/1/query"
REQUEST_TIMEOUT = 20
# USGS responde con timeouts y 502/503 de forma intermitente desde los runners
# de GitHub Actions (auditoria 22-10-2026) -- se reintenta con espera antes de
# rendirse. 3 intentos x 20 s + esperas = <90 s en el peor caso.
MAX_ATTEMPTS = 3
RETRY_PAUSE_SECONDS = (3, 8)

# Orden de preferencia de las grillas DYFI que publica USGS (mas fina primero).
_GEO_FILE_PREFERENCE = [
    "dyfi_geo_1km.geojson",
    "dyfi_geo_10km.geojson",
    "dyfi_geo_dyfi.geojson",
]


def _get(url, **kwargs):
    """requests.get con reintentos para errores transitorios (timeout, conexion,
    5xx). Un 4xx no se reintenta: no se va a arreglar solo."""
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


def fetch_events(start_time, end_time, min_magnitude, bbox):
    """Devuelve la lista cruda de features GeoJSON de USGS para el rango/zona dados."""
    params = {
        "format": "geojson",
        "starttime": start_time.strftime("%Y-%m-%dT%H:%M:%S"),
        "endtime": end_time.strftime("%Y-%m-%dT%H:%M:%S"),
        "minmagnitude": min_magnitude,
        "minlatitude": bbox["minlatitude"],
        "maxlatitude": bbox["maxlatitude"],
        "minlongitude": bbox["minlongitude"],
        "maxlongitude": bbox["maxlongitude"],
        "orderby": "time",
    }
    response = _get(FEED_URL, params=params)
    response.raise_for_status()
    return response.json().get("features", [])


def fetch_dyfi_points(detail_url):
    """
    Dado el link de detalle de un evento, busca el producto DYFI y devuelve
    los puntos de intensidad percibida (lat, lon, intensidad) reportados por
    la ciudadania. Si el evento no tiene producto DYFI, devuelve lista vacia.
    """
    if not detail_url:
        return []

    response = _get(detail_url)
    if response.status_code != 200:
        return []

    products = response.json().get("properties", {}).get("products", {})
    dyfi_products = products.get("dyfi")
    if not dyfi_products:
        return []

    geo_file = _pick_geo_file(dyfi_products[0].get("contents", {}))
    if not geo_file:
        return []

    geo_response = _get(geo_file["url"])
    if geo_response.status_code != 200:
        return []

    points = []
    for feature in geo_response.json().get("features", []):
        props = feature.get("properties", {})
        intensity = props.get("cdi") or props.get("intensity")
        if intensity is None:
            continue
        latlon = _feature_centroid(feature.get("geometry") or {})
        if latlon is None:
            continue
        lat, lon = latlon
        points.append(
            {
                "lat": lat,
                "lon": lon,
                "intensity": intensity,
                "responses": props.get("nresp") or props.get("nresponses"),
            }
        )
    return points


def _feature_centroid(geometry):
    """
    Calcula el centro de una celda de la grilla DYFI. USGS publica estas
    grillas como poligonos (el cuadrado de cada celda), no como puntos, asi
    que se promedian las esquinas del primer anillo para obtener un
    lat/lon representativo de la celda.
    """
    geom_type = geometry.get("type")
    coords = geometry.get("coordinates")
    if not coords:
        return None

    if geom_type == "Point":
        lon, lat = coords[0], coords[1]
        return lat, lon

    if geom_type == "Polygon":
        ring = coords[0]
    elif geom_type == "MultiPolygon":
        ring = coords[0][0]
    else:
        return None

    if not ring:
        return None
    lons = [c[0] for c in ring]
    lats = [c[1] for c in ring]
    return sum(lats) / len(lats), sum(lons) / len(lons)


def _pick_geo_file(contents):
    for name in _GEO_FILE_PREFERENCE:
        if name in contents:
            return contents[name]
    for name, value in contents.items():
        if name.startswith("dyfi_geo") and name.endswith(".geojson"):
            return value
    return None
