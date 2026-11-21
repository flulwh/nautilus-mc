import {
  Badge,
  Box,
  Button,
  Card,
  Divider,
  Grid,
  Group,
  Loader,
  Modal,
  NumberInput,
  Select,
  SimpleGrid,
  Stack,
  Switch,
  Text,
  TextInput,
  Title,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import {
  IconCpu,
  IconPlus,
  IconServer2,
  IconUsers,
  IconStack2,
} from "@tabler/icons-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";

import { api, ApiError } from "../api";
import { ServerCard } from "../components/ServerCard";
import type { Server } from "../types";

function Stat({ icon, label, value, hint }: { icon: React.ReactNode; label: string; value: string; hint?: string }) {
  return (
    <Card className="glass" p="md" withBorder>
      <Group justify="space-between" mb={6}>
        <Text className="stat-label">{label}</Text>
        <Box c="abyss.4">{icon}</Box>
      </Group>
      <Text className="stat-value tabular">{value}</Text>
      {hint && (
        <Text size="10.5px" c="dimmed" mt={4}>
          {hint}
        </Text>
      )}
    </Card>
  );
}

const KINDS = [
  { value: "paper", label: "Paper · 插件服（推荐）" },
  { value: "purpur", label: "Purpur · Paper 分支" },
  { value: "vanilla", label: "Vanilla · 原版" },
  { value: "fabric", label: "Fabric · 模组服" },
  { value: "forge", label: "Forge · 模组服（手动上传）" },
  { value: "bedrock", label: "Bedrock · 基岩版（手动上传）" },
  { value: "custom", label: "Custom · 自有 jar" },
];

export function Dashboard() {
  const qc = useQueryClient();
  const [opened, setOpened] = useState(false);
  const [busyId, setBusyId] = useState<number | null>(null);

  const [name, setName] = useState("");
  const [kind, setKind] = useState("paper");
  const [version, setVersion] = useState("");
  const [memory, setMemory] = useState(2048);
  const [eula, setEula] = useState(false);

  const me = useQuery<{ permissions: string[]; user: { role: string } }>({
    queryKey: ["me"],
    queryFn: () => api.get("/auth/me"),
    staleTime: 60_000,
  });
  const canControl =
    !!me.data && (me.data.user.role === "admin" || me.data.permissions.some((p) => p === "*:.*" || p.startsWith("server:control")));

  const servers = useQuery<Server[]>({
    queryKey: ["servers"],
    queryFn: () => api.get<Server[]>("/servers"),
    refetchInterval: 4000,
  });

  const versions = useQuery<{ versions: string[]; manual?: boolean }>({
    queryKey: ["versions", kind],
    queryFn: () => api.get(`/servers/versions/${kind}`),
    enabled: opened && !["forge", "bedrock", "custom"].includes(kind),
    staleTime: 300_000,
    retry: false,
  });

  useEffect(() => {
    if (versions.data?.versions?.length && !versions.data.versions.includes(version)) {
      setVersion(versions.data.versions.find((v) => /^\d+\.\d+(\.\d+)?$/.test(v)) ?? versions.data.versions[0]);
    }
  }, [versions.data, version]);

  const control = useMutation({
    mutationFn: async ({ id, action }: { id: number; action: string }) => {
      setBusyId(id);
      return api.post(`/servers/${id}/${action}`);
    },
    onError: (err) => notifications.show({ color: "red", title: "操作失败", message: err instanceof ApiError ? err.detail : "请重试" }),
    onSettled: () => {
      setBusyId(null);
      qc.invalidateQueries({ queryKey: ["servers"] });
    },
  });

  const create = useMutation({
    mutationFn: () =>
      api.post<Server>("/servers", {
        name: name.trim(),
        kind,
        version,
        memory_mb: memory,
        eula_accepted: eula,
        install: !!version && !["forge", "bedrock", "custom"].includes(kind),
      }),
    onSuccess: (server) => {
      notifications.show({
        color: "teal",
        title: "服务器已创建",
        message: `${server.name} 正在后台安装，进度可在详情页查看`,
      });
      setOpened(false);
      setName("");
      setEula(false);
      qc.invalidateQueries({ queryKey: ["servers"] });
      qc.setQueryData(["server", String(server.id)], server);
      window.location.assign(`/servers/${server.id}`);
    },
    onError: (err) => notifications.show({ color: "red", title: "创建失败", message: err instanceof ApiError ? err.detail : "请检查输入" }),
  });

  const stats = useMemo(() => {
    const list = servers.data ?? [];
    return {
      total: list.length,
      running: list.filter((s) => s.runtime.state === "running").length,
      players: list.reduce((acc, s) => acc + s.runtime.player_count, 0),
      mem: list.filter((s) => s.runtime.pid).reduce((acc, s) => acc + s.runtime.rss_mb, 0),
    };
  }, [servers.data]);

  return (
    <Stack gap="lg">
      <Group justify="space-between" align="flex-end">
        <Box>
          <Text className="stat-label">Overview</Text>
          <Title order={2} mt={2}>
            服务器总览
          </Title>
        </Box>
        <Button leftSection={<IconPlus size={16} />} color="abyss" onClick={() => setOpened(true)}>
          新建服务器
        </Button>
      </Group>

      <SimpleGrid cols={{ base: 2, md: 4 }} spacing="md">
        <Stat icon={<IconStack2 size={18} />} label="服务器" value={String(stats.total)} hint={`${stats.running} 个运行中`} />
        <Stat icon={<IconServer2 size={18} />} label="在线玩家" value={String(stats.players)} hint="实时日志解析" />
        <Stat icon={<IconCpu size={18} />} label="面板内存" value={`${stats.mem.toFixed(0)} MB`} hint="进程树合计 RSS" />
        <Stat icon={<IconUsers size={18} />} label="运行中" value={`${stats.running}`} hint="就绪检测通过" />
      </SimpleGrid>

      {servers.isLoading ? (
        <Group justify="center" py="xl">
          <Loader color="abyss" size="sm" />
        </Group>
      ) : servers.data?.length ? (
        <Grid gutter="md">
          {servers.data.map((server) => (
            <Grid.Col key={server.id} span={{ base: 12, sm: 6, lg: 4 }}>
              <ServerCard
                server={server}
                canControl={canControl}
                busy={busyId === server.id}
                onStart={() => control.mutate({ id: server.id, action: "start" })}
                onStop={() => control.mutate({ id: server.id, action: "stop" })}
              />
            </Grid.Col>
          ))}
        </Grid>
      ) : (
        <Card className="glass" p={44} withBorder>
          <Stack align="center" gap="xs">
            <IconServer2 size={30} color="#3d5568" />
            <Text fw={550}>还没有服务器</Text>
            <Text size="sm" c="dimmed" ta="center" maw={420}>
              新建一个 Paper / Purpur / 原版 / Fabric 服务器，面板会自动从官方源下载 jar
              并校验哈希，全程不用碰命令行。
            </Text>
            <Button mt="sm" leftSection={<IconPlus size={15} />} color="abyss" onClick={() => setOpened(true)}>
              创建第一个服务器
            </Button>
          </Stack>
        </Card>
      )}

      <Modal opened={opened} onClose={() => setOpened(false)} title="新建服务器" size="lg">
        <Stack gap="md">
          <TextInput
            label="名称"
            placeholder="survival-main"
            value={name}
            onChange={(e) => setName(e.currentTarget.value)}
            description="仅支持字母、数字、点、横线与下划线"
            required
          />
          <Select
            label="服务端类型"
            data={KINDS}
            value={kind}
            onChange={(v) => {
              setKind(v || "paper");
              setVersion("");
            }}
            allowDeselect={false}
          />
          {["forge", "bedrock", "custom"].includes(kind) ? (
            <Text size="xs" c="dimmed">
              该类型没有稳定的公开分发 API，创建后请在详情页手动上传服务端 jar。
            </Text>
          ) : (
            <Select
              label="游戏版本"
              placeholder={versions.isLoading ? "加载中…" : "选择版本"}
              data={versions.data?.versions ?? []}
              value={version}
              onChange={(v) => setVersion(v || "")}
              searchable
              nothingFoundMessage="没有匹配版本"
            />
          )}
          <NumberInput
            label="最大内存 (MB)"
            value={memory}
            onChange={(v) => setMemory(Number(v) || 2048)}
            min={512}
            max={32768}
            step={512}
          />
          <Divider />
          <Group justify="space-between" align="flex-start" wrap="nowrap">
            <Box>
              <Text size="sm" fw={550}>
                接受 Minecraft EULA
              </Text>
              <Text size="xs" c="dimmed">
                启动真实服务端必须同意 Mojang 最终用户许可协议。
              </Text>
            </Box>
            <Switch checked={eula} onChange={(e) => setEula(e.currentTarget.checked)} color="abyss" />
          </Group>
          <Group justify="flex-end" mt="xs">
            <Button variant="subtle" color="gray" onClick={() => setOpened(false)}>
              取消
            </Button>
            <Button color="abyss" loading={create.isPending} disabled={name.trim().length < 2 || (!version && !["forge", "bedrock", "custom"].includes(kind))} onClick={() => create.mutate()}>
              创建并安装
            </Button>
          </Group>
        </Stack>
      </Modal>
    </Stack>
  );
}
