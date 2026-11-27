"use client";

import cytoscape, { type Core, type ElementDefinition } from "cytoscape";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import { Header } from "@/components/Header";
import { api, ApiError } from "@/lib/api";
import type { Candidate, Types } from "@/lib/knowledge";
import { useMe } from "@/lib/useMe";

type GNode = { id: string; slug: string; name: string; type_key: string; status: string; center: boolean; degree: number };
type GEdge = { id: string; source: string; target: string; type_key: string; label: string; status: string; origin: string };
type GraphData = { nodes: GNode[]; edges: GEdge[]; truncated: boolean };
type Option = { id: string; name: string };

// One colour per family of entity types (legend below the graph).
const COLORS: Record<string, string> = {
  concept: "#2563eb", principle: "#7c3aed", theory: "#9333ea", model: "#9333ea", mechanism: "#dc2626",
  methodology: "#0d9488", application: "#0d9488", case: "#0d9488", variable: "#ea580c",
  author: "#65a30d", institution: "#65a30d", study: "#ca8a04", comparison: "#64748b",
  synthesis: "#64748b", open_question: "#db2777",
};

function GraphView() {
  const user = useMe();
  const router = useRouter();
  const params = useSearchParams();
  const entity = params.get("entity");
  const box = useRef<HTMLDivElement>(null);
  const cy = useRef<Core | null>(null);
  const expansions = useRef<Map<string, Set<string>>>(new Map());
  const [types, setTypes] = useState<Types | null>(null);
  const [disciplines, setDisciplines] = useState<Option[]>([]);
  const [persons, setPersons] = useState<Option[]>([]);
  const [sources, setSources] = useState<Option[]>([]);
  const [f, setF] = useState({ depth: 1, types: [] as string[], proposed: true, discipline: "", person: "", source: "" });
  const [selected, setSelected] = useState<GNode | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [cands, setCands] = useState<Candidate[]>([]);

  useEffect(() => {
    if (!user) return;
    api<Types>("/api/knowledge/types").then(setTypes);
    api<{ id: string; name: string }[]>("/api/disciplines").then(setDisciplines);
    api<{ id: string; name: string }[]>("/api/persons").then(setPersons);
    api<{ items: { id: string; title: string }[] }>("/api/sources?limit=200").then((r) => setSources(r.items.map((s) => ({ id: s.id, name: s.title }))));
  }, [user]);

  const query = useCallback((center: string, depth: number) => {
    const p = new URLSearchParams({ entity: center, depth: String(depth), include_proposed: String(f.proposed) });
    f.types.forEach((t) => p.append("types", t));
    if (f.discipline) p.append("discipline", f.discipline);
    if (f.person) p.append("person", f.person);
    if (f.source) p.append("source", f.source);
    return api<GraphData>(`/api/graph/neighbourhood?${p}`);
  }, [f]);

  const toElements = (g: GraphData): ElementDefinition[] => [
    ...g.nodes.map((n) => ({ group: "nodes" as const, data: { ...n, color: COLORS[n.type_key] ?? "#475569" } })),
    ...g.edges.map((e) => ({ group: "edges" as const, data: { ...e } })),
  ];

  // Rings around the centre: hop distance from the central concept decides the ring.
  const layout = () => {
    const c = cy.current;
    if (!c) return;
    const center = c.nodes("[?center]");
    const dist = new Map<string, number>();
    if (center.length) {
      c.elements().bfs({ roots: center, directed: false, visit: (v, _e, _u, _i, depth) => { dist.set(v.id(), depth); } });
    }
    const max = Math.max(0, ...dist.values());
    c.layout({
      name: "concentric", animate: false, minNodeSpacing: 70, spacingFactor: 1.15, avoidOverlap: true,
      concentric: (n) => max - (dist.get(n.id()) ?? max + 1) + 1, levelWidth: () => 1,
    }).run();
  };

  // (Re)load the whole view around the centre.
  useEffect(() => {
    if (!user || !entity || !box.current) return;
    let cancelled = false;
    (async () => {
      let centerId = entity;
      if (!/^[0-9a-f-]{36}$/.test(entity)) {
        try { centerId = (await api<{ id: string }>(`/api/entities/${encodeURIComponent(entity)}`)).id; }
        catch { setInfo("Concepto no encontrado."); return; }
      }
      try {
        const g = await query(centerId, f.depth);
        if (cancelled) return;
        expansions.current = new Map();
        cy.current?.destroy();
        cy.current = cytoscape({
          container: box.current,
          elements: toElements(g),
          wheelSensitivity: 0.3,
          style: [
            { selector: "node", style: { "background-color": "data(color)", label: "data(name)", color: "#111827", "font-size": 11,
              "text-valign": "bottom", "text-margin-y": 4, width: 18, height: 18, "text-wrap": "wrap", "text-max-width": "120px",
              "text-background-color": "#ffffff", "text-background-opacity": 0.8, "text-background-padding": "2px" } },
            { selector: "node[?center]", style: { width: 30, height: 30, "border-width": 3, "border-color": "#111827", "font-weight": "bold" } },
            { selector: "node:selected", style: { "border-width": 3, "border-color": "#f59e0b" } },
            { selector: "edge", style: { width: 1.5, "line-color": "#94a3b8", "target-arrow-color": "#94a3b8", "target-arrow-shape": "triangle",
              "curve-style": "bezier", label: "data(label)", "font-size": 9, color: "#475569", "text-rotation": "autorotate",
              "text-background-color": "#ffffff", "text-background-opacity": 0.8 } },
            { selector: "edge[status = 'proposed']", style: { "line-style": "dashed" } },
          ],
        });
        cy.current.on("tap", "node", (ev) => setSelected(ev.target.data() as GNode));
        cy.current.on("tap", (ev) => { if (ev.target === cy.current) setSelected(null); });
        layout();
        setSelected(null);
        setInfo(`${g.nodes.length} conceptos, ${g.edges.length} relaciones${g.truncated ? " (vista recortada: usa filtros o menos profundidad)" : ""}.`);
      } catch (err) {
        setInfo(err instanceof ApiError ? err.message : "No se pudo cargar el grafo.");
      }
    })();
    return () => { cancelled = true; };
  }, [user, entity, query, f.depth]);

  useEffect(() => () => cy.current?.destroy(), []);

  useEffect(() => {
    if (q.trim().length < 2) { setCands([]); return; }
    const t = setTimeout(() => api<Candidate[]>(`/api/entities/candidates?${new URLSearchParams({ name: q })}`).then(setCands).catch(() => {}), 250);
    return () => clearTimeout(t);
  }, [q]);

  async function expand(n: GNode) {
    if (!cy.current) return;
    const g = await query(n.id, 1);
    const added = new Set<string>();
    for (const el of toElements(g)) {
      const id = String(el.data.id);
      if (cy.current.getElementById(id).length === 0) {
        cy.current.add(el);
        if (el.group === "nodes") added.add(id);
      }
    }
    expansions.current.set(n.id, added);
    layout();
    setInfo(`+${added.size} conceptos alrededor de «${n.name}».`);
  }

  function collapse(n: GNode) {
    const added = expansions.current.get(n.id);
    if (!cy.current || !added) return;
    // Keep nodes that another expansion also brought in.
    const keep = new Set([...expansions.current.entries()].filter(([k]) => k !== n.id).flatMap(([, s]) => [...s]));
    added.forEach((id) => { if (!keep.has(id)) cy.current!.getElementById(id).remove(); });
    expansions.current.delete(n.id);
    setInfo(`Contraído «${n.name}».`);
  }

  function exportPng() {
    if (!cy.current) return;
    const a = document.createElement("a");
    a.href = cy.current.png({ full: true, scale: 2, bg: "#ffffff" });
    a.download = `grafo-${selected?.slug ?? entity}.png`;
    a.click();
  }

  const toggleType = (k: string) => setF({ ...f, types: f.types.includes(k) ? f.types.filter((t) => t !== k) : [...f.types, k] });

  return (
    <main style={{ maxWidth: 1200 }}>
      <Header user={user} />
      <h1>Grafo de conocimiento</h1>
      <div className="row" style={{ alignItems: "flex-start" }}>
        <div style={{ flex: 1, minWidth: 240 }}>
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Concepto central…" />
          {cands.map((c) => (
            <div key={c.id}><button onClick={() => { setQ(""); setCands([]); router.push(`/graph?entity=${c.id}`); }}>{c.canonical_name}</button></div>
          ))}
        </div>
        <label className="muted" style={{ margin: 0 }}>Profundidad{" "}
          <select value={f.depth} onChange={(e) => setF({ ...f, depth: Number(e.target.value) })}>
            {[1, 2, 3].map((d) => <option key={d} value={d}>{d}</option>)}
          </select>
        </label>
        <label className="muted" style={{ margin: 0 }}>
          <input type="checkbox" style={{ width: "auto" }} checked={f.proposed} onChange={(e) => setF({ ...f, proposed: e.target.checked })} /> Incluir propuestas
        </label>
      </div>
      <details style={{ margin: "8px 0" }}>
        <summary className="muted">Filtros</summary>
        <div className="card">
          <div className="row" style={{ justifyContent: "flex-start" }}>
            <select value={f.discipline} onChange={(e) => setF({ ...f, discipline: e.target.value })}>
              <option value="">Cualquier disciplina</option>
              {disciplines.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
            </select>
            <select value={f.person} onChange={(e) => setF({ ...f, person: e.target.value })}>
              <option value="">Cualquier autor</option>
              {persons.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
            </select>
            <select value={f.source} onChange={(e) => setF({ ...f, source: e.target.value })}>
              <option value="">Cualquier fuente</option>
              {sources.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
            </select>
          </div>
          <p className="muted" style={{ marginBottom: 4 }}>Tipos de relación (ninguno marcado = todos):</p>
          <div className="row" style={{ justifyContent: "flex-start", gap: 4 }}>
            {types?.relationship_types.map((t) => (
              <label key={t.key} className="badge" style={{ margin: 0, cursor: "pointer" }}>
                <input type="checkbox" style={{ width: "auto" }} checked={f.types.includes(t.key)} onChange={() => toggleType(t.key)} /> {t.label}
              </label>
            ))}
          </div>
        </div>
      </details>

      {!entity ? <p className="muted">Elige un concepto central para ver su contexto.</p> : (
        <div className="row" style={{ alignItems: "stretch", flexWrap: "wrap" }}>
          <div ref={box} className="card" style={{ flex: "1 1 600px", height: 560, padding: 0 }} aria-label="Grafo" />
          <div className="card" style={{ flex: "0 1 260px" }}>
            {selected ? (<>
              <strong>{selected.name}</strong>
              <p className="muted">{types?.entity_types.find((t) => t.key === selected.type_key)?.label} · {selected.degree} relaciones</p>
              <p><Link href={`/concepts/${selected.slug}`}>Abrir ficha</Link></p>
              <p><button onClick={() => expand(selected)}>Expandir</button>{" "}
                <button onClick={() => collapse(selected)} disabled={!expansions.current.has(selected.id)}>Contraer</button></p>
              <p><button onClick={() => router.push(`/graph?entity=${selected.id}`)}>Centrar aquí</button></p>
            </>) : <p className="muted">Toca un concepto para ver opciones. Rueda o pellizco para el zoom; arrastra para moverte.</p>}
            <hr />
            <button onClick={exportPng}>Descargar imagen</button>
            <p className="muted" style={{ fontSize: "0.8rem" }}>Línea discontinua: relación propuesta (pendiente de revisar).</p>
          </div>
        </div>
      )}
      {info && <p className="muted">{info}</p>}
      <div className="row" style={{ justifyContent: "flex-start", gap: 6, fontSize: "0.8rem" }}>
        {types?.entity_types.map((t) => (
          <span key={t.key}><span style={{ display: "inline-block", width: 10, height: 10, borderRadius: 5, background: COLORS[t.key] ?? "#475569" }} /> {t.label}</span>
        ))}
      </div>
    </main>
  );
}

export default function GraphPage() {
  return <Suspense><GraphView /></Suspense>;
}
