import { Badge } from "@mantine/core";

const LABELS: Record<string, string> = {
  running: "运行中",
  starting: "启动中",
  stopping: "停止中",
  stopped: "已停止",
  crashed: "已崩溃",
};

const COLORS: Record<string, string> = {
  running: "teal",
  starting: "yellow",
  stopping: "orange",
  stopped: "gray",
  crashed: "red",
};

export function StatusBadge({ state, size = "sm" }: { state: string; size?: string }) {
  return (
    <Badge
      variant="light"
      color={COLORS[state] ?? "gray"}
      size={size}
      radius="sm"
      leftSection={<span className={`status-dot ${state}`} style={{ marginRight: -2 }} />}
      styles={{ label: { display: "flex", alignItems: "center", gap: 6 } }}
    >
      {LABELS[state] ?? state}
    </Badge>
  );
}
