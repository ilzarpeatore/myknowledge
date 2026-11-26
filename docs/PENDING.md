# Pendientes

Lista viva de lo que queda por hacer o comprobar, por fase. Se actualiza en cada entrega: lo resuelto se marca `[x]` con la fecha y se deja unas semanas antes de borrarlo.

Leyenda: 🔑 necesita la clave de Anthropic · 📄 necesita material real · 🖥️ necesita el VPS · 👤 decisión o acción del usuario

## General e infraestructura
- [ ] 👤 GitHub Actions no se ejecuta (las ejecuciones fallan en segundos sin logs): revisar la pestaña Actions / facturación de la cuenta.
- [ ] 👤 Llevar el código de la rama `claude/friendly-mccarthy-mk6zyp` a `main` (pull request pendiente de autorización).
- [ ] Revisión de seguridad del código antes de exponerlo a internet (`/security-review`).
- [ ] 🖥️ Probar la interfaz en móvil y tableta (solo se ha probado en escritorio).
- [ ] El entorno de nube `myknowledge` tiene un script de preparación que no se probará hasta que arranque una sesión nueva con él.
- [ ] Botón «Elegir archivos» de la biblioteca sale en el idioma del navegador (control nativo).

## Despliegue en el VPS (Fase 8 adelantada)
- [x] 2026-11-26 `preflight.sh` en el VPS (sin dominio aún): Ubuntu 24.04, 4 núcleos AMD EPYC-Milan, 7,7 GB de RAM (4,3 GB disponibles con los servicios actuales), 189 GB libres, Docker 29.9 + Compose 5.6, Caddy 2.11 **como servicio del sistema** (no contenedor), ufw solo 22/80/443.
- [x] 2026-11-26 Plan aprobado: `/opt/myknowledge`, puerto 3010, `KOS_WORKER_MEM_LIMIT=3g`, `KOS_WHISPER_THREADS=2`, subdominio `myknowledge.bestronger.es`.
- [x] 2026-11-26 `.env` en el VPS (600, contraseña de la BD generada allí en hexadecimal). Falta `ANTHROPIC_API_KEY` (la pone el usuario) y `KOS_ALERT_URL` (paso de monitorización).
- [x] 2026-11-26 La guía generaba la contraseña con `base64`, que puede contener `/` o `+` y romper la URL de la base de datos: cambiado a `openssl rand -hex 32`.
- [x] 2026-11-26 El puerto 3000 está ocupado en el VPS (otro servicio): `KOS_HTTP_PORT=3010`.
- [ ] 🖥️ RAM justa: `KOS_WORKER_MEM_LIMIT=3g` (con 4g el resto del VPS se quedaría con ~0,3 GB). Vigilar con material real que ningún PDF se quede sin memoria.
- [ ] 🖥️ CPU compartida con producción: `KOS_WHISPER_THREADS=2` (con 4 ocuparía los 4 núcleos); medir cuánto tarda 1 h de audio así.
- [ ] `scripts/README.md` no existe (lo cita la documentación de trabajo); el uso de cada script está en su cabecera y en DEPLOYMENT.md.
- [ ] 👤 Deploy key de solo lectura: generada en el VPS (`/root/.ssh/myknowledge_deploy`, alias `github-myknowledge`) el 2026-11-26; **falta registrarla en GitHub** (Settings → Deploy keys, sin permiso de escritura). Mientras, el código se llevó al VPS con un `git bundle` de la misma rama.
- [ ] 👤 Subdominio `myknowledge.bestronger.es` → 31.70.155.184. Hoy (2026-11-26) resuelve a la IP genérica de IONOS (217.160.0.227) **y tiene un AAAA** (2001:8d8:100f:f000::200) que hay que borrar: el VPS no publica IPv6 y Let's Encrypt/los navegadores irían allí.
- [ ] 👤 Destino de copias de seguridad fuera del VPS (rclone/rsync).
- [x] 2026-11-26 Copia y restauración en Docker en una instalación limpia, con transcripción, OCR, conceptos y tarjetas: todo idéntico.
- [x] 2026-11-26 Imágenes construidas en el VPS en ~12 min (worker 5,6 GB, api/migrate 0,98 GB, web 0,36 GB) y sistema arrancado: `migrate` exited 0, API healthy, `/api/health` ok en 127.0.0.1:3010, worker con Docling, OCR, transcripción, búsqueda por significado y yt-dlp 2026.11.23. En reposo: worker 0,5 GB, resto < 0,2 GB.
- [x] 2026-11-26 Modelo de transcripción descargado (`kos download-whisper`, volumen `whisper`); el worker lo detecta tras reiniciarlo.
- [x] 2026-11-26 Los demás servicios del VPS siguen igual tras construir y arrancar (mismos códigos HTTP en los 7 sitios, mismos 7 contenedores, Caddyfile sin cambios por SHA-256).
- [ ] 🖥️ Probar `backup.sh`/`restore.sh` en el VPS (la restauración, en un proyecto aparte).
- [x] 2026-11-26 Usuario `hamza` creado con una contraseña aleatoria generada en el VPS (fichero `/root/kos-contrasena-inicial-hamza.txt`, solo root); inicio de sesión comprobado (200).
- [ ] 👤 Guardar esa contraseña (o cambiarla con `kos reset-password hamza`) y autorizar el borrado del fichero.
- [ ] `kos create-user` solo acepta la contraseña por terminal interactiva; para automatizar haría falta leerla de un fichero o de stdin.
- [ ] 🖥️ Bloque de Caddy para `myknowledge.bestronger.es` y comprobar HTTPS (espera al DNS).
- [x] 2026-11-26 Red entre Caddy y el gateway: en el VPS Caddy es un servicio del sistema, así que `reverse_proxy 127.0.0.1:<puerto>` funciona sin tocar redes Docker.
- [x] 2026-11-26 Modelo de búsqueda int8 en una CPU sin AVX-512 VNNI (VPS, AMD EPYC-Milan, solo AVX2): funciona. Frente a fp32: similitud media 0,996 (mínima 0,995), primer resultado correcto 7/7 en ambos, y int8 1,6× más rápido (14 frente a 22 ms por fragmento largo con 2 hilos). No hace falta la alternativa de D-15.
- [x] 2026-11-26 Monitorización y alertas (worker, cola, disco, copias, presupuesto; ntfy/Slack).
- [ ] 🖥️👤 Programar en el VPS el cron de `kos monitor` y `kos remind` y elegir canal de avisos (ntfy o Slack).
- [ ] 🖥️ Medir rendimiento y transcripción con la CPU del VPS (aquí: búsqueda ≤ 1 s a 100 000 fragmentos; worker 1,8 GB transcribiendo).

