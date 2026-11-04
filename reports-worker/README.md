# Servidor de "¿Lo sentiste?" (Cloudflare Workers + D1, plan gratuito)

El dashboard es una página estática y no puede guardar datos; este pequeño servidor
recibe las respuestas de las personas y entrega el resumen por sismo.
**Mientras no lo actives, el botón funciona en MODO DEMOSTRACIÓN** (las respuestas
quedan solo en el navegador de quien prueba; `js/config.js` → `reportsApi` vacío).

## Qué guarda y qué no
- Guarda: el nivel elegido (0 = no lo sentí, 1–5), una comuna opcional de una lista
  fija, la hora, y un **hash** (SHA-256 con sal secreta) de la IP para contar una
  vez por persona y sismo. **Nunca guarda la IP ni texto libre.**
- El servidor solo devuelve totales, nunca respuestas individuales.

## Defensas contra reportes falsos (todas del lado del servidor)
- El sismo debe existir en los datos públicos del dashboard, ser de Chile (CSN) o de
  **Perú o Argentina** (USGS) y tener menos de **7 días** (`REPORT_WINDOW_HOURS = 168`, que
  debe coincidir con `REPORT_WINDOW_HOURS` de `js/app.js` en el sitio). El país de un sismo
  de USGS sale del texto de referencia de los datos del dashboard ("42 km SW of Tacna, Peru"),
  nunca de lo que manda el navegador. Bolivia también limita con Chile: para sumarla, agregarla
  a `NEIGHBOR_COUNTRY_PATTERN` (aquí y en `js/reports.js`).
- Una persona cuenta una vez por sismo (puede cambiar su respuesta), máx. 20 por hora.
- Solo se aceptan comunas de la lista; nivel solo 0–5; el id del sismo debe tener la forma
  exacta de un informe del CSN (`csn:https://www.sismologia.cl/...`) o de un id de USGS
  (`us7000tut4`: 2 letras minúsculas + 4–20 letras o dígitos).
- Despliegue: `npx wrangler deploy` desde esta carpeta (requiere `wrangler login`). La
  configuración (variables incluidas) vive en `wrangler.toml`; `preview_urls = false`.
- CORS: solo el dashboard (`ALLOWED_ORIGINS`) puede llamarlo desde un navegador.
- Cloudflare Turnstile (anti-robots): el servidor lo verifica si existe el secreto
  `TURNSTILE_SECRET`, y la página (`js/reports.js`, clave pública en `js/config.js`)
  ya carga el widget. Está ACTIVO en producción. El widget solo admite el dominio
  `blarracea.github.io`; para probar en local hay que agregar `localhost` en el panel
  de Turnstile y quitarlo después (si no, cualquiera podría sacar tokens válidos).
  Los navegadores incrustados/automatizados suelen fallar con el error 600010; usar
  `test/turnstile-test.html` en un navegador normal.

## Cómo activarlo (lo haces tú; yo no puedo crear cuentas)
Necesitas una cuenta gratuita de Cloudflare (sin tarjeta) y Node.js instalado.
```bash
cd reports-worker
npx wrangler login
npx wrangler d1 create lo-sentiste-db        # copia el database_id a wrangler.toml
npx wrangler d1 execute lo-sentiste-db --remote --file=schema.sql
npx wrangler secret put VOTER_SALT           # pega un texto largo al azar
npx wrangler deploy                          # imprime la URL https://lo-sentiste.<tu-usuario>.workers.dev
```
Luego pon esa URL en `js/config.js` (`reportsApi`) y publica.

## Límites gratuitos (según resúmenes de terceros, confirmar en Cloudflare)
Unas 100.000 solicitudes/día y 100.000 escrituras/día en D1, 500 MB por base. Al
pasarse, el servicio falla hasta el día siguiente; **no cobra** (sin tarjeta asociada).

## Pruebas (sin instalar nada)
- Lógica: servir el proyecto (`py -m http.server 8001`) y abrir
  `/reports-worker/test/worker-test.html` → debe decir `27/27 pruebas OK`.
- SQL contra SQLite real: `python reports-worker/test/sql-test.py`.
