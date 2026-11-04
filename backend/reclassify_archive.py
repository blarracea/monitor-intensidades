"""
Recalcula el campo `chile` de todo el archivo permanente (data/archive/) con las
reglas ACTUALES de keywords.is_chile_quake_post -- el archivo solo agrega
enlaces nuevos y nunca reescribe lo ya guardado, asi que cuando se mejora la
clasificacion (ej. la auditoria del 24-09-2026: el trago "terremoto" de
Fiestas Patrias y "Los Angeles" de EE.UU. salian como "sobre Chile") hay que
correr esto una vez para corregir lo anterior:

    cd backend
    python reclassify_archive.py [--dry-run]

Es idempotente. Live: se recalcula desde el texto. Media: el archivo no guarda
el dominio del medio, asi que se parte del valor guardado (que si lo uso al
archivar) y solo se le exige ademas que el titulo hable de un sismo.
"""
import argparse
import json

import comuna_coords
import keywords
import storage
from sources import social


def _write(path, items):
    with path.open("w", encoding="utf-8") as f:
        f.write("[\n" + ",\n".join(json.dumps(m, ensure_ascii=False, separators=(",", ":")) for m in items) + "\n]\n")


def reclassify(kind, item):
    if kind == "live":
        place, _ = comuna_coords.find_known_place(item["text"])
        return keywords.is_chile_quake_post(item["text"], place, item.get("author_handle"))
    place = item.get("place")
    keeps_chile_source = item["chile"] or social.is_chilean_outlet_name(item.get("source"))
    return keywords.is_quake_report(item["title"]) and (keeps_chile_source or keywords.mentions_chile(item["title"], place))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    for kind in ("live", "media"):
        turned_off = turned_on = 0
        for path in sorted((storage.ARCHIVE_DIR / kind).glob("*.json")):
            items = json.loads(path.read_text(encoding="utf-8"))
            changed = False
            for item in items:
                new = reclassify(kind, item)
                if new != item["chile"]:
                    turned_off += item["chile"]
                    turned_on += new
                    print(f"  {kind} {item['published'][:16]} {'-> NO chile' if not new else '-> chile   '} | {(item.get('text') or item.get('title'))[:80]!r}")
                    item["chile"] = new
                    changed = True
            if changed and not args.dry_run:
                _write(path, items)
        print(f"{kind}: {turned_off} pasan a NO-Chile, {turned_on} pasan a Chile")


if __name__ == "__main__":
    main()
