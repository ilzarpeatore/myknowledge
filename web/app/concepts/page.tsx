"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Header } from "@/components/Header";
import { api, ApiError } from "@/lib/api";
import { loadDisciplines, ordered, type Discipline } from "@/lib/disciplines";
import type { Candidate, EntitySummary, Types } from "@/lib/knowledge";
import { useMe } from "@/lib/useMe";

type Item = {
  id: string; slug: string; canonical_name: string; type_key: string; short_definition: string | null;
  status: string; origin: string; sources: number; courses: number; claims: number; relationships: number;
  importance: number; disciplines: { id: string; name: string }[];
};
type Page = {
  items: Item[]; total: number; page: number; page_size: number;
  facets: { disciplines: Record<string, number>; no_discipline: number; types: Record<string, number> };
};
type Filters = {
  q: string; status: string; type: string; branch: string; source: string; flags: string[]; sort: string; page: number;
};

const EMPTY: Filters = { q: "", status: "live", type: "", branch: "", source: "", flags: [], sort: "importance", page: 1 };
const PAGE_SIZE = 50;
const FLAGS: [string, string][] = [
  ["no_definition", "sin definición"], ["single_source", "en una sola fuente"],
  ["no_claims", "sin afirmaciones"], ["no_discipline", "sin rama"],
];

function fromUrl(): Filters {
  const p = new URLSearchParams(window.location.search);
  return {
    q: p.get("q") ?? "", status: p.get("status") ?? "live", type: p.get("type") ?? "",
    branch: p.get("branch") ?? "", source: p.get("source") ?? "", flags: p.getAll("flag"),
    sort: p.get("sort") ?? "importance", page: Number(p.get("page") ?? 1) || 1,
  };
}

function toQuery(f: Filters, forApi = false): URLSearchParams {
  const p = new URLSearchParams();
  if (f.q.trim()) p.set("q", f.q.trim());
  if (f.status !== "live") p.set(forApi ? "status_filter" : "status", f.status);
  if (f.type) p.set(forApi ? "type_key" : "type", f.type);
  if (f.branch) p.set(forApi ? "discipline" : "branch", f.branch);
  if (f.source) p.set(forApi ? "source_id" : "source", f.source);
  f.flags.forEach((x) => p.append("flag", x));
  if (f.sort !== "importance") p.set("sort", f.sort);
  if (f.page > 1) p.set("page", String(f.page));
  if (forApi) p.set("page_size", String(PAGE_SIZE));
  return p;
}

function NewConcept({ types, onCreated }: { types: Types | null; onCreated: () => void }) {
  const [form, setForm] = useState({ type_key: "concept", name: "", definition: "", aliases: "" });
  const [cands, setCands] = useState<Candidate[]>([]);
  const [msg, setMsg] = useState<{ ok: boolean; text: string; slug?: string } | null>(null);
  useEffect(() => {
    const name = form.name.trim();
    if (name.length < 3) { setCands([]); return; }
    const t = setTimeout(
      () => api<Candidate[]>(`/api/entities/candidates?${new URLSearchParams({ name })}`).then(setCands).catch(() => {}),
      300,
    );
    return () => clearTimeout(t);
  }, [form.name]);

  async function create(e: React.FormEvent) {
    e.preventDefault();
    try {
      const created = await api<EntitySummary>("/api/entities", {
        method: "POST",
        body: JSON.stringify({
          type_key: form.type_key, name: form.name, definition: form.definition || null,
          aliases: form.aliases.split(",").map((a) => a.trim()).filter(Boolean).map((alias) => ({ alias })),
        }),
      });
      setMsg({ ok: true, text: `Creado «${created.canonical_name}».`, slug: created.slug });
      setForm({ ...form, name: "", definition: "", aliases: "" });
      onCreated();
    } catch (err) {
      setMsg({ ok: false, text: err instanceof ApiError ? err.message : "No se pudo crear." });
    }
  }

  return (
    <details className="card" style={{ marginBottom: 12 }}>
      <summary><strong>Nuevo concepto</strong></summary>
      <form onSubmit={create}>
        <div className="row" style={{ alignItems: "flex-end" }}>
          <div style={{ flex: "0 0 180px" }}>
            <label>Tipo</label>
            <select value={form.type_key} onChange={(e) => setForm({ ...form, type_key: e.target.value })} style={{ width: "100%" }}>
              {types?.entity_types.map((t) => <option key={t.key} value={t.key}>{t.label}</option>)}
            </select>
          </div>
          <div style={{ flex: 1, minWidth: 200 }}>
            <label>Nombre</label>
            <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} required />
          </div>
        </div>
        {cands.length > 0 && (
          <p className="muted">
            Ya existen conceptos parecidos:{" "}
            {cands.map((c, i) => (
              <span key={c.id}>{i > 0 && ", "}<Link href={`/concepts/${c.id}`}>{c.canonical_name}</Link> ({Math.round(c.score * 100)} %)</span>
            ))}
            . Si es el mismo, añade el nombre como alias en lugar de crear otro.
          </p>
        )}
        <label>Definición breve (opcional)</label>
        <input value={form.definition} onChange={(e) => setForm({ ...form, definition: e.target.value })} />
        <label>Otros nombres o traducciones, separados por comas (opcional)</label>
        <input value={form.aliases} onChange={(e) => setForm({ ...form, aliases: e.target.value })} placeholder="p. ej. RIR, repetitions in reserve" />
        <p><button className="primary">Crear</button></p>
        {msg && <p className={msg.ok ? "ok" : "error"}>{msg.text} {msg.slug && <Link href={`/concepts/${msg.slug}`}>Abrir</Link>}</p>}
      </form>
    </details>
  );
}

