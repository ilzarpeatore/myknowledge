"use client";

import { useEffect, useState } from "react";
import { Header } from "@/components/Header";
import { api } from "@/lib/api";
import { useMe } from "@/lib/useMe";

type Check = { key: string; label: string; status: "ok" | "warn" | "fail"; detail: string };
type Health = {
  checks: Check[]; alerts_configured: boolean;
  workers: { id: string; last_seen: string; info: Record<string, unknown> }[];
  events: { kind: string; detail: Record<string, unknown>; created_at: string }[];
};
type Job = { id: string; kind: string; status: string; progress: number; last_error: string | null; created_at: string };

const COLOR = { ok: "#2e7d32", warn: "#b26a00", fail: "#c62828" };
const MARK = { ok: "Correcto", warn: "Revisar", fail: "Fallo" };
const CAPS: Record<string, string> = {
  docling: "Docling (tablas, DOCX/PPTX/EPUB)", ocr: "OCR (PDF escaneados)", transcription: "Transcripción (ffmpeg + Whisper)",
  whisper_model: "Modelo de voz descargado", embeddings: "Búsqueda por significado",
};
const JOB_STATUS: Record<string, string> = { pending: "en cola", processing: "en curso", completed: "hecho", failed: "fallido", cancelled: "cancelado" };
const EVENTS: Record<string, string> = { backup_ok: "Copia de seguridad", alert_state: "Aviso", reminder: "Recordatorio de repaso" };

export default function SystemPage() {
  const user = useMe();
  const [h, setH] = useState<Health | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  useEffect(() => {
    if (!user) return;
    const load = () => {
      api<Health>("/api/system/health").then(setH).catch(() => {});
      api<Job[]>("/api/jobs?limit=20").then(setJobs).catch(() => {});
    };
    load();
    const t = setInterval(load, 15000);
    return () => clearInterval(t);
  }, [user]);

  return (
    <main>
      <Header user={user} />
      <h1>Sistema</h1>
      {!h ? <p className="muted">Cargando…</p> : (
        <>
          <div className="card">
            {h.checks.map((c) => (
              <div key={c.key} className="row" style={{ justifyContent: "flex-start", gap: 10, padding: "4px 0" }}>
                <span className="badge" style={{ color: COLOR[c.status], borderColor: COLOR[c.status], minWidth: 70, textAlign: "center" }}>{MARK[c.status]}</span>
                <strong style={{ minWidth: 180 }}>{c.label}</strong>
                <span className="muted">{c.detail}</span>
              </div>
            ))}
            <p className="muted" style={{ fontSize: "0.85rem", marginBottom: 0 }}>
              {h.alerts_configured ? "Los avisos se envían a la dirección configurada (KOS_ALERT_URL) cuando algo cambia." : "Avisos desactivados: configura KOS_ALERT_URL (por ejemplo un tema de ntfy) y la tarea programada de DEPLOYMENT.md."}
            </p>
          </div>

          <h2>Workers</h2>
          <div className="card">
            {h.workers.length === 0 ? <p className="muted">Ninguno ha dado señales de vida.</p> : h.workers.map((w) => (
              <div key={w.id} style={{ marginBottom: 6 }}>
                <strong>{w.id}</strong> <span className="muted">· última señal {new Date(w.last_seen).toLocaleString("es-ES")}</span>
                <div className="muted" style={{ fontSize: "0.85rem" }}>
                  {Object.entries(CAPS).map(([k, label]) => (
                    <span key={k} style={{ marginRight: 12 }}>{w.info[k] === true ? "✓" : w.info[k] === false ? "✗" : "?"} {label}</span>
                  ))}
                </div>
              </div>
            ))}
          </div>

          <h2>Trabajos recientes</h2>
          <div className="card table-wrap">
            <table><tbody>
              {jobs.map((j) => (
                <tr key={j.id}>
                  <td>{j.kind}</td><td>{JOB_STATUS[j.status] ?? j.status}{j.status === "processing" ? ` ${Math.round(j.progress * 100)} %` : ""}</td>
                  <td className="muted">{new Date(j.created_at).toLocaleString("es-ES")}</td>
                  <td className="error" style={{ fontSize: "0.85rem", maxWidth: 420 }}>{j.last_error}</td>
                </tr>
              ))}
            </tbody></table>
          </div>

          <h2>Registro</h2>
          <div className="card">
            {h.events.length === 0 ? <p className="muted">Sin eventos todavía (copias, avisos, recordatorios).</p> : h.events.map((e, i) => (
              <div key={i} className="muted" style={{ fontSize: "0.9rem" }}>
                {new Date(e.created_at).toLocaleString("es-ES")} · {EVENTS[e.kind] ?? e.kind} · {Object.entries(e.detail).map(([k, v]) => `${k}: ${Array.isArray(v) ? v.join(", ") || "—" : String(v)}`).join(" · ")}
              </div>
            ))}
          </div>
        </>
      )}
    </main>
  );
}
