"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { ExtractKnowledge } from "@/components/ExtractKnowledge";
import { Header } from "@/components/Header";
import { PdfViewer } from "@/components/PdfViewer";
import { SourceContribution } from "@/components/SourceContribution";
import { StudyLink } from "@/components/StudyLink";
import { api, ApiError } from "@/lib/api";
import { LINK_KIND } from "@/lib/analysis";
import { loadSections, type Section } from "@/lib/sections";
import { loadDisciplines, type Discipline } from "@/lib/disciplines";
import { BranchPicker } from "@/components/BranchPicker";
import { useMe } from "@/lib/useMe";

type Version = { id: string; mime: string; original_filename: string | null };
type Source = {
  id: string; title: string; kind: string; status: string; authors_text: string | null; url: string | null;
  publication_id: string | null; sections: string[]; versions: Version[]; disciplines: { id: string; name: string }[];
};
type Related = {
  concepts: { id: string; slug: string; name: string; claims: number }[];
  related: { id: string; title: string; kind: string; sections: string[]; shared_concepts: string[]; shared_count: number; link: { id: string; note: string | null; kind: string; status: string; origin: string } | null }[];
  courses: { id: string; title: string }[];
};

export default function SourcePage() {
  const user = useMe();
  const { id } = useParams<{ id: string }>();
  const [s, setS] = useState<Source | null>(null);
  const [rel, setRel] = useState<Related | null>(null);
  const [sections, setSections] = useState<Section[]>([]);
  const [branches, setBranches] = useState<Discipline[]>([]);
  const loadBranches = useCallback(() => loadDisciplines().then(setBranches).catch(() => {}), []);
  const [all, setAll] = useState<{ id: string; title: string }[]>([]);
  const [linkTo, setLinkTo] = useState({ other: "", note: "" });
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const load = useCallback(async () => {
    setS(await api<Source>(`/api/sources/${id}`));
    setRel(await api<Related>(`/api/sources/${id}/related`));
  }, [id]);
  useEffect(() => {
    if (!user) return;
    load().catch(() => setMsg({ ok: false, text: "Fuente no encontrada." }));
    loadSections().then(setSections).catch(() => {});
    loadBranches();
    api<{ items: { id: string; title: string }[] }>("/api/sources?limit=200").then((r) => setAll(r.items)).catch(() => {});
  }, [user, load]);

  async function act(fn: () => Promise<unknown>, ok: string) {
    try { await fn(); setMsg({ ok: true, text: ok }); await load(); }
    catch (e) { setMsg({ ok: false, text: e instanceof ApiError ? e.message : "Error." }); }
  }
  const toggleSection = (key: string) => {
    const next = s!.sections.includes(key) ? s!.sections.filter((k) => k !== key) : [...s!.sections, key];
    if (next.length === 0) { setMsg({ ok: false, text: "Debe estar al menos en una sección." }); return; }
    act(() => api(`/api/sources/${id}/sections`, { method: "PUT", body: JSON.stringify({ sections: next }) }), "Secciones guardadas.");
  };
  const name = (k: string) => sections.find((x) => x.key === k)?.name ?? k;

  if (!s) return <main><Header user={user} /><p className={msg ? "error" : "muted"}>{msg?.text ?? "Cargando…"}</p></main>;
  const v = s.versions.at(-1);
  const bySection = new Map<string, Related["related"]>();
  rel?.related.forEach((r) => (r.sections.length ? r.sections : ["recursos"]).forEach((k) => bySection.set(k, [...(bySection.get(k) ?? []), r])));

  return (
    <main>
      <Header user={user} />
      <p className="muted"><Link href="/library">← Biblioteca</Link></p>
      <h1 style={{ marginBottom: 4 }}>{s.title}</h1>
      <p className="muted" style={{ marginTop: 0 }}>
        {s.authors_text ?? ""}{v && <>{s.authors_text ? " · " : ""}<a href={`/api/sources/${s.id}/versions/${v.id}/file`} target="_blank" rel="noopener">abrir original</a></>}
        {s.url && <> · <a href={s.url} target="_blank" rel="noopener noreferrer">enlace</a></>}
      </p>
      {s.url?.startsWith("https://www.youtube.com/") && (
        <p style={{ marginTop: 0 }}>
          <button onClick={() => { if (confirm("¿Volver a descargar los subtítulos (o el audio) de YouTube? La transcripción actual se sustituye; el archivo anterior se conserva.")) act(() => api(`/api/sources/${id}/refetch`, { method: "POST" }), "Descargando de nuevo; tardará unos minutos."); }}>
            Volver a descargar de YouTube
          </button>{" "}
          <span className="muted" style={{ fontSize: "0.85rem" }}>Útil si la transcripción salió en otro idioma o incompleta.</span>
        </p>
      )}
      {msg && <p className={msg.ok ? "ok" : "error"}>{msg.text}</p>}

      {v?.mime === "application/pdf" && (
        <details open style={{ marginBottom: 12 }}>
          <summary><strong>Leer el documento</strong> <span className="muted" style={{ fontSize: "0.85rem" }}>· <Link href={`/read/${s.id}?v=${v.id}`}>pantalla completa</Link></span></summary>
          <PdfViewer url={`/api/sources/${s.id}/versions/${v.id}/file`} docKey={v.id} height="75vh" />
        </details>
      )}

      <h2>Secciones</h2>
      <div className="card row" style={{ justifyContent: "flex-start", gap: 6, flexWrap: "wrap" }}>
        {sections.map((x) => (
          <button key={x.key} className={s.sections.includes(x.key) ? "primary" : ""} onClick={() => toggleSection(x.key)} aria-pressed={s.sections.includes(x.key)}>
            {x.name}
          </button>
        ))}
        <span className="muted" style={{ fontSize: "0.85rem" }}>Puede estar en varias a la vez.</span>
      </div>

      <h2>Rama</h2>
      <div className="card">
        <BranchPicker all={branches} value={s.disciplines.map((d) => d.id)} onCreated={loadBranches}
          onChange={(ids) => act(() => api(`/api/sources/${id}/disciplines`, { method: "PUT", body: JSON.stringify({ discipline_ids: ids }) }), "Rama guardada.")} />
      </div>

      <h2>De qué trata ({rel?.concepts.length ?? 0} conceptos)</h2>
      <div className="card">
        {!rel || rel.concepts.length === 0 ? <p className="muted" style={{ margin: 0 }}>Aún no hay afirmaciones que citen esta fuente. Extrae conocimiento o crea afirmaciones desde la búsqueda.</p> :
          rel.concepts.map((c) => <Link key={c.id} href={`/concepts/${c.slug}`} className="badge" style={{ marginRight: 6, display: "inline-block", marginBottom: 4 }}>{c.name} ({c.claims})</Link>)}
      </div>

      <h2>Relacionado en otras secciones</h2>
      <p className="muted" style={{ marginTop: 0 }}>Fuentes que tratan los mismos conceptos, y las que tú has enlazado.</p>
      {bySection.size === 0 ? <div className="card"><p className="muted" style={{ margin: 0 }}>Nada todavía.</p></div> : (
        <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))" }}>
          {[...bySection.entries()].sort((a, b) => sections.findIndex((x) => x.key === a[0]) - sections.findIndex((x) => x.key === b[0])).map(([k, items]) => (
            <div className="card" key={k}>
              <strong>{name(k)}</strong>
              {items.map((r) => (
                <div key={r.id} style={{ marginTop: 6 }}>
                  <Link href={`/sources/${r.id}`}>{r.title}</Link>
                  <div className="muted" style={{ fontSize: "0.85rem" }}>
                    {r.shared_count > 0 && <>{r.shared_count} concepto(s) en común: {r.shared_concepts.join(", ")}</>}
                    {r.link && <> {r.shared_count > 0 ? "· " : ""}{r.link.status === "proposed" ? "enlace propuesto" : "enlazado"} ({LINK_KIND[r.link.kind] ?? r.link.kind}){r.link.note ? `: ${r.link.note}` : ""} <button style={{ fontSize: "0.75rem", padding: "0 6px" }} onClick={() => act(() => api(`/api/source-links/${r.link!.id}`, { method: "DELETE" }), "Enlace quitado.")}>quitar</button></>}
                  </div>
                </div>
              ))}
            </div>
          ))}
        </div>
      )}
      <form className="row card" style={{ justifyContent: "flex-start", gap: 6, marginTop: 10 }}
        onSubmit={(e) => { e.preventDefault(); if (linkTo.other) act(() => api(`/api/sources/${id}/links`, { method: "POST", body: JSON.stringify({ other_id: linkTo.other, note: linkTo.note || null }) }), "Enlazado.").then(() => setLinkTo({ other: "", note: "" })); }}>
        <span>Enlazar con</span>
        <select value={linkTo.other} onChange={(e) => setLinkTo({ ...linkTo, other: e.target.value })} style={{ maxWidth: 320 }}>
          <option value="">— elige una fuente —</option>
          {all.filter((x) => x.id !== id).map((x) => <option key={x.id} value={x.id}>{x.title}</option>)}
        </select>
        <input placeholder="nota (opcional): p. ej. «libro que recomienda el curso»" value={linkTo.note} onChange={(e) => setLinkTo({ ...linkTo, note: e.target.value })} style={{ maxWidth: 360 }} />
        <button disabled={!linkTo.other}>Enlazar</button>
      </form>

      <h2>Qué aporta a tu biblioteca</h2>
      <SourceContribution sourceId={s.id} />

      {rel && rel.courses.length > 0 && (<><h2>Cursos que la usan</h2><div className="card">{rel.courses.map((c) => <div key={c.id}><Link href={`/learning/courses/${c.id}`}>{c.title}</Link></div>)}</div></>)}

      {s.status === "ready" && (
        <>
          <h2>Conocimiento</h2>
          <div className="card"><ExtractKnowledge sourceId={s.id} onClose={() => {}} /></div>
          <h2>¿Es un estudio científico?</h2>
          <div className="card"><StudyLink sourceId={s.id} current={s.publication_id} onDone={load} /></div>
        </>
      )}
    </main>
  );
}
