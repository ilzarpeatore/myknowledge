# Pendientes

Lista viva de lo que queda por hacer o comprobar, por fase. Se actualiza en cada entrega: lo resuelto se marca `[x]` con la fecha y se deja unas semanas antes de borrarlo.

Leyenda: 🔑 necesita la clave de Anthropic · 📄 necesita material real · 🖥️ necesita el VPS · 👤 decisión o acción del usuario

## General e infraestructura
- [x] 2026-11-27 GitHub Actions vuelve a ejecutarse. Las pruebas que daban por hecho los extras (Docling, transcripción) se saltan sin ellos, como en CI.
- [x] 2026-11-27 Código llevado a `main` (PR #1, con la CI en verde).
- [ ] 🖥️ Que el VPS siga `main` (`git checkout main` en `/opt/myknowledge`): hasta entonces sigue la rama de trabajo, que también recibe todo.
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
- [x] 2026-11-27 Un PDF de 221 páginas agotaba los 3 GB del worker (el kernel lo mataba) y, al reiniciarse, el worker lo retomaba sin fin (14 intentos con máximo 3). Arreglado: Docling convierte por tandas de 20 páginas, cada una en un proceso nuevo (pico medido ~2,2 GB por tanda), y una tanda que falla deja esas páginas con extracción básica; un trabajo que mata al worker en todos sus intentos pasa a «error» (y su fuente también) en vez de repetirse.
- [ ] 🖥️ RAM justa: `KOS_WORKER_MEM_LIMIT=3g` (con 4g el resto del VPS se quedaría con ~0,3 GB). Vigilar con material real que ningún PDF se quede sin memoria.
- [ ] 🖥️ CPU compartida con producción: `KOS_WHISPER_THREADS=2` (con 4 ocuparía los 4 núcleos); medir cuánto tarda 1 h de audio así.
- [ ] `scripts/README.md` no existe (lo cita la documentación de trabajo); el uso de cada script está en su cabecera y en DEPLOYMENT.md.
- [x] 2026-11-26 Deploy key de solo lectura (`/root/.ssh/myknowledge_deploy`, alias `github-myknowledge`) registrada en GitHub; `git fetch`/`git pull --ff-only` desde el VPS funcionan.
- [x] 2026-11-26 Subdominio `myknowledge.bestronger.es` → 31.70.155.184 (A cambiado y AAAA de IONOS borrado; comprobado en 1.1.1.1, 8.8.8.8 y el DNS autoritativo).
- [ ] 👤 Destino de copias de seguridad fuera del VPS (rclone/rsync).
- [x] 2026-11-26 Copia y restauración en Docker en una instalación limpia, con transcripción, OCR, conceptos y tarjetas: todo idéntico.
- [x] 2026-11-26 Imágenes construidas en el VPS en ~12 min (worker 5,6 GB, api/migrate 0,98 GB, web 0,36 GB) y sistema arrancado: `migrate` exited 0, API healthy, `/api/health` ok en 127.0.0.1:3010, worker con Docling, OCR, transcripción, búsqueda por significado y yt-dlp 2026.11.23. En reposo: worker 0,5 GB, resto < 0,2 GB.
- [x] 2026-11-26 Modelo de transcripción descargado (`kos download-whisper`, volumen `whisper`); el worker lo detecta tras reiniciarlo.
- [x] 2026-11-26 Los demás servicios del VPS siguen igual tras construir y arrancar (mismos códigos HTTP en los 7 sitios, mismos 7 contenedores, Caddyfile sin cambios por SHA-256).
- [ ] 🖥️ Probar `backup.sh`/`restore.sh` en el VPS (la restauración, en un proyecto aparte).
- [x] 2026-11-26 Usuario `hamza` creado con una contraseña aleatoria generada en el VPS (fichero `/root/kos-contrasena-inicial-hamza.txt`, solo root); inicio de sesión comprobado (200).
- [ ] 👤 Guardar esa contraseña (o cambiarla con `kos reset-password hamza`) y autorizar el borrado del fichero.
- [ ] `kos create-user` solo acepta la contraseña por terminal interactiva; para automatizar haría falta leerla de un fichero o de stdin.
- [x] 2026-11-26 Bloque de Caddy añadido al final del Caddyfile (copia `Caddyfile.bak.20261126190848`, `caddy validate`, `systemctl reload`). Certificado de Let's Encrypt obtenido al primer intento; `https://myknowledge.bestronger.es` da 200, `/api/health` ok, CSP y `X-Frame-Options` presentes; inicio y cierre de sesión por HTTPS comprobados (200 → `/api/auth/me` 200 → logout 204 → 401).
- [x] 2026-11-26 Los 7 sitios existentes del VPS dan los mismos códigos que antes tras recargar Caddy.
- [x] 2026-11-26 HSTS: Caddy no lo envía por defecto; añadido `header Strict-Transport-Security "max-age=31536000"` solo al bloque de `myknowledge` (copia `Caddyfile.bak.20261126191522`). Comprobado en la respuesta; los otros 7 sitios, igual que antes.
- [x] 2026-11-26 Red entre Caddy y el gateway: en el VPS Caddy es un servicio del sistema, así que `reverse_proxy 127.0.0.1:<puerto>` funciona sin tocar redes Docker.
- [x] 2026-11-26 Modelo de búsqueda int8 en una CPU sin AVX-512 VNNI (VPS, AMD EPYC-Milan, solo AVX2): funciona. Frente a fp32: similitud media 0,996 (mínima 0,995), primer resultado correcto 7/7 en ambos, y int8 1,6× más rápido (14 frente a 22 ms por fragmento largo con 2 hilos). No hace falta la alternativa de D-15.
- [x] 2026-11-26 Monitorización y alertas (worker, cola, disco, copias, presupuesto; ntfy/Slack).
- [x] 2026-11-27 Cron de `kos monitor` (cada 15 min) y `kos remind` (8:31 UTC, el VPS está en UTC) en el crontab de root, que es quien gestiona Docker. Canal: ntfy. `kos monitor` a mano: las 6 comprobaciones en «ok».
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
- [x] 2026-11-27 Idioma de la transcripción: se detecta solo (español, inglés…) y, en YouTube sin subtítulos, se usa el idioma que declara el vídeo. Comprobado con audio sintético en inglés y en español.
- [x] 2026-11-27 🖥️ En el VPS: `KOS_WHISPER_LANGUAGE=` vacío en el `.env` (estaba en `es`) y worker reiniciado; el worker lo ve vacío.
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
- [x] 2026-11-27 Nueva sección fija «Manuales» (tras Libros; migración 0018).
- [ ] Reordenar secciones arrastrando (hoy orden fijo de creación).
- [ ] Sugerir enlaces entre fuentes con IA (hoy: por conceptos compartidos y a mano).

## Cruce y análisis de contenido + referencias (2026-11-27)
- [x] 2026-11-27 Referencias bibliográficas: detección sin IA (citas, bibliografías, DOI), agrupación por obra, contador de menciones y documentos, añadir/vincular a Estudios, descartar, unir.
- [x] 2026-11-27 «Ver también» por fragmento, mapa de cobertura, consenso con nivel de evidencia (según tu evaluación), comprobación frente a estudios, contradicciones y enlaces propuestos al analizar cada fuente, «qué aporta», perfil de profesor. Probado con IA simulada y el modelo de significado real sobre material PRUEBA.
- [ ] 🔑📄 Probar el análisis con la clave y material real: calidad de los tipos de enlace, de las contradicciones y de las comprobaciones frente a estudios; coste por fuente.
- [ ] 📄 Recalibrar los umbrales de parecido (0,95 / 0,90 / 0,88, ver D-23) con tu material: medidos solo con textos sintéticos.
- [ ] 📄 Revisar la detección de referencias con tus diapositivas y libros reales (formatos de cita que falten, falsos positivos).
- [x] 2026-11-27 🖥️ En el VPS, tras actualizar a 6fd6508: `docker compose exec api kos scan-references` ejecutado (la biblioteca aún tiene 0 fuentes: 0 trabajos encolados, sin errores). Copia previa en `/var/backups/myknowledge/kos-20261127-095742`.
- [ ] Referencias: buscar automáticamente el DOI de las que no lo tienen (Crossref por autor, año y título).
- [ ] Herramientas MCP para referencias, consenso y perfil de profesor (hoy solo en la web).
- [ ] El menú superior ocupa dos líneas en pantallas medianas: agrupar entradas.

## Coste y recursos (2026-11-27)
- [x] 2026-11-27 OCR solo en páginas sin texto (`KOS_PDF_OCR=auto`, D-24).
- [x] 2026-11-27 Haiku 5.5 para extract, classify, resolve y check (migración 0020).
- [ ] 🖥️ Medir en el VPS el antes/después del OCR con el libro de 221 páginas (tiempo, CPU, memoria) y comprobar que el texto sale igual.
- [ ] 🔑📄 Con la clave puesta: comparar la calidad de la extracción con Haiku frente a Opus en unos capítulos del libro antes de procesarlo entero.
- [ ] Limitar el modelo de tablas a las páginas que tienen tablas (segunda parte más pesada de Docling).

## Clave de Anthropic desde la web (2026-11-27)
- [x] 2026-11-27 Página IA: guardar, probar y quitar la clave (cifrada con KOS_SECRET_KEY; migración 0021).
- [ ] 🖥️ En el VPS: generar `KOS_SECRET_KEY` en el `.env` (openssl rand -hex 32), desplegar y guardar copia de esa clave maestra fuera del servidor.
- [ ] 👤 Revocar la clave de prueba que se pegó en el chat cuando acaben las pruebas.

## Despliegue automático (2026-11-27)
- [x] 2026-11-27 `scripts/auto-update.sh` (copia, actualización, comprobación, vuelta atrás y aviso) y `.github/workflows/deploy.yml`. Probado con un repositorio y un Docker simulados: actualización buena, versión rota con vuelta atrás, no reintento, cambios locales, forzado y llamada SSH restringida.
- [x] 2026-11-27 Opción 1: desplegado `0813732` a mano (copia previa, `KOS_SECRET_KEY` añadida al `.env`, `/api/health` ok); `auto-update.sh` a mano dice «up to date»; línea de cron cada 30 min en el crontab de root, con registro en `/root/kos-auto-update.log`.
- [ ] 👤 Opción 1, opcional: el token de solo lectura para exigir CI en verde (`KOS_DEPLOY_REQUIRE_CI=1`).
- [ ] 👤🖥️ Opción 2: llevar el código a `main` (pull request), poner el VPS en `main`, crear la clave SSH restringida y los secretos en GitHub.
- [x] 2026-11-27 `KOS_ALERT_URL` configurado con un tema de ntfy.sh generado al azar (el nombre solo está en el `.env` del VPS y en el móvil del usuario); aviso de prueba recibido en el móvil.

## MCP con escritura (2026-11-27)
- [x] 2026-11-27 Herramientas por lotes: propose_contradictions, propose_merges, propose_study_checks, propose_source_links, save_note, review_queue, decide (token «decidir»). Respuestas compactas y paginadas, get_fragments por lotes. Medido: 20 herramientas ≈ 11 000 caracteres de definiciones; un concepto de 62 afirmaciones en páginas de 15 (≈ 9 700 caracteres).
- [ ] 🖥️ Desplegar y crear un token «escribir» (o «decidir») en IA → Conectar Claude.
- [ ] 👤 Probar con Claude Desktop o Claude Code sobre el libro y el estudio: comparar, proponer contradicciones y guardar una síntesis.

## Ramas de las fuentes (2026-11-27)
- [x] 2026-11-27 Ramas (disciplinas) por fuente: al subir y en YouTube, editar, filtrar en biblioteca, Buscar, Preguntar y MCP (migración 0022).
- [ ] Sugerir la rama de una fuente a partir de sus conceptos (o con IA) cuando se suba sin rama.

## YouTube: vídeos doblados (2026-11-27)
- [x] 2026-11-27 Un vídeo en español llegó con la transcripción en árabe: YouTube declaraba el idioma de una pista doblada y se cogieron los subtítulos traducidos. Ahora se usa el idioma original (pista «-orig» o audio marcado como original); si no se sabe, se transcribe el audio original; los subtítulos en otra escritura se rechazan. La «cascada» de los subtítulos automáticos se limpia. Botón «Volver a descargar de YouTube» en la página de la fuente.
- [ ] 👤 Pulsar «Volver a descargar de YouTube» en el vídeo de Ibon Suberbiola cuando el VPS se actualice.
- [ ] 🖥️ Si YouTube bloquea al VPS («confirma que no eres un bot»), descargar subtítulos o audio en el PC y subirlos.

## Eliminar fuentes (2026-11-27)
- [x] 2026-11-27 Eliminar definitivamente desde la papelera (archivados), con vista previa de lo que se pierde; borra archivos y datos derivados, conserva conceptos y afirmaciones con otras citas; queda en la auditoría.

## Definición de conceptos (2026-11-27)
- [x] 2026-11-27 Sección «Definición» en la ficha: escrita por el usuario o propuesta por la IA (basada en la biblioteca, con citas comprobadas literalmente; sin citas válidas se marca como conocimiento general), propuesta para aceptar, editar o descartar, explicación ampliada opcional, «Cómo lo definen tus fuentes» e historial con recuperación. Tarea de IA propia (`define`, empieza con el modelo de «Redactar páginas»). Herramienta MCP `propose_definitions` y `decide` con definiciones. Las definiciones ya guardadas pasan a ser la actual (migración 0023).
- [ ] 🔑 Probar «Generar con IA» con conceptos reales y ajustar el prompt (longitud, tono, cuándo marca «conocimiento general»).
- [ ] Generar definiciones en lote (p. ej. todos los conceptos de una rama sin definición), con coste estimado antes de empezar.

## Autores (2026-11-27)
- [x] 2026-11-27 Autores de cada fuente (en orden) al subir archivos o vídeos y en «Editar»; biblioteca **Autores** con buscador mientras escribes (sin tildes, inicio de cualquier palabra, en cualquier orden y, si no hay nada, nombres parecidos), sin duplicados por nombre, editar, fusionar, borrar si no tiene nada; filtro por autor en la Biblioteca. Migración 0024 (`source_authors`, nombre normalizado con índice de trigramas).
- [ ] Al vincular una fuente a un estudio con DOI, proponer como autores los del artículo (hoy quedan solo como texto en el estudio).
- [ ] Atribuir a su autor las afirmaciones que se extraigan de una fuente con un solo autor (profesor de una clase), en vez de dejarlas sin atribuir.
- [ ] Convertir en autores el texto de autores de las fuentes ya subidas (hoy se hace fuente a fuente con «Convertir en autores»).

## Visor de PDF integrado (Inkbar) (2026-11-27)
- [x] 2026-11-27 Fase 1: visor dentro de la página de la fuente y de la ficha del estudio, página de lectura `/read/{id}` y citas que abren en la página con la frase resaltada. Inkbar 0.18.0 fijado (la versión de npm dibuja con PDF.js 6.5; el README de su repositorio describe una versión futura con PDFium, que aún no está publicada). Archivos de PDF.js copiados a `public/inkbar/<versión>/` al compilar; CSP con `wasm-unsafe-eval` y `worker-src 'self'`; estilos de elementos de la web en `@layer base` para que no deformen el visor.
- [x] 2026-11-28 Fase 2: subrayados (resaltado, subrayado, tachado, ondulado) y notas con respuestas, deshacer/rehacer, guardado automático en Knowledge OS (`pdf_annotations`, migración 0025), nunca en el PDF; el texto que cubre cada subrayado se calcula del texto de la página y se guarda; panel «Mis subrayados». API `GET /api/annotations?q=` para buscarlos en toda la biblioteca.
- [ ] Página o sección para ver y buscar todos tus subrayados de la biblioteca (la API ya existe).
- [ ] Convertir un subrayado en afirmación con su cita o en tarjeta de estudio.
- [ ] El texto de un subrayado se calcula para texto horizontal; en columnas giradas o texto de derecha a izquierda puede salir incompleto.
- [ ] 🖥️ Probar el visor con PDFs reales grandes (libros de cientos de páginas) en el VPS y en móvil.
- [ ] Inkbar es un proyecto muy joven (0.x): revisar cada versión nueva antes de actualizar.

