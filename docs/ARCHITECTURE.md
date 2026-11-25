# Arquitectura — Knowledge OS

**Estado:** Fase 0 cerrada. Decisiones confirmadas el 2026-11-25 (§8).

## 1. Principio rector

El conocimiento se modela en **PostgreSQL como entidades, alias, relaciones, afirmaciones y evidencias**. Las páginas Markdown son la vista legible y portable de ese modelo, no un almacén paralelo. Cada dato derivado de una fuente apunta a un **segmento** concreto (página, offset, marca temporal) de una **versión inmutable** de esa fuente.

```
FUENTE (inmutable) → SEGMENTOS (con localización) → MENCIONES / AFIRMACIONES
      → ENTIDADES (conceptos, principios…) + ALIAS → RELACIONES TIPADAS (con evidencia)
      → PÁGINAS (síntesis versionada) → CONSULTAS / APRENDIZAJE
```

## 2. Vista de componentes

```
                    ┌──────────────────────────────┐
  Navegador ───────►│  web  (Next.js, TypeScript)  │  UI: Biblioteca, Wiki, Grafo, Learning, Workspace
                    └──────────────┬───────────────┘
                                   │ REST (JSON) + SSE para progreso
                    ┌──────────────▼───────────────┐
  Agentes MCP ─────►│  api  (FastAPI, Python)      │  auth, CRUD, búsqueda, revisión, servidor MCP
  (Claude, Codex)   └───────┬───────────┬──────────┘
                            │           │ encola jobs (tabla jobs)
                    ┌───────▼───┐   ┌───▼──────────────────────────┐
                    │ PostgreSQL│◄──┤ worker (Python)              │ extracción, OCR, transcripción,
                    │ +pgvector │   │  - pipelines de ingesta       │ segmentación, embeddings,
                    └───────────┘   │  - pipelines de conocimiento │ extracción de conceptos/afirmaciones,
                                    │  - converter aislado (Office)│ propuestas de cambios
                    ┌───────────┐   └───┬──────────────────────────┘
                    │ Ficheros  │◄──────┘
                    │ (volumen o│   originales inmutables, artefactos procesados, export Markdown
                    │  S3/MinIO)│
                    └───────────┘
```

### Por qué un backend Python y no API dentro de Next.js
El trabajo pesado (PDF, OCR, Whisper, embeddings, LibreOffice) vive en el ecosistema Python, y el código que trasplantamos de Lucas es Python. Un único backend FastAPI sirve a la vez la API web y el servidor MCP, y comparte modelos con el worker. Next.js queda como frontend puro. Son **3 servicios propios + PostgreSQL**; no se añade Redis hasta demostrar necesidad (la cola usa `SELECT … FOR UPDATE SKIP LOCKED`).

## 3. Módulos (bounded contexts)

| Módulo | Responsabilidad | Paquete |
|---|---|---|
| `library` | Fuentes, versiones, ficheros, segmentos, colecciones | `services/core/library` |
| `ingestion` | Extractores por formato, OCR, transcripción, segmentación, deduplicación por hash | `services/worker/ingestion` |
| `knowledge` | Entidades, tipos, alias, relaciones, páginas y revisiones | `services/core/knowledge` |
| `evidence` | Afirmaciones, evidencias, autores, publicaciones, contradicciones | `services/core/evidence` |
| `learning` | Disciplinas, áreas, formaciones, cursos, módulos, lecciones, progreso | `services/core/learning` |
| `review` | Propuestas de cambio de la IA, tareas de revisión, aplicación y reversión | `services/core/review` |
| `ai` | Adaptadores de proveedores, prompts versionados, ejecuciones (`ai_runs`), presupuestos | `services/core/ai` |
| `search` | FTS (PostgreSQL `tsvector`, configuración por idioma) + vectorial (pgvector) + híbrida | `services/core/search` |
| `mcp` | Herramientas para agentes externos con permisos por herramienta | `services/api/mcp` |

Regla: los módulos se comunican por funciones de servicio, no accediendo a las tablas de otros.

## 4. Pipeline de ingesta (worker)

