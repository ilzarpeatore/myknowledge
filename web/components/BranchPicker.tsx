"use client";

import { useState } from "react";
import { api, ApiError } from "@/lib/api";
import { ordered, type Discipline } from "@/lib/disciplines";

/** Pick the branches of a source (Nutrición, Biomecánica...). The areas come from the
 *  Disciplinas tree; roots with areas are shown as headings. Empty tree: offers to
 *  create the initial training-science branches. */
export function BranchPicker({ all, value, onChange, onCreated }: {
  all: Discipline[]; value: string[]; onChange: (ids: string[]) => void; onCreated: () => void;
}) {
  const [name, setName] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const toggle = (id: string) => onChange(value.includes(id) ? value.filter((x) => x !== id) : [...value, id]);
  const parents = new Set(all.map((d) => d.parent_id).filter(Boolean));

  async function seed() {
    try { await api("/api/disciplines/seed-training-science", { method: "POST" }); onCreated(); }
    catch (e) { setErr(e instanceof ApiError ? e.message : "No se pudo crear."); }
  }
  async function create(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;
    const root = all.find((d) => d.parent_id === null && parents.has(d.id));
    try {
      const r = await api<{ id: string }>("/api/disciplines", { method: "POST", body: JSON.stringify({ name: name.trim(), parent_id: root?.id ?? null }) });
      setName(""); onCreated(); onChange([...value, r.id]);
    } catch (x) { setErr(x instanceof ApiError ? x.message : "No se pudo crear."); }
  }

  if (all.length === 0) {
    return (
      <div className="row" style={{ justifyContent: "flex-start", gap: 6, flexWrap: "wrap" }}>
        <span className="muted">Aún no hay ramas.</span>
        <button type="button" onClick={seed}>Crear las de Ciencias del entrenamiento</button>
        {err && <span className="error">{err}</span>}
      </div>
    );
  }
  return (
    <div className="row" style={{ justifyContent: "flex-start", gap: 6, flexWrap: "wrap" }}>
      {ordered(all).map(({ d, depth }) =>
        parents.has(d.id) && depth === 0 ? (
          <strong key={d.id} style={{ fontSize: "0.85rem", width: "100%" }}>{d.name}</strong>
        ) : (
          <button type="button" key={d.id} className={value.includes(d.id) ? "primary" : ""} aria-pressed={value.includes(d.id)}
            onClick={() => toggle(d.id)} style={{ fontSize: "0.85rem", padding: "2px 10px" }}>
            {d.name}
          </button>
        ),
      )}
      <form onSubmit={create} className="row" style={{ gap: 4 }}>
        <input placeholder="+ nueva rama" value={name} onChange={(e) => setName(e.target.value)} style={{ width: 140, fontSize: "0.85rem" }} />
      </form>
      {err && <span className="error">{err}</span>}
    </div>
  );
}
