"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Candidate } from "@/lib/knowledge";

export type ConceptRef = { id?: string; name: string };

/** Choose a concept as you type (existing ones, by name or alias), or keep the typed
 *  name: a new concept is created with it. */
export function ConceptInput({ value, onChange }: { value: ConceptRef | null; onChange: (v: ConceptRef | null) => void }) {
  const [q, setQ] = useState(value?.name ?? "");
  const [hits, setHits] = useState<Candidate[]>([]);
  const [open, setOpen] = useState(false);
  useEffect(() => {
    const text = q.trim();
    if (text.length < 2 || value?.id) { setHits([]); return; }
    const t = setTimeout(() => api<Candidate[]>(`/api/entities/candidates?${new URLSearchParams({ name: text })}`).then(setHits).catch(() => {}), 150);
    return () => clearTimeout(t);
  }, [q, value?.id]);
  return (
    <div style={{ position: "relative" }}>
      <input value={q} placeholder="Concepto (elige uno o escribe uno nuevo)" onFocus={() => setOpen(true)} onBlur={() => setTimeout(() => setOpen(false), 150)}
        onChange={(e) => { setQ(e.target.value); setOpen(true); onChange(e.target.value.trim() ? { name: e.target.value.trim() } : null); }} />
      {value?.id ? <div className="muted" style={{ fontSize: "0.8rem" }}>Concepto existente</div>
        : value?.name ? <div className="muted" style={{ fontSize: "0.8rem" }}>Si no lo eliges de la lista, se crea «{value.name}» (o se usa el que ya se llame así)</div> : null}
      {open && hits.length > 0 && (
        <ul className="suggest" role="listbox">
          {hits.slice(0, 8).map((h) => (
            <li key={h.id} role="option" aria-selected={false} onMouseDown={(e) => { e.preventDefault(); setQ(h.canonical_name); onChange({ id: h.id, name: h.canonical_name }); setOpen(false); }}>
              {h.canonical_name}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
