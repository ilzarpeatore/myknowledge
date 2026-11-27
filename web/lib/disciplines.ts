import { api } from "@/lib/api";

export type Discipline = { id: string; name: string; slug: string; parent_id: string | null };

export async function loadDisciplines(): Promise<Discipline[]> {
  return api<Discipline[]>("/api/disciplines");
}

/** Branches in tree order (roots, then their areas), with their depth. */
export function ordered(all: Discipline[]): { d: Discipline; depth: number }[] {
  const out: { d: Discipline; depth: number }[] = [];
  const walk = (parent: string | null, depth: number) =>
    all.filter((d) => d.parent_id === parent).sort((a, b) => a.name.localeCompare(b.name, "es"))
      .forEach((d) => { out.push({ d, depth }); walk(d.id, depth + 1); });
  walk(null, 0);
  return out;
}
