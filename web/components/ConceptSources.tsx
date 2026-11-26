"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";

type Group = { key: string; name: string; sources: { id: string; title: string; claims: number }[] };

/** Where a concept appears, grouped by library section (cursos, libros, estudios...). */
export function ConceptSources({ entityId }: { entityId: string }) {
  const [groups, setGroups] = useState<Group[] | null>(null);
  useEffect(() => { api<Group[]>(`/api/entities/${entityId}/sources`).then(setGroups).catch(() => setGroups([])); }, [entityId]);
  if (!groups) return null;
  if (groups.length === 0) return <p className="muted">Ninguna fuente lo cita todavía.</p>;
  return (
    <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))" }}>
      {groups.map((g) => (
        <div className="card" key={g.key}>
          <strong><Link href={`/library?section=${g.key}`}>{g.name}</Link></strong> <span className="muted">({g.sources.length})</span>
          {g.sources.map((s) => (
            <div key={s.id} style={{ fontSize: "0.9rem" }}>
              <Link href={`/sources/${s.id}`}>{s.title}</Link> <span className="muted">· {s.claims} afirmación(es)</span>
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}
