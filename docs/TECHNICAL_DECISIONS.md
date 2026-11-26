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
| D-07 | Extracción PDF: **Docling + Tesseract** (tablas, títulos, OCR) en la imagen del worker; **pypdfium2** como respaldo automático y en entornos sin Docling. PyMuPDF descartado (AGPL-3.0) | A (revisada 2026-11-26) |
| D-08 | Transcripción: interfaz `Transcriber`; implementación local con WhisperX (BSD-2, timestamps por palabra; diarización pyannote opcional) y cloud configurable | A |
| D-09 | Proveedores IA vía interfaz propia (`complete_json(schema)`) con adaptadores Anthropic y OpenAI-compatible (cubre OpenRouter y Ollama) en Fase 1 | A |
| D-10 | MCP integrado en la API, con permisos por herramienta | A |
| D-11 | Autenticación desde Fase 1 (usuario único, sesión con cookie segura); detrás de Caddy. Sin multiusuario | A |
| D-13 | Repetición espaciada con py-fsrs (MIT) en Fase 7 | A |
| D-15 | Embeddings locales: `intfloat/multilingual-e5-small` (MIT), ONNX int8 con onnxruntime; búsqueda híbrida (palabras + significado, RRF) | A |
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

## D-07 Extracción documental (revisada el 2026-11-26: la mayoría de PDF tienen tablas)
Medido sobre un PDF sintético realista (encabezados, tabla con bordes, tabla sin bordes, dos columnas, página escaneada), 4 núcleos de CPU:

| | pypdfium2 | pdfplumber | Docling + Tesseract |
|---|---|---|---|
| Tabla con bordes | texto plano | correcta | correcta (Markdown) |
| Tabla sin bordes | texto plano | basura | correcta (≥3 filas; una de 2 filas no se detectó) |
| Títulos / secciones | no | no | sí |
| OCR página escaneada | no | no | sí, con tildes (spa+eng) |
| Tiempo | ~1 ms/pág. | rápido | ~1,1–1,5 s/pág. (+8–20 s al cargar modelos) |
| RAM pico | 42 MB | baja | ~1,9 GB |

**Decisión:** Docling (MIT) con modelos *layout* (Apache-2.0) y *TableFormer* (CDLA-Permissive-2.0 / Apache-2.0), licencias verificadas en Hugging Face, y OCR con Tesseract (Apache-2.0). El OCR por defecto de Docling (RapidOCR) descarga modelos de modelscope.cn: descartado.

**Despliegue:** imagen `worker` separada (PyTorch CPU, sin CUDA: 1,5 GB de dependencias + 0,67 GB de modelos incluidos en la imagen, ejecución sin internet). La imagen `app` (API) se queda en ~85 MB de dependencias. El VPS necesita **≥4 GB de RAM libres** para el worker.

**Respaldo:** si Docling falla con un archivo se usa pypdfium2 (el documento queda buscable y la interfaz lo indica). PDFs cifrados o corruptos siguen fallando como error permanente.

**Limitaciones conocidas:** el orden de lectura de Docling puede colocar un título de tabla después de la tabla en algunas maquetaciones; tablas sin bordes muy pequeñas pueden no detectarse. Se revisará con los PDF reales del piloto.

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

## D-15 Búsqueda por significado con modelo local
**Modelo:** `intfloat/multilingual-e5-small` (licencia MIT, verificada en Hugging Face), 384 dimensiones, multilingüe. Exportación ONNX cuantizada int8 (118 MB) ejecutada con onnxruntime: sin PyTorch, también en la imagen de la API.

**Medido:** frente al modelo completo (fp32, 470 MB), similitud de vectores 0,997 y el doble de rápido (~1 ms por fragmento en 4 núcleos). En 7 consultas de prueba (sinónimos, inglés→español, «VO2max» → «consumo máximo de oxígeno») acierta el primer resultado en todas. La variante int8 se llama «avx512_vnni»; la CPU de pruebas tenía esa instrucción, así que no se ha comprobado en una CPU sin ella. Si diera problemas: `KOS_EMBEDDING_ONNX_FILE=onnx/model.onnx` y `kos download-models` + `kos reembed`.

**Limitación medida:** las puntuaciones de E5 están comprimidas; consultas sin relación (0,79–0,83) se solapan con relevantes (0,81–0,88). El umbral (`KOS_SEMANTIC_MIN_SCORE`, 0,80) no filtra bien lo irrelevante. Mitigación: la búsqueda combinada usa *Reciprocal Rank Fusion*, los resultados solo semánticos quedan detrás de los de palabras y la interfaz los marca «por significado».

**Datos:** tabla `segment_embeddings` (HNSW, coseno) con el modelo que generó cada vector; vectores de otro modelo nunca se comparan. El trabajo `source.embed` se encadena tras la extracción y es idempotente. Sin modelo instalado, la búsqueda por palabras sigue funcionando.
