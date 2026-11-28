"use client";

import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { Header } from "@/components/Header";
import { PdfViewer } from "@/components/PdfViewer";
import { api } from "@/lib/api";
import { useMe } from "@/lib/useMe";

type Version = { id: string; mime: string; original_filename: string | null };
type Source = {
  id: string; title: string; authors_text: string | null; publication_id: string | null; versions: Version[];
  authors?: { id: string; name: string }[];
};

/** Reads a PDF inside the app: at the cited page (?page=) with the cited text (?q=)
 *  highlighted. Other formats open as the original file. */
function Reader() {
  const user = useMe();
  const { id } = useParams<{ id: string }>();
  const params = useSearchParams();
  const [s, setS] = useState<Source | null>(null);
  const [err, setErr] = useState(false);
  useEffect(() => { if (user) api<Source>(`/api/sources/${id}`).then(setS).catch(() => setErr(true)); }, [user, id]);
  if (!s) return <main><Header user={user} /><p className={err ? "error" : "muted"}>{err ? "Fuente no encontrada." : "Cargando…"}</p></main>;
  const v = s.versions.find((x) => x.id === params.get("v")) ?? s.versions.at(-1);
  const file = v ? `/api/sources/${s.id}/versions/${v.id}/file` : null;
  const page = Number(params.get("page")) || null;
  const quote = params.get("q");
  return (
    <main className="reader">
      <Header user={user} />
      <p className="muted" style={{ margin: "0 0 4px" }}>
        <Link href={`/sources/${s.id}`}>← {s.title}</Link>
        {s.publication_id && <> · <Link href={`/studies/${s.publication_id}`}>ficha del estudio</Link></>}
        {file && <> · <a href={file} target="_blank" rel="noopener">abrir original</a></>}
      </p>
      {quote && <p className="muted" style={{ margin: "0 0 6px", fontSize: "0.85rem" }}>Cita: «{quote.length > 160 ? `${quote.slice(0, 160)}…` : quote}»{page ? ` · p. ${page}` : ""}</p>}
      {!v || !file ? <p className="muted">Esta fuente no tiene archivo.</p>
        : v.mime !== "application/pdf" ? <p className="card">El visor integrado es para PDF. <a href={file} target="_blank" rel="noopener">Abrir el original</a>.</p>
        : <PdfViewer url={file} docKey={v.id} page={page} quote={quote} height="calc(100vh - 150px)" annotationsUrl={`/api/sources/${s.id}/versions/${v.id}/annotations`} />}
    </main>
  );
}

export default function ReadPage() {
  return <Suspense fallback={<main><p className="muted">Cargando…</p></main>}><Reader /></Suspense>;
}