export default function ConceptsPage() {
  const user = useMe();
  const [types, setTypes] = useState<Types | null>(null);
  const [branches, setBranches] = useState<Discipline[]>([]);
  const [sources, setSources] = useState<{ id: string; title: string; concepts: number }[]>([]);
  const [f, setF] = useState<Filters | null>(null);
  const [data, setData] = useState<Page | null>(null);
  const [sel, setSel] = useState<Set<string>>(new Set());
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [tick, setTick] = useState(0);
  const [target, setTarget] = useState({ branch: "", type: "", into: "" });

  useEffect(() => { setF(fromUrl()); }, []);
  useEffect(() => {
    if (!user) return;
    api<Types>("/api/knowledge/types").then(setTypes);
    loadDisciplines().then(setBranches).catch(() => {});
    api<{ id: string; title: string; concepts: number }[]>("/api/concepts/sources").then(setSources).catch(() => {});
  }, [user]);
  useEffect(() => {
    if (!user || !f) return;
    const qs = toQuery(f).toString();
    window.history.replaceState(null, "", qs ? `?${qs}` : window.location.pathname);
    const t = setTimeout(() => api<Page>(`/api/concepts?${toQuery(f, true)}`).then(setData)
      .catch((e) => setMsg({ ok: false, text: e instanceof ApiError ? e.message : "No se pudo cargar." })), 200);
    return () => clearTimeout(t);
  }, [user, f, tick]);

  const typeLabel = useCallback((k: string) => types?.entity_types.find((t) => t.key === k)?.label ?? k, [types]);
  const tree = useMemo(() => ordered(branches), [branches]);
  const set = (patch: Partial<Filters>) => { setF((old) => ({ ...(old ?? EMPTY), page: 1, ...patch })); setSel(new Set()); };
  const selected = data?.items.filter((x) => sel.has(x.id)) ?? [];

  async function bulk(action: string, extra: Record<string, string> = {}, ask?: string) {
    if (ask && !confirm(ask)) return;
    try {
      const r = await api<{ changed: number }>("/api/concepts/bulk", {
        method: "POST", body: JSON.stringify({ ids: [...sel], action, ...extra }),
      });
      setMsg({ ok: true, text: `${r.changed} concepto(s) cambiados.` });
      setSel(new Set());
      setTick((t) => t + 1);
    } catch (e) {
      setMsg({ ok: false, text: e instanceof ApiError ? e.message : "No se pudo aplicar." });
    }
  }

  if (!f) return <main><Header user={user} /></main>;
  const pages = data ? Math.max(1, Math.ceil(data.total / PAGE_SIZE)) : 1;
  const fc = data?.facets;
  const chip = (active: boolean): React.CSSProperties => ({
    cursor: "pointer", marginRight: 6, marginBottom: 6,
    ...(active ? { borderColor: "currentColor", color: "inherit", fontWeight: 600 } : {}),
  });
  const pageIds = data?.items.map((x) => x.id) ?? [];

  return (
    <main>
      <Header user={user} />
      <div className="row">
        <h1 style={{ marginBottom: 8 }}>Conceptos</h1>
        <span className="muted">{data ? `${data.total} con estos filtros` : ""}</span>
      </div>
      <NewConcept types={types} onCreated={() => setTick((t) => t + 1)} />

      <div className="card">
        <div style={{ marginBottom: 6 }}>
          <span className="badge" role="button" style={chip(!f.branch)} onClick={() => set({ branch: "" })}>Todas las ramas</span>
          {tree.filter(({ d }) => d.parent_id).map(({ d }) => (
            <span key={d.id} className="badge" role="button" style={chip(f.branch === d.id)} onClick={() => set({ branch: f.branch === d.id ? "" : d.id })}>
              {d.name} {fc?.disciplines[d.id] ?? 0}
            </span>
          ))}
          <span className="badge" role="button" style={chip(f.branch === "none")} onClick={() => set({ branch: f.branch === "none" ? "" : "none" })}>
            Sin rama {fc?.no_discipline ?? 0}
          </span>
        </div>
        <div className="row" style={{ justifyContent: "flex-start" }}>
          <input placeholder="Buscar por nombre o alias…" value={f.q} onChange={(e) => set({ q: e.target.value })} style={{ maxWidth: 260 }} />
          <select value={f.type} onChange={(e) => set({ type: e.target.value })}>
            <option value="">Todos los tipos</option>
            {types?.entity_types.filter((t) => fc?.types[t.key] || f.type === t.key).map((t) => (
              <option key={t.key} value={t.key}>{t.label} ({fc?.types[t.key] ?? 0})</option>
            ))}
          </select>
          <select value={f.source} onChange={(e) => set({ source: e.target.value })} style={{ maxWidth: 260 }}>
            <option value="">Todas las fuentes</option>
            {sources.map((s) => <option key={s.id} value={s.id}>{s.title} ({s.concepts})</option>)}
          </select>
          <select value={f.status} onChange={(e) => set({ status: e.target.value })}>
            <option value="live">Activos y propuestos</option>
            <option value="proposed">Por revisar</option>
            <option value="active">Aceptados</option>
            <option value="archived">Archivados</option>
          </select>
          <select value={f.sort} onChange={(e) => set({ sort: e.target.value })}>
            <option value="importance">Más importantes primero</option>
            <option value="name">Alfabético</option>
            <option value="recent">Más recientes</option>
          </select>
        </div>
        <div style={{ marginTop: 8 }}>
          {FLAGS.map(([k, label]) => (
            <label key={k} style={{ display: "inline-flex", gap: 4, margin: "0 12px 0 0", fontSize: "0.9rem" }}>
              <input type="checkbox" checked={f.flags.includes(k)}
                onChange={(e) => set({ flags: e.target.checked ? [...f.flags, k] : f.flags.filter((x) => x !== k) })} />
              {label}
            </label>
          ))}
        </div>
        <p className="muted" style={{ fontSize: "0.8rem", marginBottom: 0 }}>
          «Más importantes» pone arriba lo que tratan más fuentes y cursos, con más afirmaciones y relaciones; abajo queda el detalle (cada músculo, cada variante).
        </p>
      </div>

      {msg && <p className={msg.ok ? "ok" : "error"}>{msg.text}</p>}

      {sel.size > 0 && (
        <div className="card" style={{ marginTop: 12, position: "sticky", top: 0, zIndex: 2 }}>
          <div className="row" style={{ justifyContent: "flex-start" }}>
            <strong>{sel.size} seleccionado(s)</strong>
            <button onClick={() => bulk("accept")}>Aceptar</button>
            <button onClick={() => bulk("archive", {}, `¿Archivar ${sel.size} concepto(s)? Se recuperan desde «Archivados».`)}>Archivar</button>
            <span>
              <select value={target.branch} onChange={(e) => setTarget({ ...target, branch: e.target.value })}>
                <option value="">Rama…</option>
                {tree.map(({ d, depth }) => <option key={d.id} value={d.id}>{" ".repeat(depth * 3)}{d.name}</option>)}
              </select>{" "}
              <button disabled={!target.branch} onClick={() => bulk("add_discipline", { discipline_id: target.branch })}>Añadir a la rama</button>{" "}
              <button disabled={!target.branch} onClick={() => bulk("remove_discipline", { discipline_id: target.branch })}>Quitar</button>
            </span>
            <span>
              <select value={target.type} onChange={(e) => setTarget({ ...target, type: e.target.value })}>
                <option value="">Tipo…</option>
                {types?.entity_types.map((t) => <option key={t.key} value={t.key}>{t.label}</option>)}
              </select>{" "}
              <button disabled={!target.type} onClick={() => bulk("set_type", { type_key: target.type })}>Cambiar tipo</button>
            </span>
            {selected.length >= 2 && (
              <span>
                <select value={target.into} onChange={(e) => setTarget({ ...target, into: e.target.value })}>
                  <option value="">Fusionar en…</option>
                  {selected.map((x) => <option key={x.id} value={x.id}>{x.canonical_name}</option>)}
                </select>{" "}
                <button disabled={!target.into || !sel.has(target.into)} onClick={() => {
                  const keep = selected.find((x) => x.id === target.into)?.canonical_name;
                  bulk("merge", { into: target.into }, `¿Fusionar los otros ${sel.size - 1} en «${keep}»? Sus afirmaciones y nombres pasan a «${keep}». Se puede deshacer desde cada concepto fusionado.`);
                }}>Fusionar</button>
              </span>
            )}
            <button onClick={() => setSel(new Set())}>Quitar selección</button>
          </div>
        </div>
      )}

      <div className="card table-wrap" style={{ marginTop: 12 }}>
        {!data ? <p className="muted">Cargando…</p> : data.items.length === 0 ? <p className="muted">No hay conceptos con estos filtros.</p> : (
          <table>
            <thead>
              <tr>
                <th><input type="checkbox" aria-label="Seleccionar la página"
                  checked={pageIds.length > 0 && pageIds.every((id) => sel.has(id))}
                  onChange={(e) => setSel(e.target.checked ? new Set([...sel, ...pageIds]) : new Set([...sel].filter((id) => !pageIds.includes(id))))} /></th>
                <th>Nombre</th><th>Rama</th><th>Tipo</th>
                <th title="Fuentes de tu biblioteca que lo tratan">Fuentes</th>
                <th title="Afirmaciones con cita">Afirm.</th><th>Estado</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((e) => (
                <tr key={e.id}>
                  <td><input type="checkbox" aria-label={`Seleccionar ${e.canonical_name}`} checked={sel.has(e.id)}
                    onChange={(ev) => { const n = new Set(sel); if (ev.target.checked) n.add(e.id); else n.delete(e.id); setSel(n); }} /></td>
                  <td>
                    <Link href={`/concepts/${e.slug}`}>{e.canonical_name}</Link>
                    {e.short_definition && <div className="muted" style={{ fontSize: "0.85rem" }}>{e.short_definition}</div>}
                  </td>
                  <td>{e.disciplines.map((d) => <span key={d.id} className="badge" style={{ marginRight: 4 }}>{d.name}</span>)}</td>
                  <td>{typeLabel(e.type_key)}</td>
                  <td>{e.sources}{e.courses > 0 && <span className="muted" title="cursos cuyos materiales lo tratan"> · {e.courses} curso{e.courses > 1 ? "s" : ""}</span>}</td>
                  <td>{e.claims}</td>
                  <td><span className="badge">{e.status === "proposed" ? "por revisar" : e.status === "archived" ? "archivado" : "aceptado"}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {data && data.total > PAGE_SIZE && (
          <div className="row" style={{ marginTop: 12 }}>
            <button disabled={f.page <= 1} onClick={() => setF({ ...f, page: f.page - 1 })}>← Anteriores</button>
            <span className="muted">{(f.page - 1) * PAGE_SIZE + 1}–{Math.min(f.page * PAGE_SIZE, data.total)} de {data.total} · página {f.page} de {pages}</span>
            <button disabled={f.page >= pages} onClick={() => setF({ ...f, page: f.page + 1 })}>Siguientes →</button>
          </div>
        )}
      </div>
    </main>
  );
}
