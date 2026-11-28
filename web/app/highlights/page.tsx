"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { ConceptInput, type ConceptRef } from "@/components/ConceptInput";
import { Header } from "@/components/Header";
import { api, ApiError } from "@/lib/api";
import { CLAIM_TYPES } from "@/lib/knowledge";
import { useMe } from "@/lib/useMe";

type Item = {
  id: string; type: string; page: number; text: string | null; comment: string | null;
  claim_id: string | null; card_id: string | null; updated_at: string;
  source_id: string; version_id: string; source_title: string;
  concept: { id: string; slug: string; name: string } | null;
};
const KIND: Record<string, string> = { highlight: "Resaltado", underline: "Subrayado", strikeout: "Tachado", squiggly: "Ondulado", note: "Nota", text: "Nota" };
const PAGE = 50;

/** Turn a highlight into a claim that cites it, or into a study card. */
function Convert({ it, mode, onDone }: { it: Item; mode: "claim" | "card"; onDone: (msg: string) => void }) {
  const [concept, setConcept] = useState<ConceptRef | null>(it.concept ? { id: it.concept.id, name: it.concept.name } : null);
  const [text, setText] = useState(mode === "claim" ? it.text ?? "" : "");
  const [back, setBack] = useState(it.text ?? "");
  const [type, setType] = useState("reported_fact");
  const [err, setErr] = useState<string | null>(null);
  async function send(e: React.FormEvent) {
    e.preventDefault();
    if (!concept) { setErr("Elige o escribe un concepto."); return; }
    const entity = concept.id ? { id: concept.id } : { name: concept.name };
    try {
      const r = await api<{ concept: { name: string } }>(`/api/annotations/${it.version_id}/${encodeURIComponent(it.id)}/${mode}`, {
        method: "POST",
        body: JSON.stringify(mode === "claim" ? { entity, text, claim_type: type } : { entity, front: text, back }),
      });
      onDone(mode === "claim" ? `Afirmación creada en «${r.concept.name}», con la cita.` : `Tarjeta creada en «${r.concept.name}»: ya está en Estudiar.`);
    } catch (x) { setErr(x instanceof ApiError ? x.message : "No se pudo crear."); }
  }
  return (
    <form onSubmit={send} className="card" style={{ marginTop: 8 }}>
      <label style={{ marginTop: 0 }}>Concepto</label>
      <ConceptInput value={concept} onChange={setConcept} />
      {mode === "claim" ? (<>
        <label>Afirmación (con tus palabras o tal cual; la cita es el texto subrayado)
          <textarea rows={2} value={text} maxLength={2000} onChange={(e) => setText(e.target.value)} style={{ width: "100%" }} />
        </label>
        <label>Tipo
          <select value={type} onChange={(e) => setType(e.target.value)} style={{ display: "block" }}>
            {Object.entries(CLAIM_TYPES).filter(([k]) => k !== "ai_inference").map(([k, l]) => <option key={k} value={k}>{l}</option>)}
          </select>
        </label>
      </>) : (<>
        <label>Pregunta (anverso)
          <input value={text} maxLength={1000} onChange={(e) => setText(e.target.value)} placeholder="Ej.: ¿Más de 12 series semanales aportan más hipertrofia?" />
        </label>
        <label>Respuesta (reverso; por defecto, el texto subrayado)
          <textarea rows={2} value={back} maxLength={3000} onChange={(e) => setBack(e.target.value)} style={{ width: "100%" }} />
        </label>
      </>)}
      <div className="row" style={{ justifyContent: "flex-start", gap: 8, marginTop: 8 }}>
        <button className="primary" disabled={mode === "card" && !text.trim()}>{mode === "claim" ? "Crear afirmación" : "Crear tarjeta"}</button>
        {err && <span className="error">{err}</span>}
      </div>
    </form>
  );
}

/** Your highlights and notes across the library: search them, open them where they
 *  are, and turn them into claims or study cards. */
