"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import { sourceHref } from "@/lib/knowledge";

type Item = {
  segment_id: string; similarity: number; text: string; source_id: string; source_title: string;
  version_id: string; mime: string; where: string; page: number | null; t_ms: number | null;
  sections: { key: string; name: string }[];
};

/** «Ver también»: passages of other sources that say the same (also in another language). */
export function SeeAlso({ segmentId }: { segmentId: string }) {
  const [open, setOpen] = useState(false);
  const [data, setData] = useState<{ available: boolean; items: Item[] } | null>(null);
  const [err, setErr] = useState(false);
  async function toggle() {
    setOpen(!open);
    if (!data && !open) api<{ available: boolean; items: Item[] }>(`/api/segments/${segmentId}/similar`).then(setData).catch(() => setErr(true));
  }
  return (
    <div style={{ marginTop: 6 }}>
      <button onClick={toggle} aria-expanded={open}>{open ? "Ocultar" : "Ver también en otras fuentes"}</button>
      {open && (
        <div style={{ marginTop: 6 }}>
          {err && <p className="error">No se pudo cargar.</p>}
          {!data && !err && <p className="muted">Buscando…</p>}
          {data && !data.available && <p className="muted">Este fragmento aún no tiene vector (búsqueda por significado pendiente).</p>}
          {data && data.available && data.items.length === 0 && <p className="muted">Ninguna otra fuente dice algo parecido.</p>}
          {data?.items.map((i) => (
            <blockquote key={i.segment_id} style={{ margin: "6px 0", paddingLeft: 10, borderLeft: "3px solid var(--border)" }}>
              {i.text.length > 400 ? i.text.slice(0, 400) + "…" : i.text}
              <div className="muted" style={{ fontSize: "0.85rem" }}>
                <a href={sourceHref(i)} target="_blank" rel="noopener">{i.source_title}</a>
                {i.where ? ` · ${i.where}` : ""} · {i.sections.map((s) => s.name).join(", ")} · parecido {Math.round(i.similarity * 100)} %
              </div>
            </blockquote>
          ))}
        </div>
      )}
    </div>
  );
}
