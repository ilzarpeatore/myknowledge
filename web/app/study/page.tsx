"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import { Header } from "@/components/Header";
import { api } from "@/lib/api";
import { sourceHref, where } from "@/lib/knowledge";
import { RATINGS, type CardT } from "@/lib/study";
import { useMe } from "@/lib/useMe";

type Summary = { due_now: number; due_tomorrow: number; active: number; proposed: number; reviewed_today: number };
type Gap = { id: string; name: string; slug: string; reasons: string[]; kind?: string; claims?: number; sources?: number; cards?: number };
type Report = { concepts: number; library_gaps: Gap[]; learning_gaps: Gap[]; not_studied: Gap[]; thresholds: Record<string, number> };

function Study() {
  const user = useMe();
  const course = useSearchParams().get("course");
  const [tab, setTab] = useState<"review" | "proposed" | "gaps">(course ? "gaps" : "review");
  const [sum, setSum] = useState<Summary | null>(null);
  const [queue, setQueue] = useState<CardT[]>([]);
  const [shown, setShown] = useState(false);
  const [proposed, setProposed] = useState<CardT[]>([]);
  const [report, setReport] = useState<Report | null>(null);
  const started = useRef(Date.now());

  const load = useCallback(async () => {
    setSum(await api<Summary>("/api/study/summary"));
    setQueue(await api<CardT[]>("/api/study/due?limit=50"));
    setProposed(await api<CardT[]>("/api/flashcards?status_filter=proposed"));
  }, []);
  useEffect(() => { if (user) load().catch(() => {}); }, [user, load]);
  useEffect(() => {
    if (user && tab === "gaps") api<Report>(`/api/study/gaps${course ? `?course_id=${course}` : ""}`).then(setReport).catch(() => {});
  }, [user, tab, course]);

  const card = queue[0];
  useEffect(() => { setShown(false); started.current = Date.now(); }, [card?.id]);

  async function rate(r: number) {
    if (!card) return;
    await api(`/api/flashcards/${card.id}/review`, { method: "POST", body: JSON.stringify({ rating: r, duration_ms: Date.now() - started.current }) });
    setQueue(queue.slice(1));
    setSum(await api<Summary>("/api/study/summary"));
  }
  async function decide(id: string, status: string) {
    await api(`/api/flashcards/${id}`, { method: "PATCH", body: JSON.stringify({ status }) });
    load();
  }

  return (
    <main>
      <Header user={user} />
      <h1>Estudiar</h1>
      {sum && (
        <p className="muted">
          {sum.due_now} por repasar ahora · {sum.reviewed_today} repasadas hoy · {sum.active} tarjetas activas
          {sum.proposed > 0 && ` · ${sum.proposed} propuesta(s) de la IA por revisar`}
        </p>
      )}
      <div className="row" style={{ justifyContent: "flex-start", gap: 6 }}>
        {([["review", "Repasar"], ["proposed", `Propuestas (${proposed.length})`], ["gaps", "Lagunas"]] as const).map(([k, l]) => (
          <button key={k} className={tab === k ? "primary" : ""} onClick={() => setTab(k)}>{l}</button>
        ))}
      </div>

      {tab === "review" && (
        <div className="card" style={{ marginTop: 12, minHeight: 220 }}>
          {!card ? (
            <p className="muted">No hay tarjetas pendientes. {sum && sum.due_tomorrow > 0 ? `Mañana tocarán ${sum.due_tomorrow}.` : "Crea tarjetas desde la ficha de un concepto."}</p>
          ) : (
            <>
              <div className="muted" style={{ fontSize: "0.85rem" }}>
                <Link href={`/concepts/${card.entity.slug}`}>{card.entity.name}</Link> · {queue.length} pendiente(s)
              </div>
              <h2 style={{ marginTop: 6 }}>{card.front}</h2>
              {!shown ? (
                <button className="primary" onClick={() => setShown(true)}>Mostrar respuesta</button>
              ) : (
                <>
                  <p style={{ fontSize: "1.1rem" }}>{card.back}</p>
                  {card.quote && card.reference && (
                    <blockquote style={{ margin: "6px 0", paddingLeft: 10, borderLeft: "3px solid var(--border)" }}>
                      «{card.quote}»
                      <div className="muted" style={{ fontSize: "0.85rem" }}>
                        <a href={sourceHref(card.reference)} target="_blank" rel="noopener">{card.reference.source_title}{where(card.reference)}</a>
                      </div>
                    </blockquote>
                  )}
                  <div className="row" style={{ justifyContent: "flex-start", gap: 6, marginTop: 10 }}>
                    {RATINGS.map(([r, l]) => (
                      <button key={r} onClick={() => rate(r)} className={r === 3 ? "primary" : ""}>
                        {l}<div style={{ fontSize: "0.75rem", opacity: 0.8 }}>{card.preview?.[String(r)]}</div>
                      </button>
                    ))}
                  </div>
                </>
              )}
            </>
          )}
        </div>
      )}

      {tab === "proposed" && (
        <div className="card" style={{ marginTop: 12 }}>
          {proposed.length === 0 ? <p className="muted">No hay propuestas.</p> : (
            <>
              <div className="row" style={{ justifyContent: "flex-start" }}>
                <button onClick={async () => { for (const c of proposed) await api(`/api/flashcards/${c.id}`, { method: "PATCH", body: JSON.stringify({ status: "active" }) }); load(); }}>Aceptar todas</button>
              </div>
              {proposed.map((c) => (
                <div key={c.id} style={{ borderBottom: "1px solid var(--border)", padding: "8px 0" }}>
                  <div className="muted" style={{ fontSize: "0.85rem" }}><Link href={`/concepts/${c.entity.slug}`}>{c.entity.name}</Link></div>
                  <strong>{c.front}</strong>
                  <div>{c.back}</div>
                  {c.quote && c.reference && <div className="muted" style={{ fontSize: "0.85rem" }}>«{c.quote}» — <a href={sourceHref(c.reference)} target="_blank" rel="noopener">{c.reference.source_title}{where(c.reference)}</a></div>}
                  <div className="row" style={{ justifyContent: "flex-start", gap: 6, marginTop: 4 }}>
                    <button onClick={() => decide(c.id, "active")}>Aceptar</button>
                    <button onClick={() => decide(c.id, "archived")}>Descartar</button>
                  </div>
                </div>
              ))}
            </>
          )}
        </div>
      )}

      {tab === "gaps" && report && (
        <>
          <p className="muted">
            {course ? "Conceptos de este curso" : "Todos los conceptos"}: {report.concepts}. Se separa lo que <strong>falta en tu biblioteca</strong> de lo que <strong>te cuesta a ti</strong>; un concepto puede estar en las dos listas.
          </p>
          <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))" }}>
            <GapList title="Tu biblioteca no lo cubre bien" hint={`Sin afirmaciones con fuente, menos de ${report.thresholds.min_sources} fuentes o menos de ${report.thresholds.min_claims} afirmaciones. Solución: añadir o procesar materiales.`} items={report.library_gaps} />
            <GapList title="Te cuesta recordarlo" hint={`Aciertos < ${report.thresholds.low_accuracy * 100} % en cuestionarios, «otra vez» ≥ ${report.thresholds.high_again * 100} % de los repasos, recuerdo < ${report.thresholds.low_recall * 100} % u olvidos repetidos. Solución: repasar.`} items={report.learning_gaps} />
          </div>
          {report.not_studied.length > 0 && (
            <div className="card" style={{ marginTop: 12 }}>
              <strong>Aún sin estudiar</strong> <span className="muted">(tienen fuentes pero no los has repasado ni preguntado)</span>
              <p>{report.not_studied.map((g, i) => <span key={g.id}>{i ? ", " : ""}<Link href={`/concepts/${g.slug}`}>{g.name}</Link></span>)}</p>
            </div>
          )}
        </>
      )}
    </main>
  );
}

function GapList({ title, hint, items }: { title: string; hint: string; items: Gap[] }) {
  return (
    <div className="card">
      <h3 style={{ marginTop: 0 }}>{title} ({items.length})</h3>
      <p className="muted" style={{ fontSize: "0.85rem" }}>{hint}</p>
      {items.length === 0 ? <p className="muted">Nada que señalar.</p> : items.map((g) => (
        <div key={g.id} style={{ padding: "4px 0" }}>
          <Link href={`/concepts/${g.slug}`}>{g.name}</Link>{g.kind && <span className="badge"> {g.kind}</span>}
          <div className="muted" style={{ fontSize: "0.85rem" }}>{g.reasons.join(" · ")}</div>
        </div>
      ))}
    </div>
  );
}

export default function StudyPage() {
  return <Suspense><Study /></Suspense>;
}
