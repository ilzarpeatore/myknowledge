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
| D-16 | Capa de IA: proveedor Anthropic (SDK oficial), salidas estructuradas validadas, respaldo de servidor ante negativas, registro por llamada, presupuesto con corte duro, prompts versionados | A |
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

**Medido:** frente al modelo completo (fp32, 470 MB), similitud de vectores 0,997 y el doble de rápido (~1 ms por fragmento en 4 núcleos). En 7 consultas de prueba (sinónimos, inglés→español, «VO2max» → «consumo máximo de oxígeno») acierta el primer resultado en todas. La variante int8 se llama «avx512_vnni», pero funciona también sin esa instrucción: en el VPS (AMD EPYC-Milan, solo AVX2) da similitud 0,996 con fp32, el mismo primer resultado en 7/7 consultas y es 1,6× más rápida que fp32 (2026-11-26). Si diera problemas: `KOS_EMBEDDING_ONNX_FILE=onnx/model.onnx` y `kos download-models` + `kos reembed`.

**Limitación medida:** las puntuaciones de E5 están comprimidas; consultas sin relación (0,79–0,83) se solapan con relevantes (0,81–0,88). El umbral (`KOS_SEMANTIC_MIN_SCORE`, 0,80) no filtra bien lo irrelevante. Mitigación: la búsqueda combinada usa *Reciprocal Rank Fusion*, los resultados solo semánticos quedan detrás de los de palabras y la interfaz los marca «por significado».

**Datos:** tabla `segment_embeddings` (HNSW, coseno) con el modelo que generó cada vector; vectores de otro modelo nunca se comparan. El trabajo `source.embed` se encadena tras la extracción y es idempotente. Sin modelo instalado, la búsqueda por palabras sigue funcionando.

## D-16 Capa de IA
- **Un único punto de entrada** (`kos.ai.service.run_structured`): modelo según la tarea (`ai_task_routes`), comprobación de presupuesto, prompt versionado, llamada, validación y registro en `ai_runs` (tokens, coste, modelo servido, versión del prompt, hash de la entrada, id de petición), pase lo que pase.
- **Modelos por defecto:** todas las tareas en `claude-opus-5-5` (esfuerzo `low` para extraer/clasificar/resolver, `medium` para consultas y revisión, `high` para redactar y comparar). Cambiable por tarea en la página «IA» sin tocar código. `claude-haiku-5-5` cuesta 40 veces menos por token y es candidato para la extracción masiva, a decidir midiendo calidad con material real.
- **Salidas estructuradas** con `client.beta.messages.parse(output_format=Modelo)` (SDK `anthropic` 2.14, parámetros verificados en el paquete instalado). Sin `temperature` (rechazado por la API actual) y **sin herramientas**: el modelo solo puede devolver datos con el esquema pedido.
- **Negativas del modelo:** se pide el respaldo del servidor (`fallbacks: "default"`, beta `server-side-fallback-2026-07-01`) en los modelos que lo admiten (Opus/Sonnet 5.5; Haiku 5.5 no). Si responde otro modelo se registra y se cobra a su precio. Una negativa final queda registrada como `refused`.
- **Presupuesto:** antes de cada llamada, gasto del mes + coste en el peor caso (entrada estimada a 3 caracteres/token + `max_tokens` completos de salida) ≤ límite; si no, la llamada no se hace (`budget_exceeded`). Con varios procesos en paralelo el corte es aproximado (pueden pasar llamadas simultáneas). Precios en la tabla `ai_models` (lista del 2026-11-03; los de caché de Haiku son estimados).
- **Prompt injection:** el texto de las fuentes va en el mensaje de usuario dentro de `<source_document>` (con el cierre escapado y una nota de que es contenido no confiable), nunca en el prompt de sistema.
- **Prompts** en `kos/ai/prompts/*.md` con `version: N`; editar uno sin subir la versión da error.
- **Sin clave:** error claro y registrado (`no Anthropic credentials configured`); `kos ai-check` hace una llamada mínima real para comprobar clave, modelo y presupuesto.

