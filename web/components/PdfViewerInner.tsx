"use client";

import { useEffect, useRef, useState } from "react";
import {
  InkDownloadButton, InkFindBar, InkNextPage, InkOutline, InkPageCount, InkPageInput, InkPanel,
  InkPrevPage, InkPrintButton, InkRoot, InkSearchButton, InkSidebar, InkThumbnails, InkToolbar,
  InkToolbarSeparator, InkViewport, InkZoomIn, InkZoomOut, InkZoomSelect, useDocument, usePage,
  useSearch,
} from "@inkbar/react";
import type { PdfAssets } from "@inkbar/core";
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

export type PdfViewerProps = {
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

function Failure({ onFailed }: { onFailed?: () => void }) {
  const { status } = useDocument();
  const failed = "state" in status && status.state === "error";
  useEffect(() => { if (failed) onFailed?.(); }, [failed, onFailed]);
  return null;
}

/** The PDF reader inside the page (Inkbar on PDF.js): pages with selectable
 *  text, search, thumbnails, outline, zoom, download and print. */
export default function PdfViewerInner({ url, docKey, page, quote, height = "80vh", onFailed }: PdfViewerProps) {
  const [side, setSide] = useState(false);
  return (
    <InkRoot src={{ url, key: docKey }} assets={ASSETS} messages={inkbarEs} locale="es"
      className="pdf-viewer" style={{ height, display: "flex", flexDirection: "column" }}>
      <InkToolbar label="Documento">
        <button type="button" className="ink-side-toggle" onClick={() => setSide(!side)} aria-pressed={side} title="Miniaturas e índice">☰</button>
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
          <InkPanel id="thumbs" label="Miniaturas"><InkThumbnails /></InkPanel>
          <InkPanel id="outline" label="Índice"><InkOutline /></InkPanel>
        </InkSidebar>
        <div style={{ flex: 1, minWidth: 0, position: "relative" }}><InkViewport /></div>
      </div>
      <JumpTo page={page} quote={quote} />
      <Failure onFailed={onFailed} />
    </InkRoot>
  );
}
