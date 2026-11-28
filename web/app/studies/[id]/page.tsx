"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { QUALITY } from "@/components/Comparison";
import { Header } from "@/components/Header";
import { SourceReader } from "@/components/SourceReader";
import { api, ApiError } from "@/lib/api";
import type { StudyDetail } from "@/lib/evidence";
import { CLAIM_TYPES } from "@/lib/knowledge";
import { useMe } from "@/lib/useMe";

const TEXT_FIELDS = [
  ["study_type", "Tipo de estudio", "ECA, cohorte, transversal, revisión sistemática, metaanálisis…"],
  ["design", "Diseño", ""],
  ["population", "Población", "Quiénes participaron (edad, nivel, sexo…)"],
  ["variables", "Variables", "Qué se manipuló y qué se midió"],
  ["results", "Resultados", ""],
  ["limitations", "Limitaciones", ""],
] as const;
type Form = Record<(typeof TEXT_FIELDS)[number][0], string> & { sample_size: string };

export default function StudyPage() {
  const user = useMe();
  const { id } = useParams<{ id: string }>();
  const [s, setS] = useState<StudyDetail | null>(null);
  const [form, setForm] = useState<Form | null>(null);
  const [quality, setQuality] = useState({ methodological_quality: "not_assessed", quality_notes: "" });
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const load = useCallback(async () => {
    try {
      const d = await api<StudyDetail>(`/api/publications/${id}`);
      setS(d);
      setForm({
        ...(Object.fromEntries(TEXT_FIELDS.map(([k]) => [k, d[k] ?? ""])) as Form),
        sample_size: d.sample_size?.toString() ?? "",
      });
      setQuality({ methodological_quality: d.methodological_quality, quality_notes: d.quality_notes ?? "" });
    } catch (e) {
      setMsg({ ok: false, text: e instanceof ApiError && e.status === 404 ? "Estudio no encontrado." : "Error al cargar." });
    }
  }, [id]);
  useEffect(() => { if (user) load(); }, [user, load]);

  async function patch(body: object, ok: string) {
    try {
      await api(`/api/publications/${id}`, { method: "PATCH", body: JSON.stringify(body) });
      setMsg({ ok: true, text: ok });
      await load();
    } catch (e) {
      setMsg({ ok: false, text: e instanceof ApiError ? e.message : "No se pudo guardar." });
    }
  }

  if (!s || !form) return <main><Header user={user} /><p className={msg?.ok === false ? "error" : "muted"}>{msg?.text ?? "Cargando…"}</p></main>;
  return (
    <main>
      <Header user={user} />
      <p className="muted"><Link href="/studies">← Estudios</Link></p>
      <h1 style={{ marginBottom: 4 }}>{s.title}</h1>
      <p className="muted" style={{ marginTop: 0 }}>
        {[s.authors_text, s.journal, s.year].filter(Boolean).join(" · ")}
        {s.doi && <> · DOI <a href={`https://doi.org/${s.doi}`} target="_blank" rel="noopener noreferrer">{s.doi}</a></>}
      </p>
      <p className="muted" style={{ fontSize: "0.85rem" }}>
        Datos bibliográficos: {s.metadata_source === "crossref" ? `Crossref (${new Date(s.metadata_fetched_at!).toLocaleDateString("es-ES")})` : "introducidos a mano"}.
      </p>
      {msg && <p className={msg.ok ? "ok" : "error"}>{msg.text}</p>}

      <div className="card">
        <h3 style={{ marginTop: 0 }}>Calidad metodológica <span className="badge">{QUALITY[s.methodological_quality]}</span></h3>
        <p className="muted" style={{ marginTop: 0 }}>
          Tu evaluación; el sistema no la deduce.
          {s.quality_assessed_at && ` Evaluada el ${new Date(s.quality_assessed_at).toLocaleDateString("es-ES")}.`}
        </p>
        <div className="row" style={{ justifyContent: "flex-start", gap: 6 }}>
          <select value={quality.methodological_quality} onChange={(e) => setQuality({ ...quality, methodological_quality: e.target.value })} style={{ maxWidth: 220 }}>
            {Object.entries(QUALITY).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
        </div>
        <textarea rows={2} placeholder="Motivo (riesgo de sesgo, tamaño muestral, control…)" value={quality.quality_notes}
          onChange={(e) => setQuality({ ...quality, quality_notes: e.target.value })} style={{ marginTop: 6 }} />
        <p style={{ marginBottom: 0 }}><button onClick={() => patch(quality, "Evaluación guardada.")}>Guardar evaluación</button></p>
      </div>

      <div className="card" style={{ marginTop: 12 }}>
        <h3 style={{ marginTop: 0 }}>Ficha metodológica</h3>
        {TEXT_FIELDS.map(([k, label, ph]) => (
          <label key={k} style={{ display: "block", marginBottom: 8 }}>
            {label}
            <textarea rows={k === "study_type" ? 1 : 2} placeholder={ph} value={form[k]} onChange={(e) => setForm({ ...form, [k]: e.target.value })} />
          </label>
        ))}
        <label style={{ display: "block", maxWidth: 200 }}>
          Tamaño de la muestra
          <input inputMode="numeric" value={form.sample_size} onChange={(e) => setForm({ ...form, sample_size: e.target.value.replace(/\D/g, "") })} />
        </label>
        <p style={{ marginBottom: 0 }}>
          <button onClick={() => patch({
            ...Object.fromEntries(TEXT_FIELDS.map(([k]) => [k, form[k].trim() || null])),
            sample_size: form.sample_size ? Number(form.sample_size) : null,
          }, "Ficha guardada.")}>Guardar ficha</button>
        </p>
      </div>

      {s.abstract && (<><h2>Resumen</h2><p className="muted">{s.abstract}</p></>)}

      <h2>Documentos vinculados ({s.sources.length})</h2>
      {s.sources.length === 0
        ? <p className="muted">Ninguno. En la Biblioteca, «Vincular a estudio» en el PDF del artículo.</p>
        : <>
          <ul>{s.sources.map((x) => <li key={x.id}><Link href={`/sources/${x.id}`}>{x.title}</Link></li>)}</ul>
          {s.sources.map((x, i) => <SourceReader key={x.id} sourceId={x.id} open={i === 0} />)}
        </>}

      <h2>Afirmaciones del estudio ({s.claim_list.length})</h2>
      {s.claim_list.length === 0 && <p className="muted">Aún no hay afirmaciones. Extrae conocimiento del documento vinculado.</p>}
      {s.claim_list.map((c) => (
        <div className="card" key={c.id} style={{ marginBottom: 8 }}>
          <div className="row">
            <span>{c.text}</span>
            <span className="badge">{CLAIM_TYPES[c.claim_type] ?? c.claim_type}{c.origin === "ai" ? " · IA" : c.origin === "mcp" ? " · MCP" : ""}{c.review_status === "pending" ? " · pendiente" : ""}</span>
          </div>
          <div className="muted">{c.entities.map((e, i) => <span key={e.slug}>{i ? ", " : ""}<Link href={`/concepts/${e.slug}`}>{e.name}</Link></span>)}</div>
        </div>
      ))}
    </main>
  );
}
