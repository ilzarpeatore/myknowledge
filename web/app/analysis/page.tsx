"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { Header } from "@/components/Header";
import { LinkProposalCard } from "@/components/LinkProposalCard";
import { api } from "@/lib/api";
import type { LinkProposal } from "@/lib/analysis";
import { loadSections, type Section } from "@/lib/sections";
import { useMe } from "@/lib/useMe";

type Coverage = {
  entities: { id: string; name: string; slug: string; sources: number; claims: number; single_source: boolean }[];
  sources: { id: string; title: string; kind: string; sections: { key: string; name: string }[]; claims: number }[];
  cells: { entity_id: string; source_id: string; claims: number }[];
  totals: { entities: number; sources: number };
};
type Person = { id: string; name: string; role: string | null };

export default function AnalysisPage() {
  const user = useMe();
  const [cov, setCov] = useState<Coverage | null>(null);
  const [section, setSection] = useState("");
  const [sections, setSections] = useState<Section[]>([]);
  const [links, setLinks] = useState<LinkProposal[]>([]);
  const [people, setPeople] = useState<Person[]>([]);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const load = useCallback(async () => {
    setCov(await api<Coverage>(`/api/analysis/coverage${section ? `?section=${section}` : ""}`));
    setLinks(await api<LinkProposal[]>("/api/source-links?status=proposed"));
  }, [section]);
  useEffect(() => {
    if (!user) return;
    load().catch(() => {});
    loadSections().then(setSections).catch(() => {});
    api<Person[]>("/api/persons").then(setPeople).catch(() => {});
  }, [user, load]);

  const cell = new Map(cov?.cells.map((c) => [`${c.entity_id}|${c.source_id}`, c.claims]));
  const max = Math.max(1, ...(cov?.cells.map((c) => c.claims) ?? [1]));

  return (
    <main>
      <Header user={user} />
      <h1>Análisis</h1>
      <p className="muted">Cómo se cruza tu biblioteca: qué fuentes tratan cada concepto, qué enlaces entre fuentes se proponen y cómo se posiciona cada profesor.</p>
      {msg && <p className={msg.ok ? "ok" : "error"}>{msg.text}</p>}

      <div className="row"><h2>Mapa de cobertura</h2>
        <select value={section} onChange={(e) => setSection(e.target.value)} style={{ maxWidth: 220 }} aria-label="Sección">
          <option value="">Todas las secciones</option>
          {sections.map((s) => <option key={s.key} value={s.key}>{s.name}</option>)}
        </select>
      </div>
      <p className="muted" style={{ marginTop: 0 }}>
        Cada celda: afirmaciones de esa fuente sobre ese concepto (más oscuro = más). Arriba, los conceptos que tratan más fuentes;
        los marcados con ◦ solo aparecen en una (una sola voz).
      </p>
      <div className="card" style={{ overflowX: "auto" }}>
        {!cov || cov.entities.length === 0 ? <p className="muted" style={{ margin: 0 }}>Aún no hay afirmaciones con evidencia.</p> : (
          <table style={{ borderCollapse: "collapse", fontSize: "0.85rem" }}>
            <thead>
              <tr>
                <th />
                {cov.sources.map((s) => (
                  <th key={s.id} style={{ verticalAlign: "bottom", padding: 2, maxWidth: 36 }}>
                    <Link href={`/sources/${s.id}`} title={`${s.title} · ${s.sections.map((x) => x.name).join(", ")}`}
                      style={{ writingMode: "vertical-rl", transform: "rotate(180deg)", display: "inline-block", maxHeight: 160, overflow: "hidden", whiteSpace: "nowrap" }}>
                      {s.title}
                    </Link>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {cov.entities.map((e) => (
                <tr key={e.id}>
                  <td style={{ padding: "2px 8px 2px 0", whiteSpace: "nowrap" }}>
                    <Link href={`/concepts/${e.slug}`}>{e.name}</Link> <span className="muted">{e.single_source ? "◦" : `${e.sources}`}</span>
                  </td>
                  {cov.sources.map((s) => {
                    const n = cell.get(`${e.id}|${s.id}`) ?? 0;
                    return (
                      <td key={s.id} title={n ? `${e.name} · ${s.title}: ${n} afirmación(es)` : ""}
                        style={{ width: 28, height: 22, textAlign: "center", border: "1px solid var(--border)",
                          background: n ? `rgba(21, 101, 192, ${0.15 + 0.75 * (n / max)})` : "transparent", color: n / max > 0.5 ? "#fff" : "inherit" }}>
                        {n || ""}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {cov && <p className="muted" style={{ fontSize: "0.8rem", marginBottom: 0 }}>Mostrando {cov.entities.length} de {cov.totals.entities} conceptos y {cov.sources.length} de {cov.totals.sources} fuentes.</p>}
      </div>

      <h2>Enlaces propuestos entre fuentes ({links.length})</h2>
      <p className="muted" style={{ marginTop: 0 }}>Fuentes con pasajes que dicen lo mismo. La IA, si está disponible, indica si una amplía, resume, contradice o aplica a la otra.</p>
      {links.length === 0 ? <div className="card"><p className="muted" style={{ margin: 0 }}>Ninguno pendiente.</p></div> :
        links.map((lk) => <LinkProposalCard key={lk.id} lk={lk} onDone={load} onMsg={(ok, text) => setMsg({ ok, text })} />)}

      <h2>Profesores y autores</h2>
      <div className="card">
        {people.length === 0 ? <p className="muted" style={{ margin: 0 }}>Aún no hay personas registradas.</p> :
          people.map((p) => <div key={p.id}><Link href={`/people/${p.id}`}>{p.name}</Link> <span className="muted">{({ teacher: "profesor/a", author: "autor/a", researcher: "investigador/a" } as Record<string, string>)[p.role ?? ""] ?? ""}</span></div>)}
      </div>
    </main>
  );
}
