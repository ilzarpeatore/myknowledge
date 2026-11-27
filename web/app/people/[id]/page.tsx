"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { AuthorPicker } from "@/components/AuthorPicker";
import { Header } from "@/components/Header";
import { api, ApiError } from "@/lib/api";
import { type Author, ROLE } from "@/lib/authors";
import { Badge, STUDY_STATUS, type StudyStatus } from "@/lib/analysis";
import { useMe } from "@/lib/useMe";

type Profile = {
  person: { id: string; name: string; role: string | null; affiliation: string | null };
  claims: number; summary: Record<string, number>;
  concepts: { id: string; name: string; slug: string; claims: { id: string; text: string; study: StudyStatus }[] }[];
  peers: { id: string; name: string; agree: number; disagree: number; examples: { kind: string; mine: string; theirs: string }[] }[];
  sources: { id: string; title: string; claims: number }[];
};

type Detail = {
  id: string; name: string; role: string | null; affiliation: string | null; notes: string | null; claims: number;
  sources: { id: string; title: string; kind: string; co_authors: string[] }[];
};

/** Edit the person, merge a duplicate into another, delete one created by mistake. */
function Manage({ d, onSaved }: { d: Detail; onSaved: () => void }) {
  const router = useRouter();
  const [name, setName] = useState(d.name);
  const [role, setRole] = useState(d.role ?? "");
  const [aff, setAff] = useState(d.affiliation ?? "");
  const [into, setInto] = useState<Author[]>([]);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const fail = (x: unknown) => setMsg({ ok: false, text: x instanceof ApiError ? x.message : "Error." });
  async function save(e: React.FormEvent) {
    e.preventDefault();
    try {
      await api(`/api/persons/${d.id}`, { method: "PATCH", body: JSON.stringify({ name, role: role || null, affiliation: aff || null }) });
      setMsg({ ok: true, text: "Guardado." }); onSaved();
    } catch (x) { fail(x); }
  }
  async function merge() {
    const target = into[0];
    if (!target?.id || !confirm(`¿Fusionar «${d.name}» en «${target.name}»? Sus fuentes y afirmaciones pasan a «${target.name}» y «${d.name}» desaparece.`)) return;
    try {
      await api(`/api/persons/${d.id}/merge`, { method: "POST", body: JSON.stringify({ into: target.id }) });
      router.replace(`/people/${target.id}`);
    } catch (x) { fail(x); }
  }
  async function remove() {
    if (!confirm(`¿Borrar «${d.name}» de Autores?`)) return;
    try { await api(`/api/persons/${d.id}`, { method: "DELETE" }); router.replace("/people"); } catch (x) { fail(x); }
  }
  return (
    <details className="card" style={{ marginBottom: 12 }}>
      <summary>Editar, fusionar o borrar</summary>
      <form onSubmit={save} className="row" style={{ justifyContent: "flex-start", gap: 6, marginTop: 8 }}>
        <input value={name} onChange={(e) => setName(e.target.value)} maxLength={300} style={{ maxWidth: 300 }} aria-label="Nombre" />
        <select value={role} onChange={(e) => setRole(e.target.value)} style={{ maxWidth: 170 }} aria-label="Papel">
          <option value="">Sin papel</option>
          {Object.entries(ROLE).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
        </select>
        <input value={aff} onChange={(e) => setAff(e.target.value)} placeholder="Afiliación (universidad, centro…)" maxLength={1000} style={{ maxWidth: 300 }} />
        <button className="primary">Guardar</button>
      </form>
      <p style={{ margin: "12px 0 4px" }}>¿Es la misma persona que otra escrita de otra forma? Fusiónala en ella:</p>
      <div className="row" style={{ justifyContent: "flex-start", gap: 6, alignItems: "flex-start" }}>
        <div style={{ minWidth: 260 }}><AuthorPicker single value={into} onChange={(v) => setInto(v.filter((a) => a.id))} exclude={[d.id]} placeholder="Buscar la persona que se queda…" /></div>
        <button disabled={!into.length} onClick={merge}>Fusionar</button>
        <button className="link" onClick={remove} title="Solo si no tiene fuentes, afirmaciones ni cursos">Borrar</button>
      </div>
      {msg && <p className={msg.ok ? "ok" : "error"}>{msg.text}</p>}
    </details>
  );
}

export default function PersonPage() {
  const user = useMe();
  const { id } = useParams<{ id: string }>();
  const [p, setP] = useState<Profile | null>(null);
  const [err, setErr] = useState(false);
  const [d, setD] = useState<Detail | null>(null);
  const load = useCallback(() => {
    api<Profile>(`/api/persons/${id}/profile`).then(setP).catch(() => setErr(true));
    api<Detail>(`/api/persons/${id}`).then(setD).catch(() => {});
  }, [id]);
  useEffect(() => { if (user) load(); }, [user, load]);
  if (!p) return <main><Header user={user} /><p className={err ? "error" : "muted"}>{err ? "Persona no encontrada." : "Cargando…"}</p></main>;
  return (
    <main>
      <Header user={user} />
      <p className="muted"><Link href="/people">← Autores</Link></p>
      <h1 style={{ marginBottom: 4 }}>{p.person.name}</h1>
      <p className="muted" style={{ marginTop: 0 }}>{({ teacher: "Profesor/a", author: "Autor/a", researcher: "Investigador/a" } as Record<string, string>)[p.person.role ?? ""] ?? ""}{p.person.affiliation ? ` · ${p.person.affiliation}` : ""} · {p.claims} afirmaciones</p>

      {d && <Manage key={d.name + d.role + d.affiliation} d={d} onSaved={load} />}
      <h2>Sus fuentes {d && <span className="muted" style={{ fontSize: "0.9rem" }}>({d.sources.length})</span>}</h2>
      <div className="card">
        {!d || d.sources.length === 0 ? <p className="muted" style={{ margin: 0 }}>No figura como autor de ninguna fuente. Añádelo al subir contenido o en «Editar» de una fuente de la Biblioteca.</p> :
          d.sources.map((s) => (
            <div key={s.id}><Link href={`/sources/${s.id}`}>{s.title}</Link>{s.co_authors.length > 0 && <span className="muted"> · con {s.co_authors.join(", ")}</span>}</div>
          ))}
        {d && d.sources.length > 0 && <p style={{ marginBottom: 0 }}><Link href={`/library?author=${d.id}`}>Ver en la Biblioteca →</Link></p>}
      </div>

      <h2>Frente a los estudios</h2>
      <div className="card row" style={{ justifyContent: "flex-start", gap: 6, flexWrap: "wrap" }}>
        {Object.keys(p.summary).length === 0 ? <span className="muted">Sin afirmaciones.</span> :
          Object.entries(p.summary).map(([k, n]) => <Badge key={k} text={`${STUDY_STATUS[k].label}: ${n}`} color={STUDY_STATUS[k].color} />)}
        <span className="muted" style={{ fontSize: "0.85rem" }}>Se comprueban en la ficha de cada concepto («Comprobar con estudios») o al analizar sus fuentes.</span>
      </div>

      <h2>Coincide y discrepa con</h2>
      <div className="card">
        {p.peers.length === 0 ? <p className="muted" style={{ margin: 0 }}>Aún no hay afirmaciones de otras personas sobre los mismos conceptos.</p> :
          p.peers.map((x) => (
            <details key={x.id} style={{ marginBottom: 6 }}>
              <summary><Link href={`/people/${x.id}`}>{x.name}</Link>: coincide en {x.agree}, discrepa en {x.disagree}</summary>
              <ul style={{ margin: "4px 0" }}>{x.examples.map((e, i) => <li key={i}><Badge text={e.kind === "agree" ? "coinciden" : "discrepan"} color={e.kind === "agree" ? "#2e7d32" : "#c62828"} /> «{e.mine}» / «{e.theirs}»</li>)}</ul>
            </details>
          ))}
      </div>

      <h2>Su postura por concepto</h2>
      {p.concepts.map((c) => (
        <div className="card" key={c.id} style={{ marginBottom: 8 }}>
          <Link href={`/concepts/${c.slug}`}><strong>{c.name}</strong></Link>
          <ul style={{ margin: "4px 0" }}>
            {c.claims.map((cl) => (
              <li key={cl.id}>{cl.text} <Badge text={STUDY_STATUS[cl.study.status].label} color={STUDY_STATUS[cl.study.status].color} /></li>
            ))}
          </ul>
        </div>
      ))}

      <h2>Fuentes donde se le atribuyen afirmaciones</h2>
      <div className="card">{p.sources.map((s) => <div key={s.id}><Link href={`/sources/${s.id}`}>{s.title}</Link> <span className="muted">{s.claims} afirmaciones</span></div>)}</div>
    </main>
  );
}