export default function HighlightsPage() {
  const user = useMe();
  const [q, setQ] = useState("");
  const [source, setSource] = useState<string | null>(null);
  const [items, setItems] = useState<Item[]>([]);
  const [total, setTotal] = useState(0);
  const [open, setOpen] = useState<{ id: string; mode: "claim" | "card" } | null>(null);
  const [msg, setMsg] = useState<{ id: string; text: string } | null>(null);

  useEffect(() => {
    const p = new URLSearchParams(window.location.search);
    if (p.get("source")) setSource(p.get("source"));
    if (p.get("focus") && p.get("mode")) setOpen({ id: p.get("focus")!, mode: p.get("mode") as "claim" | "card" });
  }, []);
  const load = useCallback(async (offset = 0) => {
    const p = new URLSearchParams({ limit: String(PAGE), offset: String(offset) });
    if (q.trim()) p.set("q", q.trim());
    if (source) p.set("source_id", source);
    const r = await api<{ items: Item[]; total: number }>(`/api/annotations?${p}`).catch(() => ({ items: [] as Item[], total: 0 }));
    setItems((prev) => (offset ? [...prev, ...r.items] : r.items)); setTotal(r.total);
  }, [q, source]);
  useEffect(() => {
    if (!user) return;
    const t = setTimeout(() => load(), 200);
    return () => clearTimeout(t);
  }, [user, load]);

  const readHref = (it: Item) => `/read/${it.source_id}?${new URLSearchParams({ v: it.version_id, page: String(it.page), ...(it.text ? { q: it.text.slice(0, 400) } : {}) })}`;
  return (
    <main>
      <Header user={user} />
      <h1>Subrayados</h1>
      <p className="muted" style={{ marginTop: 0 }}>Lo que has subrayado y anotado en los PDF de tu biblioteca. Ábrelo donde está, o conviértelo en una afirmación con su cita o en una tarjeta de estudio.</p>
      <div className="row" style={{ justifyContent: "flex-start", gap: 8 }}>
        <input placeholder="Buscar en el texto subrayado o en tus notas…" value={q} onChange={(e) => setQ(e.target.value)} style={{ maxWidth: 420 }} />
        {source && <span className="badge">Solo un documento <button className="link" onClick={() => setSource(null)}>✕</button></span>}
        <span className="muted">{total} en total</span>
      </div>
      {items.length === 0 ? (
        <p className="card muted" style={{ marginTop: 12 }}>{q ? "Nada coincide." : "Aún no has subrayado nada. Abre un PDF de la Biblioteca, activa «Subrayar» y selecciona el texto."}</p>
      ) : items.map((it) => (
        <div key={`${it.version_id}:${it.id}`} className="card" style={{ marginTop: 10 }}>
          <div className="muted" style={{ fontSize: "0.85rem" }}>
            <Link href={`/sources/${it.source_id}`}>{it.source_title}</Link> · p. {it.page} · {KIND[it.type] ?? it.type} · {new Date(it.updated_at).toLocaleDateString("es-ES")}
          </div>
          {it.text && <p style={{ margin: "6px 0" }}>«{it.text}»</p>}
          {it.comment && <p className="muted" style={{ margin: "6px 0" }}>Nota: {it.comment}</p>}
          <div className="row" style={{ justifyContent: "flex-start", gap: 8 }}>
            <Link href={readHref(it)}>Abrir en el documento</Link>
            {it.claim_id
              ? <span className="badge">afirmación{it.concept && <> en <Link href={`/concepts/${it.concept.slug}`}>{it.concept.name}</Link></>}</span>
              : it.text && <button onClick={() => setOpen(open?.id === it.id && open.mode === "claim" ? null : { id: it.id, mode: "claim" })}>→ Afirmación</button>}
            {it.card_id
              ? <span className="badge">tarjeta · <Link href="/study">Estudiar</Link></span>
              : it.text && <button onClick={() => setOpen(open?.id === it.id && open.mode === "card" ? null : { id: it.id, mode: "card" })}>→ Tarjeta</button>}
            {msg?.id === it.id && <span className="ok">{msg.text}</span>}
          </div>
          {open?.id === it.id && <Convert it={it} mode={open.mode} onDone={(text) => { setOpen(null); setMsg({ id: it.id, text }); load(); }} />}
        </div>
      ))}
      {items.length < total && <button style={{ marginTop: 10 }} onClick={() => load(items.length)}>Ver más</button>}
    </main>
  );
}
