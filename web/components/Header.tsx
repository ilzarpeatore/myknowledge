"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { api } from "@/lib/api";

const LINKS = [
  { href: "/", label: "Inicio" },
  { href: "/learning", label: "Aprendizaje" },
  { href: "/study", label: "Estudiar" },
  { href: "/library", label: "Biblioteca" },
  { href: "/search", label: "Buscar" },
  { href: "/ask", label: "Preguntar" },
  { href: "/concepts", label: "Conceptos" },
  { href: "/studies", label: "Estudios" },
  { href: "/references", label: "Referencias" },
  { href: "/highlights", label: "Subrayados" },
  { href: "/people", label: "Autores" },
  { href: "/analysis", label: "Análisis" },
  { href: "/tree", label: "Árbol" },
  { href: "/graph", label: "Grafo" },
  { href: "/review", label: "Revisión" },
  { href: "/ai", label: "IA" },
];

export function Header({ user }: { user: string | null }) {
  const router = useRouter();
  const path = usePathname();
  async function logout() {
    await api("/api/auth/logout", { method: "POST" }).catch(() => undefined);
    router.replace("/login");
  }
  return (
    <header className="row header">
      <nav className="row">
        <strong>Knowledge OS</strong>
        {LINKS.map((l) => (
          <Link key={l.href} href={l.href} className={path === l.href || (l.href !== "/" && path.startsWith(l.href)) ? "active" : ""}>
            {l.label}
          </Link>
        ))}
      </nav>
      {user && (
        <span className="muted">
          {user} · <button onClick={logout}>Salir</button>
        </span>
      )}
    </header>
  );
}
