"use client";

import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";
import { Header } from "@/components/Header";
import { api } from "@/lib/api";
import { clock } from "@/lib/knowledge";
import { useMe } from "@/lib/useMe";

type Version = { id: string; mime: string; original_filename: string | null };
type Source = { id: string; title: string; kind: string; url: string | null; versions: Version[] };

function youtubeId(url: string | null) {
  const m = url?.match(/^https:\/\/www\.youtube\.com\/watch\?v=([A-Za-z0-9_-]{11})$/);
  return m ? m[1] : null;
}
type Line = { t_ms: number; text: string; segment_id: string };

/** Plays an audio/video source from the cited second, with its timed transcript. */
function Player() {
  const user = useMe();
  const { id } = useParams<{ id: string }>();
  const params = useSearchParams();
  const startMs = Number(params.get("t") ?? 0);
  const [src, setSrc] = useState<Source | null>(null);
  const [lines, setLines] = useState<Line[]>([]);
  const [now, setNow] = useState(startMs);
  const [ytStart, setYtStart] = useState({ ms: startMs, auto: false });
  const media = useRef<HTMLVideoElement & HTMLAudioElement>(null);
  const active = useRef<HTMLDivElement>(null);

  const version = src?.versions.find((v) => v.id === params.get("v")) ?? src?.versions.at(-1);
  useEffect(() => { if (user) api<Source>(`/api/sources/${id}`).then(setSrc); }, [user, id]);
  useEffect(() => {
    if (version) api<Line[]>(`/api/sources/${id}/versions/${version.id}/transcript`).then(setLines);
  }, [id, version]);
  useEffect(() => { active.current?.scrollIntoView({ block: "center" }); }, [lines.length]);

  if (!src || !version) return <main><Header user={user} /><p className="muted">Cargando…</p></main>;
  const fileUrl = `/api/sources/${id}/versions/${version.id}/file`;
  const yt = youtubeId(src.url);
  const isMedia = !!yt || version.mime.startsWith("audio/") || version.mime.startsWith("video/");
  const current = lines.reduce((acc, l, i) => (l.t_ms <= now ? i : acc), -1);
  const seek = (ms: number) => {
    if (yt) { setYtStart({ ms, auto: true }); setNow(ms); return; }
    if (media.current) { media.current.currentTime = ms / 1000; media.current.play().catch(() => {}); }
    setNow(ms);
  };
  const common = {
    ref: media, controls: true, preload: "metadata" as const, src: fileUrl,
    onLoadedMetadata: () => { if (media.current && startMs) media.current.currentTime = startMs / 1000; },
    onTimeUpdate: () => media.current && setNow(media.current.currentTime * 1000),
    style: { width: "100%", maxHeight: "45vh", background: "#000" },
  };

  return (
    <main>
      <Header user={user} />
      <p className="muted"><Link href="/library">← Biblioteca</Link></p>
      <h1 style={{ marginBottom: 8 }}>{src.title}</h1>
      {yt ? (
        <div style={{ position: "relative", paddingTop: "56.25%" }}>
          <iframe
            key={`${ytStart.ms}-${ytStart.auto}`}
            src={`https://www.youtube-nocookie.com/embed/${yt}?start=${Math.floor(ytStart.ms / 1000)}${ytStart.auto ? "&autoplay=1" : ""}`}
            title={src.title} allow="autoplay; encrypted-media; picture-in-picture" allowFullScreen
            referrerPolicy="strict-origin-when-cross-origin"
            style={{ position: "absolute", inset: 0, width: "100%", height: "100%", border: 0 }} />
        </div>
      ) : isMedia ? (
        version.mime.startsWith("video/") ? <video {...common} /> : <audio {...common} />
      ) : (
        <p className="muted">Solo hay subtítulos de esta fuente (sin audio ni vídeo).</p>
      )}
      {startMs > 0 && <p className="muted">Cita en {clock(startMs)}.</p>}
      {yt && <p className="muted"><a href={`${src.url}&t=${Math.floor(now / 1000)}s`} target="_blank" rel="noopener noreferrer">Abrir en YouTube</a></p>}
      <h2>Transcripción</h2>
      <div className="card" style={{ maxHeight: "50vh", overflowY: "auto" }}>
        {lines.length === 0 && <p className="muted">Sin transcripción todavía.</p>}
        {lines.map((l, i) => (
          <div key={i} ref={l.t_ms <= startMs && (lines[i + 1]?.t_ms ?? Infinity) > startMs ? active : undefined}
            style={{ display: "flex", gap: 10, padding: "2px 4px", background: i === current ? "var(--highlight, #fff3b0)" : undefined }}>
            <button onClick={() => seek(l.t_ms)} disabled={!isMedia} style={{ minWidth: 70 }} aria-label={`Ir a ${clock(l.t_ms)}`}>
              {clock(l.t_ms)}
            </button>
            <span>{l.text}</span>
          </div>
        ))}
      </div>
    </main>
  );
}

export default function PlayPage() {
  return <Suspense><Player /></Suspense>;
}
