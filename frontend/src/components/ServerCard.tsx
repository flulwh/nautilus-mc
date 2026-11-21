import { ActionIcon, Box, Button, Card, Group, Progress, Stack, Text, Tooltip } from "@mantine/core";
import {
  IconArrowUpRight,
  IconPlayerPlay,
  IconPlayerStop,
  IconRefresh,
  IconUsers,
} from "@tabler/icons-react";
import { Link } from "react-router-dom";

import type { Server } from "../types";
import { StatusBadge } from "./StatusBadge";

function uptime(seconds: number): string {
  if (!seconds) return "—";
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (d) return `${d}天 ${h}时`;
  if (h) return `${h}时 ${m}分`;
  return `${m}分`;
}

export function ServerCard({
  server,
  canControl,
  busy,
  onStart,
  onStop,
}: {
  server: Server;
  canControl: boolean;
  busy: boolean;
  onStart: () => void;
  onStop: () => void;
}) {
  const rt = server.runtime;
  const running = rt.state === "running" || rt.state === "starting";
  const memPct = Math.min(100, (rt.rss_mb / Math.max(1, server.memory_mb)) * 100);
  const installing = rt.install_pct >= 0 && rt.install_pct < 100;

  return (
    <Card className="server-card glass" p="lg" withBorder>
      <Group justify="space-between" align="flex-start" mb="md" wrap="nowrap">
        <Box style={{ minWidth: 0 }}>
          <Group gap={8} wrap="nowrap">
            <Text fw={600} size="md" truncate>
              {server.name}
            </Text>
            <StatusBadge state={rt.state} />
          </Group>
          <Text size="xs" c="dimmed" mt={5} className="mono">
            {server.kind} · {server.version || "未安装"} · :{server.port}
          </Text>
        </Box>
        <Group gap={4} wrap="nowrap">
          {canControl &&
            (running ? (
              <Tooltip label="停止">
                <ActionIcon variant="light" color="orange" loading={busy} onClick={onStop}>
                  <IconPlayerStop size={15} />
                </ActionIcon>
              </Tooltip>
            ) : (
              <Tooltip label="启动">
                <ActionIcon variant="light" color="teal" loading={busy} onClick={onStart}>
                  <IconPlayerPlay size={15} />
                </ActionIcon>
              </Tooltip>
            ))}
          <Tooltip label="详情">
            <ActionIcon component={Link} to={`/servers/${server.id}`} variant="subtle" color="gray">
              <IconArrowUpRight size={16} />
            </ActionIcon>
          </Tooltip>
        </Group>
      </Group>

      {installing ? (
        <Box mb="md">
          <Group justify="space-between" mb={4}>
            <Text size="xs" c="abyss.3">
              正在安装服务端…
            </Text>
            <Text size="xs" c="dimmed" className="tabular">
              {rt.install_pct}%
            </Text>
          </Group>
          <Progress value={rt.install_pct} color="abyss" size="sm" radius="xl" animated />
        </Box>
      ) : (
        <>
          <Group justify="space-between" mb={6}>
            <Group gap={6}>
              <IconUsers size={13} color="#6b8095" />
              <Text size="xs" c="dimmed">
                {rt.player_count} 在线
              </Text>
            </Group>
            <Text size="xs" c="dimmed" className="tabular">
              CPU {rt.cpu.toFixed(1)}%
            </Text>
          </Group>
          <Progress value={memPct} color={memPct > 85 ? "red" : "abyss"} size={5} radius="xl" mb={5} />
          <Group justify="space-between">
            <Text size="10.5px" c="dimmed" className="tabular">
              {rt.rss_mb.toFixed(0)} / {server.memory_mb} MB
            </Text>
            <Text size="10.5px" c="dimmed" className="tabular">
              运行 {uptime(rt.uptime)}
            </Text>
          </Group>
        </>
      )}

      {rt.state === "crashed" && (
        <Group gap={6} mt="sm">
          <IconRefresh size={13} color="#e2566b" />
          <Text size="10.5px" c="red.4" lineClamp={1}>
            {rt.last_error || `退出码 ${rt.last_exit_code}`}
          </Text>
        </Group>
      )}

      {!server.jar_ready && !installing && (
        <Button
          component={Link}
          to={`/servers/${server.id}`}
          variant="light"
          color="yellow"
          size="xs"
          fullWidth
          mt="sm"
        >
          需要安装服务端 jar
        </Button>
      )}
    </Card>
  );
}
