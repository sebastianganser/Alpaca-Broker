/** Non-component helpers shared by the signal tabs. */

export function sentimentColor(score: number | null): string {
  if (score == null) return 'var(--on-surface-dim)';
  if (score > 0.15) return 'var(--success)';
  if (score < -0.15) return 'var(--error)';
  return 'var(--warning)';
}
