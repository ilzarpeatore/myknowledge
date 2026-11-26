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
- [ ] 🖥️👤 Ejecutar `./scripts/preflight.sh <dominio>` y revisar el informe antes de instalar nada.
- [ ] 👤 Crear la deploy key de solo lectura (repositorio privado).
- [ ] 👤 Subdominio apuntando al VPS.
- [ ] 👤 Destino de copias de seguridad fuera del VPS (rclone/rsync).
- [ ] 🖥️ Construir las imágenes en el VPS (verificadas en el entorno de desarrollo, no allí) y probar `backup.sh`/`restore.sh` allí.
- [ ] 🖥️ Si Caddy corre en un contenedor: resolver la red entre Caddy y el gateway (ver DEPLOYMENT.md §3).
- [ ] 🖥️ Modelo de búsqueda int8 en una CPU sin AVX-512 VNNI: no comprobado (alternativa documentada en D-15).
- [ ] Monitorización y alertas (Fase 8).

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
- [ ] Recordatorio diario de repaso (necesita notificaciones; Fase 8).
