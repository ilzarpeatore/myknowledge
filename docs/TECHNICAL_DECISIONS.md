# Decisiones técnicas — Knowledge OS

Formato: contexto → decisión → alternativas → reversibilidad. Estado: **P** = propuesta, **A** = aceptada (confirmadas por el usuario el 2026-11-25: alcance ampliable, uso solo personal, IA híbrida + MCP, repo propio con trasplantes). D-07 sigue condicionada a la verificación de licencias.

| ID | Decisión | Estado |
|---|---|---|
| D-01 | Repositorio propio; trasplantar componentes de Lucas (Apache 2.0) y patrones de ddsyasas (MIT) en lugar de forkear | A |
| D-02 | Backend FastAPI (Python) + worker Python; Next.js solo frontend | A |
| D-03 | PostgreSQL 16 + pgvector como única base de datos; sin base de grafos | A |
| D-04 | Cola de trabajos en PostgreSQL (`SKIP LOCKED`); Redis solo si se demuestra necesario | A |
| D-05 | BD como fuente de verdad; Markdown como proyección continua (ver DATA_MODEL §1) | A |
| D-06 | Resolución de entidades: alias exactos → candidatos por embeddings/trigram → decisión IA restringida → revisión humana si confianza baja | A |
| D-07 | Extracción PDF: **pypdfium2** (BSD-3/Apache-2.0) por defecto; **Docling** como extractor opcional en imagen aparte cuando el piloto lo justifique. PyMuPDF descartado (AGPL-3.0) | A (revisada 2026-11-25) |
| D-08 | Transcripción: interfaz `Transcriber`; implementación local con WhisperX (BSD-2, timestamps por palabra; diarización pyannote opcional) y cloud configurable | A |
| D-09 | Proveedores IA vía interfaz propia (`complete_json(schema)`) con adaptadores Anthropic y OpenAI-compatible (cubre OpenRouter y Ollama) en Fase 1 | A |
| D-10 | MCP integrado en la API, con permisos por herramienta | A |
| D-11 | Autenticación desde Fase 1 (usuario único, sesión con cookie segura); detrás de Caddy. Sin multiusuario | A |
| D-13 | Repetición espaciada con py-fsrs (MIT) en Fase 7 | A |
| D-12 | Almacenamiento de ficheros con interfaz `BlobStore`: disco local primero, S3/MinIO después | A |

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
Implementación: trasplantar el esquema exacto → MinHash/LSH de Graphiti (`dedup_helpers.py`, Apache 2.0) para la ingesta incremental, y el clustering KNN + LLM por cluster de kg-gen (MIT) para la consolidación periódica.
Métrica de aceptación en el piloto: tasa de duplicados detectados manualmente tras ingerir 3 cursos solapados.

## D-07 Extracción documental (revisada al implementar la entrega 3)
**Comprobado:** Docling estándar (2.153.0) depende de `torch`, `torchvision`, `transformers` y `docling-ibm-models`, y descarga modelos de Hugging Face al ejecutarse. Eso supone cientos de MB en la imagen del worker, más CPU/RAM en el VPS, y una revisión de licencias de modelos todavía pendiente.

**Decisión:** el extractor por defecto es **pypdfium2** (BSD-3/Apache-2.0, sin modelos): texto por página con offsets exactos, que es lo imprescindible para citar. Medido con un PDF sintético de 1.000 páginas: 1,2 s y 42 MB de memoria (los PDF reales serán más lentos). Limitaciones conocidas: no detecta títulos ni tablas, puede desordenar texto a varias columnas y no hace OCR (las páginas sin texto se marcan como «¿escaneadas?»).

**Docling** queda como extractor opcional, en una imagen de worker aparte, a activar si el piloto con PDFs reales muestra problemas de orden de lectura, tablas o escaneados. Antes de activarlo: revisar las licencias de sus modelos. PyMuPDF sigue descartado (AGPL-3.0, verificado).

## D-14 Segmentos y reprocesado
Hoy reprocesar una versión borra y recrea sus páginas y segmentos (idempotente, sin duplicados). **Restricción para la entrega 5:** cuando existan afirmaciones y evidencias que apunten a segmentos, reprocesar no podrá borrar segmentos citados. Habrá que versionar las extracciones (nueva extracción = nuevos segmentos, los antiguos se conservan mientras estén citados) en lugar de sustituirlas.

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
