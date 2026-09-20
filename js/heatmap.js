/*
 * Capa de intensidad percibida (Mercalli / DYFI) y marcadores de eventos.
 * La escala de color sigue la escala de Mercalli Modificada completa (I a
 * XII, la misma que reporta SENAPRED -- ver ROMAN_VALUES / el limite <=12
 * en backend/sources/csn.py), no la magnitud Richter -- el heatmap usa la
 * intensidad de cada punto reportado por la ciudadania, no la energia
 * liberada en el epicentro.
 */
window.SismosApp = window.SismosApp || {};

// Gradiente estilo "jet" (azul -> cian -> verde -> amarillo -> rojo -> magenta)
// hasta X, y de ahi oscureciendo hacia granate/casi negro para XI y XII --
// con una franja pareja por cada numero romano de la escala (paradas en
// intensidad/12). Antes solo llegaba a X (paradas en intensidad/10): un
// reporte real de XI o XII (SENAPRED si los contempla, ver csn.py) quedaba
// recortado al mismo color que X en vez de distinguirse como mas extremo.
const INTENSITY_GRADIENT = {
  0.0833: "#14328c",
  0.1667: "#1f6fe0",
  0.25: "#1fb5e0",
  0.3333: "#22c7a0",
  0.4167: "#6fcf3e",
  0.5: "#c6d823",
  0.5833: "#f7b500",
  0.6667: "#f2701f",
  0.75: "#e8382a",
  0.8333: "#ff2fb0",
  0.9167: "#b0006e",
  1.0: "#4d0030",
};

// DYFI reporta en una grilla densa (decenas de puntos muy juntos, a veces a
// 1 km entre si), asi que un radio grande da un heatmap suave y continuo --
// los puntos se superponen y el difuminado de cada uno se compensa con el
// de los vecinos (ahi si tiene sentido sumar: mas respuestas ciudadanas
// cerca = mas densidad real). El CSN reporta por comuna -- pocos puntos
// (3-40) que en el Gran Santiago pueden estar a solo 3-5 km entre si -- si
// el blur es casi tan grande como el radio (poco "nucleo" solido), el pico
// de cada punto queda diluido y una intensidad real III-V se ve casi
// transparente. Por eso el CSN usa un radio chico (para no verse como una
// mancha gigante) pero con un nucleo bien solido (blur bajo en proporcion),
// para que el color en el centro refleje la intensidad real reportada.
// Bajado de 20/8 a 13/5 a pedido: con el blend por maximo (ver
// ComunaIntensityLayer) las manchas ya no se inflan al superponerse, pero
// todavia se veian demasiado grandes/amplias sobre el mapa.
const HEAT_STYLE_BY_SOURCE = {
  usgs_dyfi: { radius: 32, blur: 24 },
  csn: { radius: 13, blur: 5 },
};

const MIN_OPACITY = 0.15;

// Paleta de 256 colores (indexable por 0-255) que reproduce INTENSITY_GRADIENT
// -- se arma una sola vez dibujando los stops en un canvas de 1x256 y
// leyendo los pixeles resultantes, el mismo truco que usa Leaflet.heat/
// simpleheat por dentro para su propia paleta.
let _palette = null;
function _getPalette() {
  if (_palette) return _palette;
  const canvas = document.createElement("canvas");
  canvas.width = 1;
  canvas.height = 256;
  const ctx = canvas.getContext("2d");
  const grad = ctx.createLinearGradient(0, 0, 0, 256);
  Object.keys(INTENSITY_GRADIENT).forEach((stop) => grad.addColorStop(Number(stop), INTENSITY_GRADIENT[stop]));
  ctx.fillStyle = grad;
  ctx.fillRect(0, 0, 1, 256);
  _palette = ctx.getImageData(0, 0, 1, 256).data;
  return _palette;
}

