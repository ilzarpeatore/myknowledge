"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { Header } from "@/components/Header";
import { LinkProposalCard } from "@/components/LinkProposalCard";
import type { LinkProposal } from "@/lib/analysis";
import { api, ApiError } from "@/lib/api";
import { useMe } from "@/lib/useMe";

type Verdict = { verdict: string; reason: string; keep?: string; relation?: string };
type Task = { id: string; kind: string; payload: Record<string, string | number>; created_at: string };
const aiOf = (t: { payload: Task["payload"] }) => (t.payload as unknown as { ai?: Verdict }).ai;
type Dups = { same: number; unjudged: number; rejected_by_ai: number; running: number; usd: number | null };
type Rejected = { id: string; payload: Task["payload"]; note: string };
type ClaimInfo = { id: string; text: string; entities: { slug: string; name: string }[] };

export default function ReviewPage() {
  const user = useMe();
  const [tasks, setTasks] = useState<Task[]>([]);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [claims, setClaims] = useState<Record<string, ClaimInfo>>({});
  const [links, setLinks] = useState<LinkProposal[]>([]);
  const [dups, setDups] = useState<Dups | null>(null);
  const [rejected, setRejected] = useState<Rejected[] | null>(null);
  const load = useCallback(async () => {
    const ts = (await api<Task[]>("/api/review-tasks")).filter((t) => t.kind !== "source_link");
    // the pairs the AI confirms as duplicates first, then the ones it has not judged
    const rank = (t: Task) => t.kind !== "entity_merge" ? 2 : aiOf(t)?.verdict === "same" ? 0 : aiOf(t) ? 1.5 : 1;
    setTasks([...ts].sort((a, b) => rank(a) - rank(b)));
    setDups(await api<Dups>("/api/concepts/duplicates"));
    setLinks(await api<LinkProposal[]>("/api/source-links?status=proposed"));
    const ids = ts.filter((t) => t.kind === "contradiction").flatMap((t) => [String(t.payload.claim_a_id), String(t.payload.claim_b_id)]);
    const entries = await Promise.all([...new Set(ids)].map(async (i) => [i, await api<ClaimInfo>(`/api/claims/${i}`)] as const));
    setClaims(Object.fromEntries(entries));
  }, []);
  useEffect(() => { if (user) load(); }, [user, load]);

  async function resolve(t: Task, action: string, keep?: string) {
    try {
      await api(`/api/review-tasks/${t.id}/resolve`, { method: "POST", body: JSON.stringify({ action, keep_entity_id: keep }) });
      setMsg({ ok: true, text: "Hecho." }); load();
    } catch (err) { setMsg({ ok: false, text: err instanceof ApiError ? err.message : "Error." }); }
  }
  async function scan() {
    try {
      await api("/api/concepts/duplicates/scan", { method: "POST" });
      setMsg({ ok: true, text: "Buscando duplicados por nombre y por significado; la IA juzga cada pareja. Tarda unos minutos." });
      load();
    } catch (err) { setMsg({ ok: false, text: err instanceof ApiError ? err.message : "Error." }); }
  }
  useEffect(() => {
    if (!dups?.running) return;
    const t = setInterval(load, 8000);
    return () => clearInterval(t);
  }, [dups?.running, load]);
  async function showRejected() {
    setRejected(rejected ? null : await api<Rejected[]>("/api/concepts/duplicates/rejected"));
  }
  async function reopen(id: string) {
    await api(`/api/concepts/duplicates/${id}/reopen`, { method: "POST" });
    setRejected((r) => r?.filter((x) => x.id !== id) ?? null);
    load();
  }

  return (
    <main>
      <Header user={user} />
      <div className="row"><h1>Revisión</h1><button onClick={scan} disabled={Boolean(dups?.running)}>Buscar duplicados (con IA)</button></div>
      <p className="muted">Nada se fusiona ni se da por verificado sin tu decisión. Las parejas que rechaces no se vuelven a proponer.</p>
      {dups && (
        <p className="muted" style={{ fontSize: "0.9rem" }}>
          Duplicados: <strong>{dups.same}</strong> que la IA cree que son el mismo concepto (arriba) · {dups.unjudged} sin juzgar todavía
          {dups.running > 0 && <> · <em>la IA está revisando ({dups.running} lote(s) en cola)</em></>}
          {dups.unjudged > 0 && !dups.running && <> · «Buscar duplicados» los juzga ({dups.usd != null && dups.usd < 0.01 ? "menos de 1 céntimo" : `unos ${Math.ceil((dups.usd ?? 0) * 100)} céntimos`})</>}
          {dups.rejected_by_ai > 0 && <> · {dups.rejected_by_ai} descartados por la IA <button style={{ fontSize: "0.75rem", padding: "0 6px" }} onClick={showRejected}>{rejected ? "ocultar" : "ver"}</button></>}
        </p>
      )}
      {rejected && (
        <div className="card" style={{ marginBottom: 12 }}>
          <strong>Parejas que la IA ha dado por distintas</strong> <span className="muted">(si alguna sí es el mismo concepto, «Reabrir» la devuelve a la lista)</span>
          {rejected.length === 0 ? <p className="muted">Ninguna.</p> : (
            <ul style={{ marginBottom: 0 }}>
              {rejected.map((r) => (
                <li key={r.id}>{r.payload.name_a} · {r.payload.name_b} <span className="muted">— {r.note.replace(/^IA: /, "")}</span>{" "}
                  <button style={{ fontSize: "0.75rem", padding: "0 6px" }} onClick={() => reopen(r.id)}>Reabrir</button></li>
              ))}
            </ul>
          )}
        </div>
      )}
      {msg && <p className={msg.ok ? "ok" : "error"}>{msg.text}</p>}
      {tasks.length === 0 && links.length === 0 && <p className="muted">No hay nada pendiente de revisar.</p>}
      {links.map((lk) => <LinkProposalCard key={lk.id} lk={lk} onDone={load} onMsg={(ok, text) => setMsg({ ok, text })} />)}
      {tasks.map((t) => (
        <div className="card" key={t.id} style={{ marginBottom: 8 }}>
          {t.kind === "entity_merge" ? (<>
            <strong>¿Son el mismo concepto?</strong> <span className="muted">({t.payload.found_by === "meaning" ? "significado parecido" : "nombre parecido"}, {Math.round(Number(t.payload.score) * 100)} %)</span>
            {aiOf(t) && (
              <p style={{ margin: "6px 0" }} className={aiOf(t)?.verdict === "same" ? "ok" : "muted"}>
                {aiOf(t)?.verdict === "same" ? "La IA cree que sí" : "La IA cree que no"}: {aiOf(t)?.reason}
              </p>
            )}
            <p>
              <Link href={`/concepts/${t.payload.entity_a}`} target="_blank">{t.payload.name_a}</Link>
              {" · "}
              <Link href={`/concepts/${t.payload.entity_b}`} target="_blank">{t.payload.name_b}</Link>
            </p>
            <div className="row" style={{ justifyContent: "flex-start" }}>
              {(["entity_a", "entity_b"] as const).map((k) => (
                <button key={k} className={aiOf(t)?.keep === t.payload[k] ? "primary" : undefined}
                  onClick={() => resolve(t, "accept", String(t.payload[k]))}>
                  Sí, conservar «{k === "entity_a" ? t.payload.name_a : t.payload.name_b}»
                </button>
              ))}
              <button onClick={() => resolve(t, "reject")}>No, son distintos</button>
            </div>
          </>) : t.kind === "relationship" ? (<>
            <strong>Relación propuesta</strong>
            <p className="muted">Ábrela desde la ficha del concepto para ver su evidencia.</p>
            <div className="row" style={{ justifyContent: "flex-start" }}>
              <button onClick={() => resolve(t, "accept")}>Verificar</button>
              <button onClick={() => resolve(t, "reject")}>Rechazar</button>
            </div>
          </>) : t.kind === "contradiction" ? (<>
            <strong>Posible contradicción (propuesta por IA)</strong>
            {[t.payload.claim_a_id, t.payload.claim_b_id].map((cid, i) => {
              const c = claims[String(cid)];
              return <p key={i} style={{ margin: "6px 0" }}>{i === 1 && <span className="muted">frente a<br /></span>}«{c?.text ?? "…"}» {c?.entities.map((e) => <Link key={e.slug} className="badge" href={`/concepts/${e.slug}`}>{e.name}</Link>)}</p>;
            })}
            {t.payload.analysis && <p className="muted">{String(t.payload.analysis)}</p>}
            <div className="row" style={{ justifyContent: "flex-start" }}>
              {([["real", "Es una contradicción real"], ["context_difference", "Es una diferencia de contexto"], ["dismissed", "Descartar"]] as const).map(([s, label]) => (
                <button key={s} onClick={async () => {
                  try { await api(`/api/contradictions/${t.payload.contradiction_id}`, { method: "PATCH", body: JSON.stringify({ status: s }) }); setMsg({ ok: true, text: "Hecho." }); load(); }
                  catch (err) { setMsg({ ok: false, text: err instanceof ApiError ? err.message : "Error." }); }
                }}>{label}</button>
              ))}
            </div>
          </>) : (<>
            <strong>{t.kind}</strong> <button onClick={() => resolve(t, "dismiss")}>Descartar</button>
          </>)}
        </div>
      ))}
    </main>
  );
}
