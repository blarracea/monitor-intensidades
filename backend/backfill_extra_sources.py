"""
Completa gfz_url y ptwc_url en TODOS los dias ya guardados (la recoleccion normal solo reprocesa los
ultimos dias). Sirve una sola vez tras agregar GFZ (GEOFON) y PTWC, o para recuperar un hueco:

    SISMOS_DATA_DIR=<ruta a data/> python backend/backfill_extra_sources.py [dias_hacia_atras]

Baja el archivo de boletines del PTWC (el feed solo trae el ultimo, ver sources/ptwc.py) y el catalogo
de GFZ por tramos, y solo AGREGA enlaces: no cambia ningun otro dato del sismo.
"""
import sys
from datetime import datetime, timedelta, timezone

import collect
import storage
from sources import gfz, ptwc

GFZ_CHUNK_DAYS = 20


def main(days_back=120):
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=days_back)

    print("PTWC: bajando el archivo de boletines...")
    bulletins = storage.save_ptwc_bulletins(ptwc.fetch_archive(since))
    print(f"PTWC: {len(bulletins)} boletines guardados.")

    gfz_events = []
    cursor = since
    while cursor < now:
        end = min(cursor + timedelta(days=GFZ_CHUNK_DAYS), now)
        try:
            chunk = gfz.fetch_events(cursor, end, collect.MIN_MAGNITUDE)
        except Exception as exc:
            print(f"GFZ: fallo el tramo {cursor.date()} a {end.date()} ({exc}); se omite.")
            chunk = []
        gfz_events.extend(chunk)
        cursor = end
    print(f"GFZ: {len(gfz_events)} sismos descargados.")

    changed_days = 0
    for path in sorted(storage.DATA_DIR.glob("*.json")):
        if not storage._is_day_file(path):
            continue
        day = path.stem
        events = storage.load_day(day)
        before = [(e.get("gfz_url"), e.get("ptwc_url")) for e in events]
        for event in events:
            event.setdefault("gfz_url", None)
            event.setdefault("ptwc_url", None)
        collect.apply_gfz(events, gfz_events)
        collect.apply_ptwc(events, bulletins)
        after = [(e.get("gfz_url"), e.get("ptwc_url")) for e in events]
        if before != after:
            storage.save_day(day, events)
            changed_days += 1
    print(f"Listo: {changed_days} dias actualizados.")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 120)
