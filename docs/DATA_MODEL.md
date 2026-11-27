# Modelo de datos — Knowledge OS

PostgreSQL 16 + pgvector. **Uso exclusivamente personal (decisión confirmada 2026-11-25):** no hay `owner_id` ni separación entre usuarios; sí hay autenticación (tabla `users` con un único usuario y `sessions`) porque la app se expone en el VPS. Claves `uuid`, marcas `timestamptz`. Este documento es el diseño; la DDL definitiva vivirá en migraciones versionadas.

## 1. Política Markdown ↔ base de datos

- **La base de datos es la fuente de verdad** de entidades, relaciones, afirmaciones y del cuerpo de las páginas (`page_revisions.content_md`).
- **Markdown es una proyección continua y portable**: cada revisión aplicada se escribe en `storage/wiki/<tipo>/<slug>.md` con frontmatter (id estable, tipo, disciplina, estado, fechas, referencias).
- Ediciones externas del Markdown (p. ej. en Obsidian) se detectan por hash y entran como `change_proposal` de origen `user_file`; nunca se sobrescriben en silencio.
- Así no existen dos versiones independientes y contradictorias.

## 2. Biblioteca y procedencia

```sql
sources          (id, title, kind[document|video|audio|web|note], language,
                  authors_text, url, course_id?, lesson_id?, status, created_at)
source_versions  (id, source_id, sha256, storage_key, mime, size_bytes,
                  origin[upload|youtube|mcp],  -- upload: fuera de las copias diarias
                  page_count, duration_s, extractor, extractor_version, created_at)
source_segments  (id, source_version_id, ordinal, text, token_count,
                  loc_page_start, loc_page_end,        -- PDF / libro / documento
                  loc_slide,                           -- presentación
                  loc_t_start_ms, loc_t_end_ms,        -- audio / vídeo
                  loc_section, loc_char_start, loc_char_end,
                  speaker?, tsv tsvector, embedding vector(N),
                  UNIQUE(source_version_id, ordinal))
source_chapters  (id, source_version_id, parent_id?, position, level[1 capítulo|2 apartado],
                  title, page_start, page_end, is_content,
                  origin[bookmarks|index|headings|manual], edited)
chapter_summaries (id, chapter_id, level[essential|study|detailed], content jsonb
                  -- {overview, sections[{heading, points[{text, citations[{fragment_id,
                  --   page, quote, char_start}]}]}], key_terms[...]}
                  markdown, words, citations, uncited_points, origin[ai|mcp], ai_run_id)
chapter_progress (chapter_id, status[reading|read|studied], updated_at)
collections, collection_items, tags, source_tags
```
Un capítulo es un rango de páginas de una **versión**: sus fragmentos son los que empiezan en esas páginas. Los resúmenes no se sobrescriben: el más reciente de cada nivel es el actual.
Regla: una localización se guarda **solo si el extractor la conoce**; nunca se inventa.

## 3. Conocimiento

```sql
entity_types   (key PK, label, description, is_builtin)
  -- semilla: concept, principle, theory, model, mechanism, methodology, variable,
  --          author, study, institution, case, application, comparison, synthesis, open_question
disciplines    (id, parent_id?, name, slug)          -- árbol: disciplina → área → subárea
entities       (id, type_key → entity_types, canonical_name, slug,
                short_definition, status[proposed|active|merged|archived],
                merged_into_id?, created_by[ai|user], classified_at?,
                parent_id? → entities  -- familia (organización, sin evidencia)
                created_at, updated_at)
entity_disciplines (entity_id, discipline_id, origin[user|ai], PRIMARY KEY both)
entity_aliases (id, entity_id, alias, alias_normalized, language,
                kind[synonym|translation|abbreviation|variant], source_segment_id?,
                UNIQUE(entity_id, alias_normalized))
entity_mentions(id, entity_id?, segment_id, surface_text, context_definition,
                resolution[auto|reviewed|pending], confidence)
relationship_types (key PK, label, inverse_key?, is_symmetric, is_builtin)
  -- semilla: is_a, part_of, causes, contributes_to, influences, requires, contradicts,
  --          supports, explains, associated_with, measured_by, applied_in, derived_from, compared_with
relationships  (id, source_entity_id, target_entity_id, type_key,
                description, conditions, confidence,
                status[proposed|verified|rejected], origin[ai|user],
                ai_run_id?, reviewed_at?, UNIQUE(source,target,type))
relationship_evidence (relationship_id, claim_id? , segment_id?, note)
pages          (id, entity_id UNIQUE?, title, slug, current_revision_id)
page_revisions (id, page_id, revision_no, content_md, change_summary,
                author[ai|user], ai_run_id?, proposal_id?, created_at)
```
Búsquedas jerárquicas (árbol `is_a`/`part_of`) con `WITH RECURSIVE`; no se introduce base de grafos.

## 4. Evidencias

```sql
persons        (id, name, role[teacher|researcher|author], affiliation)
publications   (id, title, year, doi, journal, study_type, design, population,
                sample_size, variables, results, limitations,
                methodological_quality[not_assessed|low|moderate|high], assessed_by_user bool)
claims         (id, text, claim_type[definition|reported_fact|hypothesis|interpretation|
                opinion|recommendation|ai_inference|personal_conclusion],
                attributed_to_person_id?, publication_id?, context, language,
                extraction_confidence, review_status[pending|accepted|rejected|edited],
                ai_run_id?, created_at)
claim_entities (claim_id, entity_id, role[subject|object|mentioned])
evidence       (id, claim_id, segment_id, quote, quote_char_start, quote_char_end)
                -- la cita literal se verifica contra segment.text antes de guardarse
contradictions (id, claim_a_id, claim_b_id, status[candidate|real|context_difference|dismissed],
                analysis_md, reviewed_at)
```
La `methodological_quality` por defecto es `not_assessed`; nunca se infiere del número de citas.

