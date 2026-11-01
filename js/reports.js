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

  return {
    isDemo,

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
