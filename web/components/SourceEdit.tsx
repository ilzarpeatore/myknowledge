"use client";

import { useState } from "react";
import { api, ApiError } from "@/lib/api";
import type { Section } from "@/lib/sections";
import type { Discipline } from "@/lib/disciplines";
import { BranchPicker } from "@/components/BranchPicker";

type Editable = { id: string; title: string; authors_text: string | null; sections: string[]; disciplines?: { id: string; name: string }[] };

/** Edit a source by hand: title, authors and the library sections it belongs to
 *  (e.g. move a PDF that arrived in Recursos to Libros). Sections chosen here are
 *  marked as yours, so automatic placements never undo them. */
export function SourceEdit({ source, sections, onDone, branches = [], onBranchesChanged }: {
  source: Editable; sections: Section[]; onDone: () => void; branches?: Discipline[]; onBranchesChanged?: () => void;
}) {
  const initialBranches = (source.disciplines ?? []).map((d) => d.id);
  const [picked2, setPicked2] = useState<string[]>(initialBranches);
  const sameBranches = picked2.length === initialBranches.length && picked2.every((k) => initialBranches.includes(k));
  const [title, setTitle] = useState(source.title);
  const [authors, setAuthors] = useState(source.authors_text ?? "");
  const [picked, setPicked] = useState<string[]>(source.sections);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const toggle = (key: string) =>
    setPicked(picked.includes(key) ? picked.filter((k) => k !== key) : [...picked, key]);

  const sameSections =
    picked.length === source.sections.length && picked.every((k) => source.sections.includes(k));
  const changed =
    title.trim() !== source.title || authors.trim() !== (source.authors_text ?? "") || !sameSections || !sameBranches;

  async function save(e: React.FormEvent) {
    e.preventDefault();
    if (!title.trim()) { setMsg({ ok: false, text: "El título no puede quedar vacío." }); return; }
    if (picked.length === 0) { setMsg({ ok: false, text: "Elige al menos una sección." }); return; }
    setBusy(true);
    try {
      if (title.trim() !== source.title || authors.trim() !== (source.authors_text ?? "")) {
        await api(`/api/sources/${source.id}`, {
          method: "PATCH",
          body: JSON.stringify({ title: title.trim(), authors_text: authors.trim() || null }),
        });
      }
      if (!sameSections) {
        await api(`/api/sources/${source.id}/sections`, { method: "PUT", body: JSON.stringify({ sections: picked }) });
      }
      if (!sameBranches) {
        await api(`/api/sources/${source.id}/disciplines`, { method: "PUT", body: JSON.stringify({ discipline_ids: picked2 }) });
      }
      setMsg({ ok: true, text: "Guardado." });
      onDone();
    } catch (err) {
      setMsg({ ok: false, text: err instanceof ApiError ? err.message : "No se pudo guardar." });
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={save}>
      <div className="row" style={{ justifyContent: "flex-start", gap: 6, flexWrap: "wrap" }}>
        <label style={{ flex: "2 1 280px", margin: 0 }}>
          Título
          <input value={title} onChange={(e) => setTitle(e.target.value)} maxLength={500} />
        </label>
        <label style={{ flex: "1 1 220px", margin: 0 }}>
          Autores
          <input value={authors} onChange={(e) => setAuthors(e.target.value)} maxLength={2000} placeholder="opcional" />
        </label>
      </div>
      <p style={{ marginBottom: 4 }}>Sección <span className="muted" style={{ fontSize: "0.85rem" }}>(puede estar en varias a la vez)</span></p>
      <div className="row" style={{ justifyContent: "flex-start", gap: 6, flexWrap: "wrap" }}>
        {sections.map((x) => (
          <button type="button" key={x.key} className={picked.includes(x.key) ? "primary" : ""}
            onClick={() => toggle(x.key)} aria-pressed={picked.includes(x.key)}>
            {x.name}
          </button>
        ))}
      </div>
      <p style={{ marginBottom: 4 }}>Rama <span className="muted" style={{ fontSize: "0.85rem" }}>(Nutrición, Biomecánica…; puede tener varias)</span></p>
      <BranchPicker all={branches} value={picked2} onChange={setPicked2} onCreated={() => onBranchesChanged?.()} />
      <div className="row" style={{ justifyContent: "flex-start", gap: 6, marginTop: 10 }}>
        <button className="primary" type="submit" disabled={busy || !changed}>{busy ? "Guardando…" : "Guardar"}</button>
        {msg && <span className={msg.ok ? "ok" : "error"}>{msg.text}</span>}
      </div>
    </form>
  );
}