// Capa de intensidad por comuna con blend de "maximo" en vez de "suma".
// Leaflet.heat (usado para DYFI, ver mas arriba) SUMA la densidad de puntos
// superpuestos -- correcto para "cuantas respuestas hubo cerca de aca", pero
// el reporte de SENAPRED no es eso: cada comuna entrega un VALOR propio (su
// intensidad), no una cantidad que deba sumarse con la del vecino. Como
// muchas comunas chilenas estan a pocos km entre si (sobre todo en el Gran
// Santiago), con suma sus manchas se superponian y el color final terminaba
// reflejando cuantas comunas vecinas reportaron, no la intensidad real de
// ninguna en particular -- una comuna con IV rodeada de otras con IV podia
// pintarse como VII-VIII solo por la cantidad de vecinas (bug real, visto
// en el sismo de Farellones del 18-09-2026, 35 comunas). Con maximo, el
// color en cualquier punto nunca supera la intensidad real mas alta
// reportada cerca de ahi -- mismo radio/blur y mismo look de nube
// difuminada que antes, pero el dato ya no se infla por tener vecinos.
const ComunaIntensityLayer = L.Layer.extend({
  initialize: function (points, options) {
    this._points = points; // [{lat, lon, value}], value ya normalizado 0-1
    L.setOptions(this, options);
  },

  onAdd: function (map) {
    this._map = map;
    if (!this._canvas) this._initCanvas();
    map.getPanes().overlayPane.appendChild(this._canvas);
    map.on("moveend resize", this._reset, this);
    map.on("zoomstart", this._hide, this);
    map.on("zoomend", this._reset, this);
    this._reset();
  },

  onRemove: function (map) {
    map.getPanes().overlayPane.removeChild(this._canvas);
    map.off("moveend resize", this._reset, this);
    map.off("zoomstart", this._hide, this);
    map.off("zoomend", this._reset, this);
  },

  _initCanvas: function () {
    this._canvas = L.DomUtil.create("canvas", "leaflet-comuna-intensity-layer leaflet-layer");
    this._canvas.style.position = "absolute";
  },

  _hide: function () {
    this._canvas.style.visibility = "hidden";
  },

  _reset: function () {
    const topLeft = this._map.containerPointToLayerPoint([0, 0]);
    L.DomUtil.setPosition(this._canvas, topLeft);
    const size = this._map.getSize();
    this._canvas.width = size.x;
    this._canvas.height = size.y;
    this._canvas.style.visibility = "";
    this._redraw();
  },

  // Suaviza el borde de cada mancha (1 en el centro, 0 en el borde exterior)
  // en vez de un corte lineal -- mismo efecto de nube difuminada que el
  // gradiente radial que usaba Leaflet.heat.
  _falloff: function (dist, radius, blur) {
    if (dist <= radius) return 1;
    const outer = radius + blur;
    if (dist >= outer) return 0;
    const t = (dist - radius) / blur;
    return 1 - t * t * (3 - 2 * t); // smoothstep
  },

  _redraw: function () {
    const ctx = this._canvas.getContext("2d");
    const width = this._canvas.width;
    const height = this._canvas.height;
    ctx.clearRect(0, 0, width, height);
    if (width === 0 || height === 0 || this._points.length === 0) return;

    const radius = this.options.radius;
    const blur = this.options.blur;
    const outer = radius + blur;
    const maxGrid = new Float32Array(width * height);

    this._points.forEach((point) => {
      const pt = this._map.latLngToContainerPoint([point.lat, point.lon]);
      const x0 = Math.max(0, Math.floor(pt.x - outer));
      const x1 = Math.min(width - 1, Math.ceil(pt.x + outer));
      const y0 = Math.max(0, Math.floor(pt.y - outer));
      const y1 = Math.min(height - 1, Math.ceil(pt.y + outer));
      for (let yy = y0; yy <= y1; yy++) {
        for (let xx = x0; xx <= x1; xx++) {
          const dx = xx - pt.x;
          const dy = yy - pt.y;
          const dist = Math.sqrt(dx * dx + dy * dy);
          if (dist >= outer) continue;
          const value = point.value * this._falloff(dist, radius, blur);
          const idx = yy * width + xx;
          if (value > maxGrid[idx]) maxGrid[idx] = value;
        }
      }
    });

    const palette = this.options.palette;
    const imgData = ctx.createImageData(width, height);
    for (let i = 0; i < maxGrid.length; i++) {
      const v = maxGrid[i];
      if (v <= 0) continue;
      const p = Math.min(255, Math.floor(v * 255)) * 4;
      const o = i * 4;
      imgData.data[o] = palette[p];
      imgData.data[o + 1] = palette[p + 1];
      imgData.data[o + 2] = palette[p + 2];
      imgData.data[o + 3] = Math.round(Math.max(v, MIN_OPACITY) * 255);
    }
    ctx.putImageData(imgData, 0, 0);
  },
});

