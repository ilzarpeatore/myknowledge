"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { Header } from "@/components/Header";
import { api, ApiError } from "@/lib/api";
import { sourceHref, where } from "@/lib/knowledge";
import { useMe } from "@/lib/useMe";

type Cite = {
  ref: string; quote: string; segment_id: string; source_id: string; source_title: string;
  version_id: string; mime: string; page: number | null; slide: number | null; t_ms: number | null; section: string | null;
};
type Part = { kind: "source" | "synthesis" | "inference" | "external"; text: string; citations: Cite[]; flags: string[] };
type Answer = { coverage: "sufficient" | "partial" | "insufficient"; coverage_note: string; parts: Part[]; gaps: string[]; citations_dropped: number; fragments_sent: number };
type Q = {
  id: string; question: string; scope_kind: string; scope_id: string | null; scope_key: string | null; scope_label: string | null; mode: string; parent_id: string | null;
  status: string; answer: Answer | null; error: string | null; cost_usd: number | null; model: string | null; created_at: string;
};
type Opt = { id: string; label: string };

const KIND: Record<Part["kind"], { label: string; color: string }> = {
  source: { label: "De tus fuentes", color: "#2e7d32" },
  synthesis: { label: "Síntesis", color: "#1565c0" },
  inference: { label: "Inferencia de la IA", color: "#b26a00" },
  external: { label: "Conocimiento externo (no está en tu biblioteca)", color: "#6a1b9a" },
};
const COVERAGE = { sufficient: "Cobertura suficiente", partial: "Cobertura parcial", insufficient: "Información insuficiente" };
const SCOPES = [
  ["global", "Toda la biblioteca"], ["section", "Una sección"], ["concept", "Un concepto"], ["course", "Un curso"],
  ["discipline", "Una disciplina"], ["source", "Una fuente"],
] as const;

