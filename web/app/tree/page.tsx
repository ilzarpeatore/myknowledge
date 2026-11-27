"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Header } from "@/components/Header";
import { api } from "@/lib/api";
import { useMe } from "@/lib/useMe";

type TreeNode = {
  kind: "discipline" | "entity"; id: string | null; name: string; slug?: string; via?: string | null;
  children: TreeNode[];
};

const VIA: Record<string, string> = { is_a: "es un tipo de", part_of: "forma parte de" };

function count(n: TreeNode): number {
  return (n.kind === "entity" ? 1 : 0) + n.children.reduce((a, c) => a + count(c), 0);
}

function Node({ n, depth, open }: { n: TreeNode; depth: number; open: boolean }) {
  const label = n.kind === "entity"
    ? <><Link href={`/concepts/${n.slug}`}>{n.name}</Link>{n.via && <span className="muted" title={VIA[n.via]}> ({VIA[n.via]} ↑)</span>}</>
    : <strong>{n.name} <span className="muted">({count(n)})</span></strong>;
  if (n.children.length === 0) return <li style={{ listStyle: "none", margin: "2px 0" }}>{label}</li>;
  return (
    <li style={{ listStyle: "none", margin: "2px 0" }}>
      <details open={open}>
        <summary style={{ cursor: "pointer" }}>{label}</summary>
        <ul style={{ paddingLeft: 18, margin: 0, borderLeft: "1px solid var(--border)" }}>
          {n.children.map((c, i) => <Node key={`${c.id}-${i}`} n={c} depth={depth + 1} open={open} />)}
        </ul>
      </details>
    </li>
  );
}

export default function TreePage() {
  const user = useMe();
  const [tree, setTree] = useState<TreeNode[] | null>(null);
  const [open, setOpen] = useState(false);
  useEffect(() => { if (user) api<TreeNode[]>("/api/graph/tree").then(setTree); }, [user]);
  return (
    <main>
      <Header user={user} />
      <div className="row">
        <h1>Árbol de conocimiento</h1>
        <button onClick={() => setOpen(!open)}>{open ? "Contraer todo" : "Desplegar todo"}</button>
      </div>
      <p className="muted">
        Disciplinas y áreas con sus conceptos. Dentro de cada concepto, los que «son un tipo de» o «forman parte de» él.
        Asigna disciplinas desde la ficha de cada concepto.
      </p>
      {!tree ? <p className="muted">Cargando…</p> : tree.length === 0 ? <p className="muted">Aún no hay disciplinas ni conceptos.</p> : (
        <ul className="card" style={{ paddingLeft: 12 }} key={String(open)}>
          {tree.map((n, i) => <Node key={`${n.id}-${i}`} n={n} depth={0} open={open} />)}
        </ul>
      )}
    </main>
  );
}
