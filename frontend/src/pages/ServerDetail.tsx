import {
  ActionIcon,
  Anchor,
  Badge,
  Box,
  Button,
  Card,
  Group,
  Loader,
  Stack,
  Tabs,
  Text,
  Tooltip,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import {
  IconArchive,
  IconArrowLeft,
  IconCalendarTime,
  IconChartAreaLine,
  IconDeviceFloppy,
  IconFolder,
  IconPlayerPlay,
  IconPlayerStop,
  IconPuzzle,
  IconRefresh,
  IconSettings,
  IconTerminal2,
  IconUsers,
  IconWorld,
} from "@tabler/icons-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { motion } from "motion/react";
import { Suspense, lazy, useEffect, useState } from "react";
import { useParams } from "react-router-dom";

import { api, ApiError } from "../api";
import { Skeleton } from "../components/AsyncState";
import { StatusBadge } from "../components/StatusBadge";
import { EASE } from "../motion";
import { hasPerm } from "../perm";
import type { Me, Server } from "../types";
import { useSocket } from "../useSocket";
// Tabs are code-split: xterm (~250KB) and uPlot are only fetched when opened.
const ConsoleTab = lazy(() => import("./tabs/ConsoleTab").then((m) => ({ default: m.ConsoleTab })));
const FilesTab = lazy(() => import("./tabs/FilesTab").then((m) => ({ default: m.FilesTab })));
const ConfigTab = lazy(() => import("./tabs/ConfigTab").then((m) => ({ default: m.ConfigTab })));
const PluginsTab = lazy(() => import("./tabs/PluginsTab").then((m) => ({ default: m.PluginsTab })));
const MetricsTab = lazy(() => import("./tabs/MetricsTab").then((m) => ({ default: m.MetricsTab })));
const BackupsTab = lazy(() => import("./tabs/BackupsTab").then((m) => ({ default: m.BackupsTab })));
const PlayersTab = lazy(() => import("./tabs/PlayersTab").then((m) => ({ default: m.PlayersTab })));
const TasksTab = lazy(() => import("./tabs/TasksTab").then((m) => ({ default: m.TasksTab })));
const SettingsTab = lazy(() => import("./tabs/SettingsTab").then((m) => ({ default: m.SettingsTab })));
const WorldsTab = lazy(() => import("./tabs/WorldsTab").then((m) => ({ default: m.WorldsTab })));

function TabFallback() {
  return (
    <Stack gap={0} py="sm" aria-busy="true">
      <Skeleton h={13} w="38%" />
      <Skeleton h={13} w="62%" />
      <Skeleton h={13} w="54%" />
      <Skeleton h={13} w="71%" />
    </Stack>
  );
}

/**
 * Wraps a lazily loaded tab panel: a skeleton while the chunk downloads, then
 * a short settle so switching tabs feels like an instrument switching
 * channels rather than a hard cut.
 */
function TabBody({ children }: { children: React.ReactNode }) {
  return (
    <Suspense fallback={<TabFallback />}>
      <motion.div
        initial={{ opacity: 0, y: 6 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.26, ease: EASE.snap }}
      >
        {children}
      </motion.div>
    </Suspense>
  );
}

export function ServerDetail() {
  const { id } = useParams();
  const serverId = Number(id);
  const qc = useQueryClient();
  const [tab, setTab] = useState<string | null>("console");
  const [live, setLive] = useState<{ state?: string; players?: string[]; install_pct?: number }>({});

  const me = useQuery<Me>({ queryKey: ["me"], queryFn: () => api.get<Me>("/auth/me"), staleTime: 60_000 });
  const perms = me.data?.permissions ?? [];
  const can = (action: string) => hasPerm(perms, "server", action);
  // 后端读写共用 server:<action>，所以这里按 Tab 对应的 action 决定整块可见性。
  const tabs = {
    console: can("console"),
    players: can("players") || can("control"),
    files: can("files"),
    config: can("config"),
    plugins: can("plugins"),
    worlds: can("config"),
    backups: can("backup") || can("config"),
    tasks: can("tasks") || can("config"),
    settings: can("config"),
  };

  const server = useQuery<Server>({
    queryKey: ["server", String(serverId)],
    queryFn: () => api.get<Server>(`/servers/${serverId}`),
    enabled: Number.isFinite(serverId),
    refetchInterval: 8000,
  });

  useSocket(Number.isFinite(serverId) ? `/ws/servers/${serverId}` : null, (msg) => {
    if (msg.t === "state") {
      setLive({ state: String(msg.state), players: (msg.players as string[]) ?? [], install_pct: Number(msg.install_pct) });
    } else if (msg.t === "players") {
      setLive((v) => ({ ...v, players: (msg.players as string[]) ?? [] }));
    } else if (msg.t === "progress") {
      setLive((v) => ({ ...v, install_pct: Number(msg.pct) }));
    }
  });

  useEffect(() => {
    if (live.install_pct === 100) qc.invalidateQueries({ queryKey: ["server", String(serverId)] });
  }, [live.install_pct, qc, serverId]);

  const control = useMutation({
    mutationFn: (action: string) => api.post(`/servers/${serverId}/${action}`),
    onError: (e) => notifications.show({ color: "red", title: "操作失败", message: (e as ApiError).detail }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["server", String(serverId)] }),
  });

  if (server.isLoading || me.isLoading) {
    return (
      <Group justify="center" py="xl">
        <Loader color="steelblue" />
      </Group>
    );
  }
  if (!server.data) {
    return (
      <Text c="dimmed" size="sm">
        服务器不存在或没有访问权限。
      </Text>
    );
  }

  const s = server.data;
  // 默认选中控制台；viewer 没有 console 权限时回退到所有人可见的监控页。
  const effectiveTab =
    tab === "console" && !tabs.console
      ? "metrics"
      : tabs[tab as keyof typeof tabs] === false
        ? "metrics"
        : tab;
  const state = live.state ?? s.runtime.state;
  const players = live.players ?? s.runtime.players;
  const running = state === "running" || state === "starting";
  const installing = (live.install_pct ?? s.runtime.install_pct) >= 0 && (live.install_pct ?? s.runtime.install_pct) < 100;

  return (
    <Stack gap="md">
      <Anchor href="/" underline="never" c="dimmed" size="xs">
        <Group gap={4}>
          <IconArrowLeft size={13} /> 返回总览
        </Group>
      </Anchor>

      <Card className="glass" p="md" withBorder>
        <Group justify="space-between" align="flex-start" wrap="nowrap">
          <Box style={{ minWidth: 0 }}>
            <Group gap={10} mb={6}>
              <Text fz={15} fw={600}>
                {s.name}
              </Text>
              <StatusBadge state={state} />
              {installing && (
                <Text fz={11} style={{ color: "var(--warn)" }} className="mono">
                  安装中 {live.install_pct ?? s.runtime.install_pct}%
                </Text>
              )}
            </Group>
            <Group gap="md">
              <Text size="11px" c="dimmed" className="mono">
                {s.kind} · {s.version || "未安装"} · {s.jar}
              </Text>
              <Text size="11px" c="dimmed" className="mono">
                :{s.port} / rcon :{s.rcon_port}
              </Text>
              {s.runtime.pid && (
                <Text size="11px" c="dimmed" className="mono">
                  pid {s.runtime.pid}
                </Text>
              )}
            </Group>
            {!!players.length && (
              <Group gap={6} mt={8}>
                <IconUsers size={13} color="var(--dim)" />
                {players.slice(0, 12).map((p) => (
                  <Badge key={p} size="xs" variant="default">
                    {p}
                  </Badge>
                ))}
                {players.length > 12 && (
                  <Text size="10.5px" c="dimmed">
                    +{players.length - 12}
                  </Text>
                )}
              </Group>
            )}
          </Box>

          <Group gap={6} wrap="nowrap">
            <Tooltip label="刷新">
              <ActionIcon variant="subtle" color="gray" onClick={() => server.refetch()}>
                <IconRefresh size={14} />
              </ActionIcon>
            </Tooltip>
            {can("control") && (
              <>
                <Button
                  size="xs"
                  variant="default"
                  leftSection={<IconPlayerPlay size={12} />}
                  loading={control.isPending}
                  disabled={running || installing}
                  onClick={() => control.mutate("start")}
                >
                  启动
                </Button>
                <Button
                  size="xs"
                  variant="default"
                  leftSection={<IconPlayerStop size={12} />}
                  disabled={!running}
                  onClick={() => control.mutate("stop")}
                >
                  停止
                </Button>
                <Button
                  size="xs"
                  variant="subtle"
                  color="gray"
                  leftSection={<IconRefresh size={12} />}
                  disabled={!running}
                  onClick={() => control.mutate("restart")}
                >
                  重启
                </Button>
              </>
            )}
          </Group>
        </Group>
      </Card>

      <Tabs value={effectiveTab} onChange={setTab} variant="default" color="steelblue">
        <Tabs.List className="server-tabs">
          {tabs.console && (
            <Tabs.Tab value="console" leftSection={<IconTerminal2 size={14} />}>
              控制台
            </Tabs.Tab>
          )}
          {tabs.players && (
            <Tabs.Tab value="players" leftSection={<IconUsers size={14} />}>
              玩家
            </Tabs.Tab>
          )}
          {tabs.files && (
            <Tabs.Tab value="files" leftSection={<IconFolder size={14} />}>
              文件
            </Tabs.Tab>
          )}
          {tabs.config && (
            <Tabs.Tab value="config" leftSection={<IconDeviceFloppy size={14} />}>
              配置
            </Tabs.Tab>
          )}
          {tabs.plugins && (
            <Tabs.Tab value="plugins" leftSection={<IconPuzzle size={14} />}>
              插件
            </Tabs.Tab>
          )}
          {tabs.worlds && (
            <Tabs.Tab value="worlds" leftSection={<IconWorld size={14} />}>
              世界
            </Tabs.Tab>
          )}
          {tabs.backups && (
            <Tabs.Tab value="backups" leftSection={<IconArchive size={14} />}>
              备份
            </Tabs.Tab>
          )}
          {tabs.tasks && (
            <Tabs.Tab value="tasks" leftSection={<IconCalendarTime size={14} />}>
              任务
            </Tabs.Tab>
          )}
          <Tabs.Tab value="metrics" leftSection={<IconChartAreaLine size={14} />}>
            监控
          </Tabs.Tab>
          {tabs.settings && (
            <Tabs.Tab value="settings" leftSection={<IconSettings size={14} />}>
              设置
            </Tabs.Tab>
          )}
        </Tabs.List>

        {tabs.console && (
          <Tabs.Panel value="console" pt="sm">
            <TabBody>
              <ConsoleTab serverId={serverId} canSend={can("console")} />
            </TabBody>
          </Tabs.Panel>
        )}
        {tabs.players && (
          <Tabs.Panel value="players" pt="md">
            <TabBody>
              <PlayersTab serverId={serverId} />
            </TabBody>
          </Tabs.Panel>
        )}
        {tabs.files && (
          <Tabs.Panel value="files" pt="md">
            <TabBody>
              <FilesTab serverId={serverId} />
            </TabBody>
          </Tabs.Panel>
        )}
        {tabs.config && (
          <Tabs.Panel value="config" pt="md">
            <TabBody>
              <ConfigTab serverId={serverId} />
            </TabBody>
          </Tabs.Panel>
        )}
        {tabs.plugins && (
          <Tabs.Panel value="plugins" pt="md">
            <TabBody>
              <PluginsTab serverId={serverId} />
            </TabBody>
          </Tabs.Panel>
        )}
        {tabs.worlds && (
          <Tabs.Panel value="worlds" pt="md">
            <TabBody>
              <WorldsTab serverId={serverId} />
            </TabBody>
          </Tabs.Panel>
        )}
        {tabs.backups && (
          <Tabs.Panel value="backups" pt="md">
            <TabBody>
              <BackupsTab serverId={serverId} />
            </TabBody>
          </Tabs.Panel>
        )}
        {tabs.tasks && (
          <Tabs.Panel value="tasks" pt="md">
            <TabBody>
              <TasksTab serverId={serverId} />
            </TabBody>
          </Tabs.Panel>
        )}
        <Tabs.Panel value="metrics" pt="md">
          <TabBody>
            <MetricsTab serverId={serverId} />
          </TabBody>
        </Tabs.Panel>
        {tabs.settings && (
          <Tabs.Panel value="settings" pt="md">
            <TabBody>
              <SettingsTab server={s} onChanged={() => server.refetch()} canDelete={can("delete")} />
            </TabBody>
          </Tabs.Panel>
        )}
      </Tabs>
    </Stack>
  );
}