export default function AskPage() {
  const user = useMe();
  const [question, setQuestion] = useState("");
  const [scopeKind, setScopeKind] = useState<string>("global");
  const [scopeId, setScopeId] = useState("");
  const [options, setOptions] = useState<Opt[]>([]);
  const [mode, setMode] = useState("research");
  const [thread, setThread] = useState<Q[]>([]);
  const [history, setHistory] = useState<Q[]>([]);
  const [err, setErr] = useState<string | null>(null);

  const loadHistory = useCallback(() => api<Q[]>("/api/queries?limit=30").then(setHistory).catch(() => {}), []);
  useEffect(() => { if (user) loadHistory(); }, [user, loadHistory]);

  useEffect(() => {
    setScopeId("");
    const load: Record<string, () => Promise<Opt[]>> = {
      concept: () => api<{ id: string; canonical_name: string }[]>("/api/entities?limit=200").then((r) => r.map((x) => ({ id: x.id, label: x.canonical_name }))),
      course: () => api<{ id: string; title: string }[]>("/api/courses").then((r) => r.map((x) => ({ id: x.id, label: x.title }))),
      discipline: () => api<{ id: string; name: string }[]>("/api/disciplines").then((r) => r.map((x) => ({ id: x.id, label: x.name }))),
      section: () => api<{ key: string; name: string }[]>("/api/sections").then((r) => r.map((x) => ({ id: x.key, label: x.name }))),
      source: () => api<{ items: { id: string; title: string }[] }>("/api/sources?limit=200").then((r) => r.items.map((x) => ({ id: x.id, label: x.title }))),
    };
    if (user && load[scopeKind]) load[scopeKind]().then(setOptions).catch(() => setOptions([]));
    else setOptions([]);
  }, [user, scopeKind]);

  const pending = thread.some((q) => q.status === "pending" || q.status === "running");
  useEffect(() => {
    if (!pending) return;
    const t = setInterval(async () => {
      const updated = await Promise.all(thread.map((q) => (q.status === "pending" || q.status === "running")
        ? api<Q>(`/api/queries/${q.id}`).catch(() => q) : Promise.resolve(q)));
      setThread(updated);
      if (!updated.some((q) => q.status === "pending" || q.status === "running")) loadHistory();
    }, 2000);
    return () => clearInterval(t);
  }, [pending, thread, loadHistory]);

  async function ask(e: React.FormEvent, followUp: boolean) {
    e.preventDefault();
    setErr(null);
    const parent = followUp ? thread.at(-1) : undefined;
    try {
      const q = await api<Q>("/api/queries", {
        method: "POST",
        body: JSON.stringify({
          question, mode,
          scope_kind: parent ? parent.scope_kind : scopeKind,
          scope_id: parent ? parent.scope_id : (scopeKind === "global" || scopeKind === "section" ? null : scopeId || null),
          scope_key: parent ? parent.scope_key : (scopeKind === "section" ? scopeId : null),
          parent_id: parent?.id ?? null,
        }),
      });
      setThread(followUp ? [...thread, q] : [q]);
      setQuestion("");
    } catch (x) {
      setErr(x instanceof ApiError ? x.message : "No se pudo enviar la pregunta.");
    }
  }

  const canAsk = question.trim().length >= 3 && (scopeKind === "global" || scopeId) && !pending;

  return (
    <main>
      <Header user={user} />
      <h1>Preguntar a tu biblioteca</h1>
      <p className="muted">
        La respuesta separa lo que dicen tus fuentes (con cita literal comprobada), la síntesis, las inferencias de la IA y el conocimiento externo.
        Si tu biblioteca no tiene información suficiente, lo dice.
      </p>
      <form className="card" onSubmit={(e) => ask(e, false)}>
        <textarea rows={3} style={{ width: "100%" }} placeholder="¿Qué he aprendido sobre proximidad al fallo?" value={question} onChange={(e) => setQuestion(e.target.value)} />
        <div className="row" style={{ justifyContent: "flex-start", gap: 8, marginTop: 8, flexWrap: "wrap" }}>
          <select value={scopeKind} onChange={(e) => setScopeKind(e.target.value)} style={{ maxWidth: 200 }}>
            {SCOPES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
          </select>
          {scopeKind !== "global" && (
            <select value={scopeId} onChange={(e) => setScopeId(e.target.value)} style={{ maxWidth: 320 }}>
              <option value="">— elige —</option>
              {options.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
            </select>
          )}
          <label style={{ margin: 0 }}><input type="radio" style={{ width: "auto" }} checked={mode === "research"} onChange={() => setMode("research")} /> Investigación (solo tu biblioteca)</label>
          <label style={{ margin: 0 }}><input type="radio" style={{ width: "auto" }} checked={mode === "application"} onChange={() => setMode("application")} /> Aplicación (permite inferencias y conocimiento general, marcados)</label>
        </div>
        <div className="row" style={{ justifyContent: "flex-start", gap: 8, marginTop: 8 }}>
          <button className="primary" disabled={!canAsk}>Preguntar</button>
          {thread.length > 0 && <button type="button" disabled={question.trim().length < 3 || pending} onClick={(e) => ask(e, true)}>Seguir sobre lo anterior</button>}
        </div>
        {err && <p className="error">{err}</p>}
      </form>

      {thread.map((q) => <AnswerView key={q.id} q={q} />)}

      {history.length > 0 && (
        <>
          <h2>Preguntas anteriores</h2>
          <div className="card">
            {history.map((q) => (
              <div key={q.id} className="row" style={{ justifyContent: "flex-start", gap: 8, padding: "2px 0" }}>
                <button onClick={() => setThread([q])}>Ver</button>
                <span>{q.question}</span>
                <span className="muted" style={{ fontSize: "0.8rem" }}>
                  {q.scope_label ?? "toda la biblioteca"} · {new Date(q.created_at).toLocaleString("es-ES")}
                  {q.answer ? ` · ${COVERAGE[q.answer.coverage]}` : ` · ${q.status}`}
                </span>
              </div>
            ))}
          </div>
        </>
      )}
    </main>
  );
}

function AnswerView({ q }: { q: Q }) {
  return (
    <div className="card" style={{ marginTop: 12 }}>
      <div className="muted" style={{ fontSize: "0.85rem" }}>
        {q.scope_label ? `Ámbito: ${q.scope_label} · ` : ""}{q.mode === "application" ? "Modo aplicación" : "Modo investigación"}
        {q.model ? ` · ${q.model}` : ""}{q.cost_usd != null ? ` · ${q.cost_usd.toFixed(3)} USD` : ""}
      </div>
      <h3 style={{ marginTop: 4 }}>{q.question}</h3>
      {(q.status === "pending" || q.status === "running") && <p className="muted">Buscando en tu biblioteca y redactando la respuesta…</p>}
      {q.status === "failed" && <p className="error">No se pudo responder: {q.error}</p>}
      {q.answer && (
        <>
          <p style={{ padding: "6px 10px", borderRadius: 6, background: q.answer.coverage === "sufficient" ? "#e8f5e9" : q.answer.coverage === "partial" ? "#fff8e1" : "#fdecea", color: "#222" }}>
            <strong>{COVERAGE[q.answer.coverage]}.</strong> {q.answer.coverage_note}
          </p>
          {q.answer.parts.map((p, i) => (
            <div key={i} style={{ borderLeft: `4px solid ${KIND[p.kind].color}`, paddingLeft: 10, margin: "10px 0" }}>
              <div style={{ fontSize: "0.8rem", color: KIND[p.kind].color, fontWeight: 600 }}>
                {KIND[p.kind].label}{p.flags.length > 0 && <span className="error"> · {p.flags.join(" · ")}</span>}
              </div>
              <p style={{ margin: "4px 0" }}>{p.text}</p>
              {p.citations.map((c, j) => (
                <blockquote key={j} style={{ margin: "4px 0", paddingLeft: 10, borderLeft: "2px solid var(--border)", fontSize: "0.9rem" }}>
                  «{c.quote}»{" "}
                  <span className="muted">— <Link href={sourceHref(c)} target="_blank">{c.source_title}{where(c)}</Link></span>
                </blockquote>
              ))}
            </div>
          ))}
          {q.answer.gaps.length > 0 && (
            <div className="muted"><strong>Lo que falta en tu biblioteca:</strong><ul>{q.answer.gaps.map((g, i) => <li key={i}>{g}</li>)}</ul></div>
          )}
          <p className="muted" style={{ fontSize: "0.8rem" }}>
            {q.answer.fragments_sent} fragmentos consultados{q.answer.citations_dropped > 0 ? ` · ${q.answer.citations_dropped} cita(s) descartada(s) por no coincidir literalmente con la fuente` : ""}
          </p>
        </>
      )}
    </div>
  );
}