## Fase 1 — Núcleo de conocimiento
- [ ] 🔑 Primera llamada real: `kos ai-check`.
- [x] 2026-11-26 Extracción automática de conceptos y afirmaciones por documento, con resolución de duplicados y revisión (probada con IA simulada).
- [ ] 🔑📄 Primera extracción real: comprobar calidad de conceptos/afirmaciones, cuántas citas se descartan por no ser literales y si el coste real se parece a la estimación.
- [ ] 🔑📄 Ajustar los umbrales de duplicados de la extracción (enlace automático con confianza ≥ 0,8; similitud por significado ≥ 0,86) con datos reales.
- [ ] Revisión en bloque de propuestas (hoy se aceptan/rechazan una a una en la ficha del concepto).
- [ ] Las afirmaciones se atribuyen al profesor del primer curso que usa la fuente; si una fuente está en varios cursos de distintos profesores, la atribución puede ser incorrecta.
- [ ] 🔑 Redacción de la página de cada concepto a partir de sus afirmaciones (`pages`, `page_revisions` aún no existen).
- [ ] 🔑 Consulta con respuesta citada («¿qué he aprendido sobre…?»).
- [ ] 🔑📄 Elegir modelo por tarea midiendo calidad y coste (hoy todo en Opus 5.5; Haiku 5.5 es ~40× más barato por token).
- [ ] 📄 Criterio de aceptación de la Fase 1 con 3 documentos reales solapados.
- [ ] 📄 Docling con PDFs reales: en algunas maquetaciones coloca el título de una tabla después de la tabla; una tabla sin bordes de 2 filas no se detectó.
- [ ] 📄 Búsqueda por significado: el umbral (0,80) no separa bien lo irrelevante; ajustar con datos reales.
- [ ] Extracciones versionadas (D-14): hoy una fuente con citas no se puede reprocesar.
- [ ] Propuestas de cambio (`change_proposals`): de momento se usan `review_tasks`.

