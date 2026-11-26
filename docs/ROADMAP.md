# Roadmap — Knowledge OS

Cada fase termina solo cuando se cumplen sus criterios de aceptación con datos reales (o sintéticos marcados como tales si aún no hay materiales).

## Fase 0 — Auditoría y planificación ✅
- [x] Auditoría de código de ambos repositorios (`REPOSITORY_AUDIT.md`)
- [x] Arquitectura, modelo de datos, decisiones (`ARCHITECTURE.md`, `DATA_MODEL.md`, `TECHNICAL_DECISIONS.md`)
- [x] Confirmar alcance (ampliable), acceso (solo personal) y estrategia IA (híbrida + MCP)
- [x] Investigación de otros repositorios (`RESEARCH_OTHER_REPOS.md`): Docling, Graphiti, kg-gen, WhisperX, py-fsrs
- [ ] Verificar licencias de los modelos de Docling y de pyannote
- [ ] Inventario del VPS (requiere acceso del usuario; no disponible desde esta sesión)

## Fase 1 — Núcleo de conocimiento mínimo (prioridad absoluta)
Orden de construcción:
1. ✅ Esqueleto: monorepo, `docker-compose` (postgres+pgvector, api, worker, web), migraciones, auth de usuario único, cola de trabajos, CI con tests. *(Las imágenes Docker se validan en CI; no se pudieron construir en la sesión de desarrollo por falta de daemon Docker.)*
2. ✅ Biblioteca: subida PDF/Markdown/TXT, hash y deduplicación, almacenamiento inmutable, metadatos editables con auditoría, archivado lógico. Puerta de entrada (gateway Caddy) para que las subidas grandes no pasen por Next.js. *(Los vínculos fuente → curso/lección se añadirán con la Fase 3.)*
3. ✅ Ingesta: extracción por página (pypdfium2), segmentación con localización exacta (página + offsets; sección en Markdown), búsqueda por palabras (español + inglés), procesamiento en la cola con reintentos y reprocesado sin duplicados, detección de páginas sin texto. *(Embeddings y búsqueda por significado pasan a la entrega 4, junto con los proveedores de IA.)*
   - ✅ 3b. Docling + Tesseract: tablas como tablas Markdown (cada una, un fragmento), títulos como secciones, OCR de páginas escaneadas, imagen `worker` con modelos incluidos y respaldo automático a pypdfium2.
4. ✅ (parte local) Embeddings locales y búsqueda por significado/combinada (D-15).
   ✅ Capa de IA (D-16): adaptador Anthropic, `ai_runs`, presupuesto con corte, prompts versionados, página «IA». Probada con un proveedor simulado; falta la primera llamada real (`kos ai-check`) cuando haya clave.
5. Pipeline de conocimiento: menciones, afirmaciones, resolución de entidades con alias, cola de revisión.
   - ✅ Base sin IA: modelo de conocimiento completo, alias multilingües, detección de duplicados por similitud (pg_trgm) con revisión humana, fusiones reversibles, afirmaciones con cita verificada, relaciones tipadas con evidencia, interfaz (Conceptos, ficha, Revisión). Pendiente: extracción automática con IA (requiere clave).
6. Páginas de entidad generadas desde afirmaciones, con citas a segmento y revisiones.
7. Consulta con citas verificables (respuesta → afirmaciones/segmentos → abrir PDF en la página).
8. UI mínima: biblioteca, estado de jobs, página de entidad, cola de revisión, búsqueda, consulta.

**Criterio de aceptación:** con 3 documentos de entrenamiento con contenido solapado: conceptos repetidos se unifican (con alias), cada afirmación abre su fuente en la página correcta, una cuarta fuente actualiza sin duplicar, y una pregunta devuelve respuesta con citas comprobables.

Los criterios se comprueban con los materiales reales del piloto. Cada criterio que dependa de la IA se mide sobre una muestra revisada a mano por el usuario; los umbrales numéricos son iniciales y se ajustarán con los datos de la Fase 1.

## Fase 2 — Grafo de conocimiento
Relaciones tipadas con evidencia, vista árbol (`is_a`/`part_of`), grafo interactivo contextual con filtros, **mapa mental generado** de cada concepto (ramas agrupadas por tipo: mecanismos, variables, principios, teorías, aplicaciones, autores, estudios, preguntas abiertas; exportable a imagen y Markdown/OPML), comparación de explicaciones entre autores/cursos, revisión de fusiones. Los mapas se generan solo a partir del conocimiento; los mapas editables por el usuario quedan fuera por ahora.
**Aceptación:**
1. Un principio explicado por al menos 3 profesores muestra coincidencias, diferencias terminológicas, diferencias conceptuales y contradicciones, cada una enlazada a la afirmación y al fragmento de origen.
2. Toda relación tiene tipo del catálogo, al menos una evidencia y estado (`proposed`/`verified`/`rejected`); no existe ninguna relación sin evidencia.
3. En una muestra de 50 relaciones propuestas, el usuario acepta al menos el 80 %.
4. La vista árbol de una disciplina se despliega hasta 4 niveles; el grafo de un concepto con 200 vecinos se muestra en menos de 2 s y se puede filtrar por tipo de relación, disciplina, fuente y autor.
5. Una fusión de conceptos se puede deshacer y recupera sus alias y relaciones.
6. Al abrir «proximidad al fallo» se muestra su mapa mental con las ramas agrupadas por tipo; desde cualquier nodo se llega a su página y a su fuente, y el mapa exportado se abre en una herramienta externa (p. ej. XMind u Obsidian).

