/* Mapa base Leaflet centrado en Chile / Sudamerica. */
window.SismosApp = window.SismosApp || {};

SismosApp.initMap = function () {
  const map = L.map("map", {
    minZoom: 2,
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

  // Encuadre inicial por bounds (no un centro+zoom fijo): asi Leaflet
  // calcula el zoom que de verdad corresponde al tamano real del
  // contenedor -- un numero de zoom fijo dejaba de calzar cada vez que el
  // mapa cambiaba de ancho. Ensanchado hacia el oeste a pedido para que el
  // Oceano Pacifico se vea protagonico (no solo hasta Isla de Pascua,
  // ~-109) -- el contenedor es angosto y alto, asi que el mismo zoom que
  // ensancha el eje oeste-este tambien estira de mas el eje norte-sur;
  // -130 es el punto donde se ve bien el Pacifico sin llegar a
  // Australia/Africa por el oeste ni a latitudes irrelevantes por el
  // norte/sur.
  const initialBounds = L.latLngBounds([8, -130], [-56, -55]);

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
