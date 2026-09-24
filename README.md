# Monitor Intensidades

Dashboard gratuito que muestra sismos recientes de Chile y del resto del
mundo en un mapa, con un heatmap de **intensidad percibida (escala de
Mercalli Modificada)** — qué tan fuerte lo sintió la gente en cada zona, no
la magnitud Richter del epicentro.

## Cómo funciona

- **Backend (`backend/`)**: un script Python (`collect.py`) que arma el
  catálogo de sismos desde dos fuentes independientes, no una sola:
  - **Chile** (continental, Territorio Chileno Antártico y territorio
    insular — Isla de Pascua, Juan Fernández, San Félix/San Ambrosio):
    directo del **CSN** (Centro Sismológico Nacional), sin pasar por USGS.
    Si el CSN marca el sismo como percibido, usa el reporte de intensidad
    por comuna de **SENAPRED** que trae ese mismo informe.
  - **Resto del mundo**: catálogo de **USGS**, sin restricción geográfica
    (antes se limitaba a Sudamérica), con **DYFI** ("Did You Feel It?")
    como fuente de intensidad cuando el sismo tiene reporte ciudadano.

  Además guarda menciones recientes de sismos en medios chilenos (RSS vía
  Google News) en `data/social_mentions.json`, y posts públicos de Bluesky
  y Mastodon con las palabras clave del proyecto (ver `backend/keywords.py`)
  en `data/live_mentions.json`, para los paneles "Menciones en medios" y
  "Redes en vivo" del dashboard. Todos los sismos se guardan en
  `data/YYYY-MM-DD.json`, un archivo por día.
- **GitHub Actions (`.github/workflows/collect.yml`)**: corre `collect.py`
  cada 20 minutos (ver más abajo por qué no cada 5 o cada 15, que sería lo
  ideal), sin depender de que nadie tenga el navegador abierto, y hace
  commit + push automático de los archivos que cambiaron en `data/`.
- **Frontend (`index.html`, `css/`, `js/`)**: página estática con un mapa
  Leaflet que **solo lee** los archivos ya guardados en `data/` — nunca llama
  directamente a las APIs externas. Se sirve gratis con GitHub Pages. Se
  auto-refresca cada 60 segundos para mostrar lo último recolectado.
- **Retención**: **nada se borra**. Los sismos (`data/YYYY-MM-DD.json`) se
  guardan para siempre. Los paneles "Menciones en medios" (72 h) y "Redes en
  vivo" (24 h) solo muestran lo reciente, pero cada publicación además se
  copia a un archivo permanente por día (`data/archive/media/` y
  `data/archive/live/`, ver más abajo). Ritmo medido: ~200 KB/día en archivos
  (~73 MB/año sin comprimir, unos 20–40 MB/año dentro de git).

## Estado de cada fuente de datos

| Fuente | Estado |
|---|---|
| CSN/SENAPRED | ✅ Implementada — catálogo **y** fuente de intensidad para Chile. Ninguno de los dos tiene API pública: se lee la portada del CSN (`sismologia.cl`, sus ~15 sismos más recientes) para el catálogo, y si un sismo fue percibido, el reporte de intensidad por comuna de SENAPRED que trae ese mismo informe. `enrich_with_senapred_archive()` hace una segunda pasada contra el archivo propio de SENAPRED (`senapred.cl/eventos/`, retiene semanas) para sismos que la portada del CSN ya rotó fuera de su lista corta. El reporte de intensidad se lee con Playwright (SENAPRED es una app en React sin API documentada). Ver `backend/sources/csn.py` |
| USGS (catálogo global + DYFI) | ✅ Implementada — catálogo para todo sismo fuera de Chile, sin límite geográfico, con "Did You Feel It?" (DYFI) como intensidad percibida cuando el sismo tiene reporte ciudadano. Ver `backend/sources/usgs.py` |
| Menciones en medios (RSS) | ✅ Implementada — Google News RSS filtrado por palabras clave (agrega Emol, La Tercera, BioBioChile, Infobae y otros). Es un proxy de "dónde se habla del sismo", no intensidad verificada. Ver `backend/sources/social.py` |
| Redes en vivo (Bluesky + Mastodon) | ✅ Implementada — posts públicos con las palabras clave del proyecto, en cualquier parte del mundo. Bluesky necesita `BLUESKY_HANDLE`/`BLUESKY_APP_PASSWORD` (secrets de GitHub Actions). Mastodon busca por texto libre (cualquier post público, con o sin hashtag) si está configurado `MASTODON_ACCESS_TOKEN` — sin ese token cae a buscar solo por hashtag exacto (`#sismo`), mucho más limitado. Ninguna de las dos rompe el resto de la recolección si falta su credencial, simplemente se saltan. Ver `backend/sources/bluesky.py` y `backend/sources/mastodon.py` |
| SNAM/SHOA (segunda fuente para M≥5.0) | ⚠️ Implementada, pero **no funciona desde GitHub Actions** — el SHOA (Servicio Hidrográfico y Oceanográfico de la Armada, el mismo organismo detrás del SNAM) confirma o revisa la referencia geográfica y la magnitud del CSN/USGS ~10 min después de la primera publicación, para cualquier sismo M≥5.0. El sitio (`snamchile.cl`) está detrás de un WAF de AWS que le muestra un CAPTCHA real (no un desafío resoluble por script) a las IPs de los runners de GitHub Actions — confirmado en producción, no hay forma automática de resolverlo. En la práctica solo trae datos corriendo `collect.py` a mano desde una IP normal; desde GitHub Actions falla en silencio sin romper el resto de la recolección. Ver `backend/sources/snam.py` y `enrich_with_snam()` en `backend/collect.py` |

