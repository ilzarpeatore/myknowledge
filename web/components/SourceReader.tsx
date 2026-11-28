"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { PdfViewer } from "@/components/PdfViewer";
import { api } from "@/lib/api";

type Src = { id: string; title: string; versions: { id: string; mime: string }[] };

/** Reads a library source's PDF inside another page (e.g. a study's sheet). Shows
 *  nothing when the source has no PDF. */
export function SourceReader({ sourceId, height = "75vh", open = true }: { sourceId: string; height?: string; open?: boolean }) {
  const [s, setS] = useState<Src | null>(null);
  useEffect(() => { api<Src>(`/api/sources/${sourceId}`).then(setS).catch(() => {}); }, [sourceId]);
  const v = s?.versions.at(-1);
  if (!s || !v || v.mime !== "application/pdf") return null;
  return (
    <details open={open} style={{ marginBottom: 12 }}>
      <summary><strong>Leer: {s.title}</strong> <span className="muted" style={{ fontSize: "0.85rem" }}>· <Link href={`/read/${s.id}?v=${v.id}`}>pantalla completa</Link></span></summary>
      <PdfViewer url={`/api/sources/${s.id}/versions/${v.id}/file`} docKey={v.id} height={height} annotationsUrl={`/api/sources/${s.id}/versions/${v.id}/annotations`} />
    </details>
  );
}
