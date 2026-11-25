# Decisiones técnicas — Knowledge OS

Formato: contexto → decisión → alternativas → reversibilidad. Estado: **P** = propuesta (pendiente de confirmar), **A** = aceptada.

| ID | Decisión | Estado |
|---|---|---|
| D-01 | Repositorio propio; trasplantar componentes de Lucas (Apache 2.0) y patrones de ddsyasas (MIT) en lugar de forkear | P |
| D-02 | Backend FastAPI (Python) + worker Python; Next.js solo frontend | P |
| D-03 | PostgreSQL 16 + pgvector como única base de datos; sin base de grafos | P |
| D-04 | Cola de trabajos en PostgreSQL (`SKIP LOCKED`); Redis solo si se demuestra necesario | P |
| D-05 | BD como fuente de verdad; Markdown como proyección continua (ver DATA_MODEL §1) | P |
| D-06 | Resolución de entidades: alias exactos → candidatos por embeddings/trigram → decisión IA restringida → revisión humana si confianza baja | P |
| D-07 | Extracción PDF: PyMuPDF por defecto (sin Java); `opendataloader-pdf` como motor alternativo para maquetación compleja | P |
| D-08 | Transcripción: interfaz `Transcriber` con implementación local (faster-whisper) y cloud configurable | P |
| D-09 | Proveedores IA vía interfaz propia (`complete_json(schema)`) con adaptadores Anthropic y OpenAI-compatible (cubre OpenRouter y Ollama) en Fase 1 | P |
| D-10 | MCP integrado en la API, con permisos por herramienta | P |
| D-11 | Autenticación desde Fase 1 (usuario único, sesión con cookie segura); detrás de Caddy | P |
| D-12 | Almacenamiento de ficheros con interfaz `BlobStore`: disco local primero, S3/MinIO después | P |

## D-01 Repositorio propio
**Contexto.** Ver `REPOSITORY_AUDIT.md`. Ninguno resuelve el núcleo (entidades/alias/relaciones/afirmaciones con evidencia). ddsyasas no tiene extracción local ni troceado; Lucas no tiene pipeline IA interno.
**Alternativas.** (a) Fork de ddsyasas — reescribir casi todo el backend. (b) Fork de Lucas — mantener ~62k líneas y Supabase. (c) Propio + trasplantes ✔.
**Reversible:** parcialmente; el coste de cambiar crece tras la Fase 1.

## D-02 Python en el backend
**Contexto.** OCR, Whisper, embeddings locales, PyMuPDF y el código trasplantable de Lucas son Python. Next.js con API routes obligaría a duplicar modelos entre dos lenguajes en el backend.
**Alternativa.** Todo TypeScript (como ddsyasas) — peor ecosistema de procesamiento documental.
**Reversible:** sí a nivel de UI; no en el worker.

## D-03 PostgreSQL + pgvector
Relaciones tipadas en tablas y `WITH RECURSIVE` bastan para los volúmenes esperados (decenas de miles de entidades). Una base de grafos añade un segundo almacén que sincronizar. Se reevaluará si aparecen consultas de grafo que no se resuelvan con SQL en tiempos aceptables (medidos, no supuestos).

## D-06 Resolución de entidades (el problema central)
Sin esto, cada curso crea su "fatiga muscular". La IA nunca crea una entidad sin antes ver los candidatos existentes; nunca fusiona por nombre parecido; las fusiones se pueden revertir (`merged_into_id`, alias conservados).
Métrica de aceptación en el piloto: tasa de duplicados detectados manualmente tras ingerir 3 cursos solapados.

## D-07 PDF
PyMuPDF da texto por página con coordenadas sin dependencia de Java. **Pendiente:** revisar su licencia (AGPL/comercial) frente al uso previsto; si es incompatible, usar `pypdfium2` (Apache/BSD) o `opendataloader-pdf` (heredado de Lucas, requiere Java). Esta verificación se hace antes de escribir código de extracción.

## D-09 IA
Cada tarea del pipeline tiene un modelo asignado (`ai_task_routing`). Cada llamada registra modelo, versión de prompt, tokens, coste estimado y hash de entrada (`ai_runs`) → trazabilidad y reproducibilidad. Presupuesto mensual con corte duro.

## D-10 MCP
Permite usar la suscripción de Claude (Claude Code, Desktop, claude.ai) para consultas, revisión y análisis sin pagar API por esas operaciones. Herramientas de lectura libres; las de escritura generan `change_proposals`, no cambios directos.

## Estimación de costes operativos (orden de magnitud, a validar en el piloto)
Supuestos: 1 h de clase ≈ 9.000–10.000 palabras ≈ 13.000 tokens; un curso de 40 h ≈ 0,5 M tokens de transcripción; el pipeline lee cada segmento 1–2 veces y genera ~30 % de salida.
- **Extracción + resolución** con modelo económico: ~1–1,5 M tokens por curso de 40 h.
- **Síntesis/comparación** con modelo potente: depende del nº de entidades tocadas; estimado 0,2–0,5 M tokens por curso.
- **Transcripción**: gratis en local (faster-whisper; lento en CPU), o de pago por minuto en cloud.
- **Infraestructura**: el VPS existente; PostgreSQL + 3 servicios caben en 4 GB RAM sin transcripción local (la transcripción local en CPU necesita más).
Los precios por token cambian; se cargarán en `ai_models` y el sistema mostrará el coste real por job. No se dan cifras en euros hasta medir el piloto.
