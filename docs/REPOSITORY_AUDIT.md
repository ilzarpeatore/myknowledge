# Auditoría de repositorios — Knowledge OS (Fase 0)

**Fecha:** 2026-11-25
**Método:** clonado de ambos repositorios (`--depth 1`) y lectura directa del código, esquemas, prompts, Dockerfiles y tests. Se ejecutó la suite de tests unitarios de Lucas. La suite de ddsyasas **no se pudo ejecutar** en esta sesión (la instalación de dependencias fue bloqueada por la política de permisos del entorno); sus afirmaciones se basan solo en lectura de código.

| Repositorio | Commit auditado | Fecha del commit |
|---|---|---|
| `ddsyasas/llm-wiki` | `e8dd69e` | 2026-06-04 |
| `lucasastorian/llmwiki` | `4d887e8` | 2026-11-09 |

> Nota: ddsyasas no tiene commits desde junio de 2026 (casi 6 meses). Lucas tiene actividad este mes.

---

## 1. Correcciones a la auditoría previa (ChatGPT)

La auditoría previa se basó en la documentación publicada. Al leer el código aparecen diferencias que **cambian la decisión**:

| Afirmación previa | Lo que muestra el código |
|---|---|
| ddsyasas: "Ingesta documental de PDF, 8/10" | **No hay extracción local de PDF.** `packages/ingestion/src/pdf.ts` codifica el PDF entero en base64 y lo envía al LLM vía OpenRouter (`type: "file"`). No se conservan números de página, no hay troceado, no hay OCR propio. Con Ollama no funciona este camino. |
| ddsyasas: "selecciona hasta 15 páginas relevantes" | Cierto (`TOP_K_RELEVANT_PAGES = 15`, FTS5), **pero la fuente se envía completa en una sola llamada** (`ingestSource`). Un libro o un curso largo provoca `ContextLengthError`. No hay segmentación de fuentes. |
| ddsyasas: "trazabilidad de fuentes 8/10" | La trazabilidad es solo página-wiki ↔ fuente (`page_sources`). No hay localización (página, minuto, fragmento). Las actualizaciones reemplazan el cuerpo entero de la página. |
| Lucas: "Cuestionarios y exámenes: No" | **Sí existen**: bloques ```` ```quiz ```` en Markdown (opción múltiple y respuesta libre), validador (`api/domain/quiz_lint.py`), progreso en el frontend (`web/src/lib/quiz/`) y corrección con LLM (`api/services/quiz_grader.py`). |
| Lucas: "Módulos y lecciones: no acreditado" | Existe `kind = 'course'` (migración `008_kb_kind.sql`) con módulos = carpetas y lecciones = ficheros. **Ojo:** son cursos *generados* por la IA a partir de fuentes, no un registro de los cursos que el usuario ha hecho. |
| Lucas: "Referencias exactas a página: debe verificarse" | **Verificado**: `document_pages` (contenido por página), `document_chunks` con `page`, `start_char` y `header_breadcrumb`; el guide obliga a citar `fichero.pdf, p.3` y el `lint` comprueba que las citas resuelven. |
| Lucas: "integración con agentes" (implícito: la app usa IA) | **La aplicación no llama a ningún LLM para construir la wiki.** Toda la escritura la hace un agente externo (Claude, Codex…) conectado por MCP. La única llamada LLM interna es el corrector de quizzes. |
| Ambos: "8,2 vs 8,5" | Ver puntuación revisada en §4. |

---

## 2. Auditoría individual: `ddsyasas/llm-wiki`

**Licencia:** MIT. **Tamaño:** ~24.800 líneas TS/TSX (sin dependencias). **Gestor:** pnpm monorepo.

### Estructura
```
apps/web            Next.js 14 (App Router) + API routes, React 18, Tailwind, react-force-graph-3d
packages/core       ingest / query / lint / chat, SQLite (better-sqlite3), FTS5, historial
packages/ingestion  detección de formato y extractores
packages/llm        cliente OpenAI-compatible (OpenRouter u Ollama), validación zod, reintentos
```

### Componentes clave
| Área | Implementación real |
|---|---|
| Almacenamiento | Carpeta de wiki en disco (`raw/`, `wiki/`, `index.md`, `log.md`) + `.llm-wiki/meta.sqlite` (sources, pages, pages_fts, page_sources, chats, usage, response_cache, sync_state) |
| Tipos de página | Enum cerrado: `entity, concept, source, comparison, overview` |
| Extracción | DOCX: `mammoth`; PPTX/XLSX: `officeparser`; HTML/URL: `@mozilla/readability` + `turndown`; **PDF e imagen: se delegan al LLM (visión)** |
| Ingesta | Una llamada LLM por fuente con: schema del usuario + índice completo + 15 páginas FTS + fuente completa. Respuesta validada con zod (`IngestResponseSchema`) |
| Aprobación humana | `dryRun` → propuesta → `/api/ingest/apply`. Bien resuelto |
| Historial | Copia de la página anterior en `page-history/` antes de sobrescribir; restauración y papelera |
| Proveedores | OpenRouter y Ollama (vía API OpenAI-compatible). Modelo configurable por operación (ingest/query/chat/lint/vision). Registro de tokens y coste estimado |
| Grafo | Enlaces `[[slug]]` no tipados, visualización 3D |
| Seguridad | Local-first, escucha en `127.0.0.1`, **sin autenticación**. Clave API en keychain (`keytar`) o fichero. En `ingestion/src/url.ts` **no se observó validación de destino (SSRF)** al descargar URLs |
| Procesamiento | Síncrono dentro de la petición HTTP (`maxDuration = 180`). Sin cola |
| Tests | Vitest en core/llm/ingestion (no ejecutados en esta auditoría) |

### Qué vale la pena reutilizar (como patrón o código, MIT)
- Flujo **propuesta → revisión → aplicación** (`dryRun`/`apply`).
- Construcción de prompts con contenido de usuario cercado (`fenceMarkdown`) y ejemplo explícito de JSON.
- Validación de salida con zod + reintentos + clasificación de errores (`packages/llm/src/errors.ts`).
- Registro de uso y estimación de costes por operación.
- Lint semántico (contradicción, huérfana, laguna, enlace roto, obsoleta) como taxonomía de incidencias.

### Qué habría que reemplazar
- Extracción de PDF (núcleo del caso de uso), segmentación, localización, almacenamiento (SQLite → PostgreSQL), modelo de página cerrado, procesamiento síncrono, ausencia de autenticación, ausencia de MCP. Es decir: **casi todo el backend**.

---

## 3. Auditoría individual: `lucasastorian/llmwiki`

**Licencia:** Apache 2.0 (exige conservar avisos de copyright/NOTICE y marcar ficheros modificados). **Tamaño:** ~34.700 líneas Python + ~27.500 líneas TS/TSX.

### Estructura
```
api/        FastAPI: documentos, subida (tus), grafo, quizzes, eventos, websockets, SSRF-safe fetch, rate limit
converter/  servicio aislado: LibreOffice → PDF, opendataloader-pdf (Java), límites de páginas/elementos
mcp/        FastMCP: guide, search, read, create/edit/append, delete (con aprobación), lint, references, comments, ingest-by-URL
web/        Next.js 16, React 19, TipTap, react-pdf, Mermaid, KaTeX, grafo 2D, PWA
extension/  extensión Chrome (recorte de páginas/PDF, subrayados, comentarios)
shared/     migraciones SQLite (modo local)
supabase/   11 migraciones PostgreSQL (modo alojado)
tests/      unitarios e integración (incluye aislamiento entre usuarios y MCP)
```

### Componentes clave
| Área | Implementación real |
|---|---|
| Modos | **Local**: carpeta de trabajo + SQLite como índice derivado (reconstruible), sin autenticación, solo loopback. **Alojado**: Supabase Auth + PostgreSQL + S3 |
| Modelo de datos | `documents` (wiki/source/asset, versión, hash, estado), `document_pages`, `document_chunks` (page, start_char, breadcrumb, anotaciones), `document_references` (`cites`/`links_to` con página), `knowledge_base_events` (auditoría por triggers) |
| Extracción PDF | Local con `opendataloader-pdf` (requiere Java), texto + figuras por página; OCR Mistral opcional; Office vía LibreOffice en servicio separado |
| Búsqueda | FTS5 (local) / pgroonga (alojado). **Sin embeddings** |
| Citas | Notas al pie con fichero y página; el grafo de citas se extrae en cada escritura; `lint` determinista valida frontmatter, citas, enlaces, huérfanas, fuentes no citadas |
| Subrayados | Highlights y comentarios del usuario indexados junto al texto de la fuente |
| Cursos/quizzes | `kind='course'` (lecciones generadas por IA), bloques quiz con corrector LLM (Gemma vía Cloudflare AI Gateway) |
| IA | **Ninguna llamada LLM para ingesta/síntesis**: lo hace el agente MCP externo siguiendo `GUIDE_TEXT` |
| Seguridad | SSRF tests, endurecimiento del parser HTML, borrado vía MCP con aprobación, aislamiento por usuario, workflow de seguridad (npm audit, pip-audit) |
| Tests | **1.317 tests unitarios pasan**; 30 fallan y 19 dan error al ejecutarlos en un solo proceso. Las trazas apuntan a colisión de módulos homónimos (`api/config.py` vs `mcp/config.py`, `auth.py`): la CI los ejecuta por separado. No se ejecutaron los de integración (requieren PostgreSQL) |

### Qué vale la pena reutilizar (Apache 2.0)
- **Extracción documental**: `api/services/pdf_extract.py`, `chunker.py`, `api/html_parser/`, `converter/` (aislamiento de LibreOffice, límites de recursos).
- **Seguridad de red**: `api/infra/safe_fetch.py` (+ sus tests de SSRF).
- **Modelo de procedencia**: páginas y chunks con `page`/`start_char`/`breadcrumb`, re-chunking incremental por huella (`_count_unchanged_prefix`).
- **Servidor MCP**: diseño de herramientas y el `GUIDE_TEXT` como contrato para agentes.
- **Lint determinista de citas** y grafo de referencias derivado del Markdown.
- Formato de quiz en Markdown y su validador.

### Qué no encaja
- Su supuesto central (*el agente externo escribe todo*) no basta para procesar cientos de horas de cursos de forma reproducible, con colas, presupuestos y salida estructurada.
- Dependencia de Supabase en modo alojado; el modo local no tiene autenticación.
- Sin relaciones tipadas, sin afirmaciones/evidencias como entidades, sin LMS de cursos realizados, sin transcripción de audio/vídeo, sin búsqueda semántica.
- Tamaño y complejidad: forkear supone mantener ~62k líneas que en gran parte no necesitamos (extensión, PWA, cuotas, Railway/Netlify, Sentry, Logfire).

---

## 4. Comparativa revisada (orientada a nuestro proyecto)

Escala 1–10. Son valoraciones propias a partir de la lectura de código, no benchmarks.

| Área | ddsyasas | Lucas | Comentario |
|---|---|---|---|
| Extracción documental | 4 | 8 | ddsyasas delega PDF al LLM; Lucas extrae localmente por página |
| Procedencia / localización | 3 | 8 | Lucas: página + offset + breadcrumb |
| Ingesta de fuentes largas | 2 | 7 | ddsyasas no trocea; Lucas sí (pero la síntesis depende del agente) |
| Pipeline IA interno y reproducible | 7 | 2 | ddsyasas sí tiene pipeline con zod; Lucas no tiene |
| Revisión humana de cambios IA | 8 | 5 | ddsyasas: propuesta/aplicación; Lucas: eventos y aprobación de borrado |
| Integración con agentes (MCP) | 0 | 9 | |
| Grafo semántico tipado | 2 | 3 | Ninguno |
| Afirmaciones / evidencias | 1 | 3 | Lucas solo citas a nivel de página |
| Learning (cursos, quizzes) | 0 | 5 | Lucas: cursos generados y quizzes; no LMS |
| Multimedia (audio/vídeo) | 0 | 1 | Ninguno transcribe |
| Seguridad | 3 | 7 | |
| Tests | 5 (no verificados) | 8 | |
| Simplicidad / modificabilidad | 8 | 5 | |
| Encaje de stack con destino (Postgres, workers, Docker) | 3 | 7 | |

---

## 5. Decisión

**No forkear ninguno de los dos como base del producto.** Construir un repositorio propio (`myknowledge`) con nuestro modelo de datos, y **trasplantar componentes concretos** con su licencia:

| Origen | Qué se reutiliza | Modo |
|---|---|---|
| Lucas (Apache 2.0) | Extracción PDF/Office/HTML, chunker, safe_fetch, converter aislado, diseño MCP, lint de citas, formato quiz | Copia de código adaptada, con aviso de licencia y cabecera de "modificado" |
| ddsyasas (MIT) | Flujo propuesta→aprobación, validación estructurada con reintentos, registro de coste, taxonomía de lint, prompts con contenido cercado | Principalmente como patrón; código puntual con atribución MIT |
| Propio | Modelo de datos (entidades, alias, relaciones tipadas, afirmaciones, evidencias, cursos), pipeline de extracción de conocimiento, cola, LMS, visualización, Learning Intelligence | Desarrollo nuevo |

### Justificación
1. **El núcleo del proyecto** —reconocer conceptos repetidos entre cursos, relacionarlos, conservar la fuente exacta y comparar explicaciones— **no existe en ninguno**. Hay que diseñarlo desde el modelo de datos.
2. Elegir ddsyasas como base (propuesta previa) obligaría a reescribir extracción, almacenamiento, procesamiento y seguridad: no ahorra trabajo en lo difícil, solo en la UI.
3. Forkear Lucas arrastra ~62k líneas y su supuesto de "el agente externo escribe todo". Sus mejores piezas (extracción con procedencia, seguridad, MCP) se pueden extraer porque están razonablemente aisladas.
4. Un repositorio propio evita mantener un fork divergente de dos proyectos activos/inactivos.

### Riesgos de esta decisión
- Más trabajo inicial de UI que partiendo de una aplicación ya hecha (mitigación: shadcn/ui, vistas mínimas en Fase 1).
- Mantener sincronizado el código trasplantado si upstream corrige fallos de seguridad (mitigación: registrar commit de origen en `THIRD_PARTY.md`).
- `opendataloader-pdf` requiere Java en la imagen del worker (mitigación: alternativa PyMuPDF; ver TECHNICAL_DECISIONS D-07). **Licencias de dependencias transitivas pendientes de verificar** antes de trasplantar.

### Componentes descartados
- Extensión Chrome, PWA, cuotas/planes, Supabase, despliegue Railway/Netlify, Sentry/Logfire (Lucas).
- Grafo 3D, CLI npm, keytar, response cache por hash (ddsyasas).
