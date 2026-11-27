-- Synthetic large library (PRUEBA) for performance measurement only.
\set ON_ERROR_STOP on
CREATE TEMP TABLE words(w text[]);
INSERT INTO words VALUES (ARRAY['fuerza','hipertrofia','fatiga','volumen','intensidad','series','repeticiones','reserva','fallo','descanso','frecuencia','carga','progresión','técnica','músculo','tendón','recuperación','sueño','proteína','glucógeno','lactato','potencia','velocidad','resistencia','umbral','unidades','motoras','reclutamiento','periodización','bloque','microciclo','mesociclo','tensión','mecánica','estrés','metabólico','daño','muscular','adaptación','neural','coordinación','movilidad','estabilidad','sentadilla','press','banca','peso','muerto','dominadas','cardio','aeróbico','anaeróbico','VO2max','frecuencia','cardíaca','percepción','esfuerzo','RIR','RPE','principiantes','avanzados','mujeres','mayores','deportistas','entrenamiento','semanal','estudio','profesor','curso','evidencia','metaanálisis','ensayo','controlado','muestra','grupo','control','resultado','efecto','diferencia','significativa','similar','mayor','menor','cuando','porque','aunque','además','sin','con','para','entre','sobre','durante','después','antes']);

INSERT INTO sources (id, title, kind, status, created_at)
SELECT gen_random_uuid(), 'PRUEBA fuente ' || g, 'document', 'ready', now() - (g || ' hours')::interval
FROM generate_series(1, 500) g;
INSERT INTO source_versions (id, source_id, sha256, storage_key, mime, size_bytes, page_count, extractor, extracted_at, created_at)
SELECT gen_random_uuid(), s.id, md5(s.id::text) || md5(s.title), 'raw/00/x', 'application/pdf', 1000000, 200, 'pdfium', now(), now()
FROM sources s;
INSERT INTO source_segments (id, source_version_id, ordinal, text, token_count, loc_page_start, loc_page_end, loc_char_start, loc_char_end)
SELECT gen_random_uuid(), v.id, g,
       (SELECT string_agg(w[1 + floor(random() * array_length(w, 1))::int], ' ')
          FROM words, generate_series(1, 150) k WHERE k > g * 0),
       200, g / 2 + 1, g / 2 + 1, 0, 1000
FROM source_versions v, generate_series(0, 199) g;

INSERT INTO segment_embeddings (segment_id, model, embedding)
SELECT s.id, 'intfloat/multilingual-e5-small:model_qint8_avx512_vnni',
       (SELECT ('[' || string_agg((random() - 0.5)::text, ',') || ']')::vector
          FROM generate_series(1, 384) k WHERE k > s.ordinal * 0 AND s.id IS NOT NULL)
FROM source_segments s;

INSERT INTO entities (id, type_key, canonical_name, slug, status, origin, created_at)
SELECT gen_random_uuid(), 'concept', 'PRUEBA concepto ' || g, 'prueba-concepto-' || g, 'active', 'user', now()
FROM generate_series(1, 2000) g;
INSERT INTO entity_aliases (id, entity_id, alias, alias_normalized, kind)
SELECT gen_random_uuid(), id, canonical_name, lower(replace(canonical_name, 'PRUEBA ', 'prueba ')), 'canonical' FROM entities;

CREATE TEMP TABLE segs AS SELECT id, text, row_number() OVER () AS n FROM source_segments;
CREATE TEMP TABLE ents AS SELECT id, row_number() OVER () AS n FROM entities;
CREATE TEMP TABLE cl AS SELECT gen_random_uuid() AS id, g FROM generate_series(1, 20000) g;
INSERT INTO claims (id, text, claim_type, review_status, origin, created_at)
SELECT id, 'PRUEBA afirmación ' || g, (ARRAY['definition','reported_fact','recommendation','interpretation'])[1 + g % 4],
       CASE WHEN g % 3 = 0 THEN 'pending' ELSE 'accepted' END, 'user', now() FROM cl;
INSERT INTO claim_entities (claim_id, entity_id, role)
SELECT cl.id, e.id, 'subject' FROM cl JOIN ents e ON e.n = 1 + (cl.g % 2000);
INSERT INTO evidence (id, claim_id, segment_id, quote, quote_char_start, quote_char_end, created_at)
SELECT gen_random_uuid(), cl.id, s.id, left(s.text, 40), 0, length(left(s.text, 40)), now()
FROM cl JOIN segs s ON s.n = 1 + ((cl.g * 37) % 100000);

INSERT INTO relationships (id, source_entity_id, target_entity_id, type_key, status, origin, created_at)
SELECT gen_random_uuid(), a.id, b.id, (ARRAY['influences','is_a','part_of','requires','associated_with'])[1 + g % 5], 'verified', 'user', now()
FROM generate_series(1, 6000) g JOIN ents a ON a.n = 1 + (g % 2000) JOIN ents b ON b.n = 1 + ((g * 7 + 3) % 2000)
WHERE a.id <> b.id
ON CONFLICT DO NOTHING;
INSERT INTO relationship_evidence (id, relationship_id, segment_id)
SELECT gen_random_uuid(), r.id, (SELECT id FROM segs WHERE n = 1 + (abs(hashtext(r.id::text)) % 100000)) FROM relationships r;

INSERT INTO flashcards (id, entity_id, front, back, kind, origin, status, fsrs, due_at, reps, lapses, created_at)
SELECT gen_random_uuid(), e.id, '¿Pregunta ' || g || '?', 'Respuesta', 'qa', 'user', 'active',
       jsonb_build_object('card_id', g, 'state', 2, 'step', null, 'stability', 5 + g % 30, 'difficulty', 5,
                          'due', (now() + ((g % 40) - 10 || ' days')::interval), 'last_review', now() - ((g % 20) || ' days')::interval),
       now() + ((g % 40) - 10 || ' days')::interval, 3, g % 4, now()
FROM generate_series(1, 5000) g JOIN ents e ON e.n = 1 + (g % 2000);
INSERT INTO flashcard_reviews (id, card_id, rating, reviewed_at, state_before, state_after)
SELECT gen_random_uuid(), f.id, 1 + (k % 4), now() - (k || ' days')::interval, '{}', '{}'
FROM flashcards f, generate_series(1, 4) k;
ANALYZE;
SELECT (SELECT count(*) FROM source_segments) segs, (SELECT count(*) FROM segment_embeddings) embs,
       (SELECT count(*) FROM claims) claims, (SELECT count(*) FROM relationships) rels,
       (SELECT count(*) FROM flashcard_reviews) reviews;
