/* Mapa base Leaflet centrado en Chile / Sudamerica. */
window.SismosApp = window.SismosApp || {};

SismosApp.initMap = function () {
  const map = L.map("map", {
    minZoom: 3,
  });

  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: "&copy; OpenStreetMap contributors",
    maxZoom: 18,
  }).addTo(map);

  // El tile de OpenStreetMap trae grabado el nombre "Falkland Islands" --
  // es un raster ya renderizado, no se puede editar ese texto. Se
  // superpone una etiqueta propia en el mismo punto con "Islas Malvinas"
  // (la denominacion que usa Chile), imitando el estilo de las etiquetas
  // del mapa base para que se vea igual de nativa.
  L.marker([-51.75, -59.3], {
    icon: L.divIcon({
      className: "map-label-malvinas",
      html: "Islas Malvinas",
      iconSize: [110, 16],
      iconAnchor: [55, 8],
    }),
    interactive: false,
    keyboard: false,
  }).addTo(map);

  // Encuadre inicial por bounds (no un centro+zoom fijo): asi Leaflet
  // calcula el zoom que de verdad corresponde al tamano real del
  // contenedor -- un numero de zoom fijo dejaba de calzar cada vez que el
  // mapa cambiaba de ancho. Ensanchado a pedido para que entren tambien
  // los puntos del territorio insular (Isla de Pascua, ~-109 de longitud)
  // sin tener que alejar el zoom a mano cada vez.
  const initialBounds = L.latLngBounds([20, -120], [-58, -55]);

  // El contenedor #map recien termina su layout de CSS flex un instante
  // despues de crear el mapa (mismo problema que el heatmap en app.js) --
  // si fitBounds calcula el zoom antes de eso, lo hace contra un
  // contenedor de tamano incorrecto y el encuadre sale mal. No se puede
  // envolver esto en whenReady: el mapa nunca dispara "ready" hasta que
  // tiene una vista (center/zoom) asignada, y todavia no le dimos
  // ninguna -- fitBounds es justamente lo que se la da.
  setTimeout(() => {
    map.invalidateSize();
    map.fitBounds(initialBounds);
  }, 50);

  return map;
};