## D-17 Evidencia científica
- **Estudios por DOI con Crossref** (`api.crossref.org/works/{doi}`): gratis, sin clave, cubre la gran mayoría de revistas. Solo se envía el DOI, con un User-Agent genérico; el correo de contacto (`KOS_CROSSREF_MAILTO`) es opcional. La respuesta se trata como dato externo: se convierte a campos de texto (sin etiquetas, con longitud máxima) y nunca llega a un prompt como instrucción. Probado con una respuesta real guardada en `tests/fixtures/crossref_work.json`.
- **Calidad metodológica**: solo la cambia el usuario (`not_assessed` → baja/moderada/alta), con fecha y auditoría. Nada la deduce, ni la IA.
- **Fuente ↔ estudio**: una fuente puede ser el texto de un estudio (`sources.publication_id`). Al vincularla, las afirmaciones citadas *solo* desde esa fuente pasan a ser del estudio; al desvincularla se deshace exactamente eso. La extracción automática de una fuente-estudio atribuye las afirmaciones al estudio (no al profesor del curso) y avisa al modelo de que el texto es un estudio.
- **Origen de una afirmación** (calculado, no guardado): estudio > persona (profesor/autor) > inferencia de IA > conclusión del usuario > fuente sin atribuir.
- **Auditoría de citas**: `kos verify-quotes` comprueba que cada cita guardada sigue apareciendo literalmente en su fragmento y en su posición.

## D-18 Formatos y multimedia
- **DOCX, PPTX y EPUB con Docling** (ya instalado en el worker; no usa modelos de maquetación ni OCR para estos formatos). PPTX conserva el número de diapositiva (`loc_slide`); DOCX y EPUB, sus títulos/capítulos como sección. Las notas del ponente de PPTX no se extraen (Docling no las lee). Detección por contenido (paquete ZIP inspeccionado; se rechaza si descomprimido supera 2 GB o 20 000 entradas).
- **Subtítulos SRT/VTT con un lector propio** (los dos formatos dan exactamente las mismas líneas; se quitan etiquetas y repeticiones de los subtítulos automáticos). No se vuelve a transcribir lo que ya tiene subtítulos.
- **Transcripción local con faster-whisper** (`large-v3-turbo`, int8, CPU), audio decodificado con ffmpeg. Medido con una clase sintética de 2,5 min (voz Piper, tiempos conocidos) en 4 núcleos: turbo 2,3× tiempo real (1 h ≈ 26 min) y texto exacto; `small` 3,4× pero con errores («proximidad *del* fallo»).
- **Precisión de tiempos:** una línea por frase a partir de las marcas por palabra. El filtro de voz (VAD) desplazaba frases enteras hasta 4 s al quitar pausas; sin él, el peor error fue 0,3 s (0,5 s en la prueba completa por la aplicación; el criterio es ±5 s). Las alucinaciones en silencios largos («Gracias por ver el video.») se eliminan con `hallucination_silence_threshold=2`; probado con 30 s de silencio y ruido.
- **Reanudable:** tramos de ~10 min cortados en el medio segundo más silencioso de sus últimos 30 s (no parte palabras); cada tramo se guarda (`transcript_chunks`) antes de seguir. Un reintento o un reprocesado reutiliza los tramos ya hechos con el mismo modelo.
- **Cita al segundo:** cada fragmento guarda su línea de tiempo (desplazamiento → ms); la cita apunta al inicio de la frase donde empieza. El reproductor (`/play/…`) abre el audio/vídeo en ese segundo; el archivo se sirve con rangos de bytes para poder saltar.
- **El modelo de voz no va en la imagen** (~1,6 GB): se descarga una vez a un volumen (`kos download-whisper`).
- **YouTube (yt-dlp, en el worker):** se guarda como original, por orden: subtítulos del autor (idioma del vídeo, luego español/inglés); subtítulos automáticos solo en el idioma original (nunca las traducciones automáticas a otros idiomas, que no son lo que se dijo); si no hay, solo el audio, que se transcribe. El archivo se guarda inmutable como cualquier subida. La cita abre el vídeo incrustado (`youtube-nocookie.com`) en su segundo. Probado con un vídeo real en desarrollo (subtítulos del autor, título y canal); si YouTube bloquea la IP del servidor, el error explica cómo subir los subtítulos o el audio a mano.

## D-19 Servidor MCP
- **SDK oficial `mcp` 2.x** (`MCPServer`), transporte *streamable HTTP* sin estado y con respuestas JSON, montado en la propia API en `/api/mcp` (pasa por el gateway como el resto de `/api/*`). Probado con el cliente oficial en las dos generaciones del protocolo (2026-07-28 y 2025-11-25).
- **Autenticación con tokens personales** (`kos_…`, solo se guarda su SHA-256, se muestra una vez) comprobados por un middleware propio antes de la app MCP; sin flujo OAuth (usuario único: publicar metadatos OAuth de un servidor de autorización inexistente confundiría a los clientes). Permisos: `read` y `propose`. Revocables desde la página IA.
- **Nada se escribe como aceptado:** las herramientas `propose_*` crean conceptos `proposed`, afirmaciones `pending` (con la cita verificada literalmente) y relaciones `proposed` con tarea de revisión, todo con `origin="mcp"` y registro de auditoría con el nombre del token.
- **Protección DNS rebinding** del SDK con los hosts permitidos derivados de `KOS_APP_ORIGIN`.
- **Inyección de instrucciones:** las instrucciones del servidor avisan de que el texto de los fragmentos es material de las fuentes y no órdenes.

