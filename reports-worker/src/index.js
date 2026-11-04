/*
 * Servidor de "¿Lo sentiste?" (Cloudflare Worker + D1, plan gratuito).
 *
 * El dashboard es una pagina estatica (GitHub Pages) y no puede guardar datos
 * por si misma. Este Worker recibe los reportes de las personas y entrega el
 * resumen por sismo. No guarda nada personal: solo el nivel que la persona
 * eligio, una comuna opcional y un hash de su IP (con una sal secreta) para
 * que cada persona cuente UNA vez por sismo. Nunca devuelve reportes
 * individuales, solo totales.
 *
 * Defensas contra reportes falsos (todas del lado del servidor):
 *  - el sismo tiene que existir en los datos publicos del dashboard, ser de
 *    Chile (CSN) o de un pais vecino habilitado (hoy Peru y Argentina, via
 *    USGS) y tener menos de REPORT_WINDOW_HOURS horas (7 dias por defecto);
 *  - una persona (hash de IP) cuenta una vez por sismo (puede cambiar su
 *    respuesta) y tiene un tope de reportes por hora;
 *  - Cloudflare Turnstile opcional: si TURNSTILE_SECRET esta configurado, se
 *    exige el token del widget.
 */

const INTENSITY_LABELS = { 0: "No lo senti", 1: "Muy leve", 2: "Leve", 3: "Moderado", 4: "Fuerte", 5: "Muy fuerte o con danos" };
// Dos formas de id: informe del CSN (Chile) o id de USGS (paises vecinos). Que el id
// tenga la forma correcta no basta: el sismo igual debe existir en los datos publicos
// y cumplir isReportable() para que se acepte un reporte.
const CSN_ID_PATTERN = /^csn:https:\/\/www\.sismologia\.cl\/sismicidad\/informes\/\d{4}\/\d{2}\/\d+\.html$/;
const USGS_ID_PATTERN = /^[a-z]{2}[a-z0-9]{4,20}$/;
const isValidEventId = (id) => typeof id === "string" && (CSN_ID_PATTERN.test(id) || USGS_ID_PATTERN.test(id));

// Paises vecinos de Chile habilitados (a pedido: a veces se sienten en Chile sismos que
// ocurren alla). El pais sale del texto de referencia que USGS publica ("42 km SW of
// Tacna, Peru"), leido de los datos del dashboard y NO de lo que mande el navegador.
// Bolivia tambien limita con Chile; para agregarla basta sumarla aca y en js/reports.js.
const NEIGHBOR_COUNTRY_PATTERN = /\b(Peru|Perú|Argentina)\b/i;
const isReportable = (event) =>
  event.source === "csn" || (event.source === "usgs" && NEIGHBOR_COUNTRY_PATTERN.test(String(event.place || "")));
const DAY_PATTERN = /^\d{4}-\d{2}-\d{2}$/;
const MAX_REPORTS_PER_HOUR = 20;
// Solo se aceptan comunas de esta lista (la misma del formulario): asi no entra
// texto libre y los totales por comuna no se ensucian ni se pueden falsificar.
const COMUNAS = new Set([
  "Alto del Carmen", "Alto Hospicio", "Ancud", "Andacollo", "Angol", "Antofagasta", "Arica", "Calama", "Castro",
  "Chillán", "Concepción", "Copiapó", "Coquimbo", "Coronel", "Coyhaique", "Curicó", "Illapel", "Iquique",
  "La Florida", "La Higuera", "La Serena", "Las Condes", "Linares", "Los Andes", "Los Ángeles", "Maipú",
  "Monte Patria", "Osorno", "Ovalle", "Paihuano", "Puente Alto", "Puerto Montt", "Puerto Natales", "Puerto Varas",
  "Punitaqui", "Punta Arenas", "Quillota", "Rancagua", "Rengo", "Río Hurtado", "San Antonio", "San Fernando",
  "Santiago", "Talca", "Talcahuano", "Temuco", "Tocopilla", "Tongoy", "Valdivia", "Vallenar", "Valparaíso",
  "Vicuña", "Villarrica", "Viña del Mar",
]);

function config(env) {
  return {
    siteUrl: (env.SITE_URL || "https://blarracea.github.io/monitor-intensidades").replace(/\/$/, ""),
    allowedOrigins: (env.ALLOWED_ORIGINS || "https://blarracea.github.io,http://localhost:8000,http://localhost:8001")
      .split(",")
      .map((o) => o.trim())
      .filter(Boolean),
    windowHours: Number(env.REPORT_WINDOW_HOURS || 168), // 7 dias (antes 24 h)
  };
}

function corsHeaders(request, env) {
  const origin = request.headers.get("Origin");
  const allowed = config(env).allowedOrigins;
  const headers = { Vary: "Origin", "Access-Control-Allow-Methods": "GET, POST, OPTIONS", "Access-Control-Allow-Headers": "Content-Type" };
  if (origin && allowed.includes(origin)) headers["Access-Control-Allow-Origin"] = origin;
  return headers;
}

function json(data, status, request, env) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store", ...corsHeaders(request, env) },
  });
}

