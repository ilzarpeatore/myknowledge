/** Library sections (shared by the library, search, ask and source pages). */
import { api } from "@/lib/api";

export type Section = { key: string; name: string; description: string | null; builtin: boolean; count: number };

export const loadSections = () => api<Section[]>("/api/sections");
