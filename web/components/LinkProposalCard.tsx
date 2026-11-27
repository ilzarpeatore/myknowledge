"use client";

import Link from "next/link";
import { api, ApiError } from "@/lib/api";
import { LINK_KIND, type LinkProposal, type Passage } from "@/lib/analysis";
import { sourceHref } from "@/lib/knowledge";

/** A proposed link between two sources, with the passages that justify it. */
export function LinkProposalCard({ lk, onDone, onMsg }: { lk: LinkProposal; onDone: () => void; onMsg: (ok: boolean, t: string) => void }) {
  async function set(body: Record<string, string>, ok: string) {
    try { await api(`/api/source-links/${lk.id}`, { method: "PATCH", body: JSON.stringify(body) }); onMsg(true, ok); onDone(); }
    catch (e) { onMsg(false, e instanceof ApiError ? e.message : "Error."); }
  }
  return (
    <div className="card" style={{ marginBottom: 8 }}>
      <strong>Enlace propuesto entre fuentes</strong>{" "}
      <span className="muted">({lk.origin === "auto" ? "pasajes parecidos" : lk.origin}{lk.score ? `, parecido ${Math.round(lk.score * 100)} %` : ""})</span>
      <p style={{ margin: "6px 0" }}>
        <Link href={`/sources/${lk.a.id}`}>{lk.a.title}</Link> ↔ <Link href={`/sources/${lk.b.id}`}>{lk.b.title}</Link>
      </p>
      <p style={{ margin: "4px 0" }}>
        Tipo: <select value={lk.kind} onChange={(e) => set({ kind: e.target.value }, "Tipo cambiado.")} style={{ maxWidth: 220, display: "inline-block" }}>
          {Object.entries(LINK_KIND).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
        </select>
        {lk.note && <span className="muted"> · {lk.note}</span>}
      </p>
      <details>
        <summary>Pasajes que lo justifican ({lk.evidence.length})</summary>
        {lk.evidence.map((e, i) => (
          <div key={i} className="grid" style={{ gridTemplateColumns: "1fr 1fr", gap: 8, marginTop: 6 }}>
            <PassageQuote src={lk.a} p={e.a} />
            <PassageQuote src={lk.b} p={e.b} />
          </div>
        ))}
      </details>
      <div className="row" style={{ justifyContent: "flex-start", gap: 6, marginTop: 6 }}>
        <button className="primary" onClick={() => set({ status: "accepted" }, "Enlace aceptado.")}>Aceptar enlace</button>
        <button onClick={() => set({ status: "rejected" }, "Descartado: no se volverá a proponer.")}>Descartar</button>
      </div>
    </div>
  );
}

function PassageQuote({ src, p }: { src: { id: string; title: string }; p: Passage | null }) {
  if (!p) return <div className="muted">(fragmento ya no disponible)</div>;
  return (
    <blockquote style={{ margin: 0, paddingLeft: 8, borderLeft: "3px solid var(--border)", fontSize: "0.9rem" }}>
      {p.text.length > 350 ? p.text.slice(0, 350) + "…" : p.text}
      <div className="muted">
        <a href={sourceHref({ source_id: src.id, ...p })} target="_blank" rel="noopener">{src.title}</a> {p.where}
      </div>
    </blockquote>
  );
}
