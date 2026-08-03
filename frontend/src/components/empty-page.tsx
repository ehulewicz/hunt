import type { LucideIcon } from "lucide-react";

export function EmptyPage({ title, description, icon: Icon }: { title: string; description: string; icon: LucideIcon }) {
  return (
    <>
      <header className="page-header"><p className="eyebrow">Personal OS</p><h1>{title}</h1></header>
      <section className="empty-state"><Icon aria-hidden="true" /><h2>Not built yet</h2><p>{description}</p></section>
    </>
  );
}
