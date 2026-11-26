"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { Comparison, QUALITY } from "@/components/Comparison";
import { Header } from "@/components/Header";
import { api, ApiError } from "@/lib/api";
import {
  CLAIM_TYPES, clearPicked, pickedEvidence, sourceHref,
  type Candidate, type PickedEvidence, type Types,
} from "@/lib/knowledge";
import { useMe } from "@/lib/useMe";

type Evidence = {
  id: string; quote: string; segment_id: string; page: number | null; section: string | null;
  source_id: string; source_title: string; version_id: string; mime: string;
};
type Claim = {
  id: string; text: string; claim_type: string; role: string; review_status: string; origin: string;
  person: string | null; evidence: Evidence[]; context: string | null;
  provenance: {
    kind: "study" | "teacher" | "author" | "ai" | "user" | "source"; label: string; person?: string;
    publication?: { id: string; title: string; year: number | null; doi: string | null; methodological_quality: string };
  };
};
const EV_FILTERS = [
  ["all", "Todas"], ["study", "Respaldado por estudios"], ["teacher", "Explicaciones de profesores"], ["other", "Otras"],
] as const;
const matches = (f: string, k: string) =>
  f === "all" || (f === "study" ? k === "study" : f === "teacher" ? k === "teacher" || k === "author" : !["study", "teacher", "author"].includes(k));
type Rel = {
  id: string; type_key: string; label: string; direction: "in" | "out"; status: string; origin: string;
  description: string | null; conditions: string | null; other: { id: string; slug: string; name: string };
};
type Detail = {
  id: string; slug: string; type_key: string; canonical_name: string; short_definition: string | null;
  status: string; origin: string; merged_into: { id: string; slug: string; name: string } | null;
  aliases: { id: string; alias: string; kind: string; language: string | null }[];
  claims: Claim[]; relationships: Rel[];
  merged_from: { merge_id: string; name: string; at: string }[];
};
type Segment = { id: string; text: string; page: number | null; source_title: string };
type CourseLink = { course_id: string; title: string; claims: number; lessons: { id: string; title: string }[] };

const STATUS: Record<string, string> = { proposed: "propuesta", verified: "verificada", rejected: "rechazada" };

function EntityPicker({ onPick, exclude }: { onPick: (c: Candidate) => void; exclude: string }) {
  const [q, setQ] = useState("");
  const [res, setRes] = useState<Candidate[]>([]);
  useEffect(() => {
    if (q.trim().length < 2) { setRes([]); return; }
    const t = setTimeout(() => api<Candidate[]>(`/api/entities/candidates?${new URLSearchParams({ name: q })}`)
      .then((r) => setRes(r.filter((c) => c.id !== exclude))).catch(() => {}), 250);
    return () => clearTimeout(t);
  }, [q, exclude]);
  return (
    <div>
      <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Busca el otro concepto…" />
      {res.map((c) => (
        <div key={c.id}><button type="button" onClick={() => { onPick(c); setQ(c.canonical_name); setRes([]); }}>{c.canonical_name}</button></div>
      ))}
    </div>
  );
}

