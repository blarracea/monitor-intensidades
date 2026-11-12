"""
Script principal de recoleccion. Lo ejecuta GitHub Actions cada 20 minutos
(ver .github/workflows/collect.yml -- GitHub no garantiza un schedule
confiable a cadencias sub-horarias, ver el comentario ahi), pero tambien
se puede correr a mano:

    cd backend
    pip install -r requirements.txt
    python -m playwright install --with-deps chromium
    python collect.py

Catalogo de sismos:

- Chile: catalogo propio, directo del CSN (sources/csn.py, fetch_recent_events
  + fetch_event_detail) -- lat/lon/magnitud/hora salen de ahi, no de USGS.
  Antes se usaba USGS como catalogo base para Chile y se intentaba cruzar
  cada evento contra la portada del CSN por tiempo/ubicacion/magnitud, pero
  son dos catalogos independientes (cada uno con su propia estimacion) y
  ese cruce fallaba seguido -- una auditoria encontro semanas sin un solo
  sismo con reporte SENAPRED por esto, no porque no hubiera sismos
  percibidos. Si la portada del CSN marca el sismo como "percibido", el
  link a SENAPRED de ese mismo informe da la intensidad Mercalli por
  comuna directo. La portada solo expone sus ~15 sismos mas recientes, asi
  que enrich_with_senapred_archive() (el archivo propio de SENAPRED,
  retiene semanas) hace una segunda pasada para intensidad que no se haya
  resuelto en la primera.
- Resto del mundo: catalogo de USGS, con DYFI ("Did You Feel It?") como
  fuente de intensidad -- a pedido, ya no se restringe a Sudamerica, se
  consulta el feed global de USGS completo (ver BBOX mas abajo).

Ademas, guarda menciones recientes de sismos en medios chilenos (RSS via
Google News, ver sources/social.py) en data/social_mentions.json -- un
proxy de "donde se habla del sismo", no intensidad Mercalli verificada. Y
guarda posts publicos de Bluesky y Mastodon con las palabras clave del
proyecto (ver sources/bluesky.py y sources/mastodon.py) en
data/live_mentions.json, para el panel "Redes en vivo" del dashboard.
"""
import math
import os
from datetime import datetime, timedelta, timezone

import comuna_coords
import keywords
import storage
from sources import bluesky, csn, gfz, mastodon, ptwc, social, usgs

# Un reporte de intensidad para UN sismo no deberia tener comunas
# arbitrariamente lejos del epicentro -- si una comuna geocodifica asi de
# lejos, es casi siempre un nombre ambiguo mal resuelto (ver
# comuna_coords.NOT_A_COMUNA para el caso conocido de "El Loa") o un mal
# match entre el evento y un reporte de SENAPRED de OTRO sismo (dos sismos
# distintos el mismo dia, con hora/magnitud parecidas -- SENAPRED_MATCH no
# tiene forma de descartar esto de antemano porque su archivo no da lat/lon,
# ver enrich_with_senapred_archive). Ninguno de los dos casos es un dato
# real. Esta es la defensa general: cubre cualquier nombre/match mal
# resuelto que todavia no este identificado puntualmente.
#
# El limite escala con la magnitud porque un sismo chico no se siente
# realistamente a cientos de km (un M4.4 emparejado por error con un
# reporte a ~660 km es justamente el caso que este chequeo tiene que
# atrapar), pero un sismo grande si puede sentirse mas lejos. 150 km de piso
# y 700 km de techo son a criterio, calibrados contra los reportes reales
# vistos hasta ahora (el mas ancho, un M4.7 sentido desde Cuya hasta
# Antofagasta, no pasa de ~340 km del epicentro).
MAX_INTENSITY_POINT_DISTANCE_FLOOR_KM = 150
MAX_INTENSITY_POINT_DISTANCE_CEILING_KM = 700
MAX_INTENSITY_POINT_DISTANCE_KM_PER_MAGNITUDE = 120


