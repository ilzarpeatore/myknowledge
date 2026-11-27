"use client";

import Link from "next/link";
import { api, ApiError } from "@/lib/api";
import { Badge, STUDY_STATUS, VERDICT, type StudyStatus } from "@/lib/analysis";

/** Load where each claim of a concept stands against the studies. */
export async function loadStudyStatus(entityId: string) {
  return api<{ claims: Record<string, StudyStatus>; summary: Record<string, number> }>(`/api/entities/${entityId}/study-status`);
}

/** Badge + the checks behind it, under each non-study claim. */
export function ClaimStudyStatus({ st, onChange, onMsg }: { st?: StudyStatus; onChange: () => void; onMsg: (ok: boolean, t: string) => void }) {
  if (!st || st.status === "study") return null;
  const s = STUDY_STATUS[st.status];
  async function review(id: string, status: string) {
    try { await api(`/api/claim-checks/${id}`, { method: "PATCH", body: JSON.stringify({ status }) }); onMsg(true, "Hecho."); onChange(); }
    catch (e) { onMsg(false, e instanceof ApiError ? e.message : "Error."); }
  }
  return (
    <div style={{ marginTop: 6, fontSize: "0.9rem" }}>
      <Badge text={s.label + (st.pending_review ? " (IA, por revisar)" : "")} color={s.color} />
      {st.checks.map((c) => (
        <div key={c.id} className="muted" style={{ marginTop: 4 }}>
          {c.publication ? <Link href={`/studies/${c.publication.id}`}>{c.publication.title}{c.publication.year ? ` (${c.publication.year})` : ""}</Link> : "Estudio"}{" "}
          <strong>{VERDICT[c.verdict]}</strong>: {c.explanation}
          {c.status === "proposed" ? (
            <> <button style={{ fontSize: "0.75rem", padding: "0 6px" }} onClick={() => review(c.id, "accepted")}>Correcto</button>{" "}
              <button style={{ fontSize: "0.75rem", padding: "0 6px" }} onClick={() => review(c.id, "rejected")}>No es así</button></>
          ) : <span> · {c.status === "accepted" ? "confirmado por ti" : c.status}</span>}
        </div>
      ))}
    </div>
  );
}

export function StudySummary({ entityId, summary, onMsg }: { entityId: string; summary: Record<string, number>; onMsg: (ok: boolean, t: string) => void }) {
  async function start() {
    try { await api(`/api/entities/${entityId}/study-checks`, { method: "POST" }); onMsg(true, "Comprobación en marcha (IA). Vuelve en un momento."); }
    catch (e) { onMsg(false, e instanceof ApiError ? e.message : "Error."); }
  }
  const keys = Object.keys(summary).filter((k) => k !== "study");
  return (
    <div className="row" style={{ justifyContent: "flex-start", gap: 6, flexWrap: "wrap", marginBottom: 8 }}>
      <span className="muted">Frente a los estudios:</span>
      {keys.length === 0 ? <span className="muted">no hay afirmaciones de profesores u otras fuentes.</span> :
        keys.map((k) => <Badge key={k} text={`${STUDY_STATUS[k].label}: ${summary[k]}`} color={STUDY_STATUS[k].color} />)}
      <button onClick={start}>Comprobar con estudios (IA)</button>
    </div>
  );
}
