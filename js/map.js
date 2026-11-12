/* Mapa base Leaflet centrado en Chile / Sudamerica. */
window.SismosApp = window.SismosApp || {};

SismosApp.initMap = function () {
  const map = L.map("map", {
    minZoom: 1,
    // Por defecto Leaflet solo permite zooms enteros (zoomSnap: 1) -- eso
    // hacia que fitBounds saltara de golpe al entero siguiente completo en
    // cuanto el encuadre pedido no entraba exacto en el zoom actual (p.ej.
    // de zoom 3 directo a zoom 2, mostrando el doble de mundo de lo
    // necesario). Con zoomSnap en 0 el zoom puede ser fraccionario y
    // fitBounds calza el encuadre exacto que se pide.
    zoomSnap: 0,
  });

  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: "&copy; OpenStreetMap contributors",
    maxZoom: 18,
  }).addTo(map);

  // Encuadre inicial por bounds (no un centro+zoom fijo): asi Leaflet calcula el zoom que de verdad
  // corresponde al tamano real del contenedor. Encuadre elegido por el usuario (copiado de una captura
  // del mapa): de Colombia al sur de Chile, con solo un tramo del Pacifico y de Argentina. Quien quiera
  // ver mas (Pacifico, Japon, Asia) puede alejar el zoom a mano; minZoom se deja bajo (1) para eso.
  const initialBounds = L.latLngBounds([15.5, -105], [-58, -52]);

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