## 5. Aprendizaje

```sql
programs   (id, name, description, provider, institution, discipline_id,
            status[pending|in_progress|paused|completed|archived], start_date, end_date,
            url, image_key, goals, notes)                -- "formación"
courses    (id, program_id?, title, teacher_person_id?, ...)
course_modules (id, course_id, ordinal, title)
lessons    (id, module_id, ordinal, title, duration_s)
lesson_sources (lesson_id, source_id)
learning_progress (lesson_id, status, completed_at, time_spent_s, last_access_at, notes)
flashcards (id, entity_id?, claim_id?, question, answer, difficulty, created_at)
review_log (id, flashcard_id, reviewed_at, grade, response_ms, next_due_at, ease, interval_d)
assessments, assessment_questions, assessment_attempts
lesson_parts (id, lesson_id, position, source_version_id, chapter_id?, kind[slides|document|video],
              page_start?, page_end?, t_start_ms?, t_end_ms?)   -- el tramo de material de la lección
lesson_readings (id, lesson_id, content jsonb {objectives, sections[{heading, material[],
              library[], explanation}], checks[], library_sources}, markdown, words, citations, ai_run_id)
lesson_explanations (id, lesson_id, text, status, feedback jsonb, score, ai_run_id)
lesson_progress.understanding   -- mejor nota de «explícalo con tus palabras» (≥70 = entendida)
```
"¿De qué curso salió este concepto?" = `entity → mentions/claims → segment → source_version → source → lesson → course`.

## 6. Operación y auditoría

```sql
jobs         (id, kind, payload jsonb, status[pending|processing|completed|failed|
              cancelled|awaiting_review], progress, attempts, max_attempts, last_error,
              locked_by, locked_at, run_after, created_at, started_at, finished_at,
              parent_job_id?)
ai_providers (id, kind[anthropic|openai|gemini|openrouter|ollama], base_url, enabled)
ai_models    (id, provider_id, model_name, input_cost_per_mtok, output_cost_per_mtok, context_tokens)
ai_task_routing (task[extract|classify|resolve|synthesize|compare|query|review|vision|transcribe], model_id)
prompts      (id, key, version, template, output_schema jsonb, created_at, UNIQUE(key,version))
ai_runs      (id, job_id?, model_id, prompt_id, input_tokens, output_tokens, cost_estimate,
              status, error, request_hash, created_at)
change_proposals (id, origin[ai|user_file|mcp], ai_run_id?, payload jsonb,
              status[pending|applied|rejected|reverted], applied_at, reverted_at)
review_tasks (id, kind[entity_merge|relationship|contradiction|claim|page], subject_id,
              proposal_id?, status, created_at, resolved_at)
budgets      (period, limit_amount, spent_amount)
audit_logs   (id, actor[user|ai|mcp:<client>], action, subject_table, subject_id,
              before jsonb, after jsonb, created_at)
users, sessions
```

## 7. Índices y restricciones clave
- `source_versions(sha256)` único → deduplicación.
- `source_segments`: GIN sobre `tsv`, HNSW sobre `embedding`.
- `entity_aliases(alias_normalized)` B-tree + trigram (`pg_trgm`) para candidatos léxicos.
- FK con `ON DELETE RESTRICT` en todo lo que respalda evidencia: no se puede borrar un segmento citado.
- Borrado de fuentes = borrado lógico (`status = archived`) salvo petición explícita.

## 8. Estado de implementación (2026-11-26)
Implementado (migraciones 0001–0006): usuarios y sesiones, biblioteca (`sources`, `source_versions`, `source_pages`, `source_segments`, `segment_embeddings`), cola (`jobs`), auditoría, capa de IA (`ai_models`, `ai_task_routes`, `prompts`, `ai_runs`, `ai_budgets`) y conocimiento (`entity_types`, `relationship_types`, `disciplines`, `entities`, `entity_disciplines`, `entity_aliases`, `persons`, `publications`, `claims`, `claim_entities`, `evidence`, `relationships`, `relationship_evidence`, `contradictions`, `entity_merges`, `review_tasks`).

Diferencias con el diseño inicial:
- **`review_tasks` sustituye a `change_proposals`** de momento: cada decisión humana pendiente (duplicado posible, relación propuesta…) es una tarea con su `payload`. Se añadirán propuestas de cambio cuando exista la redacción de páginas por IA.
- **`entity_merges`** guarda todo lo que movió o descartó cada fusión, para poder deshacerla exactamente.
- **Páginas (`pages`, `page_revisions`)** pendientes: llegarán con la síntesis por IA.
- **Segmentos citados (D-14, provisional):** una fuente con fragmentos citados como evidencia no se puede reprocesar (la API devuelve 409). El versionado de extracciones queda para cuando haga falta.
- La unicidad de alias entre conceptos vivos se aplica en el servicio (no hay restricción en la base de datos porque un alias puede repetirse en conceptos fusionados o archivados).
