/* Carga data/index.json y los archivos diarios que necesita el dashboard. */
window.SismosApp = window.SismosApp || {};

// data/YYYY-MM-DD.json particiona por fecha UTC (ver storage._event_date en
// el backend), pero Chile esta en UTC-3 (o UTC-4 en horario de invierno) --
// las ultimas ~3-4 horas de cada dia CHILENO (Chile 20/21:00 a 23:59) caen
// en el archivo UTC del dia SIGUIENTE. Antes esta funcion pedia
// "index.days.slice(-days)" -- los ultimos N archivos que existieran, sin
// importar de que fecha. Eso funciona la mayor parte del dia, pero en esas
// horas de la tarde/noche chilena, el archivo del "dia UTC siguiente" ya
// existe (por el desborde de hoy) y la ventana de N archivos se CORRE un
// lugar hacia adelante en vez de agrandarse -- se pierde el archivo mas
// viejo que en realidad todavia hace falta, y un dia entero con sismos
// reales queda en 0 en el resumen semanal (confirmado: 16 sismos M>=4.5
// del 16-09 invisibles mientras se investigaba este bug). El fix es pedir
// las fechas chilenas exactas que se necesitan, calculadas a mano (nunca
// depender de "los ultimos N que haya"): desde hoy-(days-1) hasta
// hoy+1 -- ese +1 es justamente el archivo UTC que puede tener el desborde
// de la noche de hoy. Un archivo que todavia no existe (el "+1" antes de
// que llegue la noche) simplemente no esta y se lo salta mas abajo.
function _recentUtcDayKeys(days) {
  const todayChile = new Date().toLocaleDateString("en-CA", { timeZone: "America/Santiago" });
  const [y, m, d] = todayChile.split("-").map(Number);
  const anchor = Date.UTC(y, m - 1, d, 12); // mismo truco que chileDateKey en app.js
  const keys = [];
  for (let i = days - 1; i >= -1; i--) {
    const dt = new Date(anchor - i * 24 * 60 * 60 * 1000);
    keys.push(
      `${dt.getUTCFullYear()}-${String(dt.getUTCMonth() + 1).padStart(2, "0")}-${String(dt.getUTCDate()).padStart(2, "0")}`
    );
  }
  return keys;
}

SismosApp.loadRecentEvents = async function (days) {
  const indexResponse = await fetch("data/index.json", { cache: "no-store" });
  if (!indexResponse.ok) {
    throw new Error(
      "No se encontro data/index.json todavia (esperando la primera recoleccion de GitHub Actions)."
    );
  }
  // Las fechas se calculan a mano (ver _recentUtcDayKeys), pero solo se piden
  // las que existen segun index.json -- el "+1" de hoy no existe hasta la
  // noche y pedirlo daba un 404 en cada refresco.
  const index = await indexResponse.json();
  const existingDays = new Set(index.days || []);
  const dayFiles = _recentUtcDayKeys(days).filter((day) => existingDays.has(day));

  const allEvents = [];
  for (const day of dayFiles) {
    try {
      const response = await fetch(`data/${day}.json`, { cache: "no-store" });
      if (!response.ok) continue;
      const events = await response.json();
      allEvents.push(...events);
    } catch (err) {
      console.warn(`No se pudo cargar data/${day}.json`, err);
    }
  }
  return allEvents;
};

/*
 * Trazabilidad por sismo: publicaciones (Redes en vivo) y noticias (Menciones
 * en medios) archivadas alrededor de un sismo -- ver storage.archive_mentions
 * en el backend. Se muestran solo las que hablan de Chile (campo `chile`) y
 * se publicaron desde 5 minutos antes hasta 6 horas despues de la hora del
 * sismo. Los archivos son por dia UTC (igual que los de sismos), asi que una
 * ventana de 6 h puede tocar 2 archivos.
 */
const TRACE_BEFORE_MS = 5 * 60 * 1000;
const TRACE_AFTER_MS = 6 * 60 * 60 * 1000;
const TRACE_MAX_ITEMS = 100;

async function _loadArchiveDay(kind, day) {
  const response = await fetch(`data/archive/${kind}/${day}.json`, { cache: "no-store" });
  if (!response.ok) return [];
  return response.json();
}

SismosApp.loadEventTrace = async function (event) {
  const eventMs = new Date(event.time).getTime();
  const startMs = eventMs - TRACE_BEFORE_MS;
  const endMs = eventMs + TRACE_AFTER_MS;

  let meta = {};
  try {
    const metaResponse = await fetch("data/archive/meta.json", { cache: "no-store" });
    if (metaResponse.ok) meta = await metaResponse.json();
  } catch (err) {
    // sin meta.json todavia (el archivo aun no existe) -- se muestra vacio, no es un error
  }

  const DAY_MS = 24 * 60 * 60 * 1000;
  const daysBetween = [];
  for (let d = Math.floor(startMs / DAY_MS) * DAY_MS; d <= endMs; d += DAY_MS) {
    daysBetween.push(new Date(d).toISOString().slice(0, 10));
  }

  const pick = async (kind) => {
    const available = new Set(meta[`${kind}_days`] || []);
    const days = daysBetween.filter((day) => available.has(day));
    const items = (await Promise.all(days.map((day) => _loadArchiveDay(kind, day)))).flat();
    const inWindow = items
      .filter((m) => m.chile === true)
      .filter((m) => {
        const ms = new Date(m.published).getTime();
        return ms >= startMs && ms <= endMs;
      })
      .sort((a, b) => new Date(a.published) - new Date(b.published));
    return { items: inWindow.slice(0, TRACE_MAX_ITEMS), total: inWindow.length };
  };

  const [live, media] = await Promise.all([pick("live"), pick("media")]);
  return {
    live,
    media,
    liveFrom: meta.live_from ? new Date(meta.live_from) : null,
    mediaFrom: meta.media_from ? new Date(meta.media_from) : null,
    startMs,
    endMs,
  };
};

/* Fecha minima/maxima disponibles, segun data/index.json (para el selector de fecha). */
SismosApp.loadAvailableDateRange = async function () {
  const indexResponse = await fetch("data/index.json", { cache: "no-store" });
  if (!indexResponse.ok) return null;
  const index = await indexResponse.json();
  const days = index.days || [];
  if (days.length === 0) return null;
  return { min: days[0], max: days[days.length - 1] };
};

/* Trae los eventos de un dia puntual (para la tabla "Sismos por dia"). */
SismosApp.loadDay = async function (dateStr) {
  const response = await fetch(`data/${dateStr}.json`, { cache: "no-store" });
  if (!response.ok) return [];
  return response.json();
};

/* Menciones recientes en medios (RSS) -- ver js/social-layer.js. */
SismosApp.loadSocialMentions = async function () {
  const response = await fetch("data/social_mentions.json", { cache: "no-store" });
  if (!response.ok) return [];
  return response.json();
};

/* Posts de Bluesky + Mastodon con las palabras clave del proyecto -- ver js/live-feed.js. */
SismosApp.loadLiveMentions = async function () {
  const response = await fetch("data/live_mentions.json", { cache: "no-store" });
  if (!response.ok) return [];
  return response.json();
};