def _max_intensity_distance_km(magnitude):
    if magnitude is None:
        return MAX_INTENSITY_POINT_DISTANCE_CEILING_KM
    return max(
        MAX_INTENSITY_POINT_DISTANCE_FLOOR_KM,
        min(MAX_INTENSITY_POINT_DISTANCE_CEILING_KM, magnitude * MAX_INTENSITY_POINT_DISTANCE_KM_PER_MAGNITUDE),
    )

# El archivo de SENAPRED no da lat/lon, asi que la segunda pasada matchea por
# hora. La hora sale del link del reporte (hora exacta del evento en Chile,
# ver csn._senapred_slug_time), que calza a 3-6 min del sismo real -- por eso
# la ventana es angosta. Antes era de 90 min sobre la hora del listado
# (que es la de publicacion, no la del sismo): un reporte de un M4.4 se pego
# tambien a dos sismos M2.5/2.9 de 4-5 horas despues (auditoria 23-09-2026).
SENAPRED_MATCH_MAX_MINUTES = 20
# El reporte de SENAPRED se publica DESPUES del sismo (3-6 min en los casos bien
# asociados): uno fechado antes del sismo no puede describirlo. Con una ventana
# simetrica (abs) un sismo que ocurre minutos despues del reporte de otro se le
# pegaba (auditoria 22-10-2026: un M3.1 en Quintero 9 min despues del reporte
# de un M5.2, y un M4.9 en Punta de Choros 37 s despues del de un M5.1).
SENAPRED_REPORT_MAX_EARLY_MINUTES = 1
# Un reporte de SENAPRED que NO enlazo el CSN (cruce por hora, adivinado) solo
# es creible si la comuna reportada mas cercana esta cerca del epicentro. En
# los 13 sismos bien asociados esa distancia va de 16 a 46 km; el falso positivo
# encontrado (auditoria 24-09-2026: el reporte de Illapel pegado tambien a un
# M3.3 frente a Navidad) daba 259 km. 100 km deja margen para sismos en el mar.
# No se aplica a los enlaces directos del CSN (son explicitos, no adivinados).
GUESSED_MATCH_MAX_NEAREST_POINT_KM = 100
SENAPRED_MATCH_MAX_MAGNITUDE_DIFF = 1.0

# Bbox global (todo el planeta) -- a pedido, el reporte DYFI de USGS ya no
# se restringe a Sudamerica: se quiere ver intensidad percibida de
# cualquier sismo en el mundo (p.ej. Japon) en el mapa de calor. Chile
# sigue viniendo aparte, directo del CSN (ver collect_chile_events).
BBOX = {
    "minlatitude": -90,
    "maxlatitude": 90,
    "minlongitude": -180,
    "maxlongitude": 180,
}
# GFZ (GEOFON) y PTWC son fuentes adicionales: solo agregan su enlace en "Fuente" cuando tambien
# registraron el sismo. Cada catalogo calcula el sismo por su cuenta, asi que la hora y el epicentro
# difieren un poco. GFZ da hora con segundos; el PTWC solo al minuto (y a veces solo la hora de emision
# del boletin, que sale hasta ~90 min despues del sismo).
GFZ_MATCH_MAX_SECONDS = 90
GFZ_MATCH_MAX_DISTANCE_KM = 150
PTWC_MATCH_MAX_SECONDS = 180
PTWC_MATCH_MAX_DISTANCE_KM = 250
PTWC_ISSUE_WINDOW_MINUTES = 90

MIN_MAGNITUDE = 2.5
LOOKBACK_DAYS = 3  # se reconsulta para capturar revisiones de magnitud y DYFI que llegan tarde
# Tras tantas fallas de DYFI seguidas en una corrida se deja de intentar con
# USGS (lento o caido): cada intento puede tardar su timeout completo.
MAX_DYFI_FAILURES = 3
RELEVANT_MAGNITUDE = 5.0
RELEVANT_FELT_REPORTS = 50


