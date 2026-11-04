"""
Valida el SQL de reports-worker/ contra SQLite real (D1 es SQLite): corre
schema.sql y las mismas consultas que usa src/index.js. Se ejecuta sin instalar
nada:   python reports-worker/test/sql-test.py

Las consultas estan copiadas de src/index.js; si se cambian alla, cambiarlas aca
(esta prueba lo avisa si el texto ya no coincide).
"""
import pathlib
import re
import sqlite3

root = pathlib.Path(__file__).resolve().parent.parent
source = (root / "src" / "index.js").read_text(encoding="utf-8")

QUERIES = {
    "count": "SELECT COUNT(*) AS n FROM reports WHERE voter_hash = ? AND created_at >= ?",
    "by_level": "SELECT intensity, COUNT(*) AS n FROM reports WHERE event_id = ? GROUP BY intensity",
    "by_comuna": "SELECT comuna, COUNT(*) AS n FROM reports WHERE event_id = ? AND comuna IS NOT NULL AND intensity > 0 GROUP BY comuna ORDER BY n DESC LIMIT 5",
}
UPSERT = """INSERT INTO reports (event_id, voter_hash, intensity, comuna, created_at) VALUES (?, ?, ?, ?, ?)
     ON CONFLICT(event_id, voter_hash) DO UPDATE SET intensity = excluded.intensity, comuna = excluded.comuna, created_at = excluded.created_at"""

for name, query in {**QUERIES, "upsert": UPSERT}.items():
    assert " ".join(query.split()) in " ".join(source.split()), f"la consulta '{name}' ya no coincide con src/index.js"

db = sqlite3.connect(":memory:")
db.executescript((root / "schema.sql").read_text(encoding="utf-8"))
ev = "csn:https://www.sismologia.cl/sismicidad/informes/2026/11/1.html"

def put(voter, level, comuna=None, when="2026-11-01T10:00:00Z", event=ev):
    db.execute(UPSERT, (event, voter, level, comuna, when))

put("a", 3, "Iquique"); put("b", 0); put("c", 4, "Iquique"); put("d", 4, "Arica")
put("a", 5, "Arica")  # a cambia de respuesta: sigue siendo una fila
n_rows = db.execute("SELECT COUNT(*) FROM reports").fetchone()[0]
assert n_rows == 4, f"upsert duplico filas: {n_rows}"
by_level = dict(db.execute(QUERIES["by_level"], (ev,)).fetchall())
assert by_level == {0: 1, 4: 2, 5: 1}, by_level
top = db.execute(QUERIES["by_comuna"], (ev,)).fetchall()
assert dict(top) == {"Arica": 2, "Iquique": 1}, top  # 'b' (no lo sintio) no cuenta
assert db.execute(QUERIES["count"], ("a", "2026-11-01T09:00:00Z")).fetchone()[0] == 1
assert db.execute(QUERIES["count"], ("a", "2026-11-01T11:00:00Z")).fetchone()[0] == 0

for bad in (6, -1):
    try:
        put("x", bad)
        raise SystemExit(f"FALLA: el esquema acepto nivel {bad}")
    except sqlite3.IntegrityError:
        pass
print("SQL OK: esquema, upsert (1 persona = 1 fila), conteos por nivel y comuna, tope por hora y restriccion de nivel 0..5")
