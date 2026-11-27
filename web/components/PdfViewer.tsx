"use client";

import dynamic from "next/dynamic";
import { useState } from "react";
import type { PdfViewerProps } from "./PdfViewerInner";

// Loaded only in the browser (WebAssembly, workers) and only on pages that show a PDF.
const Inner = dynamic(() => import("./PdfViewerInner"), {
  ssr: false,
  loading: () => <p className="muted">Cargando el visor…</p>,
});

/** The PDF inside the page, at the cited page and quote. If the viewer cannot open the
 *  file, a link to open it in the browser's own viewer instead. */
export function PdfViewer(props: PdfViewerProps) {
  const [failed, setFailed] = useState(false);
  const href = `${props.url}${props.page ? `#page=${props.page}` : ""}`;
  if (failed) {
    return (
      <p className="card">
        El visor integrado no pudo abrir este PDF. <a href={href} target="_blank" rel="noopener">Ábrelo en una pestaña nueva</a>.
      </p>
    );
  }
  return <Inner {...props} onFailed={() => setFailed(true)} />;
}