## Fase 3 — Learning Center
✅ Implementada sin IA (2026-11-26): disciplinas y áreas (con estructura inicial de Ciencias del entrenamiento a petición), formaciones, cursos, módulos, lecciones, materiales de la biblioteca por curso o lección, progreso (completadas, tiempo, apuntes) y panel de inicio. Criterios 1–4 cubiertos por `tests/test_learning.py` con datos sintéticos; queda validarlos con un curso real.
Disciplinas, áreas, formaciones, cursos, módulos, lecciones, materiales, progreso, apuntes. Navegación concepto ↔ curso.
**Aceptación:**
1. Se crea un curso real con módulos y lecciones, se le asignan sus materiales y al procesarlos sus conceptos quedan vinculados a la lección de origen.
2. Desde «proximidad al fallo» se ve qué cursos y lecciones lo tratan y se vuelve a la lección con un clic; desde la lección se ven sus conceptos extraídos.
3. El progreso (lecciones completadas, tiempo, último acceso, porcentaje) se registra y se refleja en el dashboard.
4. Un mismo concepto tratado en 2 cursos aparece como una sola entidad vinculada a ambos.

## Fase 4 — Evidencia científica
Publicaciones con metadatos metodológicos, afirmaciones de estudios vs. de profesores, análisis de contradicciones con revisión humana.
**Aceptación:**
1. Cada afirmación muestra su tipo (definición, hecho reportado, hipótesis, opinión, recomendación, inferencia de IA, conclusión personal) y su origen (profesor, estudio, IA, usuario).
2. Cada cita literal guardada coincide exactamente con el texto del fragmento (verificación automática al 100 %).
3. Una publicación con DOI se registra con sus metadatos y su calidad metodológica aparece como «no evaluada» hasta que el usuario la evalúe.
4. Ante dos afirmaciones incompatibles, el sistema propone la contradicción con ambas fuentes y contextos, y el usuario puede clasificarla como real, diferencia de contexto o descartada; ninguna afirmación se elimina.
5. En la página de un concepto se filtra «solo respaldado por estudios» frente a «explicaciones de profesores».

## Fase 5 — Multimedia
Transcripción con marcas temporales, DOCX/PPTX/EPUB, OCR de escaneados, diapositivas. Priorizado según los formatos reales de tus materiales.
**Aceptación:**
1. Una clase grabada de 1 h se transcribe y cada afirmación extraída abre el vídeo/audio en su minuto (±5 s).
2. Una transcripción importada (SRT/VTT) se procesa sin volver a transcribir.
3. Un PDF escaneado se convierte con OCR y sus citas conservan el número de página.
4. Una presentación conserva el número de diapositiva en sus citas; DOCX y EPUB conservan sección/capítulo.
5. Un trabajo de transcripción interrumpido se reanuda sin repetir lo ya procesado.

## Fase 6 — AI Workspace + MCP
Consultas por fuente/curso/disciplina/global, comparación de autores, modos de investigación y aplicación; servidor MCP.
**Aceptación:**
1. Las 8 preguntas de prueba del piloto (§19.3 del documento maestro) obtienen respuesta con citas, y en una revisión manual ninguna cita atribuye a una fuente algo que no dice.
2. Cada respuesta separa visualmente: extraído de fuentes, síntesis, inferencia de IA y conocimiento externo, e indica la cobertura documental.
3. Si la biblioteca no contiene información suficiente, la respuesta lo dice en lugar de inventar.
4. Claude (Code o Desktop) se conecta por MCP, consulta la biblioteca y sus cambios llegan como propuestas pendientes de aprobación, nunca como escrituras directas.
5. El modelo de cada tipo de tarea se cambia en la configuración sin tocar código.

## Fase 7 — Learning Intelligence
Flashcards, repetición espaciada (FSRS), cuestionarios, detección de lagunas (distinguiendo "no está en la biblioteca" de "el usuario no lo domina").
**Aceptación:**
1. Se generan flashcards desde un concepto; cada tarjeta enlaza a su concepto y fuente.
2. Las revisiones programan la siguiente fecha con FSRS y el historial se conserva.
3. Un cuestionario generado sobre un módulo se corrige con referencia a las fuentes.
4. El informe de lagunas separa conceptos sin fuentes o poco desarrollados en la biblioteca de conceptos con bajo rendimiento del usuario en revisiones.

## Fase 8 — Producción
Seguridad, backups/restauración probados, monitorización, rendimiento medido, documentación de usuario, despliegue en el VPS.
**Aceptación:**
1. La aplicación funciona en el VPS detrás de Caddy con HTTPS, sin haber modificado servicios existentes no relacionados.
2. Una copia de seguridad se restaura en un entorno limpio y se comprueba que conserva la biblioteca, la wiki y el historial.
3. Las pruebas de seguridad (acceso sin sesión, ficheros maliciosos, prompt injection en fuentes, SSRF) pasan.
4. El coste de IA por mes se muestra y el límite de presupuesto detiene los trabajos al alcanzarse.
5. Existen guías de instalación, actualización, backup/restauración y uso.

## Necesito del usuario
1. Los materiales del piloto: 3 cursos/documentos de entrenamiento con temas solapados (idealmente con «fatiga muscular» o «proximidad al fallo» tratados por profesores distintos), indicando formato (PDF, vídeo, transcripción…).
2. Para el despliegue: acceso o inventario del VPS (servicios, Caddyfile, puertos).
3. Proveedor(es) de IA y presupuesto mensual orientativo.
