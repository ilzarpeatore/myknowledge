"use client";

import { useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";

type Extraction = {
  id: string; status: "running" | "completed" | "stopped"; last_ordinal: number;
  stats: Record<string, number>; error: string | null; finished_at: string | null;
};
type State = {
  latest: Extraction | null; in_flight: boolean;
  estimate: { segments: number; calls: number; model: string | null; cost_usd: number | null; note: string };
};

const STAT: Record<string, string> = {
  concepts_new: "conceptos propuestos", concepts_linked: "enlazados a existentes",
  merge_reviews: "posibles duplicados a revisar", claims_new: "afirmaciones",
  claims_quote_not_found: "descartadas (cita no literal)", claims_duplicate: "ya existentes",
};

/** Button + panel to run the AI extraction of concepts and claims on one source.
 *  Shows the cost estimate before starting; results go to review, never straight in. */
export function ExtractKnowledge({ sourceId, onClose }: { sourceId: string; onClose: () => void }) {
  const open = true;
  const [st, setSt] = useState<State | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const load = () => api<State>(`/api/sources/${sourceId}/knowledge-extraction`).then(setSt).catch(() => {});
  useEffect(() => { if (open) load(); }, [open]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!open || !st?.in_flight) return;
    const t = setInterval(load, 3000);
    return () => clearInterval(t);
  }, [open, st?.in_flight]); // eslint-disable-line react-hooks/exhaustive-deps

  async function start(restart: boolean) {
    setErr(null);
    try {
      await api(`/api/sources/${sourceId}/knowledge-extraction`, { method: "POST", body: JSON.stringify({ restart }) });
      await load();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "No se pudo iniciar.");
    }
  }

  if (!st) return <span className="muted">Cargando…</span>;
  const l = st.latest;
  const e = st.estimate;
  const resumable = l && l.status !== "completed";
  return (
    <div style={{ textAlign: "left" }}>
      {st.in_flight ? (
        <p>Extrayendo… <span className="muted">(se actualiza solo)</span></p>
      ) : (
        <>
          <p style={{ marginTop: 0 }}>
            {l?.status === "completed" ? "Repetir" : resumable ? "Continuar" : "Extraer"}: {e.segments} fragmento(s), {e.calls} llamada(s)
            {e.cost_usd != null && <> · ≈ {e.cost_usd.toFixed(2)} USD</>}
            {e.model && <span className="muted"> ({e.model}, {e.note})</span>}
          </p>
          <div className="row" style={{ gap: 6 }}>
            {e.segments > 0 && l?.status !== "completed" && <button onClick={() => start(false)}>{resumable ? "Continuar" : "Iniciar"}</button>}
            {l && <button onClick={() => start(true)} title="Vuelve a procesar todo; no duplica lo ya extraído">Repetir completo</button>}
            <button onClick={onClose}>Cerrar</button>
          </div>
        </>
      )}
      {l && (
        <div className="muted" style={{ marginTop: 8 }}>
          Última: {l.status === "completed" ? "completada" : l.status === "stopped" ? "detenida" : "en curso"}
          {Object.entries(STAT).filter(([k]) => l.stats[k]).map(([k, label]) => (
            <div key={k}>{l.stats[k]} {label}</div>
          ))}
          {l.error && <div className="error">{l.error}</div>}
          {l.status === "completed" && <div>Revisa las propuestas en <a href="/review">Revisión</a> y <a href="/concepts?status=proposed">Conceptos</a>.</div>}
        </div>
      )}
      {err && <p className="error">{err}</p>}
    </div>
  );
}
