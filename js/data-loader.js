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
  const dayFiles = _recentUtcDayKeys(days);

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
