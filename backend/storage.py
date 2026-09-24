"""
Guarda y lee los eventos sismicos en archivos JSON particionados por dia
(data/YYYY-MM-DD.json), y mantiene data/index.json con la lista de archivos
disponibles.

Se eligio JSON particionado por dia en vez de una base de datos porque con
commits automaticos cada 30 minutos, un archivo .db binario ensuciaria el
historial de git; con JSON por dia, cada commit solo toca el archivo del dia
en curso y cualquiera puede abrirlo con un editor de texto normal.
"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
INDEX_FILE = DATA_DIR / "index.json"
SOCIAL_FILE = DATA_DIR / "social_mentions.json"
SOCIAL_RETENTION_HOURS = 72
LIVE_MENTIONS_FILE = DATA_DIR / "live_mentions.json"
LIVE_MENTIONS_RETENTION_HOURS = 24


def _day_file(date_str):
    return DATA_DIR / f"{date_str}.json"


def _event_date(event):
    return event["time"][:10]  # "2026-08-21T14:32:10+00:00" -> "2026-08-21"


def _is_day_file(path):
    """True solo para archivos con forma de dia (YYYY-MM-DD.json) -- evita
    que otros .json de data/ (ej. index.json, comuna_coords_cache.json)
    se cuelen como si fueran un dia de eventos."""
    try:
        datetime.strptime(path.stem, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def load_day(date_str):
    path = _day_file(date_str)
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_day(date_str, events):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    events_sorted = sorted(events, key=lambda e: e["time"])
    with _day_file(date_str).open("w", encoding="utf-8") as f:
        json.dump(events_sorted, f, ensure_ascii=False, indent=2)


def upsert_events(events):
    """Agrupa los eventos nuevos por dia y los mezcla (por id) con lo ya guardado."""
    by_day = {}
    for event in events:
        by_day.setdefault(_event_date(event), []).append(event)

    for date_str, day_events in by_day.items():
        existing = {e["id"]: e for e in load_day(date_str)}
        for event in day_events:
            existing[event["id"]] = event
        save_day(date_str, list(existing.values()))


def update_index():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    files = sorted(p.stem for p in DATA_DIR.glob("*.json") if _is_day_file(p))
    with INDEX_FILE.open("w", encoding="utf-8") as f:
        json.dump(
            {"days": files, "updated": datetime.now(timezone.utc).isoformat()},
            f,
            ensure_ascii=False,
            indent=2,
        )


def load_social_mentions():
    if not SOCIAL_FILE.exists():
        return []
    with SOCIAL_FILE.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_social_mentions(new_mentions):
    """
    Mezcla las menciones nuevas con las ya guardadas (por link) y recorta a
    las ultimas SOCIAL_RETENTION_HOURS -- a diferencia de los sismos, aca
    solo importa lo reciente, no hace falta un historial de 30 dias.
    """
    existing = {m["link"]: m for m in load_social_mentions()}
    for mention in new_mentions:
        existing[mention["link"]] = mention

    cutoff = datetime.now(timezone.utc) - timedelta(hours=SOCIAL_RETENTION_HOURS)
    kept = []
    for mention in existing.values():
        published = mention.get("published")
        if published:
            try:
                if datetime.fromisoformat(published) < cutoff:
                    continue
            except ValueError:
                pass
        kept.append(mention)
    kept.sort(key=lambda m: m.get("published") or "", reverse=True)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with SOCIAL_FILE.open("w", encoding="utf-8") as f:
        json.dump(kept, f, ensure_ascii=False, indent=2)


def load_live_mentions():
    if not LIVE_MENTIONS_FILE.exists():
        return []
    with LIVE_MENTIONS_FILE.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_live_mentions(new_mentions):
    """
    Igual logica que save_social_mentions, pero con una ventana mas corta
    (LIVE_MENTIONS_RETENTION_HOURS) -- este panel se muestra como un chat en
    vivo (Bluesky + Mastodon, ver sources/bluesky.py y sources/mastodon.py),
    interesa lo que se esta diciendo ahora, no un archivo de dias.
    """
    existing = {m["link"]: m for m in load_live_mentions()}
    for mention in new_mentions:
        existing[mention["link"]] = mention

    cutoff = datetime.now(timezone.utc) - timedelta(hours=LIVE_MENTIONS_RETENTION_HOURS)
    kept = []
    for mention in existing.values():
        published = mention.get("published")
        if published:
            try:
                if datetime.fromisoformat(published) < cutoff:
                    continue
            except ValueError:
                pass
        kept.append(mention)
    kept.sort(key=lambda m: m.get("published") or "", reverse=True)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with LIVE_MENTIONS_FILE.open("w", encoding="utf-8") as f:
        json.dump(kept, f, ensure_ascii=False, indent=2)


# --- Archivo permanente de publicaciones (trazabilidad por sismo) ---
#
# Los paneles "Redes en vivo" (24 h) y "Menciones en medios" (72 h) solo
# guardan lo reciente. Para poder ver, semanas despues, que se dijo tras un
# sismo, cada publicacion se copia ademas a un archivo por dia que NUNCA se
# borra: data/archive/live/YYYY-MM-DD.json y data/archive/media/YYYY-MM-DD.json
# (fecha UTC de publicacion, igual que los archivos de sismos). Los sismos
# tampoco se borran: data/YYYY-MM-DD.json ya no tiene purga.
#
# Solo se guarda lo que el dashboard ya muestra (autor, texto, enlace, fecha),
# nada mas -- son publicaciones de terceros y se conservan para siempre.
ARCHIVE_DIR = DATA_DIR / "archive"
ARCHIVE_META_FILE = ARCHIVE_DIR / "meta.json"
ARCHIVE_FIELDS = {
    "live": ("platform", "link", "text", "author_handle", "author_name", "author_avatar", "published", "chile"),
    "media": ("title", "link", "source", "published", "place", "chile"),
}


def _archive_day_file(kind, date_str):
    return ARCHIVE_DIR / kind / f"{date_str}.json"


def _load_archive_meta():
    if not ARCHIVE_META_FILE.exists():
        return {}
    with ARCHIVE_META_FILE.open("r", encoding="utf-8") as f:
        return json.load(f)


def _save_archive_meta(meta):
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    with ARCHIVE_META_FILE.open("w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)


def archive_mentions(kind, mentions):
    """
    Copia las publicaciones al archivo permanente de su dia (por `published`,
    UTC). Solo agrega enlaces que todavia no estan -- lo ya archivado no se
    reescribe, asi el historial de git de esos archivos solo crece al final.
    Cada `mention` debe traer el campo `chile` (ver collect.py).

    Guarda ademas data/archive/meta.json: desde cuando existe archivo
    ("live_from"/"media_from", la publicacion mas antigua de la primera tanda)
    y que dias existen -- el frontend lo usa para avisar "este sismo es
    anterior al archivo" y para no pedir archivos que no existen.
    Devuelve cuantas publicaciones nuevas se archivaron.
    """
    fields = ARCHIVE_FIELDS[kind]
    by_day = {}
    for m in mentions:
        published = m.get("published")
        if not published or not m.get("link"):
            continue
        by_day.setdefault(published[:10], []).append({k: m.get(k) for k in fields})

    if not by_day:
        return 0

    meta = _load_archive_meta()
    days_key = f"{kind}_days"
    from_key = f"{kind}_from"
    known_days = set(meta.get(days_key, []))
    added = 0
    for date_str, items in by_day.items():
        path = _archive_day_file(kind, date_str)
        existing = {}
        if path.exists():
            with path.open("r", encoding="utf-8") as f:
                existing = {m["link"]: m for m in json.load(f)}
        new_items = [m for m in items if m["link"] not in existing]
        if not new_items:
            continue
        for m in new_items:
            existing[m["link"]] = m
        added += len(new_items)
        ordered = sorted(existing.values(), key=lambda m: m["published"])
        path.parent.mkdir(parents=True, exist_ok=True)
        # Un item por linea: JSON compacto (ocupa ~la mitad que con indent) y
        # los diffs de git siguen siendo legibles.
        with path.open("w", encoding="utf-8") as f:
            f.write("[\n" + ",\n".join(json.dumps(m, ensure_ascii=False, separators=(",", ":")) for m in ordered) + "\n]\n")
        known_days.add(date_str)

    if added:
        if from_key not in meta:
            meta[from_key] = min(m["published"] for items in by_day.values() for m in items)
        meta[days_key] = sorted(known_days)
        _save_archive_meta(meta)
    return added
