export type Progress = { lessons: number; completed: number; minutes: number; percent: number };
export type Course = {
  id: string; title: string; status: string; program_id: string | null; program: string | null;
  teacher: string | null; description: string | null; url: string | null; progress: Progress;
};
export const STATUS: Record<string, string> = {
  pending: "Pendiente", in_progress: "En curso", paused: "Pausada", completed: "Completada", archived: "Archivada",
};
export function Bar({ percent }: { percent: number }) {
  return (
    <div style={{ background: "var(--border)", borderRadius: 4, height: 6, marginTop: 6 }}>
      <div style={{ width: `${percent}%`, background: "var(--accent)", height: 6, borderRadius: 4 }} />
    </div>
  );
}
export const hours = (m: number) => (m >= 60 ? `${Math.floor(m / 60)} h ${m % 60} min` : `${m} min`);