### Cómo se arma el mapa de calor

El color del heatmap es intensidad de Mercalli (I a XII), no magnitud —
cada evento guarda `intensity_source` (`"csn"`, `"usgs_dyfi"` o `null`) para
saber de dónde salió el dato.

- **El heatmap continuo** (el mapa de calor que siempre está visible) solo
  pinta sismos con `intensity_source === "csn"` — es decir, sismos en Chile
  con reporte real de SENAPRED. No mezcla ahí el DYFI del resto del mundo,
  para no combinar dos escalas de participación ciudadana muy distintas.
- **Los sismos grandes en cualquier parte del mundo** (magnitud ≥ 5.0) que
  todavía no tienen reporte de intensidad se marcan igual con un punto azul
  en el mapa — antes quedaban totalmente invisibles. Al hacer click sobre
  uno de esos puntos, si el sismo sí tiene reporte DYFI de USGS, se dibuja
  su propio mapa de calor individual.
- El **resumen semanal** (gráfico de barras, últimos 7 días) es la única
  parte del dashboard que no exige reporte de intensidad: cuenta cualquier
  sismo de magnitud ≥ 4.5, tenga o no reporte.

### Trazabilidad por sismo

Al elegir un sismo (click en el mapa de calor, o desde la lupa "Sismos por
día"), los paneles "Redes en vivo" y "Menciones en medios" pasan a mostrar lo
que se publicó en torno a **ese** sismo — con un aviso arriba y un botón
"Volver a en vivo". Las reglas:

- Publicaciones y noticias **sobre Chile** (campo `chile`, calculado al
  archivar: el texto nombra Chile o un lugar chileno conocido, la cuenta es
  chilena, o la noticia viene de un medio chileno), desde 5 minutos antes
  hasta 6 horas después del sismo (`TRACE_BEFORE_MS`/`TRACE_AFTER_MS` en
  `js/data-loader.js`), en orden cronológico, hasta 100 por panel.
- El archivo (`data/archive/live/` y `data/archive/media/`, un archivo por día
  UTC, un ítem por línea) **parte el día en que se empezó a guardar** —
  `data/archive/meta.json` dice desde cuándo. Para sismos anteriores se
  **recupera** el contenido con una búsqueda histórica
  (`backend/backfill.py`, workflow manual "Recuperar historial de sismos
  anteriores"): Bluesky acepta `since`/`until`, Mastodon acepta `min_id`/`max_id`
  (sus IDs codifican la hora) y Google News acepta `after:`/`before:`. Es
  parcial —los índices de búsqueda no garantizan tener todo— y el panel lo
  avisa. Cubre los sismos chilenos que se pueden elegir (con reporte de
  SENAPRED o M≥5.0); es idempotente, se puede volver a correr cuando haya
  sismos nuevos que recuperar.
- Para que haya publicaciones chilenas que mostrar, Bluesky y Mastodon se
  consultan además con "sismo Chile", "temblor Chile", etc.
  (`keywords.SEARCH_QUERIES`): con solo palabras sueltas, las cuentas chilenas
  (BioBioChile, Emol, La Tercera, Cooperativa en mastodon.cl) quedaban tapadas
  por el ruido del resto del mundo (medido en Mastodon, ventana de 7 días: de 2 a 14 publicaciones sobre Chile).
- Solo se archiva lo que el dashboard ya muestra (autor, texto, enlace, fecha).
  Son publicaciones de terceros: si alguien borra la suya, el archivo la sigue
  conservando.

### Cruce con SNAM/SHOA

Para sismos M≥5.0, `enrich_with_snam()` cruza cada evento ya recolectado
(por cercanía de hora + distancia de epicentro, nunca por magnitud, que es
justamente el dato que puede diferir) contra la tabla de eventos de
`snamchile.cl`. Si hay match, se **actualizan** `place` y `magnitude` del
evento con los valores que publicó el SHOA, y se agrega el link "SNAM" en
"Fuente" del detalle (`snam_url`, apunta al boletín específico). El
candidato se preselecciona con un margen bajo el umbral real (ver
`SNAM_CANDIDATE_MIN_MAGNITUDE` en `collect.py`) porque el SNAM publica
según *su propia* estimación de magnitud, que puede ser más alta que la
nuestra para el mismo sismo (caso real: un sismo que el CSN informó en 4.8
salió publicado por el SNAM en 5.2).

## Correrlo en tu computador

```bash
cd backend
pip install -r requirements.txt
python -m playwright install --with-deps chromium
python collect.py
```

> **Windows**: si `python` o `pip` no se reconocen (pasa seguido en Git Bash
> por los alias de Microsoft Store), usa el lanzador `py` en su lugar:
> `py -m pip install -r requirements.txt`, `py -m playwright install chromium`
> y `py collect.py`.

El paso de Playwright descarga Chromium (~150 MB) — solo hace falta una vez;
se usa para leer el reporte de intensidad de SENAPRED cuando un sismo
chileno fue percibido.

Bluesky (posts para "Redes en vivo") necesita `BLUESKY_HANDLE` y
`BLUESKY_APP_PASSWORD` (una "contraseña de aplicación", no la contraseña
real de la cuenta) como variables de entorno; sin ellas, esa fuente
simplemente se salta. Mastodon funciona sin nada (busca solo por hashtag
exacto), pero mejora mucho con `MASTODON_ACCESS_TOKEN` (busca cualquier
post público, con o sin hashtag) — se genera en Mastodon, Preferencias >
Desarrollo > crear una aplicación con permiso "read", "Tu token de
acceso".

Esto crea/actualiza los archivos en `data/`. Luego, para ver el frontend:

```bash
python -m http.server 8000
```

y abre `http://localhost:8000` en el navegador.

## Cómo queda desplegado

1. El repo vive en GitHub (público, para que Actions y Pages sean 100%
   gratis y sin límites de minutos).
2. GitHub Pages sirve el sitio directamente desde la raíz del repo en la
   rama `master` (Settings → Pages → Branch: master / root).
3. El workflow `.github/workflows/collect.yml` corre cada 20 minutos vía
   cron, y también se puede disparar a mano desde la pestaña "Actions" del
   repo (botón "Run workflow", o `gh workflow run collect.yml`).
   `concurrency` en el workflow evita que dos corridas se pisen si una
   tarda más de lo esperado. Como el repo es público, esto no tiene costo
   ni tope mensual de minutos — es política fija de GitHub Actions para
   repos públicos, no una restricción propia de este proyecto.

## Ajustes comunes

- **Frecuencia de recolección**: cambia el cron `*/20 * * * *` en
  `.github/workflows/collect.yml`. **Importante**: el límite real no es el
  costo (minutos ilimitados en repo público) ni la sintaxis de cron (GitHub
  técnicamente permite hasta cada 5 minutos) -- es que GitHub **no
  garantiza** que un `schedule` muy frecuente se dispare de verdad con esa
  cadencia. Se probó `*/5 * * * *` (14 corridas reales en 24 horas en vez
  de las 288 esperadas) y después `*/15 * * * *`, y el problema persistió
  igual: revisando el historial real de corridas (`gh run list`), los
  espacios entre corridas "schedule" llegan hasta 2-2.5 horas, muy por
  encima del número configurado. En otras palabras, **el número exacto del
  cron no es la causa ni la solución** -- es GitHub descartando disparos
  bajo carga de la plataforma, y eso pasa a cualquier cadencia sub-horaria
  probada hasta ahora. La única forma de garantizar el intervalo de verdad
  sería un disparador externo (ej. cron-job.org llamando a la API de
  GitHub) con un token de acceso, que por ahora se descartó a propósito
  para no depender de un servicio externo ni de un token nuevo.
- **Palabras clave** (qué cuenta como mención de un sismo en RSS/redes):
  lista `KEYWORDS` en `backend/keywords.py`.
- **Bbox del catálogo de USGS**: constante `BBOX` en `backend/collect.py`
  (hoy es el planeta completo).
- **Rango de días mostrado en el mapa**: cambia el número en
  `SismosApp.loadRecentEvents(7)` dentro de `js/app.js`.
- **Magnitud mínima que se guarda**: constante `MIN_MAGNITUDE` en
  `backend/collect.py`.
- **Umbral para marcar un evento como "relevante"** (resalte visual, círculo
  rojo en el mapa): constantes `RELEVANT_MAGNITUDE` y `RELEVANT_FELT_REPORTS`
  en `backend/collect.py`.
- **Cruce con SNAM/SHOA**: constantes `SNAM_MATCH_MAX_MINUTES`,
  `SNAM_MATCH_MAX_DISTANCE_KM` y `SNAM_CANDIDATE_MIN_MAGNITUDE` en
  `backend/collect.py` (ver "Cruce con SNAM/SHOA" más arriba).
- **Umbral del resumen semanal**: constante `WEEK_CHART_MIN_MAGNITUDE` en
  `js/app.js`.
- **Umbral del punto azul** (sismo grande sin reporte de intensidad):
  constante `UNVERIFIED_MARKER_MIN_MAGNITUDE` en `js/app.js`.