## D-20 Espacio de consultas (preguntar a la biblioteca)
- **Contexto = fragmentos + ficha de concepto.** Recuperación híbrida (palabras en modo «cualquier palabra», ordenada por cuántas coinciden, más significado) limitada al ámbito elegido (toda la biblioteca, una fuente, un curso, una disciplina con sus subáreas, o un concepto). Para los conceptos que aparecen en la pregunta (por nombre o alias) se añade una ficha de la base de datos: afirmaciones con su origen (estudio con su calidad, profesor, IA…), relaciones, cursos y contradicciones; cada afirmación trae su fragmento. Así se responden también las preguntas estructurales del piloto (§19.3): qué profesores, qué cursos, qué conceptos previos, qué está respaldado por estudios.
- **Sin material relacionado no se llama al modelo**: la respuesta dice que no hay información suficiente.
- **Respuesta en partes**: de las fuentes / síntesis / inferencia de la IA / conocimiento externo, con cobertura (suficiente, parcial, insuficiente) y lagunas. Modo investigación (solo la biblioteca) o aplicación (permite inferencias y conocimiento general, marcados).
- **Validación de citas en el servidor**: cada cita debe nombrar un fragmento enviado y su texto aparecer en él (se guarda el texto original); si no, se descarta y se cuenta. Una parte «de las fuentes» sin ninguna cita válida pasa a «inferencia» con el aviso «sin cita verificable». Conocimiento externo en modo investigación se marca.
- **Preguntas de seguimiento** («¿En qué coinciden?») heredan los conceptos de la anterior y reciben la pregunta y respuesta previas como contexto (marcado como tal).
- **Trazabilidad:** cada consulta guarda el ámbito, los fragmentos y conceptos enviados, la respuesta validada y su `ai_run` (modelo, tokens, coste). Tarea `query` en la configuración de modelos (criterio 5: cambiable sin tocar código).

## D-21 Estudio: tarjetas, FSRS, cuestionarios y lagunas
- **FSRS con py-fsrs 6** (MIT): parámetros por defecto, retención deseada 0,9 (`KOS_STUDY_DESIRED_RETENTION`). El estado completo de la tarjeta se guarda en JSON; cada repaso se añade a `flashcard_reviews` (nunca se borra) con el recuerdo estimado justo antes. Cuando haya historial suficiente se podrán optimizar los parámetros con tus propios repasos.
- **Tarjetas automáticas sin IA:** una por afirmación **aceptada** del concepto (las propuestas de la IA sin revisar no son material de estudio), con la cita y el fragmento. **Tarjetas con IA:** propuestas a partir de la ficha del concepto, con cita literal comprobada; entran en el repaso solo si las aceptas.
- **Cuestionarios por módulo:** la IA ve fragmentos repartidos por los materiales de las lecciones del módulo; cada pregunta guarda el fragmento y la cita que la resuelven (se descartan las que no se verifican o están mal formadas). Las respuestas abiertas no las corrige la IA: las comparas tú con la respuesta modelo y la fuente.
- **Lagunas:** biblioteca (afirmaciones con fuente, fuentes distintas, revisadas, con estudios) y usuario (aciertos en cuestionarios, «otra vez» en los repasos de los últimos 60 días, recuerdo estimado, olvidos), en listas separadas; un concepto puede estar en las dos. Umbrales en `kos/study/gaps.py` y visibles en el informe.

## D-22 Secciones de la biblioteca
- Tablas `library_sections` (8 predefinidas, renombrables; se pueden crear más), `source_sections` (muchos a muchos: una fuente puede estar en varias) y `source_links` (enlaces manuales entre fuentes, guardados una vez por par).
- Colocación automática solo **añade**, nunca quita lo que eligió el usuario: por formato al subir (vídeo/YouTube → Vídeos, audio → Audios, EPUB → Libros, resto → Recursos), al vincular un estudio (→ Estudios) y al usarla en un curso (→ Cursos). La migración clasificó con las mismas reglas lo ya existente.
- Cruce entre secciones calculado, no copiado: dos fuentes se relacionan si hay afirmaciones (no rechazadas) que citan a ambas sobre el mismo concepto; más los enlaces manuales. Mismo dato en la ficha del concepto («Dónde aparece»), en Buscar/Preguntar (filtro por sección) y en el servidor MCP (`list_sections`, `source_connections`, `section` en `search`/`list_sources`).
