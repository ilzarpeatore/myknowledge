"use client";

import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";

type Token = { id: string; name: string; prefix: string; scopes: string; created_at: string; last_used_at: string | null; revoked_at: string | null };

/** Personal tokens for Claude (MCP). The token is shown only once, when created. */
export function McpTokens() {
  const [items, setItems] = useState<Token[]>([]);
  const [name, setName] = useState("");
  const [propose, setPropose] = useState(true);
  const [fresh, setFresh] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const load = useCallback(() => api<Token[]>("/api/tokens").then(setItems).catch(() => {}), []);
  useEffect(() => { load(); }, [load]);
  const url = typeof window !== "undefined" ? `${window.location.origin}/api/mcp` : "/api/mcp";

  async function create(e: React.FormEvent) {
    e.preventDefault();
    try {
      const r = await api<{ token: string }>("/api/tokens", { method: "POST", body: JSON.stringify({ name, propose }) });
      setFresh(r.token); setName(""); setErr(null); load();
    } catch (x) { setErr(x instanceof ApiError ? x.message : "No se pudo crear."); }
  }
  async function revoke(id: string) {
    await api(`/api/tokens/${id}`, { method: "DELETE" }).catch(() => {});
    load();
  }
  const date = (s: string | null) => (s ? new Date(s).toLocaleString("es-ES") : "nunca");

  return (
    <div className="card">
      <h2 style={{ marginTop: 0 }}>Conectar Claude (MCP)</h2>
      <p className="muted">
        Claude Code o Claude Desktop pueden consultar tu biblioteca (buscar, leer fragmentos, conceptos y comparaciones).
        Si das permiso de propuestas, lo que proponga (conceptos, afirmaciones con cita, relaciones) llega a <a href="/review">Revisión</a> y
        a los conceptos como pendiente: nunca se acepta solo.
      </p>
      <form className="row" style={{ justifyContent: "flex-start", gap: 8 }} onSubmit={create}>
        <input placeholder="Nombre (p. ej. Claude Code portátil)" value={name} onChange={(e) => setName(e.target.value)} required style={{ maxWidth: 320 }} />
        <label style={{ display: "flex", gap: 6, margin: 0 }}>
          <input type="checkbox" checked={propose} onChange={(e) => setPropose(e.target.checked)} style={{ width: "auto" }} />
          puede proponer cambios
        </label>
        <button className="primary">Crear token</button>
      </form>
      {err && <p className="error">{err}</p>}
      {fresh && (
        <div className="card" style={{ marginTop: 10 }}>
          <strong>Cópialo ahora: no se volverá a mostrar.</strong>
          <pre style={{ whiteSpace: "pre-wrap", wordBreak: "break-all" }}>{fresh}</pre>
          <p className="muted" style={{ marginBottom: 4 }}>Claude Code:</p>
          <pre style={{ whiteSpace: "pre-wrap", wordBreak: "break-all" }}>{`claude mcp add --transport http knowledge-os ${url} --header "Authorization: Bearer ${fresh}"`}</pre>
          <p className="muted" style={{ marginBottom: 4 }}>Claude Desktop (claude_desktop_config.json, con Node.js instalado):</p>
          <pre style={{ whiteSpace: "pre-wrap", wordBreak: "break-all" }}>{JSON.stringify({ mcpServers: { "knowledge-os": { command: "npx", args: ["-y", "mcp-remote", url, "--header", `Authorization:Bearer ${fresh}`] } } }, null, 2)}</pre>
          <button onClick={() => setFresh(null)}>Hecho</button>
        </div>
      )}
      {items.length > 0 && (
        <table style={{ marginTop: 10 }}>
          <thead><tr><th>Nombre</th><th>Token</th><th>Permisos</th><th>Último uso</th><th></th></tr></thead>
          <tbody>
            {items.map((t) => (
              <tr key={t.id} style={t.revoked_at ? { opacity: 0.5 } : undefined}>
                <td>{t.name}</td>
                <td><code>{t.prefix}…</code></td>
                <td>{t.scopes === "read,propose" ? "leer y proponer" : "solo leer"}</td>
                <td>{date(t.last_used_at)}</td>
                <td>{t.revoked_at ? "revocado" : <button onClick={() => revoke(t.id)}>Revocar</button>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
