"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { Header } from "@/components/Header";
import { api, ApiError } from "@/lib/api";
import { useMe } from "@/lib/useMe";

type Detail = { id: string; slug: string; canonical_name: string };

export default function MindMapPage() {
  const user = useMe();
  const { slug } = useParams<{ slug: string }>();
  const svg = useRef<SVGSVGElement>(null);
  const [entity, setEntity] = useState<Detail | null>(null);
  const [empty, setEmpty] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!user) return;
    let mm: { destroy: () => void } | null = null;
    (async () => {
      try {
        const d = await api<Detail>(`/api/entities/${encodeURIComponent(slug)}`);
        setEntity(d);
        const res = await fetch(`/api/entities/${d.id}/mindmap.md`, { credentials: "same-origin" });
        if (!res.ok) throw new ApiError(res.status, "No se pudo generar el mapa.");
        const md = await res.text();
        setEmpty(!md.includes("\n## "));
        // Browser-only libraries (d3): loaded on demand.
        const [{ Transformer }, { Markmap }] = await Promise.all([import("markmap-lib"), import("markmap-view")]);
        const { root } = new Transformer().transform(md);
        if (svg.current) {
          svg.current.innerHTML = "";
          mm = Markmap.create(svg.current, { initialExpandLevel: 3, maxWidth: 320, duration: 300 }, root);
        }
      } catch (err) {
        setError(err instanceof ApiError ? err.message : "No se pudo generar el mapa.");
      }
    })();
    return () => mm?.destroy();
  }, [user, slug]);

  function downloadSvg() {
    if (!svg.current || !entity) return;
    const clone = svg.current.cloneNode(true) as SVGSVGElement;
    clone.setAttribute("xmlns", "http://www.w3.org/2000/svg");
    const { width, height } = svg.current.getBoundingClientRect();
    clone.setAttribute("width", String(Math.round(width)));
    clone.setAttribute("height", String(Math.round(height)));
    const blob = new Blob([new XMLSerializer().serializeToString(clone)], { type: "image/svg+xml" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `${entity.slug}-mapa.svg`;
    a.click();
    URL.revokeObjectURL(a.href);
  }

  return (
    <main style={{ maxWidth: 1200 }}>
      <Header user={user} />
      {entity && <p className="muted"><Link href={`/concepts/${entity.slug}`}>← {entity.canonical_name}</Link></p>}
      <div className="row">
        <h1>Mapa mental{entity ? `: ${entity.canonical_name}` : ""}</h1>
        {entity && (
          <span className="row" style={{ gap: 6 }}>
            <button onClick={downloadSvg}>Imagen (SVG)</button>
            <a className="badge" href={`/api/entities/${entity.id}/mindmap.md`}>Markdown</a>
            <a className="badge" href={`/api/entities/${entity.id}/mindmap.opml`}>OPML (XMind)</a>
          </span>
        )}
      </div>
      <p className="muted">
        Generado a partir de las relaciones y afirmaciones guardadas. Pulsa un nodo para plegar o desplegar sus ramas;
        rueda para el zoom. Markdown sirve para Obsidian (plugins de mapas mentales) y markmap; OPML se importa en XMind.
      </p>
      {error && <p className="error">{error}</p>}
      {empty && <p className="muted">Este concepto aún no tiene relaciones ni afirmaciones: el mapa solo muestra el centro.</p>}
      <div className="card" style={{ padding: 0 }}>
        <svg ref={svg} style={{ width: "100%", height: 620, display: "block" }} aria-label="Mapa mental" />
      </div>
    </main>
  );
}