async function sha256Hex(text) {
  const bytes = new TextEncoder().encode(text);
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

async function voterHash(request, env) {
  const ip = request.headers.get("CF-Connecting-IP") || request.headers.get("X-Forwarded-For") || "desconocida";
  return sha256Hex(`${ip}|${env.VOTER_SALT || "sal-de-prueba-cambiar"}`);
}

// Busca el sismo en los datos publicos del dashboard (el mismo archivo que lee
// la pagina) para saber su hora REAL -- nunca se confia en la hora que manda el
// navegador.
async function findPublishedEvent(eventId, day, env, fetchImpl) {
  const response = await fetchImpl(`${config(env).siteUrl}/data/${day}.json`);
  if (!response.ok) return null;
  const events = await response.json();
  return events.find((e) => e.id === eventId && isReportable(e)) || null;
}

async function verifyTurnstile(token, request, env, fetchImpl) {
  if (!env.TURNSTILE_SECRET) return true; // sin secreto configurado no se exige (modo prueba)
  if (!token) return false;
  const form = new URLSearchParams({ secret: env.TURNSTILE_SECRET, response: token });
  const ip = request.headers.get("CF-Connecting-IP");
  if (ip) form.set("remoteip", ip);
  const response = await fetchImpl("https://challenges.cloudflare.com/turnstile/v0/siteverify", { method: "POST", body: form });
  const result = await response.json();
  return result.success === true;
}

export async function handleSubmit(request, env, fetchImpl = fetch, now = new Date()) {
  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: "JSON invalido" }, 400, request, env);
  }

  const { event_id: eventId, event_day: day, intensity, comuna, turnstile_token: token } = body || {};
  if (!isValidEventId(eventId)) return json({ error: "Sismo invalido" }, 400, request, env);
  if (typeof day !== "string" || !DAY_PATTERN.test(day)) return json({ error: "Dia invalido" }, 400, request, env);
  if (!Number.isInteger(intensity) || intensity < 0 || intensity > 5) return json({ error: "Nivel invalido" }, 400, request, env);

  let cleanComuna = null;
  if (comuna != null && comuna !== "") {
    if (typeof comuna !== "string" || !COMUNAS.has(comuna)) return json({ error: "Comuna invalida" }, 400, request, env);
    cleanComuna = comuna;
  }

  if (!(await verifyTurnstile(token, request, env, fetchImpl))) return json({ error: "No se pudo verificar que eres una persona" }, 403, request, env);

  const event = await findPublishedEvent(eventId, day, env, fetchImpl);
  if (!event) return json({ error: "Ese sismo no existe en el dashboard" }, 404, request, env);
  const ageHours = (now - new Date(event.time)) / 3600000;
  if (ageHours < 0 || ageHours > config(env).windowHours) {
    const hours = config(env).windowHours;
    const limit = hours % 24 === 0 ? `${hours / 24} dias` : `${hours} h`;
    return json({ error: `Solo se reciben reportes hasta ${limit} despues del sismo` }, 422, request, env);
  }

  const voter = await voterHash(request, env);
  const sinceIso = new Date(now.getTime() - 3600000).toISOString();
  const recent = await env.DB.prepare("SELECT COUNT(*) AS n FROM reports WHERE voter_hash = ? AND created_at >= ?").bind(voter, sinceIso).first();
  if ((recent?.n || 0) >= MAX_REPORTS_PER_HOUR) return json({ error: "Demasiados reportes, intenta mas tarde" }, 429, request, env);

  await env.DB.prepare(
    `INSERT INTO reports (event_id, voter_hash, intensity, comuna, created_at) VALUES (?, ?, ?, ?, ?)
     ON CONFLICT(event_id, voter_hash) DO UPDATE SET intensity = excluded.intensity, comuna = excluded.comuna, created_at = excluded.created_at`
  )
    .bind(eventId, voter, intensity, cleanComuna, now.toISOString())
    .run();

  return json(await summarize(eventId, env), 200, request, env);
}

export async function handleSummary(request, env) {
  const eventId = new URL(request.url).searchParams.get("event_id");
  if (!isValidEventId(eventId)) return json({ error: "Sismo invalido" }, 400, request, env);
  return json(await summarize(eventId, env), 200, request, env);
}

async function summarize(eventId, env) {
  const byLevel = await env.DB.prepare("SELECT intensity, COUNT(*) AS n FROM reports WHERE event_id = ? GROUP BY intensity").bind(eventId).all();
  const byComuna = await env.DB.prepare(
    "SELECT comuna, COUNT(*) AS n FROM reports WHERE event_id = ? AND comuna IS NOT NULL AND intensity > 0 GROUP BY comuna ORDER BY n DESC LIMIT 5"
  )
    .bind(eventId)
    .all();

  const levels = { 0: 0, 1: 0, 2: 0, 3: 0, 4: 0, 5: 0 };
  for (const row of byLevel.results || []) levels[row.intensity] = row.n;
  const total = Object.values(levels).reduce((a, b) => a + b, 0);
  return {
    event_id: eventId,
    total,
    felt: total - levels[0],
    not_felt: levels[0],
    by_level: levels,
    top_comunas: (byComuna.results || []).map((r) => ({ comuna: r.comuna, n: r.n })),
  };
}

export default {
  async fetch(request, env) {
    if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: corsHeaders(request, env) });
    const { pathname } = new URL(request.url);
    try {
      if (pathname === "/reports" && request.method === "POST") return await handleSubmit(request, env);
      if (pathname === "/reports" && request.method === "GET") return await handleSummary(request, env);
      if (pathname === "/") return json({ ok: true, servicio: "lo-sentiste", niveles: INTENSITY_LABELS }, 200, request, env);
      return json({ error: "No encontrado" }, 404, request, env);
    } catch (err) {
      return json({ error: "Error interno" }, 500, request, env);
    }
  },
};
