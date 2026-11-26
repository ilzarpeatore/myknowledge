"use client";

import { useCallback, useEffect, useState } from "react";
import { Header } from "@/components/Header";
import { McpTokens } from "@/components/McpTokens";
import { api } from "@/lib/api";
import { useMe } from "@/lib/useMe";

type Model = { name: string; input_usd_per_mtok: string; output_usd_per_mtok: string; supports_fallback: boolean; prices_as_of: string | null };
type Route = { task: string; model_name: string; effort: string; max_tokens: number };
type Run = {
  id: string; task: string; model_requested: string; model_served: string | null; status: string;
  input_tokens: number; output_tokens: number; cost_usd: string; error: string | null; created_at: string;
};
type Overview = {
  credentials_configured: boolean; month: string; spent_usd: string; limit_usd: string;
  models: Model[]; routes: Route[]; recent_runs: Run[];
};

const TASKS: Record<string, string> = {
  extract: "Extraer conceptos y afirmaciones", classify: "Clasificar", resolve: "Resolver duplicados",
  synthesize: "Redactar páginas", compare: "Comparar explicaciones", query: "Responder consultas",
  review: "Revisar calidad", check: "Comprobación de conexión",
};
const STATUS: Record<string, string> = {
  ok: "correcta", refused: "rechazada", invalid_output: "salida inválida", error: "error",
  budget_exceeded: "bloqueada por presupuesto",
};
const usd = (v: string | number) => `${Number(v).toFixed(4)} $`;

export default function AiPage() {
  const user = useMe();
  const [ov, setOv] = useState<Overview | null>(null);
  const [limit, setLimit] = useState("");
  const [msg, setMsg] = useState<string | null>(null);

  const load = useCallback(async () => {
    const o = await api<Overview>("/api/ai?runs=30");
    setOv(o);
    setLimit(Number(o.limit_usd).toFixed(2));
  }, []);
  useEffect(() => { if (user) load().catch(() => setMsg("No se pudo cargar.")); }, [user, load]);

  async function saveBudget() {
    await api("/api/ai/budget", { method: "PUT", body: JSON.stringify({ limit_usd: limit }) });
    setMsg("Presupuesto guardado."); load();
  }
  async function saveRoute(r: Route) {
    await api(`/api/ai/routes/${r.task}`, {
      method: "PUT", body: JSON.stringify({ model_name: r.model_name, effort: r.effort, max_tokens: r.max_tokens }),
    });
    setMsg(`Tarea «${TASKS[r.task] ?? r.task}» actualizada.`); load();
  }

  if (!ov) return <main><Header user={user} /><p className="muted">{msg ?? "Cargando…"}</p></main>;
  const pct = Math.min(100, (Number(ov.spent_usd) / Math.max(Number(ov.limit_usd), 1e-9)) * 100);

  return (
    <main>
      <Header user={user} />
      <h1>Inteligencia artificial</h1>
      {msg && <p className="ok">{msg}</p>}

      <div className="card">
        <p style={{ marginTop: 0 }}>
          Clave de Anthropic:{" "}
          {ov.credentials_configured
            ? <strong className="ok">configurada</strong>
            : <strong className="error">no configurada</strong>}
          {!ov.credentials_configured && <span className="muted"> — añade ANTHROPIC_API_KEY al archivo .env del servidor.</span>}
        </p>
        <p>Gasto de {ov.month}: <strong>{usd(ov.spent_usd)}</strong> de {usd(ov.limit_usd)} ({pct.toFixed(0)} %)</p>
        <div style={{ background: "var(--border)", borderRadius: 4, height: 8 }}>
          <div style={{ width: `${pct}%`, background: pct >= 90 ? "var(--danger)" : "var(--accent)", height: 8, borderRadius: 4 }} />
        </div>
        <div className="row" style={{ marginTop: 12, justifyContent: "flex-start" }}>
          <label style={{ margin: 0 }}>Límite mensual (USD)</label>
          <input style={{ maxWidth: 120 }} type="number" min="0" step="1" value={limit} onChange={(e) => setLimit(e.target.value)} />
          <button onClick={saveBudget}>Guardar</button>
        </div>
        <p className="muted" style={{ marginBottom: 0 }}>
          Antes de cada llamada se comprueba el gasto más el peor caso estimado; si supera el límite, la llamada no se hace.
        </p>
      </div>

      <h2>Modelo por tarea</h2>
      <div className="card table-wrap">
        <table>
          <thead><tr><th>Tarea</th><th>Modelo</th><th>Esfuerzo</th><th>Máx. tokens</th><th></th></tr></thead>
          <tbody>
            {ov.routes.map((r, i) => (
              <tr key={r.task}>
                <td>{TASKS[r.task] ?? r.task}</td>
                <td>
                  <select value={r.model_name} onChange={(e) => { const routes = [...ov.routes]; routes[i] = { ...r, model_name: e.target.value }; setOv({ ...ov, routes }); }}>
                    {ov.models.map((m) => (
                      <option key={m.name} value={m.name}>{m.name} ({Number(m.input_usd_per_mtok)} / {Number(m.output_usd_per_mtok)} $ por M)</option>
                    ))}
                  </select>
                </td>
                <td>
                  <select value={r.effort} onChange={(e) => { const routes = [...ov.routes]; routes[i] = { ...r, effort: e.target.value }; setOv({ ...ov, routes }); }}>
                    {["low", "medium", "high", "xhigh", "max"].map((x) => <option key={x}>{x}</option>)}
                  </select>
                </td>
                <td>{r.max_tokens}</td>
                <td><button onClick={() => saveRoute(r)}>Guardar</button></td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="muted" style={{ marginBottom: 0 }}>
          Precios de lista por millón de tokens (entrada / salida), actualizados a {ov.models[0]?.prices_as_of ?? "—"}.
        </p>
      </div>

      <h2>Últimas llamadas</h2>
      <div className="card table-wrap">
        {ov.recent_runs.length === 0 ? <p className="muted">Todavía no hay llamadas.</p> : (
          <table>
            <thead><tr><th>Fecha</th><th>Tarea</th><th>Modelo</th><th>Resultado</th><th>Tokens</th><th>Coste</th></tr></thead>
            <tbody>
              {ov.recent_runs.map((r) => (
                <tr key={r.id} title={r.error ?? ""}>
                  <td>{new Date(r.created_at).toLocaleString("es-ES")}</td>
                  <td>{TASKS[r.task] ?? r.task}</td>
                  <td>{r.model_served && r.model_served !== r.model_requested ? `${r.model_served} (respaldo)` : r.model_requested}</td>
                  <td className={r.status === "ok" ? "" : "error"}>{STATUS[r.status] ?? r.status}</td>
                  <td>{r.input_tokens} / {r.output_tokens}</td>
                  <td>{usd(r.cost_usd)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      <McpTokens />
    </main>
  );
}
