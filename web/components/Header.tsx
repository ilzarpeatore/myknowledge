"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { api } from "@/lib/api";

const LINKS = [
  { href: "/", label: "Inicio" },
  { href: "/library", label: "Biblioteca" },
  { href: "/search", label: "Buscar" },
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
          <Link key={l.href} href={l.href} className={path === l.href ? "active" : ""}>
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
