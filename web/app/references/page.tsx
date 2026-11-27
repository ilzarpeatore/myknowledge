"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { Header } from "@/components/Header";
import { api, ApiError } from "@/lib/api";
import { loadSections, type Section } from "@/lib/sections";
import { useMe } from "@/lib/useMe";

type Ref = {
  id: string; key: string; label: string; first_author: string | null; year: number | null; title: string | null; doi: string | null;
  ambiguous: boolean; mentions: number; sources: number; in_bibliography: number;
  publication: { id: string; title: string; methodological_quality: string } | null;
};

export default function ReferencesPage() {
  const user = useMe();
  const [items, setItems] = useState<Ref[]>([]);
  const [total, setTotal] = useState(0);
  const [q, setQ] = useState("");
  const [sort, setSort] = useState("mentions");
  const [section, setSection] = useState("");
  const [linked, setLinked] = useState("");
  const [status, setStatus] = useState("active");
  const [sections, setSections] = useState<Section[]>([]);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const load = useCallback(async () => {
    const p = new URLSearchParams({ sort, status, limit: "200" });
    if (q.trim()) p.set("q", q.trim());
    if (section) p.set("section", section);
    if (linked) p.set("linked", linked);
    const r = await api<{ total: number; items: Ref[] }>(`/api/references?${p}`);
    setItems(r.items); setTotal(r.total);
  }, [q, sort, section, linked, status]);
  useEffect(() => { if (user) { load().catch(() => {}); loadSections().then(setSections).catch(() => {}); } }, [user, load]);

  async function rescan() {
    try { const r = await api<{ queued: number }>("/api/references/rescan", { method: "POST" }); setMsg({ ok: true, text: `Buscando de nuevo en ${r.queued} fuente(s). Actualiza en un momento.` }); }
    catch (e) { setMsg({ ok: false, text: e instanceof ApiError ? e.message : "Error." }); }
  }

  return (
    <main>
      <Header user={user} />
      <div className="row"><h1>Referencias bibliográficas</h1><button onClick={rescan}>Buscar de nuevo en toda la biblioteca</button></div>
      <p className="muted">
        Las citas que aparecen en tus diapositivas, libros, apuntes y transcripciones («Schoenfeld et al., 2017», bibliografías, DOI), agrupadas por obra.
        El contador dice cuántas veces aparece cada una y en cuántos documentos. Se detectan sin IA; si alguna no es una referencia, descártala.
      </p>
      {msg && <p className={msg.ok ? "ok" : "error"}>{msg.text}</p>}
      <div className="card row" style={{ justifyContent: "flex-start", gap: 8, flexWrap: "wrap" }}>
        <input placeholder="autor, título o DOI" value={q} onChange={(e) => setQ(e.target.value)} style={{ maxWidth: 260 }} />
        <select value={sort} onChange={(e) => setSort(e.target.value)} style={{ maxWidth: 200 }} aria-label="Ordenar">
          <option value="mentions">Más citadas</option><option value="sources">En más documentos</option>
          <option value="year">Más recientes</option><option value="author">Por autor</option>
        </select>
        <select value={section} onChange={(e) => setSection(e.target.value)} style={{ maxWidth: 200 }} aria-label="Sección">
          <option value="">Todas las secciones</option>
          {sections.map((s) => <option key={s.key} value={s.key}>{s.name}</option>)}
        </select>
        <select value={linked} onChange={(e) => setLinked(e.target.value)} style={{ maxWidth: 220 }} aria-label="Estudios">
          <option value="">Con y sin ficha en Estudios</option><option value="true">Ya en Estudios</option><option value="false">Aún no en Estudios</option>
        </select>
        <select value={status} onChange={(e) => setStatus(e.target.value)} style={{ maxWidth: 160 }} aria-label="Estado">
          <option value="active">Activas</option><option value="dismissed">Descartadas</option>
        </select>
      </div>
      <p className="muted">{total} referencia(s)</p>
      <div className="card" style={{ overflowX: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse" }}>
          <thead><tr style={{ textAlign: "left" }}>
            <th>Referencia</th><th style={{ textAlign: "right" }}>Veces citada</th><th style={{ textAlign: "right" }}>Documentos</th><th>En Estudios</th>
          </tr></thead>
          <tbody>
            {items.map((r) => (
              <tr key={r.id} style={{ borderTop: "1px solid var(--border)" }}>
                <td style={{ padding: "6px 4px" }}>
                  <Link href={`/references/${r.id}`}>{r.label}</Link>
                  <div className="muted" style={{ fontSize: "0.8rem" }}>
                    {[r.first_author, r.year].filter(Boolean).join(", ")}{r.doi ? ` · DOI ${r.doi}` : ""}{r.ambiguous ? " · posible: varias obras con el mismo autor y año" : ""}
                  </div>
                </td>
                <td style={{ textAlign: "right" }}><strong>{r.mentions}</strong>{r.in_bibliography ? <div className="muted" style={{ fontSize: "0.75rem" }}>{r.in_bibliography} en bibliografía</div> : null}</td>
                <td style={{ textAlign: "right" }}>{r.sources}</td>
                <td>{r.publication ? <Link href={`/studies/${r.publication.id}`}>Sí</Link> : <span className="muted">No</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {items.length === 0 && <p className="muted">No hay referencias {q || section || linked ? "con estos filtros" : "todavía: aparecen al subir documentos que citan obras"}.</p>}
      </div>
    </main>
  );
}
