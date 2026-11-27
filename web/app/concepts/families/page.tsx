"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Header } from "@/components/Header";
import { api, ApiError } from "@/lib/api";
import { useMe } from "@/lib/useMe";

type Proposal = {
  id: string; discipline_id: string; discipline: string; head_id: string | null; head_name: string;
  head_type: string; members: { id: string; name: string }[]; reason: string;
};
type Overview = { families: number; proposals: number; without_family: number; running: number; usd: number | null };

const money = (usd: number | null) => usd == null ? "" : usd < 0.01 ? "menos de 1 céntimo" : `unos ${Math.ceil(usd * 100)} céntimos`;

function ProposalCard({ p, onDone, onMsg }: { p: Proposal; onDone: () => void; onMsg: (ok: boolean, text: string) => void }) {
  const [keep, setKeep] = useState<Set<string>>(new Set(p.members.map((m) => m.id)));
  const [head, setHead] = useState(p.head_name);
  const [busy, setBusy] = useState(false);

  async function accept() {
    setBusy(true);
    try {
      const r = await api<{ head: { slug: string; name: string }; members: number }>(`/api/concepts/families/${p.id}/accept`, {
        method: "POST", body: JSON.stringify({ members: [...keep], head_name: head.trim() || null }),
      });
      onMsg(true, `Familia «${r.head.name}» con ${r.members} concepto(s).`);
      onDone();
    } catch (e) { onMsg(false, e instanceof ApiError ? e.message : "No se pudo aprobar."); setBusy(false); }
  }
  async function reject() {
    setBusy(true);
    try { await api(`/api/concepts/families/${p.id}/reject`, { method: "POST" }); onDone(); }
    catch (e) { onMsg(false, e instanceof ApiError ? e.message : "No se pudo descartar."); setBusy(false); }
  }

  return (
    <div className="card" style={{ marginBottom: 8 }}>
      <div className="row" style={{ justifyContent: "flex-start", alignItems: "baseline" }}>
        <input value={head} onChange={(e) => setHead(e.target.value)} aria-label="Concepto que encabeza la familia"
          style={{ fontWeight: 600, maxWidth: 360 }} />
        {p.head_id && head === p.head_name
          ? <Link href={`/concepts/${p.head_id}`} target="_blank" className="muted" style={{ fontSize: "0.85rem" }}>ver concepto</Link>
          : <span className="badge" title="Se creará este concepto al aprobar (o se usará el que ya tenga ese nombre)">concepto nuevo</span>}
        <span className="muted" style={{ fontSize: "0.85rem" }}>{p.reason}</span>
      </div>
      <div style={{ margin: "8px 0" }}>
        {p.members.map((m) => (
          <label key={m.id} style={{ display: "inline-flex", gap: 4, margin: "2px 14px 2px 0", fontSize: "0.9rem" }}>
            <input type="checkbox" checked={keep.has(m.id)} onChange={(e) => {
              const n = new Set(keep); if (e.target.checked) n.add(m.id); else n.delete(m.id); setKeep(n);
            }} />
            {m.name}
          </label>
        ))}
      </div>
      <div className="row" style={{ justifyContent: "flex-start" }}>
        <button className="primary" disabled={busy || keep.size === 0 || !head.trim()} onClick={accept}>
          Aprobar con {keep.size} concepto(s)
        </button>
        <button disabled={busy} onClick={reject}>Descartar</button>
      </div>
    </div>
  );
}

export default function FamiliesPage() {
  const user = useMe();
  const [ov, setOv] = useState<Overview | null>(null);
  const [items, setItems] = useState<Proposal[]>([]);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [branch, setBranch] = useState("");

  const load = useCallback(async () => {
    setOv(await api<Overview>("/api/concepts/families"));
    setItems(await api<Proposal[]>("/api/concepts/families/proposals"));
  }, []);
  useEffect(() => { if (user) load().catch(() => setMsg({ ok: false, text: "No se pudo cargar." })); }, [user, load]);
  useEffect(() => {
    if (!ov?.running) return;
    const t = setInterval(load, 8000);
    return () => clearInterval(t);
  }, [ov?.running, load]);

  const branches = useMemo(() => {
    const m = new Map<string, { name: string; n: number }>();
    items.forEach((p) => m.set(p.discipline_id, { name: p.discipline, n: (m.get(p.discipline_id)?.n ?? 0) + 1 }));
    return [...m.entries()].sort((a, b) => b[1].n - a[1].n);
  }, [items]);
  const shown = items.filter((p) => !branch || p.discipline_id === branch);

  async function propose() {
    try {
      const r = await api<{ branches: number }>("/api/concepts/families/propose", { method: "POST", body: JSON.stringify({}) });
      setMsg({ ok: true, text: `Proponiendo familias en ${r.branches} rama(s)…` });
      load();
    } catch (e) { setMsg({ ok: false, text: e instanceof ApiError ? e.message : "No se pudo lanzar." }); }
  }

  return (
    <main>
      <Header user={user} />
      <p className="muted"><Link href="/concepts">← Conceptos</Link> · <Link href="/tree">ver el árbol</Link></p>
      <h1 style={{ marginBottom: 4 }}>Familias de conceptos</h1>
      <p className="muted" style={{ marginTop: 0 }}>
        Una familia agrupa conceptos bajo uno que los encabeza: las variantes de un ejercicio, las partes de una región, las fases de un método.
        La IA las propone por rama; tú quitas los que no encajan, cambias el nombre si quieres y apruebas. Nada se cambia sin tu aprobación.
        En el <Link href="/tree">árbol</Link> verás rama → familia → concepto.
      </p>
      {ov && (
        <div className="card" style={{ marginBottom: 12 }}>
          <strong>{ov.families}</strong> familia(s) · <strong>{ov.proposals}</strong> propuesta(s) por revisar · {ov.without_family} concepto(s) sin familia
          {ov.running > 0
            ? <p className="muted" style={{ marginBottom: 0 }}>La IA está proponiendo familias ({ov.running} rama(s) en cola)… la lista se actualiza sola.</p>
            : ov.without_family > 2 && (
              <p style={{ marginBottom: 0 }}>
                <button className="primary" onClick={propose}>Proponer familias con IA</button>{" "}
                <span className="muted" style={{ fontSize: "0.85rem" }}>{money(ov.usd)} · solo los conceptos sin familia y que no estén ya en una propuesta</span>
              </p>
            )}
        </div>
      )}
      {msg && <p className={msg.ok ? "ok" : "error"}>{msg.text}</p>}
      {branches.length > 1 && (
        <p>
          <span className="badge" role="button" style={{ cursor: "pointer", marginRight: 6, ...(branch ? {} : { fontWeight: 600 }) }} onClick={() => setBranch("")}>Todas {items.length}</span>
          {branches.map(([id, b]) => (
            <span key={id} className="badge" role="button" style={{ cursor: "pointer", marginRight: 6, ...(branch === id ? { fontWeight: 600 } : {}) }}
              onClick={() => setBranch(branch === id ? "" : id)}>{b.name} {b.n}</span>
          ))}
        </p>
      )}
      {shown.length === 0 && <p className="muted">No hay propuestas pendientes.</p>}
      {shown.map((p) => (
        <ProposalCard key={p.id} p={p} onMsg={(ok, text) => setMsg({ ok, text })}
          onDone={() => { setItems((xs) => xs.filter((x) => x.id !== p.id)); api<Overview>("/api/concepts/families").then(setOv).catch(() => {}); }} />
      ))}
    </main>
  );
}