## Fase 2 — Grafo de conocimiento
- [ ] 🔑📄 Criterio 1: análisis comparativo con 3 profesores reales (probado solo con IA simulada).
- [ ] 🔑📄 Criterio 3: medir que se acepta ≥ 80 % de las relaciones propuestas por la IA.
- [ ] Mapa mental: la imagen se exporta en SVG; PNG no es posible con markmap en el navegador.
- [ ] Grafo en anillos: alguna arista de segundo nivel cruza el centro (cosmético).

## Fase 3 — Learning Center
- [ ] 📄 Validar con un curso real.
- [ ] La interfaz no permite aún editar profesor, fechas, objetivos, URL ni descripción de formaciones y cursos (la API sí).
- [ ] Reordenar módulos y lecciones desde la interfaz (la API admite `position`).

## Fase 4 — Evidencia científica
- [x] 2026-11-26 Fichas de estudios: DOI (Crossref), tipo, diseño, muestra, variables, resultados, limitaciones, calidad metodológica («no evaluada» por defecto).
- [x] 2026-11-26 Filtro «estudios / profesores / otras» en cada concepto.
- [x] 2026-11-26 Contradicciones con contexto (ficha del estudio y contexto de cada afirmación).
- [ ] 🖥️ El VPS necesita salida HTTPS a `api.crossref.org` para añadir estudios por DOI (sin ella, se añaden a mano).
- [ ] 👤 Opcional: `KOS_CROSSREF_MAILTO` con un correo de contacto para el acceso preferente de Crossref (hoy no se envía ninguno).
- [ ] 🔑📄 Probar con artículos reales: extracción de un PDF de estudio y análisis de contradicciones estudio vs. profesor.
- [ ] Extraer la ficha metodológica (diseño, población, muestra…) del propio PDF con IA como propuesta revisable (hoy se rellena a mano).
- [ ] Un mismo documento con varios estudios (p. ej. una revisión): hoy una fuente se vincula a un solo estudio.
- [ ] Los autores de un estudio se guardan como texto, no como personas enlazables.

