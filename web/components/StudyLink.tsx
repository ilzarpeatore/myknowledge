"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import type { Study } from "@/lib/evidence";

/** Says which study a source contains. Claims cited only from this source become
 *  the study's claims (and stop being so if unlinked); nothing is deleted. */
export function StudyLink({ sourceId, current, onDone }: { sourceId: string; current: string | null; onDone: () => void }) {
  const [studies, setStudies] = useState<Study[] | null>(null);
  const [pick, setPick] = useState(current ?? "");
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  useEffect(() => { api<Study[]>("/api/publications").then(setStudies).catch(() => setStudies([])); }, []);

  async function save() {
    try {
      const r = await api<{ claims_changed: number }>(`/api/sources/${sourceId}/publication`, {
        method: "PUT", body: JSON.stringify({ publication_id: pick || null }),
      });
      setMsg({ ok: true, text: `Guardado. ${r.claims_changed} afirmación(es) actualizada(s).` });
      onDone();
    } catch (e) {
      setMsg({ ok: false, text: e instanceof ApiError ? e.message : "No se pudo guardar." });
    }
  }

  if (!studies) return <span className="muted">Cargando…</span>;
  return (
    <div>
      <p style={{ marginTop: 0 }}>¿Este documento es el texto de un estudio? Sus afirmaciones contarán como del estudio.</p>
      {studies.length === 0 ? (
        <p className="muted">Aún no hay estudios. <Link href="/studies">Añade uno</Link> (por DOI o a mano).</p>
      ) : (
        <div className="row" style={{ justifyContent: "flex-start", gap: 6 }}>
          <select value={pick} onChange={(e) => setPick(e.target.value)} style={{ maxWidth: 480 }}>
            <option value="">— No es un estudio —</option>
            {studies.map((s) => <option key={s.id} value={s.id}>{s.title}{s.year ? ` (${s.year})` : ""}</option>)}
          </select>
          <button className="primary" disabled={pick === (current ?? "")} onClick={save}>Guardar</button>
        </div>
      )}
      {msg && <p className={msg.ok ? "ok" : "error"}>{msg.text}</p>}
    </div>
  );
}
