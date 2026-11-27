"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { api, ApiError } from "@/lib/api";

export default function LoginPage() {
  const router = useRouter();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api("/api/auth/login", { method: "POST", body: JSON.stringify({ username, password }) });
      router.replace("/");
    } catch (err) {
      setError(
        err instanceof ApiError && err.status === 429
          ? "Demasiados intentos fallidos. Espera unos minutos."
          : err instanceof ApiError && err.status === 401
            ? "Usuario o contraseña incorrectos."
            : "No se pudo conectar con el servidor.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <main style={{ maxWidth: 380 }}>
      <h1>Knowledge OS</h1>
      <form className="card" onSubmit={submit}>
        <label htmlFor="u">Usuario</label>
        <input id="u" autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} required />
        <label htmlFor="p">Contraseña</label>
        <input id="p" type="password" autoComplete="current-password" value={password}
          onChange={(e) => setPassword(e.target.value)} required />
        {error && <p className="error">{error}</p>}
        <p><button className="primary" disabled={busy}>{busy ? "Entrando…" : "Entrar"}</button></p>
      </form>
    </main>
  );
}