def build_event_record(feature, dyfi_points):
    props = feature["properties"]
    lon, lat, depth = feature["geometry"]["coordinates"][:3]
    place = props.get("place") or ""
    magnitude = props.get("mag")
    felt = props.get("felt") or 0

    is_relevant = bool(
        (magnitude is not None and magnitude >= RELEVANT_MAGNITUDE)
        or props.get("tsunami")
        or felt >= RELEVANT_FELT_REPORTS
    )

    return {
        "id": feature["id"],
        "source": "usgs",
        "time": datetime.fromtimestamp(props["time"] / 1000, tz=timezone.utc).isoformat(),
        "updated": datetime.fromtimestamp(props["updated"] / 1000, tz=timezone.utc).isoformat(),
        "lat": lat,
        "lon": lon,
        "depth_km": depth,
        "magnitude": magnitude,
        "mag_type": props.get("magType"),
        "place": place,
        "cdi": props.get("cdi"),
        "mmi": props.get("mmi"),
        "felt_reports": felt,
        "tsunami_flag": bool(props.get("tsunami")),
        "dyfi_points": dyfi_points,
        "intensity_source": "usgs_dyfi" if dyfi_points else None,
        "senapred_url": None,
        "csn_informe_url": None,
        "gfz_url": None,
        "ptwc_url": None,
        "url": props.get("url"),
        "relevant": is_relevant,
        "keywords_matched": keywords.matched_keywords(place),
    }


def is_chile_event(place):
    return "chile" in (place or "").lower()