Cada paso es un job idempotente con estado propio; un fallo reanuda desde el paso fallido.

1. **Recepción y validación**: tamaño, tipo real por magic bytes, hash SHA-256 → si existe, se vincula en lugar de duplicar.
2. **Almacenamiento del original** en `storage/raw/<sha256>` (nunca se modifica).
3. **Extracción** según formato → texto estructurado por **unidad de localización** (página PDF, diapositiva, párrafo, segundo de audio).
4. **OCR** si la página no tiene capa de texto; **transcripción** con marcas temporales para audio/vídeo.
5. **Segmentación** en `source_segments` (~500–1000 tokens) respetando títulos; cada segmento conserva su localización.
6. **Embeddings** de segmentos.
7. → Encola el pipeline de conocimiento.

## 5. Pipeline de conocimiento (núcleo)

Diseñado para que añadir un curso **no duplique** conceptos:

1. **Extracción por segmento** (modelo económico, salida JSON validada): menciones de conceptos (término literal + definición contextual si existe), afirmaciones tipadas (definición, hecho reportado, hipótesis, recomendación…), autor de la afirmación (profesor/estudio citado).
2. **Resolución de entidades** (determinista primero, IA después):
   - coincidencia exacta normalizada con `entity_aliases` (multilingüe);
   - candidatos por similitud de embeddings del término + definición;
   - el modelo decide entre `same_as` / `related` / `new` **solo sobre la lista de candidatos**;
   - confianza baja → `review_task` (no se fusiona automáticamente).
3. **Relaciones**: propuestas por IA con tipo del catálogo, siempre con evidencia (segmento); estado `proposed` hasta aprobación (o auto-aprobación configurable por umbral).
4. **Síntesis de página**: se regenera la página de una entidad cuando cambian sus afirmaciones; la página cita afirmaciones, y estas citan segmentos. Las secciones sin datos quedan vacías.
5. **Comparación de explicaciones**: agrupación de afirmaciones `definition`/`explanation` por entidad y por autor/curso → análisis de coincidencias, diferencias y contradicciones como un artefacto con evidencia.
6. Todo cambio pasa por `change_proposals` → aplicar / rechazar / revertir.

## 6. Defensa frente a prompt injection

- El texto de fuentes se envía siempre como **datos cercados** en el mensaje de usuario, nunca en el system prompt.
- Las llamadas del pipeline **no tienen herramientas**: solo devuelven JSON validado contra esquema. Un texto malicioso como mucho produce una propuesta errónea, que queda en revisión.
- Las herramientas MCP de escritura/borrado requieren permisos explícitos y quedan auditadas.
- El worker no ejecuta nada derivado del contenido; el converter de Office corre aislado y con límites de recursos.

## 7. Despliegue

`docker-compose.yml` con `postgres` (imagen con pgvector), `api`, `worker`, `web`. Un único puerto publicado detrás de Caddy (TLS y autenticación de sesión en la app). Volúmenes: `pgdata`, `storage`. Copias: `pg_dump` + `storage/` con rotación.

> El VPS del usuario (Ubuntu, Docker, Caddy) **no es accesible desde esta sesión**, que corre en un contenedor en la nube. Antes de desplegar hay que inventariar en el VPS: servicios, puertos ocupados, Caddyfile y redes Docker existentes. No se tocará nada sin ese inventario.

## 8. Decisiones confirmadas (2026-11-25)

1. **Alcance**: entrenamiento primero, preparado para cualquier disciplina (la jerarquía disciplina→área y los tipos de entidad extensibles ya lo contemplan; coste adicional bajo).
2. **Acceso**: exclusivamente personal. Sin `owner_id` ni aislamiento entre usuarios; con autenticación de un único usuario porque la app se expone en el VPS. Pasar a multiusuario exigiría una migración (añadir `owner_id` y filtros), asumido como coste futuro.
3. **IA**: híbrido: modelo económico en cloud para extracción masiva, modelo potente para síntesis/comparación, Ollama opcional; y **MCP para usar tu suscripción de Claude** en consultas y revisión interactiva.
