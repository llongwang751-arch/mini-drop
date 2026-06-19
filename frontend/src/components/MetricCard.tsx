import type { Icon } from "@phosphor-icons/react";

interface MetricCardProps {
  label: string;
  value: number;
  note: string;
  icon: Icon;
}

export function MetricCard({ label, value, note, icon: IconComponent }: MetricCardProps) {
  return (
    <article className="metric">
      <IconComponent size={18} weight="duotone" />
      <span>{label}</span>
      <strong>{value}</strong>
      <small>{note}</small>
    </article>
  );
}
