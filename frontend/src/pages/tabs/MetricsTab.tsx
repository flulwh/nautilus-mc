import { Badge, Card, Grid, Group, SegmentedControl, Stack, Text } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { api } from "../../api";
import { MetricChart } from "../../components/MetricChart";
import type { MetricPoint, TpsPoint } from "../../types";
import { useSocket } from "../../useSocket";

function healthOf(p: TpsPoint | null): { label: string; color: string } | null {
  if (!p || p.tps1 == null) return null;
  if (p.tps1 >= 19.9) return { label: "流畅", color: "teal" };
  if (p.tps1 >= 18) return { label: "轻微压力", color: "yellow" };
  if (p.tps1 >= 15) return { label: "卡顿", color: "orange" };
  return { label: "严重滞后", color: "red" };
}

export function MetricsTab({ serverId }: { serverId: number }) {
  const [points, setPoints] = useState<MetricPoint[]>([]);
  const [tpsPoints, setTpsPoints] = useState<TpsPoint[]>([]);
  const [range, setRange] = useState("15");

  const history = useQuery<{ points: MetricPoint[] }>({
    queryKey: ["metrics", serverId],
    queryFn: () => api.get(`/servers/${serverId}/metrics?limit=240`),
  });

  const tpsHistory = useQuery<{ points: TpsPoint[] }>({
    queryKey: ["tps", serverId],
    queryFn: () => api.get(`/servers/${serverId}/tps?limit=240`),
  });

  useEffect(() => {
    if (history.data?.points) setPoints(history.data.points);
  }, [history.data]);

  useEffect(() => {
    if (tpsHistory.data?.points) setTpsPoints(tpsHistory.data.points);
  }, [tpsHistory.data]);

  useSocket(`/ws/servers/${serverId}`, (msg) => {
    // Two shapes share the ``metrics`` topic: a one-off backfill
    // (``metrics_history``) and one live sample per tick.
    if (msg.t === "metrics_history" && Array.isArray(msg.points)) {
      setPoints(msg.points as MetricPoint[]);
      return;
    }
    if (msg.t === "metrics" && typeof msg.cpu === "number") {
      setPoints((prev) => {
        const next = [...prev, msg as unknown as MetricPoint];
        return next.length > 240 ? next.slice(next.length - 240) : next;
      });
      return;
    }
    // TPS follows the same batch/live split (HANDOVER pitfall 8).
    if (msg.t === "tps_history" && Array.isArray(msg.points)) {
      setTpsPoints(msg.points as TpsPoint[]);
      return;
    }
    if (msg.t === "tps" && "tps1" in msg) {
      setTpsPoints((prev) => {
        const next = [...prev, msg as unknown as TpsPoint];
        return next.length > 240 ? next.slice(next.length - 240) : next;
      });
    }
  });

  // 时间范围只影响图表窗口，"当前值"始终取最新一个采样点。
  const windowMinutes = Number(range);
  const cutoff = Date.now() / 1000 - windowMinutes * 60;
  const visible = points.filter((p) => p.ts >= cutoff);
  const ts = visible.map((p) => p.ts);
  const cpu = visible.map((p) => p.cpu);
  const mem = visible.map((p) => p.rss_mb);
  const players = visible.map((p) => p.players);

  const latest = points.length ? points[points.length - 1] : null;

  const visibleTps = tpsPoints.filter((p) => p.ts >= cutoff);
  const tpsTs = visibleTps.map((p) => p.ts);
  const latestTps = tpsPoints.length ? tpsPoints[tpsPoints.length - 1] : null;
  const health = healthOf(latestTps);

  return (
    <Grid gutter="md">
      <Grid.Col span={12}>
        <Group justify="space-between">
          <Text fz={11} c="dimmed">查看窗口（点越多越细，取决于采样间隔）</Text>
          <SegmentedControl
            size="xs"
            value={range}
            onChange={setRange}
            data={[
              { value: "5", label: "5 分钟" },
              { value: "15", label: "15 分钟" },
              { value: "60", label: "1 小时" },
              { value: "1440", label: "全部" },
            ]}
          />
        </Group>
      </Grid.Col>
      <Grid.Col span={12}>
        <Card className="glass" p="md" withBorder>
          <Group justify="space-between" mb={4}>
            <Text className="stat-label">TPS（Paper 系服务端 · RCON 每 10 秒采样）</Text>
            <Group gap={8} wrap="nowrap">
              {latestTps?.mspt != null && (
                <Text size="xs" c="dimmed" className="tabular">
                  MSPT {latestTps.mspt.toFixed(2)}ms
                </Text>
              )}
              {health && (
                <Badge size="xs" variant="light" color={health.color}>
                  {health.label}
                </Badge>
              )}
              <Text size="xs" c="teal.3" className="tabular">
                {latestTps?.tps1 != null ? latestTps.tps1.toFixed(1) : "—"} / 20
              </Text>
            </Group>
          </Group>
          {tpsTs.length > 1 ? (
            <MetricChart
              timestamps={tpsTs}
              series={[
                { label: "1 分钟", color: "#48c487", data: visibleTps.map((p) => p.tps1) },
                { label: "5 分钟", color: "#8a6fd6", data: visibleTps.map((p) => p.tps5) },
                { label: "10/15 分钟", color: "#8e8e96", data: visibleTps.map((p) => p.tps10) },
              ]}
              height={190}
            />
          ) : (
            <Text size="xs" c="dimmed" py="lg">
              暂无 TPS 数据。仅 Paper / Purpur 服务端且开启 RCON 时可采样；其他服务端类型请看 CPU 与内存曲线。
            </Text>
          )}
        </Card>
      </Grid.Col>
      <Grid.Col span={12}>
        <Card className="glass" p="md" withBorder>
          <Group justify="space-between" mb={4}>
            <Text className="stat-label">CPU 占用（进程树合计，可超过 100%）</Text>
            <Text size="xs" c="steelblue.3" className="tabular">
              {latest && Number.isFinite(latest.cpu) ? `${latest.cpu.toFixed(1)}%` : "—"}
            </Text>
          </Group>
          {ts.length > 1 ? (
            <MetricChart timestamps={ts} series={[{ label: "CPU %", color: "#8e8e96", data: cpu }]} height={190} />
          ) : (
            <Text size="xs" c="dimmed" py="lg">
              等待采样数据（每 3 秒一次）
            </Text>
          )}
        </Card>
      </Grid.Col>

      <Grid.Col span={{ base: 12, md: 6 }}>
        <Card className="glass" p="md" withBorder>
          <Group justify="space-between" mb={4}>
            <Text className="stat-label">内存 RSS</Text>
            <Text size="xs" c="grape.3" className="tabular">
              {latest && Number.isFinite(latest.rss_mb) ? `${latest.rss_mb.toFixed(0)} MB` : "—"}
            </Text>
          </Group>
          {ts.length > 1 ? (
            <MetricChart timestamps={ts} series={[{ label: "RSS (MB)", color: "#8a6fd6", data: mem }]} height={170} />
          ) : (
            <Text size="xs" c="dimmed" py="lg">
              暂无数据
            </Text>
          )}
        </Card>
      </Grid.Col>

      <Grid.Col span={{ base: 12, md: 6 }}>
        <Card className="glass" p="md" withBorder>
          <Group justify="space-between" mb={4}>
            <Text className="stat-label">在线玩家</Text>
            <Text size="xs" c="teal.3" className="tabular">
              {latest?.players ?? 0}
            </Text>
          </Group>
          {ts.length > 1 ? (
            <MetricChart timestamps={ts} series={[{ label: "Players", color: "#48c487", data: players }]} height={170} />
          ) : (
            <Text size="xs" c="dimmed" py="lg">
              暂无数据
            </Text>
          )}
        </Card>
      </Grid.Col>

      <Grid.Col span={12}>
        <Card className="glass" p="md" withBorder>
          <Stack gap={4}>
            <Text className="stat-label">采样说明</Text>
            <Text size="11px" c="dimmed">
              数据来自 psutil，按进程树聚合（Windows 上 java.exe 的 JVM 在子进程中）。历史保存在内存环形缓冲区，
              保留最近 600 个采样点；面板重启后归零，不写入数据库以免污染业务表。
            </Text>
          </Stack>
        </Card>
      </Grid.Col>
    </Grid>
  );
}
