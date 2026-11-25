"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";

/** Current username; redirects to /login when the session is missing or expired. */
export function useMe(): string | null {
  const router = useRouter();
  const [user, setUser] = useState<string | null>(null);
  useEffect(() => {
    api<{ username: string }>("/api/auth/me")
      .then((me) => setUser(me.username))
      .catch((err) => {
        if (err instanceof ApiError && err.status === 401) router.replace("/login");
      });
  }, [router]);
  return user;
}
