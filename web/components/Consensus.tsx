"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { Badge, LEVEL } from "@/lib/analysis";

type Group = {
  statement: string; count: number; sources: number;
  studies: { id: string; label: string; quality: string }[]; teachers: string[];
  disputed: boolean; evidence_level: string; evidence_text: string; checked_against_studies: string[];
  claims: { id: string; text: string; perspective: { kind: string; label: string } }[];
};
type Data = { grouped_by_meaning: boolean; groups: Group[]; note: string };

/** What the claims about a concept agree on, with how many sources and studies back each point. */
export function Consensus({ entityId }: { entityId: string }) {
  const [d, setD] = useState<Data | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => { api<Data>(`/api/entities/${entityId}/consensus`).then(setD).catch(() => setFailed(true)); }, [entityId]);
  if (failed) return <div className="card"><p className="error" style={{ margin: 0 }}>No se pudo calcular el consenso.</p></div>;
  if (!d) return <div className="card"><p className="muted" style={{ margin: 0 }}>Agrupando afirmaciones por significado…</p></div>;
  if (d.groups.length === 0) return <div className="card"><p className="muted" style={{ margin: 0 }}>Aún no hay afirmaciones.</p></div>;
  return (
    <div className="card">
      {!d.grouped_by_meaning && <p className="muted">Sin el modelo de significado instalado, cada afirmación aparece sola.</p>}
      {d.groups.map((g, i) => (
        <div key={i} style={{ padding: "8px 0", borderTop: i ? "1px solid var(--border)" : "none" }}>
          <div className="row" style={{ alignItems: "flex-start" }}>
            <strong>{g.statement}</strong>
            <span style={{ whiteSpace: "nowrap" }}>
              <Badge text={LEVEL[g.evidence_level].label} color={LEVEL[g.evidence_level].color} />
              {g.checked_against_studies.includes("contradicts") && <> <Badge text="Un estudio lo contradice" color="#c62828" /></>}
              {g.checked_against_studies.includes("supports") && g.studies.length === 0 && <> <Badge text="Un estudio lo respalda" color="#2e7d32" /></>}
              {g.disputed && <> <Badge text="En disputa" color="#c62828" /></>}
            </span>
          </div>
          <div className="muted" style={{ fontSize: "0.85rem" }}>
            {g.count} afirmación(es) en {g.sources} fuente(s)
            {g.teachers.length > 0 && <> · profesores: {g.teachers.join(", ")}</>}
            {g.studies.length > 0 && <> · estudios: {g.studies.map((s, j) => <span key={s.id}>{j ? ", " : ""}<Link href={`/studies/${s.id}`}>{s.label}</Link></span>)}</>}
          </div>
          <div className="muted" style={{ fontSize: "0.8rem" }} title={g.evidence_text}>{g.evidence_text}</div>
          {g.count > 1 && (
            <details style={{ fontSize: "0.9rem" }}>
              <summary>Ver las {g.count} afirmaciones</summary>
              <ul style={{ margin: "4px 0" }}>{g.claims.map((c) => <li key={c.id}>{c.text} <span className="muted">— {c.perspective.label}</span></li>)}</ul>
            </details>
          )}
        </div>
      ))}
      <p className="muted" style={{ fontSize: "0.8rem", marginBottom: 0 }}>{d.note}</p>
    </div>
  );
}
