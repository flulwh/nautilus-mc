const LABELS: Record<string, string> = {
  running: "运行中",
  starting: "启动中",
  stopping: "停止中",
  stopped: "已停止",
  crashed: "已崩溃",
};

/** State as a square marker plus plain text — no coloured pill. */
export function StatusBadge({ state }: { state: string }) {
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 5 }}>
      <span className={`status-dot ${state}`} />
      <span className={`status-text ${state}`}>{LABELS[state] ?? state}</span>
    </span>
  );
}
