"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  InkDownloadButton, InkFindBar, InkHighlightMenu, InkNextPage, InkOutline, InkPageCount,
  InkPageInput, InkPanel, InkPrevPage, InkPrintButton, InkRedo, InkRoot, InkSearchButton,
  InkSidebar, InkThreadPopover, InkThumbnails, InkToolButton, InkToolbar, InkToolbarSeparator,
  InkUndo, InkViewport, InkZoomIn, InkZoomOut, InkZoomSelect, useDocument, usePage, useSearch,
  useSelection,
} from "@inkbar/react";
import type { Annotation, AnnotationChange, InkDocument, PdfAssets } from "@inkbar/core";
import { highlightTool, noteTool } from "@inkbar/core/dom";
import "@inkbar/theme/theme.css";
import { inkbarEs } from "@/lib/inkbarEs";

// PDF.js files copied into public/ by scripts/copy-inkbar.mjs, under the version of the
// pdfjs-dist that @inkbar/core installs (the worker must match the library).
const base = `/inkbar/${process.env.NEXT_PUBLIC_PDFJS_VERSION}/`;
const ASSETS: PdfAssets = {
  worker: `${base}pdf.worker.min.mjs`,
  cMaps: `${base}cmaps/`,
  standardFonts: `${base}standard_fonts/`,
  wasm: `${base}wasm/`,
  icc: `${base}iccs/`,
};

export type SavedItem = { id: string; type: string; page: number; text: string | null; comment: string | null; reply_to: string | null };
export type Saved = { annotations: Annotation[]; items: SavedItem[] };

export type PdfViewerProps = {
  /** Where highlights and notes are kept: /api/sources/{s}/versions/{v}/annotations.
   *  Without it the viewer is read-only. */
  annotationsUrl?: string | null;
  saved?: Saved | null;
  url: string;          // same-origin file URL
  docKey: string;       // stable id (the version id): no reload when the URL changes
  page?: number | null; // 1-based page to open at
  quote?: string | null; // text to find and highlight (a citation)
  height?: string;
  onFailed?: () => void;
};

function words(s: string) { return s.replace(/\s+/g, " ").trim().split(" "); }

/** Goes to the cited page and highlights the quote. A long quote is retried with its
 *  first words (text extracted from a PDF can differ slightly from the page). */
function JumpTo({ page, quote }: { page?: number | null; quote?: string | null }) {
  const { isReady } = useDocument();
  const pages = usePage();
  const search = useSearch();
  const done = useRef<string | null>(null);
  const tries = useRef<string[]>([]);
  const target = page ? page - 1 : null;
  const key = `${page ?? ""}|${quote ?? ""}`;

  useEffect(() => {
    if (!isReady || done.current === key) return;
    done.current = key;
    const q = (quote ?? "").trim();
    if (!q) { if (target != null) pages.goTo(target); return; }
    const w = words(q);
    tries.current = [w.slice(0, 25).join(" "), w.slice(0, 10).join(" "), w.slice(0, 5).join(" ")]
      .filter((t, i, a) => t && a.indexOf(t) === i);
    search.open(tries.current.shift());
  }, [isReady, key]); // eslint-disable-line react-hooks/exhaustive-deps

  const st = search.state;
  useEffect(() => {
    if (done.current !== key || !st.open || st.scanning || !(quote ?? "").trim()) return;
    if (st.hits.length) {
      const i = target != null ? st.hits.findIndex((h) => h.page === target) : 0;
      if (i >= 0 && i !== st.current) search.select(i);
      else if (i < 0 && st.current < 0) search.select(0);
      return;
    }
    const next = tries.current.shift();
    if (next) search.setQuery(next);
    else { search.close(); if (target != null) pages.goTo(target); }
  }, [st.scanning, st.hits.length, st.open]); // eslint-disable-line react-hooks/exhaustive-deps
  return null;
}

const TOOLS = [highlightTool(), noteTool()];

type Pt = { x: number; y: number };
type Q = { p1: Pt; p2: Pt; p3: Pt; p4: Pt };
const box = (q: Q) => {
  const xs = [q.p1.x, q.p2.x, q.p3.x, q.p4.x], ys = [q.p1.y, q.p2.y, q.p3.y, q.p4.y];
  return { x0: Math.min(...xs), x1: Math.max(...xs), y0: Math.min(...ys), y1: Math.max(...ys) };
};

