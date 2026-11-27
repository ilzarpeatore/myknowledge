# Investigación de otros repositorios útiles

**Fecha:** 2026-11-25. **Método:** búsqueda web para encontrar candidatos y después clonado de cada uno para comprobar la licencia (fichero `LICENSE`) y el código relevante. Lo que no se ha comprobado en código se marca como **no verificado**.

## 1. Resumen

| Repositorio | Licencia (verificada) | Último commit | Para qué nos sirve | Decisión |
|---|---|---|---|---|
| `docling-project/docling` | MIT | 2026-11-25 | Extracción PDF/DOCX/PPTX/HTML/imagen con **página + bbox + rango de caracteres** por elemento; audio/vídeo con ASR | **Adoptar como dependencia** del worker (sustituye a PyMuPDF) |
| `pymupdf/PyMuPDF` | **AGPL-3.0** | 2026-11-24 | Extracción PDF rápida | **Descartar** como motor por defecto (licencia copyleft de red) |
| `datalab-to/marker` | Código Apache 2.0; **pesos OpenRAIL-M modificados** | 2026-11-20 | PDF→Markdown de alta calidad | Opcional; uso personal permitido por su licencia de pesos, pero no lo necesitamos si Docling basta |
| `getzep/graphiti` | Apache 2.0 | 2026-11-25 | Resolución de entidades (normalización exacta → MinHash/LSH → LLM), aristas temporales | **Referencia/trasplante** del algoritmo de deduplicación; no como motor (exige Neo4j/FalkorDB/Neptune) |
| `stair-lab/kg-gen` | MIT (pyproject y README) | 2026-03-24 | Extracción de entidades/relaciones y deduplicación por clustering KNN + LLM dentro de cada cluster | **Referencia** para D-06 |
| `HKUDS/LightRAG` | MIT | 2026-11-25 | GraphRAG con backend PostgreSQL (Apache AGE + pgvector) | **No adoptar**: relaciones no tipadas, sin revisión humana, orientado a recuperación. Útil como referencia de prompts de extracción |
| `topoteretes/cognee` | Apache 2.0 | 2026-11-20 | Memoria/grafo para agentes | No adoptar (mismo motivo que Graphiti; no revisado en profundidad) |
| `m-bain/whisperX` | BSD-2-Clause | 2026-11-21 | Transcripción con faster-whisper + **timestamps por palabra** + diarización (pyannote) | **Adoptar** en Fase 5 (diarización opcional: los modelos pyannote tienen licencia propia y requieren token de Hugging Face) |
| `open-spaced-repetition/py-fsrs` | MIT | 2026-11-13 | Algoritmo FSRS de repetición espaciada | **Adoptar** en Fase 7 |
| `lfnovo/open-notebook` | MIT | 2026-11-18 | Alternativa a NotebookLM (FastAPI + Next.js + SurrealDB, podcasts) | No adoptar: modelo de cuadernos y RAG, sin grafo tipado; SurrealDB choca con D-03 |
| `MODSetter/SurfSense` | Apache 2.0 | 2026-11-23 | NotebookLM/Glean para equipos, conectores externos | No adoptar: orientado a equipos y conectores |
| `gavischneider/awesome-llm-wiki` | CC0 | 2026-11-25 | Lista curada (~1.600 líneas) | Referencia. Destaca el gist **"LLM Wiki v3"** (observaciones estructuradas + motor de creencias + páginas como vistas deterministas), que coincide con nuestro diseño de afirmaciones → evidencias → páginas |

## 2. Hallazgos que cambian decisiones

### 2.1 Docling sustituye a PyMuPDF (D-07)
- Comprobado: PyMuPDF se distribuye bajo **AGPL-3.0**. Para un uso solo personal no hay incumplimiento práctico, pero ata el proyecto a copyleft de red si algún día se abre a terceros.
- Comprobado en `docling-core` (2.108.0): `ProvenanceItem` tiene `page_no`, `bbox` y `charspan` para cada elemento extraído. Es exactamente la localización que necesita `source_segments`.
- Docling cubre además DOCX, PPTX, HTML e imágenes con un único modelo de documento, y declara soporte de audio/vídeo con ASR (**no verificado en código**).
- **Pendiente:** revisar las licencias de los modelos que descarga (layout, OCR, tablas). El código es MIT, los modelos tienen licencias propias.
- Coste: necesita más CPU/RAM que PyMuPDF. Se medirá en el piloto.

### 2.2 Resolución de entidades (D-06): no partir de cero
- **Graphiti** (`graphiti_core/utils/maintenance/dedup_helpers.py`): normalización exacta, luego similitud aproximada (shingles + MinHash/LSH, umbral Jaccard 0,9, con filtros de entropía para nombres cortos) y solo después el LLM. Es un patrón casi idéntico al de D-06, ya probado. Se puede trasplantar (Apache 2.0, conservando la cabecera).
- **kg-gen** (`steps/_3_deduplicate.py`): "semantic hashing" y después clusters KNN de embeddings, con el LLM decidiendo **dentro de cada cluster**. Encaja con la consolidación periódica (unificar toda la biblioteca), complementaria a la resolución incremental de cada ingesta.
- Ninguno distingue sinónimos multilingües ni pide revisión humana: eso sigue siendo desarrollo propio.

### 2.3 Por qué no adoptar un motor GraphRAG completo (LightRAG, Graphiti, cognee)
Producen grafos para **recuperar contexto para un LLM**, no un modelo de conocimiento revisable. Las relaciones son descripciones libres sin catálogo tipado, no hay estados propuesto/verificado y no hay vínculo afirmación → evidencia → segmento. Además Graphiti y cognee añaden una base de grafos (contra D-03). LightRAG sí puede funcionar sobre PostgreSQL, pero mediante Apache AGE (su código advierte de versiones de AGE que tumban el backend), lo que añade una extensión frágil.

## 3. Qué no se ha encontrado
- Ningún proyecto abierto que combine LMS de cursos realizados + grafo tipado + afirmaciones con evidencia. Ese sigue siendo el núcleo a desarrollar.
- Ningún proyecto abierto maduro con una ontología de ciencias del ejercicio reutilizable (no se buscó en profundidad en ontologías biomédicas; posible tarea futura).
