"use client";

import dynamic from "next/dynamic";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { PdfViewerProps, Saved } from "./PdfViewerInner";

// Loaded only in the browser (WebAssembly, workers) and only on pages that show a PDF.
const Inner = dynamic(() => import("./PdfViewerInner"), {
  ssr: false,
  loading: () => <p className="muted">Cargando el visor…</p>,
});

/** The PDF inside the page, at the cited page and quote, with your highlights and notes
 *  (kept in Knowledge OS, never in the file) when ``annotationsUrl`` is given. If the viewer cannot open the
 *  file, a link to open it in the browser's own viewer instead. */
export function PdfViewer(props: PdfViewerProps) {
  const [failed, setFailed] = useState(false);
  // highlights and notes are loaded before the viewer opens (they are its starting list)
  const [saved, setSaved] = useState<Saved | null | undefined>(props.annotationsUrl ? undefined : null);
  useEffect(() => {
    if (!props.annotationsUrl) return;
    api<Saved>(props.annotationsUrl).then(setSaved).catch(() => setSaved({ annotations: [], items: [] }));
  }, [props.annotationsUrl]);
  const href = `${props.url}${props.page ? `#page=${props.page}` : ""}`;
  if (failed) {
    return (
      <p className="card">
        El visor integrado no pudo abrir este PDF. <a href={href} target="_blank" rel="noopener">Ábrelo en una pestaña nueva</a>.
      </p>
    );
  }
  if (saved === undefined) return <p className="muted">Cargando tus subrayados…</p>;
  return <Inner {...props} saved={saved} onFailed={() => setFailed(true)} />;
}
