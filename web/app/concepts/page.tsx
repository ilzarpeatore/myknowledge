"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Header } from "@/components/Header";
import { api, ApiError } from "@/lib/api";
import type { Candidate, EntitySummary, Types } from "@/lib/knowledge";
import { useMe } from "@/lib/useMe";

export default function ConceptsPage() {
  const user = useMe();
  const [types, setTypes] = useState<Types | null>(null);
  const [items, setItems] = useState<EntitySummary[]>([]);
  const [q, setQ] = useState("");
  const [typeFilter, setTypeFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("live");
  useEffect(() => {
    const s = new URLSearchParams(window.location.search).get("status");
    if (s) setStatusFilter(s);
  }, []);
  const [form, setForm] = useState({ type_key: "concept", name: "", definition: "", aliases: "" });
  const [cands, setCands] = useState<Candidate[]>([]);
  const [msg, setMsg] = useState<{ ok: boolean; text: string; slug?: string } | null>(null);

  useEffect(() => { if (user) api<Types>("/api/knowledge/types").then(setTypes); }, [user]);
  useEffect(() => {
    if (!user) return;
    const p = new URLSearchParams();
    if (q.trim()) p.set("q", q.trim());
    if (typeFilter) p.set("type_key", typeFilter);
    p.set("status_filter", statusFilter);
    const t = setTimeout(() => api<EntitySummary[]>(`/api/entities?${p}`).then(setItems), 200);
    return () => clearTimeout(t);
  }, [user, q, typeFilter, statusFilter, msg]);
  useEffect(() => {
    const name = form.name.trim();
    if (name.length < 3) { setCands([]); return; }
    const t = setTimeout(
      () => api<Candidate[]>(`/api/entities/candidates?${new URLSearchParams({ name })}`).then(setCands).catch(() => {}),
      300,
    );
    return () => clearTimeout(t);
  }, [form.name]);

  const typeLabel = (k: string) => types?.entity_types.find((t) => t.key === k)?.label ?? k;

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
    } catch (err) {
      setMsg({ ok: false, text: err instanceof ApiError ? err.message : "No se pudo crear." });
    }
  }

  return (
    <main>
      <Header user={user} />
      <h1>Conceptos</h1>

      <form className="card" onSubmit={create}>
        <strong>Nuevo</strong>
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
        {msg && (
          <p className={msg.ok ? "ok" : "error"}>
            {msg.text} {msg.slug && <Link href={`/concepts/${msg.slug}`}>Abrir</Link>}
          </p>
        )}
      </form>

      <div className="row" style={{ marginTop: 20 }}>
        <input placeholder="Buscar por nombre o alias…" value={q} onChange={(e) => setQ(e.target.value)} style={{ maxWidth: 320 }} />
        <select value={typeFilter} onChange={(e) => setTypeFilter(e.target.value)}>
          <option value="">Todos los tipos</option>
          {types?.entity_types.map((t) => <option key={t.key} value={t.key}>{t.label}</option>)}
        </select>
        <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
          <option value="live">Activos y propuestos</option>
          <option value="proposed">Solo propuestos (por revisar)</option>
          <option value="active">Solo activos</option>
        </select>
      </div>
      <div className="card table-wrap" style={{ marginTop: 12 }}>
        {items.length === 0 ? <p className="muted">No hay conceptos{q || typeFilter ? " con ese filtro" : " todavía"}.</p> : (
          <table>
            <thead><tr><th>Nombre</th><th>Tipo</th><th>Afirmaciones</th><th>Relaciones</th><th>Estado</th></tr></thead>
            <tbody>
              {items.map((e) => (
                <tr key={e.id}>
                  <td>
                    <Link href={`/concepts/${e.slug}`}>{e.canonical_name}</Link>
                    {e.short_definition && <div className="muted">{e.short_definition}</div>}
                  </td>
                  <td>{typeLabel(e.type_key)}</td>
                  <td>{e.claims}</td>
                  <td>{e.relationships}</td>
                  <td><span className="badge">{e.status === "proposed" ? "propuesto" : "activo"}{e.origin === "ai" ? " · IA" : ""}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </main>
  );
}