/** The page text under a highlight: the characters of each text run whose middle falls
 *  inside one of its quads (horizontal text; the viewer stores no text itself). */
async function textUnder(doc: InkDocument, a: Annotation): Promise<string> {
  const quads = ("quads" in a ? (a.quads as Q[]) : []).map(box);
  if (!quads.length) return "";
  const { items } = await doc.text(a.page);
  const out: string[] = [];
  for (const it of items) {
    const r = it.rect;
    const ib = { x0: r.x, x1: r.x + r.width, y0: r.y, y1: r.y + r.height };
    for (const q of quads) {
      const overlap = Math.min(ib.y1, q.y1) - Math.max(ib.y0, q.y0);
      if (overlap < 0.5 * Math.min(ib.y1 - ib.y0, q.y1 - q.y0) || ib.x1 < q.x0 || ib.x0 > q.x1) continue;
      const n = it.str.length;
      const adv = it.advances && it.advances.length === n + 1 ? it.advances : Array.from({ length: n + 1 }, (_, i) => i / n);
      let s = "";
      for (let i = 0; i < n; i++) {
        const mid = ib.x0 + ((adv[i] + adv[i + 1]) / 2) * (ib.x1 - ib.x0);
        if (mid >= q.x0 && mid <= q.x1) s += it.str[i];
      }
      if (s) out.push(s);
      break;
    }
  }
  return out.join(" ").replace(/\s+/g, " ").trim();
}

/** Gives the store the open document, to read the text under new highlights. */
function DocRef({ target }: { target: { current: InkDocument | null } }) {
  const { document } = useDocument();
  useEffect(() => { target.current = document; }, [document, target]);
  return null;
}
const MARKUP = new Set(["highlight", "underline", "strikeout", "squiggly"]);
const KIND: Record<string, string> = {
  highlight: "Resaltado", underline: "Subrayado", strikeout: "Tachado", squiggly: "Ondulado", note: "Nota", text: "Nota",
};

/** Keeps the highlights and notes in Knowledge OS: the whole list of this file version
 *  is sent a moment after each change (and when leaving the page). The PDF is never
 *  modified. The text selected when a highlight is made is sent with it. */
function useAnnotationStore(url: string | null | undefined, saved: Saved | null | undefined) {
  const [list, setList] = useState<Annotation[]>(saved?.annotations ?? []);
  const texts = useRef(new Map((saved?.items ?? []).map((i) => [i.id, i.text])));
  const [state, setState] = useState<"saved" | "saving" | "error" | "idle">("idle");
  const pending = useRef<Annotation[] | null>(null);
  const latest = useRef<Annotation[]>(saved?.annotations ?? []);
  const doc = useRef<InkDocument | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const body = (anns: Annotation[]) => JSON.stringify({
    annotations: anns.filter((a) => a.source !== "pdf")  // the file's own ones stay in the file
      .map((a) => ({ data: a, text: texts.current.get(a.id) ?? null })),
  });
  const flush = useCallback(async (keepalive = false) => {
    if (!url || !pending.current) return;
    const anns = pending.current;
    pending.current = null;
    setState("saving");
    try {
      const r = await fetch(url, { method: "PUT", headers: { "Content-Type": "application/json" }, body: body(anns), keepalive, credentials: "same-origin" });
      setState(r.ok ? "saved" : "error");
    } catch { setState("error"); }
  }, [url]); // eslint-disable-line react-hooks/exhaustive-deps

  const schedule = useCallback((next: Annotation[]) => {
    if (!url) return;
    pending.current = next;
    setState("saving");
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => flush(), 800);
  }, [url, flush]);

  const onAnnotationsChange = useCallback((next: Annotation[]) => {
    setList(next);
    latest.current = next;
    schedule(next);
  }, [schedule]);

  // a new highlight: keep the text it covers (for the list, search and citations)
  const onChange = useCallback((c: AnnotationChange) => {
    if (c.kind !== "add" || !MARKUP.has(c.annotation.type) || !doc.current) return;
    const a = c.annotation;
    textUnder(doc.current, a).then((text) => {
      if (!text) return;
      texts.current.set(a.id, text.slice(0, 5000));
      setList((l) => [...l]);  // show it in the list
      schedule(latest.current);
    }).catch(() => {});
  }, [schedule]);

  useEffect(() => {
    const leave = () => { if (pending.current) flush(true); };
    window.addEventListener("pagehide", leave);
    return () => { window.removeEventListener("pagehide", leave); leave(); };
  }, [flush]);

  return { list, onAnnotationsChange, onChange, state, texts, doc };
}