def _haversine_km(lat1, lon1, lat2, lon2):
    earth_radius_km = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    delta_lat = math.radians(lat2 - lat1)
    delta_lon = math.radians(lon2 - lon1)
    a = math.sin(delta_lat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(delta_lon / 2) ** 2
    return 2 * earth_radius_km * math.asin(math.sqrt(a))


def _has_point_near_epicenter(event, points):
    return any(
        _haversine_km(event["lat"], event["lon"], p["lat"], p["lon"]) <= GUESSED_MATCH_MAX_NEAREST_POINT_KM
        for p in points
        if p.get("lat") is not None
    )


def _build_intensity_point(entry, event):
    """
    Geocodifica una entrada de comuna del reporte y arma el punto para
    dyfi_points -- o descarta el punto (devuelve None) si el nombre no se
    pudo geocodificar o geocodifico a un lugar demasiado lejos del
    epicentro del sismo (ver MAX_INTENSITY_POINT_DISTANCE_KM).
    """
    coords = comuna_coords.get_coords(entry["comuna"])
    if coords is None:
        return None

    distance_km = _haversine_km(event["lat"], event["lon"], coords[0], coords[1])
    if distance_km > _max_intensity_distance_km(event["magnitude"]):
        print(
            f"Aviso: se descarta el punto de intensidad de '{entry['comuna']}' para "
            f"{event['id']} -- geocodifico a {distance_km:.0f} km del epicentro, "
            "probablemente un nombre mal resuelto."
        )
        return None

    return {
        "lat": coords[0],
        "lon": coords[1],
        "intensity": entry["intensity"],
        "responses": None,
        "comuna": entry["comuna"],
        "region": entry["region"],
    }


def collect_chile_events():
    """
    Catalogo de sismos en Chile, construido directo desde el CSN (no desde
    USGS) -- lat/lon/magnitud/hora vienen del propio informe del CSN, y si
    la portada lo marca "percibido", el link a SENAPRED que trae ese mismo
    informe da la intensidad por comuna directo, sin adivinar.

    Antes, el catalogo base para Chile era USGS y se intentaba cruzar cada
    evento contra la portada del CSN por cercania de tiempo/ubicacion/
    magnitud (ver find_csn_match, sacado) -- son dos organismos con
    catalogos independientes, cada uno con su propia estimacion de
    magnitud y epicentro, asi que ese cruce fallaba seguido incluso cuando
    ambos catalogaban el mismo sismo real (auditoria: semanas completas sin
    un solo sismo con reporte SENAPRED, no porque no hubiera sismos
    percibidos, sino porque el cruce nunca calzaba). Al construir el evento
    directo desde el CSN no hay nada que cruzar: la fuente del sismo y la
    fuente de "fue percibido" son la misma pagina.
    """
    try:
        recent = csn.fetch_recent_events()
    except Exception as exc:
        print(f"Aviso: no se pudo consultar CSN ({exc}), no hay sismos de Chile esta corrida.")
        return []

    events = []
    for item in recent:
        try:
            detail = csn.fetch_event_detail(item["csn_url"])
        except Exception as exc:
            print(f"Aviso: no se pudo leer el informe CSN {item['csn_url']} ({exc}).")
            continue
        if not detail or detail["lat"] is None or detail["lon"] is None or detail["utc_time"] is None:
            continue

        place = detail["place"] or item["place"] or "Chile"
        if not is_chile_event(place):
            place = f"{place}, Chile"  # el CSN no repite el pais en su propio texto

        event = {
            "id": f"csn:{item['csn_url']}",
            "source": "csn",
            "time": detail["utc_time"].isoformat(),
            "updated": detail["utc_time"].isoformat(),
            "lat": detail["lat"],
            "lon": detail["lon"],
            "depth_km": detail["depth_km"],
            "magnitude": detail["magnitude"],
            "mag_type": detail["magnitude_type"],
            "place": place,
            "cdi": None,
            "mmi": None,
            "felt_reports": None,
            "tsunami_flag": False,
            "dyfi_points": [],
            "intensity_source": None,
            "senapred_url": None,
            "csn_informe_url": item["csn_url"],
            "gfz_url": None,
            "ptwc_url": None,
            "url": item["csn_url"],
            "relevant": bool(detail["magnitude"] is not None and detail["magnitude"] >= RELEVANT_MAGNITUDE),
            "keywords_matched": keywords.matched_keywords(place),
        }

        if item["felt"] and detail["senapred_url"]:
            try:
                intensity_report = csn.fetch_intensity_report(detail["senapred_url"])
            except Exception as exc:
                print(f"Aviso: no se pudo leer el reporte SENAPRED de {item['csn_url']} ({exc}).")
                intensity_report = []
            points = [p for p in (_build_intensity_point(entry, event) for entry in intensity_report) if p]
            if points:
                event["dyfi_points"] = points
                event["intensity_source"] = "csn"
                event["senapred_url"] = detail["senapred_url"]

        events.append(event)

    return events


def _report_lag_minutes(event_time, report_time):
    """Minutos entre el sismo y su reporte (positivo = reporte posterior)."""
    return (report_time - event_time).total_seconds() / 60


def _report_time_is_plausible(lag_minutes):
    return -SENAPRED_REPORT_MAX_EARLY_MINUTES <= lag_minutes <= SENAPRED_MATCH_MAX_MINUTES


def enforce_single_report_owner(events):
    """
    Red de seguridad final: un reporte de SENAPRED describe UN sismo. Si varios
    eventos terminaron con el mismo reporte, el que lo enlazo el CSN
    directamente (sin senapred_match_time) es el dueno; los que lo recibieron
    por cruce de hora lo pierden. Si ninguno lo enlazo directo, queda el de hora
    mas cercana. Dos listados del CSN del MISMO sismo (a 1-3 s) que lo enlazan
    los dos directo se respetan: el CSN mismo los asocia.
    Devuelve cuantos eventos perdieron un reporte que no era suyo.
    """
    by_url = {}
    for e in events:
        if e.get("intensity_source") == "csn" and e.get("senapred_url"):
            by_url.setdefault(e["senapred_url"], []).append(e)

    stripped = 0
    for url, owners in by_url.items():
        if len(owners) < 2:
            continue
        direct = [e for e in owners if not e.get("senapred_match_time")]
        if direct:
            keep = direct
        else:
            keep = [min(owners, key=lambda e: abs(_report_lag_minutes(datetime.fromisoformat(e["time"]), datetime.fromisoformat(e["senapred_match_time"]))))]
        for e in owners:
            if e not in keep:
                e["intensity_source"] = None
                e["dyfi_points"] = []
                e["senapred_url"] = None
                e.pop("senapred_match_time", None)
                stripped += 1
    return stripped


def enrich_with_senapred_archive(events):
    """
    Segunda pasada de intensidad para sismos chilenos sin resolver todavia
    (collect_chile_events() ya resuelve la mayoria directo desde el CSN):
    busca en el archivo propio de SENAPRED (senapred.cl/eventos/), que
    retiene semanas de historial -- a diferencia de la portada del CSN, que
    solo expone sus ~15 sismos mas recientes y por eso puede no tener
    todavia el link a SENAPRED de un sismo que se percibio recien. Matchea
    por cercania de tiempo (SENAPRED no da lat/lon en su listado) y por la
    magnitud que la propia pagina del reporte menciona, como cross-check.
    """
    pending = [e for e in events if is_chile_event(e["place"]) and e["intensity_source"] != "csn"]
    if not pending:
        return

    try:
        candidates = csn.fetch_senapred_seismic_events()
    except Exception as exc:
        print(f"Aviso: no se pudo consultar el archivo de eventos de SENAPRED ({exc}).")
        return

    # Un reporte de SENAPRED describe UN sismo: si ya lo tiene otro evento
    # (por el link directo del CSN o por un match anterior de esta pasada),
    # no se le puede pegar a un segundo.
    claimed_urls = {e["senapred_url"] for e in events if e.get("senapred_url")}

    for event in pending:
        usgs_time = datetime.fromisoformat(event["time"])
        nearby = sorted(
            (
                c
                for c in candidates
                if c["local_time"] is not None
                and _report_time_is_plausible(_report_lag_minutes(usgs_time, c["local_time"]))
            ),
            key=lambda c: abs((usgs_time - c["local_time"]).total_seconds()),
        )
        if not nearby:
            continue

        for candidate in nearby:
            if candidate["url"] in claimed_urls:
                continue
            try:
                report = csn.fetch_senapred_report(candidate["url"])
            except Exception as exc:
                print(f"Aviso: no se pudo leer {candidate['url']} ({exc}).")
                continue
            if report is None or not report["points"]:
                continue
            if (
                report["magnitude"] is not None
                and event["magnitude"] is not None
                and abs(report["magnitude"] - event["magnitude"]) > SENAPRED_MATCH_MAX_MAGNITUDE_DIFF
            ):
                continue

            points = [p for p in (_build_intensity_point(entry, event) for entry in report["points"]) if p]
            if points and _has_point_near_epicenter(event, points):
                event["dyfi_points"] = points
                event["intensity_source"] = "csn"
                event["senapred_url"] = candidate["url"]
                claimed_urls.add(candidate["url"])
                # Se guarda la hora del candidato que se matcheo (no solo el
                # resultado) para que preserve_existing_csn_data pueda
                # revalidar tambien la cercania de tiempo mas adelante, no
                # solo la distancia -- ver el comentario ahi.
                event["senapred_match_time"] = candidate["local_time"].isoformat()
                break  # encontramos un match valido, no probar mas candidatos


def collect_social_mentions():
    """Guarda menciones recientes de sismos en medios chilenos (ver sources/social.py)."""
    try:
        mentions = social.fetch_rss_mentions()
    except Exception as exc:
        print(f"Aviso: no se pudo consultar RSS de menciones ({exc}).")
        return
    storage.save_social_mentions(mentions)
    archived = storage.archive_mentions("media", _with_chile_flag(storage.load_social_mentions(), "title"))
    print(f"Menciones en medios: {len(mentions)} nuevas encontradas, {archived} archivadas.")


def collect_live_mentions():
    """Guarda posts publicos de Bluesky y Mastodon con las palabras clave
    (ver sources/bluesky.py y sources/mastodon.py) -- Mastodon es fuente
    secundaria, complementa a Bluesky en el mismo panel/archivo."""
    mentions = []
    try:
        mentions.extend(bluesky.fetch_bluesky_mentions())
    except Exception as exc:
        print(f"Aviso: no se pudo consultar Bluesky ({exc}).")
    try:
        mentions.extend(mastodon.fetch_mastodon_mentions())
    except Exception as exc:
        print(f"Aviso: no se pudo consultar Mastodon ({exc}).")
    storage.save_live_mentions(mentions)
    archived = storage.archive_mentions("live", _with_chile_flag(storage.load_live_mentions(), "text"))
    print(f"Redes en vivo: {len(mentions)} posts nuevos encontrados (Bluesky + Mastodon), {archived} archivados.")


def _with_chile_flag(items, text_key):
    """Agrega el campo `chile` a las publicaciones que aun no lo traen (las
    noticias ya lo traen desde sources/social.py; los posts de Bluesky/Mastodon
    y las noticias guardadas antes de este campo, no). Se archiva desde el
    archivo de "lo reciente" completo (no solo lo nuevo de esta corrida) para
    que la primera vez tambien se archive lo que ya estaba guardado."""
    flagged = []
    for m in items:
        m = dict(m)
        if "chile" not in m:
            m["chile"] = keywords.is_chile_quake_post(
                m.get(text_key),
                m.get("place"),
                m.get("author_handle"),
                chilean_source=social.is_chilean_outlet_name(m.get("source")),
            )
        flagged.append(m)
    return flagged


def preserve_existing_csn_data(events):
    """
    El CSN solo expone sus ~15 sismos mas recientes -- un evento que tuvo
    match en una corrida puede dejar de tenerlo en la siguiente corrida
    simplemente porque ya roto fuera de esa lista, no porque el dato haya
    dejado de ser valido. Como collect.py reconstruye cada evento desde cero
    en cada corrida (para capturar revisiones de USGS), sin esto se perderia
    la intensidad del CSN ya capturada. Se restaura desde lo guardado si la
    corrida actual no encontro un match nuevo.

    Se revalida la distancia Y la cercania de tiempo al restaurar (no solo
    al construir el match por primera vez). La distancia sola no alcanza:
    un caso real (auditoria) tenia el mismo informe de SENAPRED (un sismo
    real cerca de Tongoy) reusado en otros dos sismos distintos esa misma
    tarde (Los Pelambres, Pichidangui) porque CSN le revisa la hora a un
    evento despues de la primera pasada (por eso existe LOOKBACK_DAYS) --
    el match se hizo en una corrida donde la hora todavia no estaba
    revisada y caia dentro de SENAPRED_MATCH_MAX_MINUTES del informe, pero
    la hora final revisada quedo a 150-250 km Y a mas de 90 minutos del
    informe real. Sin revalidar tiempo, la distancia sola no bastaba para
    descartarlo (150 km de piso deja pasar sismos chicos igual). Los
    matches directo de la portada del CSN (ver collect_chile_events(), sin
    "senapred_match_time") no tienen esta nocion de ventana de tiempo -- son
    un link explicito en la misma pagina del informe, no una adivinanza por
    cercania horaria, asi que solo se les revalida distancia.
    """
    stored_by_date = {}
    # Un reporte describe UN sismo. Al restaurar, los matches directos del CSN
    # (sin senapred_match_time) van primero y reclaman su reporte; un match
    # adivinado guardado que apunte al mismo reporte se descarta (auditoria
    # 24-09-2026: el reporte de Salamanca seguia pegado tambien a otro sismo
    # porque se restauraba sin mirar si ya tenia dueno).
    claimed = {e["senapred_url"] for e in events if e["intensity_source"] == "csn" and e.get("senapred_url")}
    pending = []
    for event in events:
        if event["intensity_source"] == "csn":
            continue  # ya tiene match fresco de esta corrida
        date_str = event["time"][:10]
        if date_str not in stored_by_date:
            stored_by_date[date_str] = {e["id"]: e for e in storage.load_day(date_str)}
        previous = stored_by_date[date_str].get(event["id"])
        if previous and previous.get("intensity_source") == "csn":
            pending.append((event, previous))
    pending.sort(key=lambda pair: pair[1].get("senapred_match_time") is not None)

    for event, previous in pending:
        match_time = previous.get("senapred_match_time")
        if previous.get("senapred_url") in claimed:
            continue  # otro sismo ya es el dueno de ese reporte
        if match_time is not None:
            event_time = datetime.fromisoformat(event["time"])
            if not _report_time_is_plausible(_report_lag_minutes(event_time, datetime.fromisoformat(match_time))):
                continue  # la hora del evento se reviso y el match guardado ya no es plausible -- no se preserva

        max_distance_km = _max_intensity_distance_km(event["magnitude"])
        valid_points = [
            p
            for p in previous["dyfi_points"]
            if p.get("lat") is not None
            and _haversine_km(event["lat"], event["lon"], p["lat"], p["lon"]) <= max_distance_km
        ]
        if not valid_points:
            continue  # el match guardado ya no pasa la validacion -- no se preserva
        if match_time is not None and not _has_point_near_epicenter(event, valid_points):
            continue  # match adivinado sin ninguna comuna cerca del epicentro
        event["dyfi_points"] = valid_points
        event["intensity_source"] = "csn"
        event["senapred_url"] = previous.get("senapred_url")
        claimed.add(previous.get("senapred_url"))
        event["csn_informe_url"] = previous.get("csn_informe_url")
        if match_time is not None:
            event["senapred_match_time"] = match_time


def enrich_with_gfz(events):
    """Agrega gfz_url a los sismos que GFZ (GEOFON) tambien registro (cruce por hora y epicentro)."""
    if not events:
        return
    now = datetime.now(timezone.utc)
    try:
        gfz_events = gfz.fetch_events(now - timedelta(days=LOOKBACK_DAYS + 1), now, MIN_MAGNITUDE)
    except Exception as exc:
        print(f"Aviso: no se pudo consultar GFZ ({exc}).")
        return

    for event in events:
        if event.get("lat") is None or event.get("lon") is None:
            continue
        event_time = datetime.fromisoformat(event["time"])
        nearby = sorted(
            (
                g
                for g in gfz_events
                if abs((event_time - g["time"]).total_seconds()) <= GFZ_MATCH_MAX_SECONDS
                and _haversine_km(event["lat"], event["lon"], g["lat"], g["lon"]) <= GFZ_MATCH_MAX_DISTANCE_KM
            ),
            key=lambda g: abs((event_time - g["time"]).total_seconds()),
        )
        if nearby:
            event["gfz_url"] = nearby[0]["url"]


def _ptwc_matches(event, event_time, bulletin):
    if bulletin.get("origin_time"):
        origin = datetime.fromisoformat(bulletin["origin_time"])
        # La hora de origen del boletin es al minuto: se compara con tolerancia.
        if abs((event_time - origin).total_seconds()) > PTWC_MATCH_MAX_SECONDS:
            return None
        gap = abs((event_time - origin).total_seconds())
    else:
        issued = datetime.fromisoformat(bulletin["issue_time"].replace("Z", "+00:00"))
        minutes_before_issue = (issued - event_time).total_seconds() / 60
        if not 0 <= minutes_before_issue <= PTWC_ISSUE_WINDOW_MINUTES:
            return None
        gap = minutes_before_issue * 60
    if _haversine_km(event["lat"], event["lon"], bulletin["lat"], bulletin["lon"]) > PTWC_MATCH_MAX_DISTANCE_KM:
        return None
    return gap


def enrich_with_ptwc(events):
    """
    Guarda los boletines nuevos del PTWC/NTWC (tsunami.gov; ver sources/ptwc.py: el feed solo trae el
    ultimo, por eso se acumulan en data/ptwc_bulletins.json) y agrega ptwc_url a los sismos que
    tuvieron boletin. El PTWC no emite boletin para cada sismo, solo para los importantes.
    """
    try:
        known = {b["event_key"]: b["number"] for b in storage.load_ptwc_bulletins()}
        bulletins = storage.save_ptwc_bulletins(ptwc.fetch_new_bulletins(known))
    except Exception as exc:
        print(f"Aviso: no se pudo actualizar PTWC ({exc}).")
        return

    for event in events:
        if event.get("lat") is None or event.get("lon") is None:
            continue
        event_time = datetime.fromisoformat(event["time"])
        scored = []
        for bulletin in bulletins:
            gap = _ptwc_matches(event, event_time, bulletin)
            if gap is not None:
                scored.append((gap, bulletin))
        if scored:
            event["ptwc_url"] = min(scored, key=lambda item: item[0])[1]["url"]


def main():
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=LOOKBACK_DAYS)

    # USGS es una fuente SECUNDARIA (solo el resto del mundo): si no responde,
    # la corrida sigue con Chile (CSN), noticias y redes. Antes cualquier
    # timeout/502 de USGS tumbaba TODA la recoleccion -- auditoria 22-10-2026:
    # 247 corridas fallidas en 10 dias (108 de 288 el 20-10), casi todas por
    # "earthquake.usgs.gov: Read timed out". Los eventos de USGS ya guardados
    # no se pierden: upsert_events solo reemplaza los que llegan en esta corrida.
    try:
        raw_events = usgs.fetch_events(start, now, MIN_MAGNITUDE, BBOX)
    except Exception as exc:
        print(f"Aviso: no se pudo consultar USGS ({exc}); esta corrida solo trae Chile y menciones.")
        raw_events = []

    # Chile ya no sale de USGS -- ver collect_chile_events(). Se descartan
    # aca los eventos de USGS que caen en Chile para no duplicar el mismo
    # sismo dos veces (uno por cada fuente).
    events = []
    dyfi_failures = 0
    for feature in raw_events:
        props = feature["properties"]
        place = props.get("place") or ""
        if is_chile_event(place):
            continue
        has_dyfi = props.get("felt") or "dyfi" in (props.get("types") or "")
        dyfi_points = []
        if has_dyfi:
            if dyfi_failures >= MAX_DYFI_FAILURES:
                continue  # USGS esta lento: se deja el evento como estaba guardado
            try:
                dyfi_points = usgs.fetch_dyfi_points(props.get("detail"))
            except Exception as exc:
                # Se OMITE el evento (no se guarda con puntos vacios): asi no
                # se pisa la intensidad DYFI que ya tenia guardada.
                dyfi_failures += 1
                print(f"Aviso: no se pudo leer DYFI de {props.get('detail')} ({exc}).")
                continue
        events.append(build_event_record(feature, dyfi_points))

    events.extend(collect_chile_events())

    enrich_with_senapred_archive(events)
    preserve_existing_csn_data(events)
    stripped = enforce_single_report_owner(events)
    if stripped:
        print(f"Aviso: {stripped} evento(s) perdieron un reporte de SENAPRED que era de otro sismo.")

    # Fuentes adicionales (solo agregan enlaces): un error inesperado aca no debe tumbar la recoleccion.
    for extra_source in (enrich_with_gfz, enrich_with_ptwc):
        try:
            extra_source(events)
        except Exception as exc:
            print(f"Aviso: {extra_source.__name__} fallo ({exc}).")

    storage.upsert_events(events)
    storage.update_index()
    collect_social_mentions()
    collect_live_mentions()

    print(f"Procesados {len(events)} eventos ({start.date()} a {now.date()}).")


if __name__ == "__main__":
    main()
