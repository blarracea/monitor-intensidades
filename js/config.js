/* Configuracion del boton "¿Lo sentiste?".
 *
 * reportsApi: URL del Worker de reports-worker/ (ej. "https://lo-sentiste.TU-USUARIO.workers.dev").
 *   Vacio = MODO DEMOSTRACION: las respuestas se guardan solo en este navegador
 *   (localStorage) para poder probar como se ve y se siente, sin servidor.
 * turnstileSiteKey: clave publica de Cloudflare Turnstile (opcional, anti-spam).
 */
window.SISMOS_CONFIG = {
  reportsApi: "https://lo-sentiste.b-larracea.workers.dev",
  turnstileSiteKey: "0x4AAAAAAFwAWrK7kaU25pYw",
};
