"use client";

import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { type Author, norm, type PersonRow, ROLE } from "@/lib/authors";

/** Choose the authors of a source: type and pick from matching people of the Autores
 *  library (as you type, ignoring accents), or add a new name. Keyboard: ↑ ↓ to move,
 *  Enter to choose, Backspace on an empty box removes the last one. Several names at
 *  once separated by «;». Order matters (first author first); ◂ moves one earlier. */
export function AuthorPicker({ value, onChange, placeholder = "Escribe un nombre…", single = false, exclude = [] }: {
  value: Author[]; onChange: (v: Author[]) => void; placeholder?: string; single?: boolean; exclude?: string[];
}) {
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<PersonRow[]>([]);
  const [hitsFor, setHitsFor] = useState("");  // the text those suggestions answer
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const box = useRef<HTMLInputElement>(null);
  const chosen = new Set([...value.map((a) => a.id ?? `n:${norm(a.name)}`), ...exclude]);

  const fetchHits = (text: string) =>
    api<PersonRow[]>(`/api/persons?${new URLSearchParams({ q: text, limit: "8" })}`).catch(() => [] as PersonRow[]);
  useEffect(() => {
    const text = q.split(";").pop()!.trim();
    if (!text) { setHits([]); setHitsFor(""); return; }
    let live = true;
    const t = setTimeout(() => {
      fetchHits(text).then((r) => { if (live) { setHits(r); setHitsFor(text); setActive(0); } });
    }, 150);
    return () => { live = false; clearTimeout(t); };
  }, [q]);

  type Option = { kind: "person"; p: PersonRow } | { kind: "new"; name: string };
  const optionsFor = (text: string, rows: PersonRow[]): Option[] => {
    const exact = rows.some((h) => norm(h.name) === norm(text)) || chosen.has(`n:${norm(text)}`);
    return [
      ...rows.filter((h) => !chosen.has(h.id)).map((p) => ({ kind: "person" as const, p })),
      ...(text && !exact ? [{ kind: "new" as const, name: text }] : []),
    ];
  };
  const text = q.split(";").pop()!.trim();
  const options = hitsFor === text ? optionsFor(text, hits) : [];  // nothing stale

  function add(items: Author[]) {
    const next = single ? items.slice(-1) : [...value];
    if (!single) for (const a of items) {
      const key = a.id ?? `n:${norm(a.name)}`;
      if (!next.some((b) => (b.id ?? `n:${norm(b.name)}`) === key)) next.push(a);
    }
    onChange(next);
    setQ(""); setHits([]); setOpen(false);
    box.current?.focus();
  }
  async function choose(i: number) {
    let o = options[i];
    if (hitsFor !== text) {  // Enter before the suggestions arrived: ask now, so an
      const fresh = await fetchHits(text);  // existing person is never duplicated
      o = optionsFor(text, fresh)[0];
    }
    if (!o) return;
    const earlier = q.includes(";") ? q.split(";").slice(0, -1).map((s) => s.trim()).filter(Boolean).map((name) => ({ name })) : [];
    add([...earlier, o.kind === "person" ? { id: o.p.id, name: o.p.name, role: o.p.role } : { name: o.name }]);
  }
  function key(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "ArrowDown") { e.preventDefault(); setOpen(true); setActive((a) => Math.min(a + 1, options.length - 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setActive((a) => Math.max(a - 1, 0)); }
    else if (e.key === "Enter") { if (q.trim()) { e.preventDefault(); choose(active); } }
    else if (e.key === "Escape") setOpen(false);
    else if (e.key === "Backspace" && !q && value.length) onChange(value.slice(0, -1));
  }
  const move = (i: number) => { const v = [...value]; [v[i - 1], v[i]] = [v[i], v[i - 1]]; onChange(v); };

  return (
    <div className="picker">
      {value.length > 0 && (
        <div className="row" style={{ justifyContent: "flex-start", gap: 4, marginBottom: 4 }}>
          {value.map((a, i) => (
            <span key={a.id ?? a.name} className="badge chip" title={a.id ? (ROLE[a.role ?? ""] ?? "En Autores") : "Nuevo: se añadirá a Autores al guardar"}>
              {!single && i > 0 && <button type="button" className="link" aria-label={`Mover ${a.name} antes`} onClick={() => move(i)}>◂</button>}
              {a.name}{!a.id && <em className="muted"> (nuevo)</em>}
              <button type="button" className="link" aria-label={`Quitar ${a.name}`} onClick={() => onChange(value.filter((_, j) => j !== i))}>✕</button>
            </span>
          ))}
        </div>
      )}
      {!(single && value.length) && (
        <div style={{ position: "relative" }}>
          <input ref={box} value={q} placeholder={placeholder} role="combobox" aria-expanded={open && options.length > 0} aria-autocomplete="list"
            onChange={(e) => { setQ(e.target.value); setOpen(true); }} onKeyDown={key}
            onFocus={() => setOpen(true)} onBlur={() => setTimeout(() => setOpen(false), 150)} />
          {open && options.length > 0 && (
            <ul className="suggest" role="listbox">
              {options.map((o, i) => (
                <li key={o.kind === "person" ? o.p.id : "new"} role="option" aria-selected={i === active}
                  className={i === active ? "active" : ""} onMouseDown={(e) => { e.preventDefault(); choose(i); }} onMouseEnter={() => setActive(i)}>
                  {o.kind === "person" ? (
                    <>{o.p.name} <span className="muted" style={{ fontSize: "0.8rem" }}>
                      {[ROLE[o.p.role ?? ""], o.p.sources ? `${o.p.sources} fuente${o.p.sources > 1 ? "s" : ""}` : null].filter(Boolean).join(" · ")}
                    </span></>
                  ) : <>Añadir <strong>«{o.name}»</strong> como autor nuevo</>}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
