# Roadmap — Knowledge OS

Cada fase termina solo cuando se cumplen sus criterios de aceptación con datos reales (o sintéticos marcados como tales si aún no hay materiales).

## Fase 0 — Auditoría y planificación ✅
- [x] Auditoría de código de ambos repositorios (`REPOSITORY_AUDIT.md`)
- [x] Arquitectura, modelo de datos, decisiones (`ARCHITECTURE.md`, `DATA_MODEL.md`, `TECHNICAL_DECISIONS.md`)
- [x] Confirmar alcance (ampliable), acceso (solo personal) y estrategia IA (híbrida + MCP)
- [ ] Verificar licencias de dependencias a trasplantar (PyMuPDF, opendataloader-pdf)
- [ ] Inventario del VPS (requiere acceso del usuario; no disponible desde esta sesión)

## Fase 1 — Núcleo de conocimiento mínimo (prioridad absoluta)
Orden de construcción:
1. Esqueleto: monorepo, `docker-compose` (postgres+pgvector, api, worker, web), migraciones, auth de usuario único, CI con tests.
2. Biblioteca: subida PDF/Markdown/TXT, hash y deduplicación, almacenamiento inmutable.
3. Ingesta: extracción por página, segmentación con localización, FTS + embeddings, cola de jobs con reanudación.
4. Adaptadores IA + `ai_runs` + presupuesto.
5. Pipeline de conocimiento: menciones, afirmaciones, resolución de entidades con alias, cola de revisión.
6. Páginas de entidad generadas desde afirmaciones, con citas a segmento y revisiones.
7. Consulta con citas verificables (respuesta → afirmaciones/segmentos → abrir PDF en la página).
8. UI mínima: biblioteca, estado de jobs, página de entidad, cola de revisión, búsqueda, consulta.

**Criterio de aceptación:** con 3 documentos de entrenamiento con contenido solapado: conceptos repetidos se unifican (con alias), cada afirmación abre su fuente en la página correcta, una cuarta fuente actualiza sin duplicar, y una pregunta devuelve respuesta con citas comprobables.

## Fase 2 — Grafo de conocimiento
Relaciones tipadas con evidencia, vista árbol (`is_a`/`part_of`), grafo interactivo contextual con filtros, comparación de explicaciones entre autores/cursos, revisión de fusiones.
**Aceptación:** un principio explicado por varios profesores muestra coincidencias, diferencias y contradicciones, cada una con su fuente.

## Fase 3 — Learning Center
Disciplinas, áreas, formaciones, cursos, módulos, lecciones, materiales, progreso, apuntes. Navegación concepto ↔ curso.
**Aceptación:** desde «proximidad al fallo» se ve qué cursos/lecciones lo tratan y se vuelve a la lección.

## Fase 4 — Evidencia científica
Publicaciones con metadatos metodológicos, afirmaciones de estudios vs. de profesores, análisis de contradicciones con revisión humana.

## Fase 5 — Multimedia
Transcripción con marcas temporales, DOCX/PPTX/EPUB, OCR de escaneados, diapositivas. Priorizado según los formatos reales de tus materiales.

## Fase 6 — AI Workspace + MCP
Consultas por fuente/curso/disciplina/global, comparación de autores, modos de investigación y aplicación; servidor MCP.

## Fase 7 — Learning Intelligence
Flashcards, repetición espaciada (FSRS o SM-2 configurable), cuestionarios, detección de lagunas (distinguiendo "no está en la biblioteca" de "el usuario no lo domina").

## Fase 8 — Producción
Seguridad, backups/restauración probados, monitorización, rendimiento medido, documentación de usuario, despliegue en el VPS.

## Necesito del usuario
1. Los materiales del piloto: 3 cursos/documentos de entrenamiento con temas solapados (idealmente con «fatiga muscular» o «proximidad al fallo» tratados por profesores distintos), indicando formato (PDF, vídeo, transcripción…).
2. Para el despliegue: acceso o inventario del VPS (servicios, Caddyfile, puertos).
3. Proveedor(es) de IA y presupuesto mensual orientativo.