export default function ConceptPage() {
  const user = useMe();
  const router = useRouter();
  const { slug } = useParams<{ slug: string }>();
  const [d, setD] = useState<Detail | null>(null);
  const [types, setTypes] = useState<Types | null>(null);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [alias, setAlias] = useState("");
  const [evFilter, setEvFilter] = useState<string>("all");
  const [picked, setPicked] = useState<PickedEvidence | null>(null);
  const [segment, setSegment] = useState<Segment | null>(null);
  const [claim, setClaim] = useState({ text: "", claim_type: "definition", quote: "" });
  const [rel, setRel] = useState<{ type_key: string; other: Candidate | null; reverse: boolean; description: string }>(
    { type_key: "influences", other: null, reverse: false, description: "" });
  const [mergeTarget, setMergeTarget] = useState<Candidate | null>(null);
  const [courses, setCourses] = useState<CourseLink[]>([]);
  const [allDisc, setAllDisc] = useState<{ id: string; name: string; parent_id: string | null }[]>([]);
  const [myDisc, setMyDisc] = useState<string[]>([]);

  const load = useCallback(async () => {
    try {
      const detail = await api<Detail>(`/api/entities/${encodeURIComponent(slug)}`);
      setD(detail);
      setCourses(await api<CourseLink[]>(`/api/entities/${detail.id}/courses`));
      setMyDisc((await api<{ id: string }[]>(`/api/entities/${detail.id}/disciplines`)).map((x) => x.id));
    } catch (err) {
      setMsg({ ok: false, text: err instanceof ApiError && err.status === 404 ? "Concepto no encontrado." : "Error al cargar." });
    }
  }, [slug]);
  useEffect(() => {
    if (user) {
      load(); api<Types>("/api/knowledge/types").then(setTypes); setPicked(pickedEvidence());
      api<{ id: string; name: string; parent_id: string | null }[]>("/api/disciplines").then(setAllDisc);
    }
  }, [user, load]);
  useEffect(() => {
    if (picked) api<Segment>(`/api/segments/${picked.segment_id}`).then(setSegment).catch(() => setSegment(null));
  }, [picked]);

  async function act(fn: () => Promise<unknown>, ok: string) {
    try { await fn(); setMsg({ ok: true, text: ok }); await load(); }
    catch (err) { setMsg({ ok: false, text: err instanceof ApiError ? err.message : "Error." }); }
  }

  if (!d) return <main><Header user={user} /><p className={msg?.ok === false ? "error" : "muted"}>{msg?.text ?? "Cargando…"}</p></main>;
  const typeLabel = types?.entity_types.find((t) => t.key === d.type_key)?.label ?? d.type_key;
  const evidenceSegments = Array.from(new Map(d.claims.flatMap((c) => c.evidence).map((e) => [e.segment_id, e])).values());

  return (
    <main>
      <Header user={user} />
      <p className="muted"><Link href="/concepts">← Conceptos</Link></p>
      <h1 style={{ marginBottom: 4 }}>{d.canonical_name}</h1>
      <p className="muted" style={{ marginTop: 0 }}>
        {typeLabel}{d.origin === "ai" ? " · propuesto por IA" : ""}{d.status !== "active" ? ` · ${d.status}` : ""}
      </p>
      {d.merged_into && (
        <p className="card">Este concepto se fusionó en <Link href={`/concepts/${d.merged_into.slug}`}>{d.merged_into.name}</Link>.</p>
      )}
      {d.short_definition && <p>{d.short_definition}</p>}
      {d.status === "proposed" && (
        <p className="card row" style={{ justifyContent: "flex-start", gap: 8 }}>
          <span>Concepto propuesto: revísalo antes de darlo por bueno.</span>
          <button onClick={() => act(() => api(`/api/entities/${d.id}`, { method: "PATCH", body: JSON.stringify({ status: "active" }) }), "Concepto aceptado.")}>Aceptar</button>
          <button onClick={() => act(() => api(`/api/entities/${d.id}`, { method: "PATCH", body: JSON.stringify({ status: "archived" }) }), "Concepto descartado (archivado).")}>Descartar</button>
        </p>
      )}
      <p className="row" style={{ justifyContent: "flex-start", gap: 8 }}>
        <Link className="badge" href={`/graph?entity=${d.id}`}>Ver en el grafo</Link>
        <Link className="badge" href={`/concepts/${d.slug}/map`}>Mapa mental</Link>
      </p>
      {allDisc.length > 0 && d.status !== "merged" && (
        <details style={{ marginBottom: 8 }}>
          <summary className="muted">Disciplinas: {allDisc.filter((x) => myDisc.includes(x.id)).map((x) => x.name).join(", ") || "ninguna"}</summary>
          <div className="card row" style={{ justifyContent: "flex-start", gap: 4 }}>
            {allDisc.map((x) => (
              <label key={x.id} className="badge" style={{ margin: 0 }}>
                <input type="checkbox" style={{ width: "auto" }} checked={myDisc.includes(x.id)} onChange={(e) => {
                  const next = e.target.checked ? [...myDisc, x.id] : myDisc.filter((i) => i !== x.id);
                  setMyDisc(next);
                  act(() => api(`/api/entities/${d.id}/disciplines`, { method: "PUT", body: JSON.stringify({ discipline_ids: next }) }), "Disciplinas guardadas.");
                }} /> {x.parent_id ? "" : "▸ "}{x.name}
              </label>
            ))}
          </div>
        </details>
      )}
      {msg && <p className={msg.ok ? "ok" : "error"}>{msg.text}</p>}

      {courses.length > 0 && (
        <div className="card" style={{ marginBottom: 12 }}>
          <strong>Cursos donde aparece:</strong>{" "}
          {courses.map((c, i) => (
            <span key={c.course_id}>{i > 0 && " · "}
              <Link href={`/learning/courses/${c.course_id}`}>{c.title}</Link>
              {c.lessons.length > 0 && <span className="muted"> ({c.lessons.map((l) => l.title).join(", ")})</span>}
            </span>
          ))}
        </div>
      )}

      <h2>Nombres y alias</h2>
      <div className="card">
        {d.aliases.map((a) => (
          <span key={a.id} className="badge" style={{ marginRight: 6 }}>
            {a.alias}{a.language ? ` (${a.language})` : ""}
            {a.kind !== "canonical" && d.status !== "merged" && (
              <button style={{ border: 0, padding: "0 0 0 6px", background: "none" }} title="Quitar alias"
                onClick={() => act(() => api(`/api/entities/${d.id}/aliases/${a.id}`, { method: "DELETE" }), "Alias eliminado.")}>×</button>
            )}
          </span>
        ))}
        {d.status !== "merged" && (
          <form className="row" style={{ marginTop: 10, justifyContent: "flex-start" }} onSubmit={(e) => {
            e.preventDefault();
            act(() => api(`/api/entities/${d.id}/aliases`, { method: "POST", body: JSON.stringify({ alias }) }), "Alias añadido.").then(() => setAlias(""));
          }}>
            <input style={{ maxWidth: 280 }} value={alias} onChange={(e) => setAlias(e.target.value)} placeholder="Sinónimo, traducción o abreviatura" required />
            <button>Añadir</button>
          </form>
        )}
      </div>

      <h2>Afirmaciones ({d.claims.length})</h2>
      {d.claims.length > 0 && (
        <div className="row" style={{ justifyContent: "flex-start", gap: 6, marginBottom: 8 }} role="group" aria-label="Filtrar por origen">
          {EV_FILTERS.map(([k, label]) => {
            const n = d.claims.filter((c) => matches(k, c.provenance.kind)).length;
            return (
              <button key={k} className={evFilter === k ? "primary" : ""} aria-pressed={evFilter === k} onClick={() => setEvFilter(k)}>
                {label} ({n})
              </button>
            );
          })}
        </div>
      )}
      {d.claims.length === 0 && <p className="muted">Aún no hay afirmaciones con evidencia sobre este concepto.</p>}
      {d.claims.filter((c) => matches(evFilter, c.provenance.kind)).map((c) => (
        <div className="card" key={c.id} style={{ marginBottom: 8 }}>
          <div className="row">
            <strong>{c.text}</strong>
            <span className="badge">{CLAIM_TYPES[c.claim_type] ?? c.claim_type}{c.origin === "ai" ? " · IA" : ""}{c.review_status === "pending" ? " · pendiente" : c.review_status === "rejected" ? " · rechazada" : ""}</span>
          </div>
          <div className="muted">
            Origen: <span className="badge">{c.provenance.label}</span>{" "}
            {c.provenance.publication ? (
              <>
                <Link href={`/studies/${c.provenance.publication.id}`}>{c.provenance.publication.title}</Link>
                {c.provenance.publication.year ? ` (${c.provenance.publication.year})` : ""} ·{" "}
                {QUALITY[c.provenance.publication.methodological_quality]}
              </>
            ) : c.person}
          </div>
          {c.context && <div className="muted" style={{ fontSize: "0.85rem" }}>{c.context}</div>}
          {c.review_status === "pending" && (
            <div className="row" style={{ justifyContent: "flex-start", gap: 6, marginTop: 6 }}>
              <button onClick={() => act(() => api(`/api/claims/${c.id}`, { method: "PATCH", body: JSON.stringify({ review_status: "accepted" }) }), "Afirmación aceptada.")}>Aceptar</button>
              <button onClick={() => act(() => api(`/api/claims/${c.id}`, { method: "PATCH", body: JSON.stringify({ review_status: "rejected" }) }), "Afirmación rechazada.")}>Rechazar</button>
            </div>
          )}
          {c.evidence.map((e) => (
            <blockquote key={e.id} style={{ margin: "8px 0 0", paddingLeft: 10, borderLeft: "3px solid var(--border)" }}>
              «{e.quote}»
              <div className="muted">
                <a href={sourceHref(e)} target="_blank" rel="noopener">{e.source_title}</a>
                {e.page ? `, p. ${e.page}` : ""}{e.section ? ` · ${e.section}` : ""}
              </div>
            </blockquote>
          ))}
        </div>
      ))}

      {d.status !== "merged" && (
        <div className="card">
          <strong>Añadir afirmación con evidencia</strong>
          {!picked ? (
            <p className="muted">Busca el fragmento en <Link href="/search">Buscar</Link> y pulsa «Usar como evidencia»; después vuelve aquí.</p>
          ) : (
            <form onSubmit={(e) => {
              e.preventDefault();
              act(() => api("/api/claims", { method: "POST", body: JSON.stringify({
                text: claim.text, claim_type: claim.claim_type, entities: [{ id: d.id }],
                evidence: [{ segment_id: picked.segment_id, quote: claim.quote }],
              }) }), "Afirmación guardada.").then(() => { clearPicked(); setPicked(null); setSegment(null); setClaim({ ...claim, text: "", quote: "" }); });
            }}>
              <p className="muted">
                Fragmento: {picked.source_title}{picked.page ? `, p. ${picked.page}` : ""}{" "}
                <button type="button" onClick={() => { clearPicked(); setPicked(null); }}>Descartar</button>
              </p>
              {segment && <pre style={{ whiteSpace: "pre-wrap", maxHeight: 180, overflow: "auto", background: "var(--bg)", padding: 8 }}>{segment.text}</pre>}
              <label>Cita literal (copia un trozo exacto del fragmento)</label>
              <textarea value={claim.quote} onChange={(e) => setClaim({ ...claim, quote: e.target.value })} required rows={2} style={{ width: "100%" }} />
              <label>Afirmación (con tus palabras)</label>
              <input value={claim.text} onChange={(e) => setClaim({ ...claim, text: e.target.value })} required />
              <label>Tipo</label>
              <select value={claim.claim_type} onChange={(e) => setClaim({ ...claim, claim_type: e.target.value })}>
                {Object.entries(CLAIM_TYPES).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </select>
              <p><button className="primary">Guardar afirmación</button></p>
            </form>
          )}
        </div>
      )}

      {d.status !== "merged" && (<>
        <h2>Comparar explicaciones</h2>
        <Comparison entityId={d.id} onChange={load} />
      </>)}

      <h2>Relaciones ({d.relationships.length})</h2>
      <div className="card table-wrap">
        {d.relationships.length === 0 ? <p className="muted">Sin relaciones.</p> : (
          <table><tbody>
            {d.relationships.map((r) => (
              <tr key={`${r.id}-${r.direction}`}>
                <td>{d.canonical_name} <em>{r.label}</em> <Link href={`/concepts/${r.other.slug}`}>{r.other.name}</Link>
                  {r.conditions && <div className="muted">Condiciones: {r.conditions}</div>}</td>
                <td><span className="badge">{STATUS[r.status] ?? r.status}{r.origin === "ai" ? " · IA" : ""}</span></td>
                <td>{r.status === "proposed" && (<>
                  <button onClick={() => act(() => api(`/api/relationships/${r.id}`, { method: "PATCH", body: JSON.stringify({ status: "verified" }) }), "Relación verificada.")}>Verificar</button>{" "}
                  <button onClick={() => act(() => api(`/api/relationships/${r.id}`, { method: "PATCH", body: JSON.stringify({ status: "rejected" }) }), "Relación rechazada.")}>Rechazar</button>
                </>)}</td>
              </tr>
            ))}
          </tbody></table>
        )}
        {d.status !== "merged" && (
          <form style={{ marginTop: 12 }} onSubmit={(e) => {
            e.preventDefault();
            if (!rel.other) return;
            const [a, b] = rel.reverse ? [rel.other.id, d.id] : [d.id, rel.other.id];
            const ev = picked ? [{ segment_id: picked.segment_id }] : evidenceSegments.map((x) => ({ segment_id: x.segment_id }));
            act(() => api("/api/relationships", { method: "POST", body: JSON.stringify({
              source_id: a, target_id: b, type_key: rel.type_key, description: rel.description || null, evidence: ev,
            }) }), "Relación guardada.");
          }}>
            <strong>Nueva relación</strong>
            <div className="row" style={{ alignItems: "flex-start" }}>
              <select value={`${rel.type_key}|${rel.reverse ? 1 : 0}`} onChange={(e) => { const [k, r] = e.target.value.split("|"); setRel({ ...rel, type_key: k, reverse: r === "1" }); }}>
                {types?.relationship_types.flatMap((t) => [
                  <option key={t.key} value={`${t.key}|0`}>{d.canonical_name} {t.label} …</option>,
                  ...(t.is_symmetric ? [] : [<option key={`${t.key}-r`} value={`${t.key}|1`}>{d.canonical_name} {t.inverse_label} …</option>]),
                ])}
              </select>
              <div style={{ flex: 1, minWidth: 220 }}><EntityPicker exclude={d.id} onPick={(c) => setRel({ ...rel, other: c })} /></div>
            </div>
            <p className="muted">
              Evidencia: {picked ? `el fragmento seleccionado (${picked.source_title})` : evidenceSegments.length ? `los fragmentos citados en las afirmaciones de este concepto (${evidenceSegments.length})` : "ninguna — selecciona un fragmento en Buscar"}.
            </p>
            <button disabled={!rel.other || (!picked && evidenceSegments.length === 0)}>Guardar relación</button>
          </form>
        )}
      </div>

      {d.merged_from.length > 0 && (<>
        <h2>Fusiones</h2>
        <div className="card">
          {d.merged_from.map((m) => (
            <div key={m.merge_id} className="row">
              <span>Incluye «{m.name}» ({new Date(m.at).toLocaleDateString("es-ES")})</span>
              <button onClick={() => act(() => api(`/api/merges/${m.merge_id}/undo`, { method: "POST" }), `Fusión con «${m.name}» deshecha.`)}>Deshacer</button>
            </div>
          ))}
        </div>
      </>)}

      {d.status !== "merged" && (<>
        <h2>Fusionar</h2>
        <div className="card">
          <p className="muted" style={{ marginTop: 0 }}>Si este concepto es el mismo que otro, fusiónalo: sus alias, afirmaciones y relaciones pasan al otro. Se puede deshacer.</p>
          <EntityPicker exclude={d.id} onPick={setMergeTarget} />
          <p><button disabled={!mergeTarget} onClick={async () => {
            if (!mergeTarget) return;
            try {
              await api(`/api/entities/${d.id}/merge`, { method: "POST", body: JSON.stringify({ into: mergeTarget.id }) });
              router.push(`/concepts/${mergeTarget.id}`);
            } catch (err) { setMsg({ ok: false, text: err instanceof ApiError ? err.message : "Error." }); }
          }}>Fusionar en «{mergeTarget?.canonical_name ?? "…"}»</button></p>
        </div>
      </>)}
    </main>
  );
}
