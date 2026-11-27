"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { Header } from "@/components/Header";
import { api, ApiError } from "@/lib/api";
import { type PersonRow, ROLE } from "@/lib/authors";
import { useMe } from "@/lib/useMe";

const PAGE = 100;

/** The Autores library: everyone who wrote or teaches something in your library, with a
 *  search that answers as you type. */
export default function PeoplePage() {
  const user = useMe();
  const [q, setQ] = useState("");
  const [role, setRole] = useState("");
  const [rows, setRows] = useState<PersonRow[]>([]);
  const [more, setMore] = useState(false);
  const [name, setName] = useState("");
  const [newRole, setNewRole] = useState("author");
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const load = useCallback(async (offset = 0) => {
    const p = new URLSearchParams({ limit: String(PAGE), offset: String(offset) });
    if (q.trim()) p.set("q", q.trim());
    if (role) p.set("role", role);
    const r = await api<PersonRow[]>(`/api/persons?${p}`).catch(() => [] as PersonRow[]);
    setRows((prev) => (offset ? [...prev, ...r] : r));
    setMore(r.length === PAGE);
  }, [q, role]);
  useEffect(() => {
    if (!user) return;
    const t = setTimeout(() => load(), 150);
    return () => clearTimeout(t);
  }, [user, load]);

  async function create(e: React.FormEvent) {
    e.preventDefault();
    try {
      const r = await api<{ id: string; name: string; existing: boolean }>("/api/persons", { method: "POST", body: JSON.stringify({ name, role: newRole }) });
      setMsg({ ok: true, text: r.existing ? `«${r.name}» ya estaba en Autores.` : `«${r.name}» añadido.` });
      setName(""); load();
    } catch (x) { setMsg({ ok: false, text: x instanceof ApiError ? x.message : "No se pudo añadir." }); }
  }

  return (
    <main>
      <Header user={user} />
      <h1>Autores</h1>
      <p className="muted" style={{ marginTop: 0 }}>Profesores, investigadores y autores de tu biblioteca. Se añaden al subir contenido o aquí; abre uno para ver sus fuentes, su postura por concepto, editarlo o fusionar duplicados.</p>
      <div className="row" style={{ justifyContent: "flex-start", gap: 8 }}>
        <input autoFocus placeholder="Buscar por nombre (sin tildes también: «lopez», «schoen»…)" value={q} onChange={(e) => setQ(e.target.value)} style={{ maxWidth: 420 }} />
        <div className="row" style={{ gap: 4 }} role="tablist" aria-label="Papel">
          {[["", "Todos"], ["teacher", "Profesores"], ["researcher", "Investigadores"], ["author", "Autores"]].map(([k, l]) => (
            <button key={k} className={role === k ? "primary" : ""} onClick={() => setRole(k)}>{l}</button>
          ))}
        </div>
      </div>
      <div className="card table-wrap" style={{ marginTop: 12 }}>
        {rows.length === 0 ? <p className="muted" style={{ margin: 0 }}>{q ? "Nadie coincide." : "Aún no hay autores. Añádelos al subir contenido o con el formulario de abajo."}</p> : (
          <table>
            <thead><tr><th>Nombre</th><th>Papel</th><th>Fuentes</th><th>Afirmaciones</th></tr></thead>
            <tbody>
              {rows.map((p) => (
                <tr key={p.id}>
                  <td><Link href={`/people/${p.id}`}>{p.name}</Link>{p.affiliation && <div className="muted" style={{ fontSize: "0.85rem" }}>{p.affiliation}</div>}</td>
                  <td className="muted">{ROLE[p.role ?? ""] ?? "—"}</td>
                  <td>{p.sources ? <Link href={`/library?author=${p.id}`}>{p.sources}</Link> : 0}</td>
                  <td>{p.claims}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {more && <button style={{ marginTop: 8 }} onClick={() => load(rows.length)}>Ver más</button>}
      </div>
      <form className="row card" style={{ justifyContent: "flex-start", gap: 6, marginTop: 12 }} onSubmit={create}>
        <strong>Añadir</strong>
        <input placeholder="Nombre" value={name} onChange={(e) => setName(e.target.value)} maxLength={300} style={{ maxWidth: 320 }} />
        <select value={newRole} onChange={(e) => setNewRole(e.target.value)} style={{ maxWidth: 180 }}>
          {Object.entries(ROLE).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
        </select>
        <button disabled={!name.trim()}>Añadir</button>
        {msg && <span className={msg.ok ? "ok" : "error"}>{msg.text}</span>}
      </form>
    </main>
  );
}
