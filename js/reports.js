/*
 * Cliente de "¿Lo sentiste?": enviar la respuesta de una persona y leer el
 * resumen de un sismo. Dos modos segun js/config.js:
 *  - reportsApi con URL: habla con el Worker (reports-worker/), que es la
 *    version real, compartida entre todas las personas.
 *  - reportsApi vacio: MODO DEMOSTRACION, todo queda en localStorage de este
 *    navegador (nadie mas lo ve). Sirve para probar la experiencia sin servidor.
 */
window.SismosApp = window.SismosApp || {};

SismosApp.LEVELS = [
  { value: 0, label: "No lo sentí" },
  { value: 1, label: "Muy leve" },
  { value: 2, label: "Leve" },
  { value: 3, label: "Moderado" },
  { value: 4, label: "Fuerte" },
  { value: 5, label: "Muy fuerte o con daños" },
];

// Comunas para el campo opcional (las mismas del set base del backend).
SismosApp.COMUNAS = [
  "Alto del Carmen", "Alto Hospicio", "Ancud", "Andacollo", "Angol", "Antofagasta", "Arica", "Calama", "Castro",
  "Chillán", "Concepción", "Copiapó", "Coquimbo", "Coronel", "Coyhaique", "Curicó", "Illapel", "Iquique",
  "La Florida", "La Higuera", "La Serena", "Las Condes", "Linares", "Los Andes", "Los Ángeles", "Maipú",
  "Monte Patria", "Osorno", "Ovalle", "Paihuano", "Puente Alto", "Puerto Montt", "Puerto Natales", "Puerto Varas",
  "Punitaqui", "Punta Arenas", "Quillota", "Rancagua", "Rengo", "Río Hurtado", "San Antonio", "San Fernando",
  "Santiago", "Talca", "Talcahuano", "Temuco", "Tocopilla", "Tongoy", "Valdivia", "Vallenar", "Valparaíso",
  "Vicuña", "Villarrica", "Viña del Mar",
];

