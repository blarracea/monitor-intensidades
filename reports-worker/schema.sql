-- Tabla de reportes "¿Lo sentiste?". Una fila por (sismo, persona): una persona
-- cuenta una vez por sismo y puede cambiar su respuesta (ver ON CONFLICT en
-- src/index.js). voter_hash es SHA-256(ip + sal secreta), nunca la IP.
-- intensity: 0 = no lo senti, 1..5 = de muy leve a muy fuerte.
CREATE TABLE IF NOT EXISTS reports (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id TEXT NOT NULL,
  voter_hash TEXT NOT NULL,
  intensity INTEGER NOT NULL CHECK (intensity BETWEEN 0 AND 5),
  comuna TEXT,
  created_at TEXT NOT NULL,
  UNIQUE (event_id, voter_hash)
);
CREATE INDEX IF NOT EXISTS idx_reports_event ON reports (event_id);
CREATE INDEX IF NOT EXISTS idx_reports_voter_time ON reports (voter_hash, created_at);