## Fase 5 — Multimedia
- [x] 2026-11-26 👤 Formatos: de todo (vídeo, audio, transcripciones, presentaciones, EPUB) y vídeos de YouTube.
- [x] 2026-11-26 DOCX, PPTX (diapositiva), EPUB (capítulo), subtítulos SRT/VTT (minuto exacto).
- [x] 2026-11-26 Transcripción local de audio y vídeo, reanudable, con reproductor que abre la cita en su segundo.
- [x] 2026-11-26 Vídeos de YouTube: subtítulos del autor, automáticos en el idioma original o audio transcrito; reproductor con el vídeo incrustado.
- [ ] 🖥️ YouTube bloquea a menudo las descargas desde servidores («confirma que no eres un bot»). Si pasa en el VPS: descargar subtítulos o audio en tu ordenador y subirlos (el mensaje de error lo indica). Sin probar allí.
- [ ] YouTube sin subtítulos (descarga de audio): no probado (la red de desarrollo solo permite www.youtube.com).
- [ ] yt-dlp debe actualizarse a menudo (YouTube cambia); avisa de que sin un motor JavaScript (deno) algunos formatos pueden faltar. Revisar si hace falta deno en el worker.
- [ ] 👤 Descargar de YouTube puede ir contra sus condiciones de uso; es uso personal, decisión tuya.
- [ ] 📄 Probar con una clase real de 1 h (velocidad y calidad en el VPS; solo se ha medido con voz sintética).
- [ ] 🖥️ Construir la nueva imagen del worker (ffmpeg + faster-whisper) y descargar el modelo al volumen; no se ha construido aún con estos cambios.
- [ ] 🖥️ Medir la velocidad en la CPU del VPS; si es lenta, `KOS_WHISPER_MODEL=small` (más rápido, peor calidad).
- [ ] Notas del ponente de PowerPoint (Docling no las extrae).
- [ ] Identificar quién habla (varios ponentes); hoy todo va sin hablante.
- [ ] Formatos antiguos (DOC, PPT) y otros (ODT, Keynote): Docling lee algunos; no activados ni probados.
- [ ] Ver el texto de una diapositiva junto a la diapositiva (hoy la cita da el número; el PPTX se descarga).

## Fase 6 — AI Workspace y MCP
- [x] 2026-11-26 Servidor MCP con tokens (lectura y propuestas a revisión), probado con el cliente oficial.
- [x] 2026-11-26 Página «Preguntar»: respuestas con citas verificadas, partes separadas, cobertura, ámbitos, modos y seguimiento (IA simulada).
- [ ] 🔑📄 Revisión manual de las 8 preguntas del piloto con la clave y tus materiales (criterio 1).
- [ ] 🖥️👤 Conectar Claude Code (comando en la página IA) o Claude Desktop (vía `mcp-remote`, necesita Node.js) al VPS y probarlo.
- [ ] Claude Desktop sin `mcp-remote`: sus conectores remotos piden OAuth; hoy usamos tokens personales.
- [ ] 🔑 Medir coste y calidad de las respuestas (Opus 5.5, esfuerzo medio); probar Sonnet 5.5 para preguntas sencillas.
- [ ] Comparación de autores como modo propio de la página Preguntar (hoy: en la ficha del concepto y por la pregunta «¿En qué discrepan?»).
- [ ] Respuestas en streaming (hoy se espera a la respuesta completa, ~10-60 s).

## Fase 7 — Learning Intelligence
- [x] 2026-11-26 Tarjetas con repetición espaciada FSRS (automáticas y propuestas por IA), con historial.
- [x] 2026-11-26 Cuestionarios por módulo corregidos con la fuente (IA simulada).
- [x] 2026-11-26 Informe de lagunas: biblioteca frente a usuario.
- [ ] 🔑📄 Probar tarjetas y cuestionarios de IA con la clave y un curso real (calidad de preguntas, coste).
- [ ] Ajustar los umbrales de lagunas con tu uso real (hoy fijos y visibles en el informe).
- [ ] Optimizar los parámetros de FSRS con tu historial cuando haya cientos de repasos.
- [ ] Tarjetas con huecos (cloze) y con imágenes de diapositivas.
- [x] 2026-11-26 Recordatorio diario de repaso (`kos remind`, con los avisos de monitorización).

## Secciones de la biblioteca
- [x] 2026-11-26 Secciones (Cursos, Libros, Estudios, Artículos, Vídeos, Audios, Apuntes, Recursos), varias por fuente, colocación automática, relacionado entre secciones, enlaces manuales, «Dónde aparece» por sección, búsqueda/preguntas/MCP por sección.
- [ ] 👤 Revisar con material real si la colocación automática acierta (p. ej. PDF de libro cae en Recursos: muévelo a Libros a mano).
- [ ] Reordenar secciones arrastrando (hoy orden fijo de creación).
- [ ] Sugerir enlaces entre fuentes con IA (hoy: por conceptos compartidos y a mano).
