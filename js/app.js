/* Orquesta la carga de datos y el armado del mapa. */
(async function () {
  const statusEl = document.getElementById("status");
  const detailPlaceholder = document.getElementById("event-detail-placeholder");
  const detailBody = document.getElementById("event-detail-body");
  const socialToggle = document.getElementById("toggle-social");
  const socialFeedBody = document.getElementById("social-feed-body");
  const liveFeedBody = document.getElementById("live-feed-body");
  const dayPicker = document.getElementById("day-picker");
  const dayTableBody = document.getElementById("day-table-body");
  const weekChartEl = document.getElementById("week-chart");
  const searchToggle = document.getElementById("search-toggle");
  const searchPanel = document.getElementById("search-panel");
  const liveTraceBanner = document.getElementById("live-trace-banner");
  const mediaTraceBanner = document.getElementById("media-trace-banner");

  // Trazabilidad: cuando hay un sismo elegido (desde el mapa o la lupa), los
  // paneles "Redes en vivo" y "Menciones en medios" dejan de mostrar lo
  // ultimo y pasan a mostrar lo que se publico en torno a ESE sismo (ver
  // SismosApp.loadEventTrace). Mientras eso pasa, el refresco automatico de
  // cada 60 s no debe pisar la vista -- traceEvent lo frena.
  let traceEvent = null;
  let traceRequestId = 0;

  // La pagina no se refresca sola por si misma -- sin esto, alguien que deja
  // la pestana abierta nunca ve un sismo nuevo ni una mencion nueva sin
  // recargar a mano. El ciclo re-pide todo lo que cambia con el tiempo
  // (eventos, mapa, ambos feeds, tabla del dia de hoy) sin tocar la vista
  // del mapa ni el detalle que la persona tenga seleccionado.
  //
  // No hay forma de que esta pagina estatica se entere del momento exacto
  // en que GitHub Actions termina una recoleccion (no existe un canal para
  // que el backend le avise al navegador) -- y ademas el ciclo de
  // recoleccion no corre en horario fijo (recolecta, espera 300s, recolecta
  // de nuevo), asi que "a los N segundos de la recoleccion" no es algo a lo
  // que este cliente se pueda sincronizar. En su lugar, se pregunta seguido
  // (cada 60s) si hay datos nuevos -- logra el mismo objetivo real (ver lo
  // nuevo poco despues de que se recolecto) sin depender de un timing exacto.
  const REFRESH_INTERVAL_MS = 60 * 1000;

  const map = SismosApp.initMap();

  // Solo se muestran sismos con reporte de percepcion real del CSN/SENAPRED
  // -- se deja afuera el catalogo completo de USGS (incluye sismos chicos
  // sin ningun reporte ciudadano) y los que solo tienen DYFI de USGS.
  const hasSenapredReport = (event) => event.intensity_source === "csn";

  // --- Resumen semanal ("Sismos por dia", el mini grafico de barras) ---
  const WEEK_DAY_LETTERS = ["D", "L", "M", "M", "J", "V", "S"]; // Date#getUTCDay(): 0=domingo..6=sabado
  // A pedido: este resumen no depende de si el sismo tiene reporte
  // SENAPRED (a diferencia del resto del dashboard) -- solo cuenta
  // magnitud, sin importar si fue "sentido"/reportado o no.
  const WEEK_CHART_MIN_MAGNITUDE = 4.5;

  // yyyy-mm-dd de una fecha en hora de Chile, sin depender de la zona
  // horaria del navegador de quien mira el dashboard.
  const chileDateKey = (isoTime) =>
    new Date(isoTime).toLocaleDateString("en-CA", { timeZone: "America/Santiago" });

  const renderWeekChart = (events) => {
    if (!weekChartEl) return;
    const todayKey = chileDateKey(new Date().toISOString());
    const [y, m, d] = todayKey.split("-").map(Number);
    // Anclado a mediodia UTC del "hoy" en Chile: evita que sumar/restar
    // dias con la zona horaria local del navegador (que puede ser
    // cualquiera) empuje la fecha al dia de al lado.
    const anchor = Date.UTC(y, m - 1, d, 12);

    const days = [];
    for (let i = 6; i >= 0; i--) {
      const dt = new Date(anchor - i * 24 * 60 * 60 * 1000);
      const key = `${dt.getUTCFullYear()}-${String(dt.getUTCMonth() + 1).padStart(2, "0")}-${String(dt.getUTCDate()).padStart(2, "0")}`;
      days.push({ key, letter: WEEK_DAY_LETTERS[dt.getUTCDay()], count: 0 });
    }

    events
      .filter((event) => (event.magnitude ?? 0) >= WEEK_CHART_MIN_MAGNITUDE)
      .forEach((event) => {
        const key = chileDateKey(event.time);
        const day = days.find((d) => d.key === key);
        if (day) day.count += 1;
      });

    const maxCount = Math.max(1, ...days.map((day) => day.count));
    weekChartEl.innerHTML = days
      .map((day) => {
        const isToday = day.key === todayKey;
        const isEmpty = day.count === 0;
        const heightPct = isEmpty ? 6 : Math.max(14, Math.round((day.count / maxCount) * 100));
        const classes = ["week-chart-bar"];
        if (isToday) classes.push("week-chart-bar--today");
        if (isEmpty) classes.push("week-chart-bar--empty");
        return `
          <div class="${classes.join(" ")}" title="${day.count} sismo${day.count === 1 ? "" : "s"}">
            <span class="week-chart-count">${day.count}</span>
            <div class="week-chart-track">
              <div class="week-chart-fill" style="height:${heightPct}%"></div>
            </div>
            <span class="week-chart-label">${day.letter}</span>
          </div>
        `;
      })
      .join("");
  };

  // Anillo amarillo que marca cual es el sismo seleccionado (desde la tabla
  // o clickeando un marcador), para ubicarlo de un vistazo en el mapa.
  let selectionMarker = null;
  // Capa de intensidad solo del sismo seleccionado -- ver el comentario
  // en showEventDetail, mas abajo, para el problema que resuelve.
  let selectedEventHeatLayer = null;
  // URLs de los sismos que ya estan en el heatmap general (ultimos 7
  // dias) -- si el sismo elegido ya esta ahi, no hace falta (ni conviene)
  // agregarle una segunda capa encima: sus mismos puntos se dibujarian
  // dos veces y esa comuna se veria mas intensa de lo real mientras dure
  // seleccionada.
  let recentEventKeys = new Set();
  const highlightEvent = (event) => {
    if (selectionMarker) {
      map.removeLayer(selectionMarker);
      selectionMarker = null;
    }
    if (event.lat == null || event.lon == null) return;
    selectionMarker = L.circleMarker([event.lat, event.lon], {
      radius: 14,
      color: "#ffd400",
      weight: 3,
      fillOpacity: 0,
      className: "selection-ring",
    }).addTo(map);
  };

  // Mismo patron que _escapeHtml/_escapeAttr/_safeUrl en social-layer.js y
  // live-feed.js (sufijo "Detail" para no chocar con esos, que se cargan
  // como scripts sueltos sin modulos) -- innerHTML trata el string como
  // HTML, asi que hay que escapar texto libre y validar/escapar cualquier
  // URL antes de ponerla en un href.
  const _escapeHtmlDetail = (text) => {
    const div = document.createElement("div");
    div.textContent = text == null ? "" : String(text);
    return div.innerHTML;
  };
  const _escapeAttrDetail = (text) =>
    String(text || "")
      .replace(/&/g, "&amp;")
      .replace(/"/g, "&quot;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  const _safeUrlDetail = (url) => (typeof url === "string" && /^https?:\/\//i.test(url) ? url : "#");

  const showEventDetail = (event) => {
    highlightEvent(event);
    const eventDate = new Date(event.time);
    const timeFormat = {
      hour12: false,
      day: "2-digit",
      month: "2-digit",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    };
    const utcTime = eventDate.toLocaleString("es-CL", { ...timeFormat, timeZone: "UTC" });
    const chileTime = eventDate.toLocaleString("es-CL", { ...timeFormat, timeZone: "America/Santiago" });
    // Los sismos de Chile ya no pasan por USGS (ver auditoria/collect.py) --
    // su catalogo base es el CSN directo, asi que no tiene sentido mostrar
    // un link a USGS que ni se consulto para ellos. Para el resto de la
    // region (fuera de Chile) el catalogo sigue siendo USGS.
    //
    // place/url/csn_informe_url/senapred_url vienen de USGS y del scraping
    // del CSN -- fuentes externas igual que el RSS/redes (ver el mismo
    // escapeo en social-layer.js/live-feed.js), asi que se tratan con el
    // mismo cuidado aunque el riesgo real sea mas bajo (no es texto libre
    // de usuarios, pero tampoco es texto que este codigo controle).
    const fuenteParts =
      event.source === "csn"
        ? []
        : [`<a href="${_escapeAttrDetail(_safeUrlDetail(event.url))}" target="_blank" rel="noopener">USGS</a>`];
    if (event.csn_informe_url) {
      fuenteParts.push(`<a href="${_escapeAttrDetail(_safeUrlDetail(event.csn_informe_url))}" target="_blank" rel="noopener">CSN</a>`);
    }
    if (event.senapred_url) {
      fuenteParts.push(`<a href="${_escapeAttrDetail(_safeUrlDetail(event.senapred_url))}" target="_blank" rel="noopener">SENAPRED</a>`);
    }
    if (event.snam_url) {
      fuenteParts.push(`<a href="${_escapeAttrDetail(_safeUrlDetail(event.snam_url))}" target="_blank" rel="noopener">SNAM</a>`);
    }
    const fuenteLinks = fuenteParts.join(" · ");
    detailBody.innerHTML = `
      <dt>Referencia geográfica</dt><dd>${_escapeHtmlDetail(event.place || "-")}</dd>
      <dt>Magnitud</dt><dd>${_escapeHtmlDetail(event.magnitude ?? "-")} ${_escapeHtmlDetail(event.mag_type || "")}</dd>
      <dt>Hora (UTC)</dt><dd>${utcTime}</dd>
      <dt>Hora (Chile)</dt><dd>${chileTime}</dd>
      <dt>Fuente</dt><dd>${fuenteLinks}</dd>
    `;
    detailPlaceholder.classList.add("hidden");

    // El heatmap normal solo tiene los ultimos 7 dias (ver refreshEvents)
    // -- un sismo elegido desde "Sismos por dia" puede ser de cualquier
    // fecha del historial, bastante mas vieja que eso. Sin esto, el
    // detalle se llenaba pero el mapa de calor de ESE sismo (sus
    // intensidades por comuna) nunca aparecia porque sus puntos nunca
    // habian sido cargados. Se arma una capa aparte solo con este evento,
    // independiente de la ventana de 7 dias -- pero solo si hace falta:
    // si el sismo ya esta en la ventana de 7 dias, el heatmap general ya
    // tiene esos mismos puntos pintados, y agregar otra capa encima solo
    // duplicaria la intensidad de esa comuna mientras siga seleccionado.
    if (selectedEventHeatLayer) {
      map.removeLayer(selectedEventHeatLayer);
      selectedEventHeatLayer = null;
    }
    if (!recentEventKeys.has(event.url)) {
      selectedEventHeatLayer = SismosApp.buildHeatLayer([event]);
      if (selectedEventHeatLayer) {
        selectedEventHeatLayer.addTo(map);
      }
    }

    showTrace(event);
  };

  const focusEvent = (event) => {
    showEventDetail(event);
    if (event.lat != null && event.lon != null) {
      map.setView([event.lat, event.lon], 7);
    }
  };

  // --- Heatmap de intensidad + marcadores de eventos ---
  let heatLayer = null;
  let markersLayer = null;
  let unverifiedMarkersLayer = null;
  // A pedido: un sismo M5.0+ percibido puede no llegar nunca a tener un
  // reporte formal de intensidad -- antes eso lo dejaba totalmente fuera
  // del mapa, como si no hubiera pasado. 5.0 explicito (no
  // WEEK_CHART_MIN_MAGNITUDE): son dos pedidos distintos, con umbrales
  // que el usuario fijo por separado (4.5 para el resumen semanal, 5.0
  // para esto), aunque hoy coincida el mismo criterio de "sismo grande".
  const UNVERIFIED_MARKER_MIN_MAGNITUDE = 5.0;

  const addHeatLayer = () => {
    if (!heatLayer) return;
    // El contenedor #map recien termina su layout de CSS grid un instante
    // despues de whenReady -- si Leaflet.heat lee el ancho del canvas antes
    // de eso, lee 0 y el canvas queda roto para siempre (no se autocorrige
    // solo). setTimeout (no requestAnimationFrame, que no dispara si la
    // pestana no esta compositando frames) da tiempo a que el layout ya
    // este resuelto. Cada sub-capa (CSN, DYFI) se agrega por separado: si
    // una tira un error, L.LayerGroup no debe cortar el loop y dejar a la
    // otra sin agregar.
    map.invalidateSize();
    setTimeout(() => {
      heatLayer.eachLayer((layer) => {
        try {
          layer.addTo(map);
        } catch (err) {
          console.warn("No se pudo agregar una capa de heatmap", err);
        }
      });
    }, 50);
  };

  const removeHeatLayer = () => {
    if (!heatLayer) return;
    heatLayer.eachLayer((layer) => map.removeLayer(layer));
  };

  // El checkbox #toggle-heatmap se elimino del todo de index.html (ya no
  // existe ni oculto) -- el heatmap general no depende de ningun estado
  // "checked". Antes, si en algun refresco no habia ningun sismo con
  // reporte SENAPRED, el codigo dejaba el checkbox desmarcado para
  // "apagar" el heatmap, pero como nada lo volvia a marcar despues, el
  // mapa de calor general se quedaba apagado para siempre aunque despues
  // sí aparecieran sismos nuevos. Ahora simplemente se muestra cada vez
  // que hay datos, sin ese estado intermedio que nadie puede tocar.

  // Trae los eventos, reconstruye heatmap y marcadores. Se llama al iniciar
  // y despues cada REFRESH_INTERVAL_MS -- no toca el zoom/centro del mapa
  // ni el detalle seleccionado, solo los datos.
  const refreshEvents = async () => {
    let events;
    let allEvents;
    try {
      allEvents = await SismosApp.loadRecentEvents(7);
      events = allEvents.filter(hasSenapredReport);
      statusEl.textContent = `${events.length} sismos con reporte de SENAPRED (ultimos 7 dias).`;
    } catch (err) {
      statusEl.textContent = err.message;
      return;
    }

    recentEventKeys = new Set(events.map((event) => event.url));
    // A diferencia del resto del dashboard, el resumen semanal no exige
    // reporte SENAPRED -- se le pasan TODOS los eventos de la ventana
    // (allEvents, no el "events" ya filtrado) y el propio renderWeekChart
    // filtra solo por magnitud.
    renderWeekChart(allEvents);

    removeHeatLayer();
    heatLayer = SismosApp.buildHeatLayer(events);
    if (heatLayer) {
      map.whenReady(addHeatLayer);
    }

    if (markersLayer) map.removeLayer(markersLayer);
    markersLayer = SismosApp.addEventMarkers(map, events, showEventDetail);

    // Puntos azules para sismos grandes sin reporte de intensidad -- se
    // sacan de "allEvents" (no de "events", que ya filtro por reporte
    // SENAPRED) y se excluyen los que SI tienen reporte para no dibujar
    // dos marcadores superpuestos sobre el mismo sismo.
    if (unverifiedMarkersLayer) map.removeLayer(unverifiedMarkersLayer);
    const unverifiedBigEvents = allEvents.filter(
      (event) => (event.magnitude ?? 0) >= UNVERIFIED_MARKER_MIN_MAGNITUDE && !hasSenapredReport(event)
    );
    unverifiedMarkersLayer = SismosApp.addUnverifiedMarkers(map, unverifiedBigEvents, showEventDetail);
  };

  // --- Menciones en medios (RSS, no verificado) ---
  let socialLayer = null;
  socialToggle.addEventListener("change", () => {
    if (!socialLayer) return;
    if (socialToggle.checked) {
      socialLayer.addTo(map);
    } else {
      map.removeLayer(socialLayer);
    }
  });

  const refreshSocialFeed = async () => {
    try {
      const mentions = await SismosApp.loadSocialMentions();
      if (!traceEvent) SismosApp.renderSocialFeed(mentions, socialFeedBody);
      if (socialLayer) map.removeLayer(socialLayer);
      socialLayer = SismosApp.buildSocialMapLayer(mentions);
      if (socialLayer && socialToggle.checked) socialLayer.addTo(map);
    } catch (err) {
      socialFeedBody.innerHTML = '<p class="social-feed-empty">No se pudieron cargar las menciones.</p>';
    }
  };

  // --- Redes en vivo (Bluesky + Mastodon, posts con las palabras clave del proyecto) ---
  const refreshLiveFeed = async () => {
    if (traceEvent) return;
    try {
      const mentions = await SismosApp.loadLiveMentions();
      if (traceEvent) return; // se eligio un sismo mientras cargaba
      SismosApp.renderLiveFeed(mentions, liveFeedBody);
    } catch (err) {
      liveFeedBody.innerHTML = '<p class="live-feed-empty">No se pudieron cargar los posts.</p>';
    }
  };

  // --- Trazabilidad: publicaciones y noticias de un sismo elegido ---
  const chileDateTime = (date) => SismosApp.formatChileDateTime24(date);

  const traceBannerHtml = (event, title, summary, notes) => `
    <strong>${title} del sismo M${_escapeHtmlDetail(event.magnitude ?? "?")} · ${_escapeHtmlDetail(event.place || "-")}</strong>
    ${_escapeHtmlDetail(chileDateTime(new Date(event.time)))} (hora Chile) · ${_escapeHtmlDetail(summary)}
    ${notes.map((note) => `<div class="trace-note">${_escapeHtmlDetail(note)}</div>`).join("")}
    <button type="button" data-trace-reset>Volver a en vivo</button>
  `;

  // Aviso cuando el archivo continuo no alcanza a cubrir el sismo (parte el dia
  // que se empezo a guardar). Si el sismo se recupero por busqueda historica
  // (backfill) no se avisa nada, a pedido.
  const traceNotes = (archiveFrom, startMs, total, shown, backfilled) => {
    const notes = [];
    if (archiveFrom && startMs < archiveFrom.getTime() && !backfilled) {
      notes.push(`El archivo parte el ${chileDateTime(archiveFrom)}; este sismo es anterior, por eso puede faltar información.`);
    }
    if (total > shown) notes.push(`Mostrando las primeras ${shown} de ${total}.`);
    return notes;
  };

  const showTrace = async (event) => {
    traceEvent = event;
    const requestId = ++traceRequestId;

    // Solo los sismos chilenos del CSN tienen trazabilidad: el archivo es
    // "solo Chile", asi que para un sismo de otro pais lo unico que caeria en
    // su ventana horaria seria contenido de OTRO sismo chileno (auditoria
    // 24-09-2026: un sismo en Indonesia mostraba 22 posts y 58 noticias de
    // Farellones).
    if (event.source !== "csn") {
      const note = `
        <strong>${_escapeHtmlDetail(event.place || "Sismo")} · M${_escapeHtmlDetail(event.magnitude ?? "?")}</strong>
        <div class="trace-note">Este sismo ocurrió fuera de Chile: las publicaciones y noticias solo se asocian a sismos en Chile.</div>
        <button type="button" data-trace-reset>Volver a en vivo</button>
      `;
      liveTraceBanner.innerHTML = note;
      mediaTraceBanner.innerHTML = note;
      liveTraceBanner.classList.remove("hidden");
      mediaTraceBanner.classList.remove("hidden");
      liveFeedBody.innerHTML = '<p class="live-feed-empty">Sin publicaciones asociadas.</p>';
      socialFeedBody.innerHTML = '<p class="social-feed-empty">Sin noticias asociadas.</p>';
      return;
    }

    const loadingNotes = [];
    liveTraceBanner.innerHTML = traceBannerHtml(event, "Publicaciones", "buscando…", loadingNotes);
    mediaTraceBanner.innerHTML = traceBannerHtml(event, "Noticias", "buscando…", loadingNotes);
    liveTraceBanner.classList.remove("hidden");
    mediaTraceBanner.classList.remove("hidden");
    liveFeedBody.innerHTML = '<p class="live-feed-empty">Buscando publicaciones…</p>';
    socialFeedBody.innerHTML = '<p class="social-feed-empty">Buscando noticias…</p>';

    let trace;
    try {
      trace = await SismosApp.loadEventTrace(event);
    } catch (err) {
      if (requestId !== traceRequestId) return;
      liveFeedBody.innerHTML = '<p class="live-feed-empty">No se pudo cargar el archivo.</p>';
      socialFeedBody.innerHTML = '<p class="social-feed-empty">No se pudo cargar el archivo.</p>';
      return;
    }
    if (requestId !== traceRequestId) return; // se eligio otro sismo (o "volver a en vivo") mientras cargaba

    const { live, media } = trace;
    liveTraceBanner.innerHTML = traceBannerHtml(
      event,
      "Publicaciones",
      `${live.total} publicaci${live.total === 1 ? "ón" : "ones"}`,
      traceNotes(trace.liveFrom, trace.startMs, live.total, live.items.length, trace.liveBackfilled)
    );
    mediaTraceBanner.innerHTML = traceBannerHtml(
      event,
      "Noticias",
      `${media.total} noticia${media.total === 1 ? "" : "s"}`,
      traceNotes(trace.mediaFrom, trace.startMs, media.total, media.items.length, trace.mediaBackfilled)
    );
    SismosApp.renderLiveFeed(live.items, liveFeedBody, {
      emptyMessage: "No hay publicaciones archivadas de Chile para este sismo.",
    });
    SismosApp.renderSocialFeed(media.items, socialFeedBody, {
      emptyMessage: "No hay noticias archivadas para este sismo.",
    });
    liveFeedBody.scrollTop = 0;
    socialFeedBody.scrollTop = 0;
  };

  const clearTrace = () => {
    traceEvent = null;
    traceRequestId++;
    liveTraceBanner.classList.add("hidden");
    mediaTraceBanner.classList.add("hidden");
    refreshLiveFeed();
    refreshSocialFeed();
  };

  [liveTraceBanner, mediaTraceBanner].forEach((banner) =>
    banner.addEventListener("click", (e) => {
      if (e.target.matches("[data-trace-reset]")) clearTrace();
    })
  );

  // --- Desplegable de busqueda ("Sismos por dia", la lupa del header) ---
  const closeSearchPanel = () => {
    searchPanel.classList.add("hidden");
    searchToggle.classList.remove("is-active");
  };

  searchToggle.addEventListener("click", () => {
    const willOpen = searchPanel.classList.contains("hidden");
    searchPanel.classList.toggle("hidden", !willOpen);
    searchToggle.classList.toggle("is-active", willOpen);
  });

  // Cerrar al clickear afuera o con Escape -- son los dos gestos
  // esperados para un desplegable como este, sin eso quedaria abierto
  // hasta que alguien vuelva a clickear la lupa a proposito.
  document.addEventListener("click", (e) => {
    if (searchPanel.classList.contains("hidden")) return;
    if (searchPanel.contains(e.target) || searchToggle.contains(e.target)) return;
    closeSearchPanel();
  });

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeSearchPanel();
  });

  // --- Tabla "Sismos por dia" ---
  const renderDayTable = async (dateStr) => {
    dayTableBody.innerHTML = '<tr><td colspan="3">Cargando...</td></tr>';
    const dayEvents = (await SismosApp.loadDay(dateStr)).filter(hasSenapredReport);
    if (dayEvents.length === 0) {
      dayTableBody.innerHTML = '<tr><td colspan="3">Sin sismos con reporte de SENAPRED ese dia.</td></tr>';
      return;
    }
    dayTableBody.innerHTML = dayEvents
      .slice()
      .reverse()
      .map((event, idx) => {
        const hora = new Date(event.time).toLocaleTimeString("es-CL", {
          timeZone: "America/Santiago",
          hour: "2-digit",
          minute: "2-digit",
          hourCycle: "h23", // 24 horas, sin a. m./p. m. (medianoche = 00:05, no 24:05)
        });
        return `<tr data-idx="${idx}"><td>${hora}</td><td>${event.place || "-"}</td><td>${event.magnitude ?? "-"}</td></tr>`;
      })
      .join("");

    dayTableBody.querySelectorAll("tr[data-idx]").forEach((row) => {
      row.addEventListener("click", () => {
        const event = dayEvents.slice().reverse()[Number(row.dataset.idx)];
        focusEvent(event);
        closeSearchPanel();
      });
    });
  };

  dayPicker.addEventListener("change", () => {
    if (dayPicker.value) renderDayTable(dayPicker.value);
  });

  // Solo re-renderiza la tabla si la persona esta viendo el dia mas
  // reciente -- si esta mirando un dia pasado (que ya no cambia), no la
  // interrumpe ni la saca de ahi cada vez que corre el refresco.
  const refreshDayTable = async () => {
    try {
      const range = await SismosApp.loadAvailableDateRange();
      if (!range) {
        dayTableBody.innerHTML = '<tr><td colspan="3">Todavia no hay datos.</td></tr>';
        return;
      }
      const wasOnLatest = !dayPicker.value || dayPicker.value === dayPicker.max;
      dayPicker.min = range.min;
      dayPicker.max = range.max;
      if (wasOnLatest) {
        dayPicker.value = range.max;
        await renderDayTable(range.max);
      }
    } catch (err) {
      dayTableBody.innerHTML = '<tr><td colspan="3">No se pudo cargar el listado.</td></tr>';
    }
  };

  await Promise.all([refreshEvents(), refreshSocialFeed(), refreshLiveFeed(), refreshDayTable()]);

  setInterval(() => {
    refreshEvents();
    refreshSocialFeed();
    refreshLiveFeed();
    refreshDayTable();
  }, REFRESH_INTERVAL_MS);
})();
