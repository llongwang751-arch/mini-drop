import type { Icon } from "@phosphor-icons/react";

interface EmptyStateProps {
  icon: Icon;
  title: string;
  description: string;
  compact?: boolean;
}

export function EmptyState({ icon: IconComponent, title, description, compact = false }: EmptyStateProps) {
  return (
    <div className={`empty ${compact ? "compact" : ""}`}>
      <IconComponent size={compact ? 26 : 30} />
      <b>{title}</b>
      <span>{description}</span>
    </div>
  );
}