/** «Mis subrayados»: this file's highlights and notes; a click goes to it. */
function MyMarks({ list, texts }: { list: Annotation[]; texts: Map<string, string | null> }) {
  const pages = usePage();
  const sel = useSelection();
  const mine = list.filter((a) => a.source !== "pdf" && !("inReplyTo" in a && a.inReplyTo))
    .sort((a, b) => a.page - b.page);
  if (!mine.length) return <p className="muted" style={{ padding: 8, fontSize: "0.85rem" }}>Aún no hay subrayados. Selecciona texto y pulsa el botón de subrayar (o Enter); con la nota añades un comentario.</p>;
  return (
    <ul className="my-marks">
      {mine.map((a) => (
        <li key={a.id}>
          <button type="button" className="link" onClick={() => { pages.goTo(a.page); sel.select([a.id]); }}>
            p. {a.page + 1} · {KIND[a.type] ?? a.type}
          </button>
          {texts.get(a.id) && <div>«{texts.get(a.id)}»</div>}
          {"contents" in a && a.contents && <div className="muted">{a.contents}</div>}
        </li>
      ))}
    </ul>
  );
}

function Failure({ onFailed }: { onFailed?: () => void }) {
  const { status } = useDocument();
  const failed = "state" in status && status.state === "error";
  useEffect(() => { if (failed) onFailed?.(); }, [failed, onFailed]);
  return null;
}

/** The PDF reader inside the page (Inkbar on PDF.js): pages with selectable
 *  text, search, thumbnails, outline, zoom, download and print. */
export default function PdfViewerInner({ url, docKey, page, quote, height = "80vh", onFailed, annotationsUrl, saved }: PdfViewerProps) {
  const [side, setSide] = useState(false);
  const store = useAnnotationStore(annotationsUrl, saved);
  const editable = !!annotationsUrl;
  const src = useMemo(() => ({ url, key: docKey }), [url, docKey]);
  return (
    <InkRoot src={src} assets={ASSETS} messages={inkbarEs} locale="es"
      {...(editable ? { tools: TOOLS, annotations: store.list, onAnnotationsChange: store.onAnnotationsChange, onChange: store.onChange, history: true } : {})}
      className="pdf-viewer" style={{ height, display: "flex", flexDirection: "column" }}>
      <InkToolbar label="Documento">
        <button type="button" className="ink-side-toggle" onClick={() => setSide(!side)} aria-pressed={side} title="Miniaturas, índice y tus subrayados">☰</button>
        {editable && <>
          <InkToolButton tool="select-text" />
          <InkToolButton tool="highlight" />
          <InkHighlightMenu />
          <InkToolButton tool="note" />
          <InkUndo />
          <InkRedo />
          <span className={`ink-save ${store.state}`} aria-live="polite">
            {{ saving: "guardando…", saved: "guardado", error: "no se pudo guardar", idle: "" }[store.state]}
          </span>
          <InkToolbarSeparator />
        </>}
        <InkPrevPage />
        <InkPageInput />
        <InkPageCount />
        <InkNextPage />
        <InkToolbarSeparator />
        <InkZoomOut />
        <InkZoomSelect />
        <InkZoomIn />
        <InkToolbarSeparator />
        <InkSearchButton />
        <InkDownloadButton />
        <InkPrintButton />
      </InkToolbar>
      <InkFindBar />
      <div style={{ display: "flex", flex: 1, minHeight: 0 }}>
        <InkSidebar visible={side} layout="tabs">
          {editable && <InkPanel id="mine" label="Mis subrayados" badge={store.list.filter((a) => a.source !== "pdf" && !("inReplyTo" in a && a.inReplyTo)).length}><MyMarks list={store.list} texts={store.texts.current} /></InkPanel>}
          <InkPanel id="thumbs" label="Miniaturas"><InkThumbnails /></InkPanel>
          <InkPanel id="outline" label="Índice"><InkOutline /></InkPanel>
        </InkSidebar>
        <div style={{ flex: 1, minWidth: 0, position: "relative" }}><InkViewport /></div>
      </div>
      {editable && <><InkThreadPopover /><DocRef target={store.doc} /></>}
      <JumpTo page={page} quote={quote} />
      <Failure onFailed={onFailed} />
    </InkRoot>
  );
}
