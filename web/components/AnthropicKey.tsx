"use client";

import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";

type Status = {
  configured: boolean; source: "environment" | "app" | null; hint: string | null; updated_at: string | null;
  stored_unreadable: boolean; can_store: boolean;
};

/** Set the Anthropic API key from the web: stored encrypted, never shown again. */
export function AnthropicKey({ onChange }: { onChange?: () => void }) {
  const [st, setSt] = useState<Status | null>(null);
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const load = useCallback(() => api<Status>("/api/ai/credentials").then(setSt).catch(() => setSt(null)), []);
  useEffect(() => { load(); }, [load]);

  async function run(fn: () => Promise<unknown>, ok?: string) {
    setBusy(true); setMsg(null);
    try { await fn(); if (ok) setMsg({ ok: true, text: ok }); await load(); onChange?.(); }
    catch (e) { setMsg({ ok: false, text: e instanceof ApiError ? e.message : "Error." }); }
    finally { setBusy(false); }
  }
  async function save(e: React.FormEvent) {
    e.preventDefault();
    await run(() => api("/api/ai/credentials", { method: "PUT", body: JSON.stringify({ api_key: key }) }), "Clave guardada (cifrada). Pruébala.");
    setKey("");
  }
  async function test() {
    setBusy(true); setMsg(null);
    try {
      const r = await api<{ ok: boolean; error?: string; model?: string; cost_usd?: string }>("/api/ai/credentials/test", { method: "POST" });
      setMsg(r.ok ? { ok: true, text: `Funciona: ha respondido ${r.model} (coste ${Number(r.cost_usd).toFixed(5)} USD).` } : { ok: false, text: `No funciona: ${r.error}` });
    } catch (e) { setMsg({ ok: false, text: e instanceof ApiError ? e.message : "Error." }); }
    finally { setBusy(false); }
  }

  if (!st) return <p className="muted">Cargando…</p>;
  return (
    <div>
      <p style={{ marginTop: 0 }}>
        Clave de Anthropic:{" "}
        {st.configured ? <strong className="ok">configurada</strong> : <strong className="error">no configurada</strong>}
        {st.source === "environment" && <span className="muted"> (en el archivo .env del servidor, que tiene prioridad)</span>}
        {st.source === "app" && <span className="muted"> (guardada desde aquí, termina en …{st.hint}{st.updated_at ? `, ${new Date(st.updated_at).toLocaleDateString("es-ES")}` : ""})</span>}
      </p>
      {st.stored_unreadable && <p className="error">Hay una clave guardada que no se puede leer (cambió la clave maestra del servidor). Vuelve a introducirla.</p>}
      {!st.can_store ? (
        <p className="muted">Para guardar la clave desde aquí, el servidor necesita <code>KOS_SECRET_KEY</code> en su .env (ver DEPLOYMENT.md).</p>
      ) : st.source !== "environment" && (
        <form onSubmit={save} className="row" style={{ justifyContent: "flex-start", gap: 6, flexWrap: "wrap" }}>
          <input type="password" autoComplete="off" spellCheck={false} placeholder={st.source === "app" ? "Nueva clave (sustituye a la actual)" : "sk-ant-…"}
            value={key} onChange={(e) => setKey(e.target.value)} style={{ maxWidth: 380 }} aria-label="Clave de Anthropic" />
          <button className="primary" disabled={busy || key.trim().length < 20}>Guardar clave</button>
        </form>
      )}
      <div className="row" style={{ justifyContent: "flex-start", gap: 6, marginTop: 6 }}>
        {st.configured && <button onClick={test} disabled={busy}>Probar la conexión</button>}
        {st.source === "app" && <button onClick={() => { if (confirm("¿Quitar la clave guardada? La IA dejará de funcionar hasta que pongas otra.")) run(() => api("/api/ai/credentials", { method: "DELETE" }), "Clave quitada."); }} disabled={busy}>Quitar clave</button>}
      </div>
      {msg && <p className={msg.ok ? "ok" : "error"}>{msg.text}</p>}
      <p className="muted" style={{ fontSize: "0.85rem", marginBottom: 0 }}>
        Se guarda cifrada y no se vuelve a mostrar. Créala en console.anthropic.com con un límite de gasto, y no la pegues en chats ni correos.
      </p>
    </div>
  );
}