SismosApp.reports = (function () {
  const DEMO_KEY = "sismos_demo_reports_v1"; // { [event_id]: {intensity, comuna} }
  const MY_KEY = "sismos_my_reports_v1"; // lo que ESTE navegador ya respondio (modo real)

  const apiUrl = () => ((window.SISMOS_CONFIG || {}).reportsApi || "").replace(/\/$/, "");
  const isDemo = () => !apiUrl();

  const readJson = (key) => {
    try {
      return JSON.parse(localStorage.getItem(key) || "{}");
    } catch (err) {
      return {};
    }
  };
  const writeJson = (key, value) => {
    try {
      localStorage.setItem(key, JSON.stringify(value));
    } catch (err) {
      /* sin almacenamiento (modo privado): la respuesta igual se envia/muestra */
    }
  };

  const emptySummary = (eventId) => ({
    event_id: eventId,
    total: 0,
    felt: 0,
    not_felt: 0,
    by_level: { 0: 0, 1: 0, 2: 0, 3: 0, 4: 0, 5: 0 },
    top_comunas: [],
  });

  // En demo el "resumen" es solo la respuesta de esta persona (no hay nadie mas).
  const demoSummary = (eventId) => {
    const mine = readJson(DEMO_KEY)[eventId];
    const summary = emptySummary(eventId);
    if (mine) {
      summary.total = 1;
      summary.by_level[mine.intensity] = 1;
      if (mine.intensity > 0) {
        summary.felt = 1;
        if (mine.comuna) summary.top_comunas = [{ comuna: mine.comuna, n: 1 }];
      } else {
        summary.not_felt = 1;
      }
    }
    return summary;
  };

  // Turnstile (anti-robots de Cloudflare): se carga solo cuando hace falta y en modo
  // "interaction-only", asi casi nadie ve un desafio. Cada token sirve para UN envio.
  const siteKey = () => (window.SISMOS_CONFIG || {}).turnstileSiteKey || "";
  let turnstileScript = null;
  const loadTurnstile = () => {
    if (!turnstileScript) {
      turnstileScript = new Promise((resolve, reject) => {
        const script = document.createElement("script");
        script.src = "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";
        script.async = true;
        script.onload = () => resolve(window.turnstile);
        script.onerror = () => {
          turnstileScript = null;
          reject(new Error("No se pudo cargar la verificación anti-robots"));
        };
        document.head.appendChild(script);
      });
    }
    return turnstileScript;
  };

  // Sismos en los que se puede responder: los de Chile (CSN) y los de Peru y Argentina,
  // que vienen de USGS y llevan el pais al final de la referencia ("42 km SW of Tacna,
  // Peru"). Es solo para mostrar el recuadro: el servidor aplica la misma regla sobre
  // sus propios datos. Bolivia tambien limita con Chile; para sumarla, agregarla aca y
  // en NEIGHBOR_COUNTRY_PATTERN del servidor.
  const NEIGHBOR_COUNTRY_PATTERN = /\b(Peru|Perú|Argentina)\b/i;
  const isReportable = (event) =>
    event.source === "csn" || (event.source === "usgs" && NEIGHBOR_COUNTRY_PATTERN.test(String(event.place || "")));

  return {
    isDemo,
    isReportable,

    // Pone el widget anti-robots en `container`. Devuelve null si no hace falta (demo o
    // sin clave), o { token(), reset(), remove() }.
    mountTurnstile(container) {
      if (isDemo() || !siteKey()) return null;
      let token = "";
      let widgetId = null;
      let waiters = [];
      const settle = (fn) => waiters.splice(0).forEach(fn);
      const ready = loadTurnstile().then((turnstile) => {
        widgetId = turnstile.render(container, {
          sitekey: siteKey(),
          appearance: "interaction-only",
          callback: (value) => {
            token = value;
            settle((w) => w.resolve(value));
          },
          "expired-callback": () => {
            token = "";
          },
          "error-callback": () => settle((w) => w.reject(new Error("La verificación anti-robots falló. Recarga la página."))),
        });
      });
      return {
        async token() {
          await ready;
          if (token) return token;
          return new Promise((resolve, reject) => {
            waiters.push({ resolve, reject });
            setTimeout(() => reject(new Error("La verificación tarda demasiado. Intenta de nuevo.")), 20000);
          });
        },
        reset() {
          token = "";
          if (widgetId !== null && window.turnstile) window.turnstile.reset(widgetId);
        },
        remove() {
          ready.then(() => {
            if (widgetId !== null && window.turnstile) window.turnstile.remove(widgetId);
          }).catch(() => {});
        },
      };
    },

    // Lo que esta persona ya respondio para este sismo (o null).
    myAnswer(eventId) {
      const store = readJson(isDemo() ? DEMO_KEY : MY_KEY);
      return store[eventId] || null;
    },

    async summary(eventId) {
      if (isDemo()) return demoSummary(eventId);
      const response = await fetch(`${apiUrl()}/reports?event_id=${encodeURIComponent(eventId)}`);
      if (!response.ok) throw new Error("No se pudo leer el resumen");
      return response.json();
    },

    async submit(event, intensity, comuna, turnstileToken) {
      const key = isDemo() ? DEMO_KEY : MY_KEY;
      let summary;
      if (isDemo()) {
        const store = readJson(DEMO_KEY);
        store[event.id] = { intensity, comuna: comuna || null };
        writeJson(DEMO_KEY, store);
        summary = demoSummary(event.id);
      } else {
        const response = await fetch(`${apiUrl()}/reports`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            event_id: event.id,
            event_day: event.time.slice(0, 10),
            intensity,
            comuna: comuna || null,
            turnstile_token: turnstileToken || null,
          }),
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.error || "No se pudo enviar tu respuesta");
        summary = data;
        const mine = readJson(key);
        mine[event.id] = { intensity, comuna: comuna || null };
        writeJson(key, mine);
      }
      return summary;
    },
  };
})();
