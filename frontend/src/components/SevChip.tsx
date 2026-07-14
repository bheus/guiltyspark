export function SevChip({ level }: { level: string }) {
  return <span className={`sev sev-${level}`}>{level}</span>;
}
