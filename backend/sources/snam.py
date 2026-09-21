"""
Fuente SNAM/SHOA (Sistema Nacional de Alarma de Maremotos, snamchile.cl) --
segunda pasada para sismos M>=5.0: el SHOA (el mismo organismo que publica
el SNAM, no son dos fuentes distintas) confirma o revisa la referencia
geografica y la magnitud del CSN/USGS unos ~10 minutos despues de la primera
publicacion.

Como funciona (investigado navegando el sitio, no hay API publica):
  1. La portada (snamchile.cl) es HTML plano generado en el servidor (PHP
     clasico), con una tabla de los ultimos eventos -- "Se publican solo
     los sismos iguales o mayores a 5 en la escala de Richter", segun la
     propia pagina. A diferencia de SENAPRED no hace falta Playwright: el
     contenido ya esta en el HTML de la primera respuesta, no lo arma un
     framework de JS despues de cargar.
  2. Cada fila tiene, en atributos "onclick" de sus botones:
     - "Ver Boletin": modalBol(id, ...) -- el id del boletin. Se confirmo
       leyendo js/funciones.js que ese boton en realidad carga (via AJAX
       jQuery) boletin.php?idBol={id}, que es una pagina HTML propia y
       fetcheable directo -- se usa como el link de "Fuente: SNAM".
     - "Ver" (mapa): modalMapa(lat, lon, referencia, magnitud, fecha,
       fuente) -- unicos lugares del HTML con lat/lon exactos, la magnitud
       que uso el SHOA, y que catalogo origino el dato (CSN, USGS/NEIC,
       GFZ, etc). No estan en ningun otro lado de la pagina.
  3. Se cruza contra los eventos ya recolectados por cercania de tiempo +
     distancia de epicentro (ver enrich_with_snam en collect.py) -- nunca
     por magnitud, que es justamente el dato que puede diferir y se quiere
     actualizar.
"""
import re
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.snamchile.cl"
REQUEST_TIMEOUT = 30
# A diferencia del offset fijo que usa sources.csn (CHILE_UTC_OFFSET, sin
# manejo de horario de verano), aca se usa la zona horaria real -- Chile
# tiene horario de verano (UTC-3) buena parte del año, y esta fuente
# necesita la hora exacta para el cruce por cercania de tiempo con SNAM_MATCH.
CHILE_TZ = ZoneInfo("America/Santiago")
# El sitio devuelve 403 al User-Agent por defecto de requests
# ("python-requests/x.y") -- algun WAF/CDN delante bloquea firmas de bot
# conocidas. Un User-Agent de navegador comun lo evita (probado).
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}

# Grupos: lat, lon, referencia (sin usar, cells[1] ya trae el texto limpio
# de la tabla), magnitud, fecha (sin usar, cells[0] ya trae lo mismo), fuente.
MODAL_MAPA_RE = re.compile(
    r"modalMapa\(\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*,\s*'[^']*'\s*,\s*(-?[\d.]+)\s*,\s*'[^']*'\s*,\s*'([^']*)'\s*\)"
)
MODAL_BOL_RE = re.compile(r"modalBol\(\s*(\d+)")


def fetch_snam_events():
    """
    Trae la tabla de eventos de la portada de SNAM. Devuelve una lista de
    {"local_time", "place", "lat", "lon", "magnitude", "source_label",
    "boletin_url"} -- ver el docstring del modulo para de donde sale cada
    campo.
    """
    response = requests.get(BASE_URL + "/", timeout=REQUEST_TIMEOUT, headers=HEADERS)
    response.raise_for_status()
    response.encoding = "utf-8"  # el sitio no siempre declara charset en el header HTTP
    soup = BeautifulSoup(response.text, "html.parser")

    table = soup.find("table", class_="table-stripped")
    if table is None:
        return []

    events = []
    for row in table.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < 6:
            continue  # fila de encabezado (usa <th>, no <td>)

        local_time = _parse_local_time(cells[0].get_text(strip=True))
        place = cells[1].get_text(strip=True)

        mapa_link = cells[5].find("a")
        mapa_onclick = mapa_link["onclick"] if mapa_link and mapa_link.has_attr("onclick") else ""
        match = MODAL_MAPA_RE.search(mapa_onclick)
        if local_time is None or not place or match is None:
            continue

        lat, lon, magnitude, source_label = match.groups()

        bol_link = cells[4].find("a")
        bol_onclick = bol_link["onclick"] if bol_link and bol_link.has_attr("onclick") else ""
        bol_match = MODAL_BOL_RE.search(bol_onclick)
        boletin_url = f"{BASE_URL}/boletin.php?idBol={bol_match.group(1)}" if bol_match else None

        events.append(
            {
                "local_time": local_time,
                "place": place,
                "lat": float(lat),
                "lon": float(lon),
                "magnitude": float(magnitude),
                "source_label": source_label.strip(),
                "boletin_url": boletin_url,
            }
        )
    return events


def _parse_local_time(text):
    try:
        naive = datetime.strptime(text, "%d-%m-%Y %H:%M")
    except ValueError:
        return None
    return naive.replace(tzinfo=CHILE_TZ)
