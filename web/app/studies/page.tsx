"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { QUALITY } from "@/components/Comparison";
import { Header } from "@/components/Header";
import { api, ApiError } from "@/lib/api";
import type { Study } from "@/lib/evidence";
import { useMe } from "@/lib/useMe";

type Meta = { doi: string; title: string; year: number | null; journal: string | null; authors_text: string | null; abstract: string | null };

export default function StudiesPage() {
  const user = useMe();
  const [items, setItems] = useState<Study[]>([]);
  const [q, setQ] = useState("");
  const [doi, setDoi] = useState("");
  const [preview, setPreview] = useState<{ metadata: Meta; existing_id: string | null } | null>(null);
  const [manual, setManual] = useState({ title: "", year: "", study_type: "" });
  const [msg, setMsg] = useState<{ ok: boolean; text: string; id?: string } | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!user) return;
    const t = setTimeout(() => api<Study[]>(`/api/publications?${new URLSearchParams(q.trim() ? { q: q.trim() } : {})}`).then(setItems), 200);
    return () => clearTimeout(t);
  }, [user, q, msg]);

  async function run(fn: () => Promise<void>) {
    setBusy(true); setMsg(null);
    try { await fn(); }
    catch (e) { setMsg({ ok: false, text: e instanceof ApiError ? e.message : "Error." }); }
    finally { setBusy(false); }
  }
  const lookup = (e: React.FormEvent) => { e.preventDefault(); run(async () => {
    setPreview(await api("/api/publications/lookup", { method: "POST", body: JSON.stringify({ doi }) }));
  }); };
  const save = () => run(async () => {
    const s = await api<Study>("/api/publications/from-doi", { method: "POST", body: JSON.stringify({ doi }) });
    setPreview(null); setDoi("");
    setMsg({ ok: true, text: `Añadido «${s.title}».`, id: s.id });
  });
  const createManual = (e: React.FormEvent) => { e.preventDefault(); run(async () => {
    const s = await api<Study>("/api/publications", { method: "POST", body: JSON.stringify({
      title: manual.title, year: manual.year ? Number(manual.year) : null, study_type: manual.study_type || null }) });
    setManual({ title: "", year: "", study_type: "" });
    setMsg({ ok: true, text: `Añadido «${s.title}».`, id: s.id });
  }); };

  return (
    <main>
      <Header user={user} />
      <h1>Estudios</h1>
      <p className="muted">
        Publicaciones científicas con su ficha metodológica. La calidad metodológica queda «no evaluada» hasta que la evalúes tú;
        el sistema nunca la deduce. Vincula en la Biblioteca el PDF de cada estudio para que sus afirmaciones cuenten como del estudio.
      </p>

      <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(300px, 1fr))" }}>
        <form className="card" onSubmit={lookup}>
          <h3 style={{ marginTop: 0 }}>Añadir por DOI</h3>
          <div className="row" style={{ gap: 6 }}>
            <input placeholder="10.xxxx/… o https://doi.org/…" value={doi} onChange={(e) => { setDoi(e.target.value); setPreview(null); }} required />
            <button disabled={busy}>Buscar</button>
          </div>
          <p className="muted" style={{ fontSize: "0.85rem" }}>Se consulta Crossref enviando solo el DOI.</p>
          {preview && (
            <div>
              <strong>{preview.metadata.title}</strong>
              <div className="muted">{[preview.metadata.authors_text, preview.metadata.journal, preview.metadata.year].filter(Boolean).join(" · ")}</div>
              {preview.existing_id ? (
                <p>Ya está en tu lista: <Link href={`/studies/${preview.existing_id}`}>abrir</Link>.</p>
              ) : <p><button className="primary" type="button" disabled={busy} onClick={save}>Guardar estudio</button></p>}
            </div>
          )}
        </form>
        <form className="card" onSubmit={createManual}>
          <h3 style={{ marginTop: 0 }}>Añadir a mano (sin DOI)</h3>
          <input placeholder="Título" value={manual.title} onChange={(e) => setManual({ ...manual, title: e.target.value })} required />
          <div className="row" style={{ gap: 6, marginTop: 6 }}>
            <input placeholder="Año" inputMode="numeric" value={manual.year} onChange={(e) => setManual({ ...manual, year: e.target.value.replace(/\D/g, "") })} />
            <input placeholder="Tipo (ECA, metaanálisis…)" value={manual.study_type} onChange={(e) => setManual({ ...manual, study_type: e.target.value })} />
          </div>
          <p><button disabled={busy}>Añadir</button></p>
        </form>
      </div>
      {msg && <p className={msg.ok ? "ok" : "error"}>{msg.text} {msg.id && <Link href={`/studies/${msg.id}`}>Completar la ficha</Link>}</p>}

      <div className="row" style={{ marginTop: 20 }}>
        <input placeholder="Buscar por título, autor o DOI…" value={q} onChange={(e) => setQ(e.target.value)} style={{ maxWidth: 360 }} />
      </div>
      <div className="card table-wrap" style={{ marginTop: 12 }}>
        {items.length === 0 ? <p className="muted">{q ? "No hay resultados." : "Aún no hay estudios."}</p> : (
          <table>
            <thead><tr><th>Estudio</th><th>Año</th><th>Tipo</th><th>Calidad</th><th>Afirmaciones</th></tr></thead>
            <tbody>
              {items.map((s) => (
                <tr key={s.id}>
                  <td>
                    <Link href={`/studies/${s.id}`}>{s.title}</Link>
                    <div className="muted">{[s.authors_text?.split("; ").slice(0, 3).join("; "), s.journal].filter(Boolean).join(" · ")}</div>
                  </td>
                  <td>{s.year ?? "—"}</td>
                  <td>{s.study_type ?? "—"}</td>
                  <td><span className="badge">{QUALITY[s.methodological_quality]}</span></td>
                  <td>{s.claims}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </main>
  );
}