SismosApp.buildHeatLayer = function (events) {
  const dyfiPoints = [];
  const csnPoints = [];
  events.forEach((event) => {
    (event.dyfi_points || []).forEach((p) => {
      if (p.intensity == null) return;
      const value = Math.min(p.intensity / 12, 1);
      if (event.intensity_source === "csn") {
        csnPoints.push({ lat: p.lat, lon: p.lon, value });
      } else if (event.intensity_source === "usgs_dyfi") {
        dyfiPoints.push([p.lat, p.lon, value]);
      }
    });
  });

  const layers = [];
  if (dyfiPoints.length > 0) {
    layers.push(
      L.heatLayer(dyfiPoints, {
        ...HEAT_STYLE_BY_SOURCE.usgs_dyfi,
        max: 1.0,
        // Sin minOpacity, Leaflet.heat usa un piso interno de 0.05 -- un
        // reporte real de intensidad III-V (lo mas comun) queda con una
        // opacidad maxima de ~13%, practicamente invisible. 0.15 lo hace
        // visible sin volver a generar un halo marcado donde no hay dato
        // (el halo grande era con 0.35, ver commit anterior).
        minOpacity: MIN_OPACITY,
        gradient: INTENSITY_GRADIENT,
      })
    );
  }
  if (csnPoints.length > 0) {
    layers.push(new ComunaIntensityLayer(csnPoints, { ...HEAT_STYLE_BY_SOURCE.csn, palette: _getPalette() }));
  }

  if (layers.length === 0) return null;
  return L.layerGroup(layers);
};

SismosApp.addEventMarkers = function (map, events, onSelect) {
  // Marcadores chicos: son solo el punto de click para ver el detalle del
  // epicentro, no deben competir visualmente con el heatmap de intensidad.
  const layer = L.layerGroup();
  events.forEach((event) => {
    if (event.lat == null || event.lon == null) return;
    const radius = 1.5 + Math.max(event.magnitude || 0, 0) * 0.55;
    const select = () => onSelect(event);

    // El circulo visible es chico a proposito (para no competir con el
    // heatmap), pero eso lo hacia dificil de pinchar -- el area de click
    // de un circleMarker es su propio radio, no hay una opcion aparte
    // para "radio de deteccion". Se agrega un circulo invisible mas
    // grande, debajo del visible, solo para ampliar donde reacciona el
    // click sin cambiar en nada el tamano que se ve.
    L.circleMarker([event.lat, event.lon], {
      radius: Math.max(radius + 10, 14),
      stroke: false,
      fillOpacity: 0,
    })
      .on("click", select)
      .addTo(layer);

    const marker = L.circleMarker([event.lat, event.lon], {
      radius,
      color: "#ffffff",
      fillColor: event.relevant ? "#e74c3c" : "#123a5e",
      fillOpacity: 0.9,
      weight: 0.75,
      className: event.relevant ? "quake-marker--relevant" : "",
    });
    marker.on("click", select);
    marker.addTo(layer);
  });
  layer.addTo(map);
  return layer;
};

// Puntos azules para sismos M5.0+ que NO tienen reporte de intensidad
// (ni CSN/SENAPRED ni DYFI de USGS) -- a pedido: un sismo grande se puede
// percibir sin que nadie llegue a reportarlo formalmente, y antes esos
// sismos no aparecian en el mapa en absoluto (quedaba "en blanco" como si
// no hubiera pasado nada). No dibujan heatmap -- son solo la marca de
// "esto ocurrio", separado de la intensidad verificada.
SismosApp.addUnverifiedMarkers = function (map, events, onSelect) {
  const layer = L.layerGroup();
  events.forEach((event) => {
    if (event.lat == null || event.lon == null) return;
    const radius = 3 + Math.max(event.magnitude || 0, 0) * 0.5;
    const select = () => onSelect(event);
    const tooltip = `M${event.magnitude ?? "?"} · sin reporte de intensidad`;

    // Mismo truco que en addEventMarkers: un circulo invisible mas grande
    // debajo, solo para ampliar el area de click sin agrandar el punto.
    L.circleMarker([event.lat, event.lon], {
      radius: Math.max(radius + 10, 14),
      stroke: false,
      fillOpacity: 0,
    })
      .on("click", select)
      .addTo(layer);

    const marker = L.circleMarker([event.lat, event.lon], {
      radius,
      color: "#ffffff",
      fillColor: "#1565c0",
      fillOpacity: 0.85,
      weight: 1,
    });
    marker.bindTooltip(tooltip, { direction: "top" });
    marker.on("click", select);
    marker.addTo(layer);
  });
  layer.addTo(map);
  return layer;
};
