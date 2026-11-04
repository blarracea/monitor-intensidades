"""
Recupera publicaciones (Bluesky, Mastodon) y noticias (Google News) de sismos
ANTERIORES al archivo continuo -- para que la trazabilidad de un sismo de hace
semanas (ej. Farellones 18-09) no salga vacia. Se corre a mano desde GitHub
Actions (workflow "Recuperar historial", necesita los secrets de Bluesky y
Mastodon), no en cada recoleccion:

    cd backend
    python backfill.py [--dry-run] [--event-id ID]

Que sismos: los chilenos del CSN que el dashboard deja elegir -- con reporte
de SENAPRED (los de la lupa) o M>=5.0 (los puntos azules del mapa). Los
extranjeros no: la trazabilidad es "solo Chile" y casi no traerian nada.

Como se recupera (cada fuente lo permite distinto, verificado):
- Bluesky: searchPosts acepta since/until (ver bluesky.search_window).
- Mastodon: /api/v2/search acepta min_id/max_id, y los IDs de Mastodon
  codifican la hora (ver mastodon.search_window).
- Google News: los operadores after:/before: van dentro de la busqueda, por
  dia (ver social.fetch_rss_window).

Es idempotente: archive_mentions solo agrega enlaces nuevos, asi que correrlo
dos veces (o despues de la recoleccion normal) no duplica nada. La ventana de
cada sismo es la misma que usa el frontend (js/data-loader.js: 5 min antes,
6 h despues) y solo se archiva contenido sobre Chile. Es "lo mejor que se
puede": los indices de busqueda de cada red no garantizan tener todo.
"""
import argparse
import os
import time
from datetime import datetime, timedelta, timezone

import collect
import keywords
import storage
from sources import bluesky, mastodon, social

BEFORE = timedelta(minutes=5)
AFTER = timedelta(hours=6)
PAUSE_SECONDS = 0.4  # entre consultas, para no golpear las APIs


def load_events():
    events = {}
    for path in sorted(storage.DATA_DIR.glob("*.json")):
        if storage._is_day_file(path):
            for event in storage.load_day(path.stem):
                events[event["id"]] = event
    return list(events.values())


def select_events(events):
    return sorted(
        (
            e
            for e in events
            if e["source"] == "csn"
            and (e.get("intensity_source") == "csn" or (e.get("magnitude") or 0) >= collect.RELEVANT_MAGNITUDE)
        ),
        key=lambda e: e["time"],
    )


def _place_queries(event):
    place = keywords.place_search_term(event.get("place"))
    return [f"sismo {place}", f"temblor {place}"] if place else []


def _in_window(published, since, until):
    return published is not None and since <= datetime.fromisoformat(published) <= until


def fetch_live(event, since, until, bluesky_jwt, mastodon_token):
    queries = keywords.SEARCH_QUERIES + _place_queries(event)
    found = {}
    for query in queries:
        for label, search, credential in (
            ("Bluesky", bluesky.search_window, bluesky_jwt),
            ("Mastodon", mastodon.search_window, mastodon_token),
        ):
            if not credential:
                continue
            try:
                for post in search(query, credential, since, until):
                    if _in_window(post["published"], since, until):
                        found[post["link"]] = post
            except Exception as exc:
                print(f"  Aviso: {label} fallo para '{query}' ({exc}).")
            time.sleep(PAUSE_SECONDS)
    return list(found.values())


def fetch_media(event, since, until):
    queries = [f"{k} Chile" for k in keywords.KEYWORDS] + _place_queries(event)
    # after:/before: de Google News cortan por dia en una zona horaria que no
    # es UTC (un sismo de las 03:07 UTC caia en el dia anterior y devolvia 0
    # noticias) -- se pide un dia de margen a cada lado y el corte exacto lo
    # hace _in_window.
    items = social.fetch_rss_window(queries, since.date() - timedelta(days=1), until.date() + timedelta(days=2))
    return [m for m in items if _in_window(m["published"], since, until)]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="solo cuenta lo encontrado, no escribe nada")
    parser.add_argument("--event-id", help="recuperar solo este sismo (id, ej. csn:https://...)")
    args = parser.parse_args()

    events = select_events(load_events())
    if args.event_id:
        events = [e for e in events if e["id"] == args.event_id]

    handle, password = os.environ.get("BLUESKY_HANDLE"), os.environ.get("BLUESKY_APP_PASSWORD")
    bluesky_jwt = bluesky._login(handle, password) if handle and password else None
    mastodon_token = os.environ.get("MASTODON_ACCESS_TOKEN")
    print(
        f"Sismos a recuperar: {len(events)} | Bluesky: {'si' if bluesky_jwt else 'NO (sin credenciales)'} "
        f"| Mastodon: {'si' if mastodon_token else 'NO (sin token)'}"
    )

    now = datetime.now(timezone.utc)
    done_ids = []
    total_live = total_media = 0
    for event in events:
        event_time = datetime.fromisoformat(event["time"])
        since, until = event_time - BEFORE, event_time + AFTER
        if until > now:
            print(f"- {event['time'][:16]} {event['place']}: su ventana de 6 h no termino, la cubre la recoleccion normal.")
            continue

        live = collect._with_chile_flag(fetch_live(event, since, until, bluesky_jwt, mastodon_token), "text")
        media = collect._with_chile_flag(fetch_media(event, since, until), "title")
        live = [m for m in live if m["chile"]]
        media = [m for m in media if m["chile"]]
        print(f"- {event['time'][:16]} M{event['magnitude']} {event['place']}: {len(live)} publicaciones, {len(media)} noticias sobre Chile")

        if not args.dry_run:
            total_live += storage.archive_mentions("live", live)
            total_media += storage.archive_mentions("media", media)
        done_ids.append(event["id"])

    if not args.dry_run and done_ids:
        storage.record_backfilled("live", done_ids)
        storage.record_backfilled("media", done_ids)
    print(f"Listo: {total_live} publicaciones y {total_media} noticias nuevas archivadas ({len(done_ids)} sismos).")


if __name__ == "__main__":
    main()
