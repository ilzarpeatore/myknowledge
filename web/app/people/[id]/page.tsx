"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { Header } from "@/components/Header";
import { api } from "@/lib/api";
import { Badge, STUDY_STATUS, type StudyStatus } from "@/lib/analysis";
import { useMe } from "@/lib/useMe";

type Profile = {
  person: { id: string; name: string; role: string | null; affiliation: string | null };
  claims: number; summary: Record<string, number>;
  concepts: { id: string; name: string; slug: string; claims: { id: string; text: string; study: StudyStatus }[] }[];
  peers: { id: string; name: string; agree: number; disagree: number; examples: { kind: string; mine: string; theirs: string }[] }[];
  sources: { id: string; title: string; claims: number }[];
};

export default function PersonPage() {
  const user = useMe();
  const { id } = useParams<{ id: string }>();
  const [p, setP] = useState<Profile | null>(null);
  const [err, setErr] = useState(false);
  useEffect(() => { if (user) api<Profile>(`/api/persons/${id}/profile`).then(setP).catch(() => setErr(true)); }, [user, id]);
  if (!p) return <main><Header user={user} /><p className={err ? "error" : "muted"}>{err ? "Persona no encontrada." : "Cargando…"}</p></main>;
  return (
    <main>
      <Header user={user} />
      <p className="muted"><Link href="/analysis">← Análisis</Link></p>
      <h1 style={{ marginBottom: 4 }}>{p.person.name}</h1>
      <p className="muted" style={{ marginTop: 0 }}>{({ teacher: "Profesor/a", author: "Autor/a", researcher: "Investigador/a" } as Record<string, string>)[p.person.role ?? ""] ?? ""}{p.person.affiliation ? ` · ${p.person.affiliation}` : ""} · {p.claims} afirmaciones</p>

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

      <h2>Fuentes</h2>
      <div className="card">{p.sources.map((s) => <div key={s.id}><Link href={`/sources/${s.id}`}>{s.title}</Link> <span className="muted">{s.claims} afirmaciones</span></div>)}</div>
    </main>
  );
}
