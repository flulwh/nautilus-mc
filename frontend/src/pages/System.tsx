import {
  ActionIcon,
  Badge,
  Button,
  Card,
  Code,
  Divider,
  Grid,
  Group,
  MultiSelect,
  NumberInput,
  Select,
  Stack,
  Switch,
  Table,
  Text,
  TextInput,
  Title,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconCheck, IconCopy, IconPlus, IconRefresh, IconSend, IconTrash } from "@tabler/icons-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { api, ApiError } from "../api";
import { AddressSettings } from "./AddressSettings";
import { AiSettings } from "./AiSettings";
import type { Me } from "../types";
import { useSocket } from "../useSocket";

interface JavaRuntime {
  major: number;
  version: string;
  vendor: string;
  path: string;
  source: "system" | "managed";
  arch: string;
}

interface JavaPayload {
  runtimes: JavaRuntime[];
  majors: number[];
  default: string;
  managed_dir: string;
  downloading: number[];
}

interface Hook {
  id?: string;
  name: string;
  url: string;
  enabled: boolean;
  events: string[];
  format: "auto" | "discord" | "json";
}

interface HookPayload {
  webhooks: Hook[];
  events: string[];
}

interface TokenRow {
  id: number;
  name: string;
  prefix: string;
  created_at: string | null;
  last_used_at: string | null;
  expires_at: string | null;
}

interface SystemInfo {
  python: string;
  platform: string;
  java: string;
  data_dir: string;
  servers: number;
  users: number;
  now: string;
}

interface MetaInfo {
  name: string;
  version: string;
  tagline: string;
  runtime: string;
  authz_ready: boolean;
  extensions: string[];
  server_kinds: string[];
  limits: Record<string, number>;
}

const EVENT_LABELS: Record<string, string> = {
  "*": "全部事件",
  "server.started": "服务器启动",
  "server.stopped": "服务器停止",
  "server.crashed": "服务器崩溃",
  "backup.created": "备份完成",
  "backup.restored": "备份恢复",
  "task.failed": "定时任务失败",
  "install.failed": "安装失败",
  "player.join": "玩家加入",
  "player.leave": "玩家离开",
};

const errText = (e: unknown) => (e instanceof ApiError ? e.detail : "操作失败");

export function SystemPage() {
  const qc = useQueryClient();
  const me = useQuery<Me>({ queryKey: ["me"], queryFn: () => api.get<Me>("/auth/me"), staleTime: 60_000 });
  const perms = me.data?.permissions ?? [];
  const canManage = perms.includes("*:.*") || perms.includes("system:manage");

  const info = useQuery<SystemInfo>({
    queryKey: ["system-info"],
    queryFn: () => api.get<SystemInfo>("/system"),
    staleTime: 60_000,
  });
  const meta = useQuery<MetaInfo>({
    queryKey: ["system-meta"],
    queryFn: () => api.get<MetaInfo>("/meta"),
    staleTime: 300_000,
  });

  // ---------------------------------------------------------------- java
  const java = useQuery<JavaPayload>({
    queryKey: ["java"],
    queryFn: () => api.get<JavaPayload>("/system/java"),
    staleTime: 20_000,
  });
  const [progress, setProgress] = useState<{ major: number; pct: number; stage: string } | null>(null);
  useSocket("/ws/events", (msg) => {
    if (msg.t !== "java") return;
    const pct = Number(msg.pct);
    setProgress({ major: Number(msg.major), pct, stage: String(msg.stage ?? "") });
    if (pct === 100 || pct < 0) {
      qc.invalidateQueries({ queryKey: ["java"] });
    }
  });

  const downloadJava = useMutation({
    mutationFn: (major: number) => api.post(`/system/java/${major}`),
    onSuccess: () => notifications.show({ color: "teal", message: "开始下载，完成后会出现在列表里" }),
    onError: (e) => notifications.show({ color: "red", message: errText(e) }),
  });
  const removeJava = useMutation({
    mutationFn: (path: string) => api.del(`/system/java?path=${encodeURIComponent(path)}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["java"] }),
    onError: (e) => notifications.show({ color: "red", message: errText(e) }),
  });

  // ------------------------------------------------------------ webhooks
  const hooksQuery = useQuery<HookPayload>({
    queryKey: ["webhooks"],
    queryFn: () => api.get<HookPayload>("/system/webhooks"),
  });
  const [hooks, setHooks] = useState<Hook[]>([]);
  const [draft, setDraft] = useState<Hook>({ name: "", url: "", enabled: true, events: ["server.crashed"], format: "auto" });
  useEffect(() => {
    if (hooksQuery.data) setHooks(hooksQuery.data.webhooks);
  }, [hooksQuery.data]);

  const saveHooks = useMutation({
    mutationFn: () => api.put<HookPayload>("/system/webhooks", { webhooks: hooks }),
    onSuccess: (data) => {
      setHooks(data.webhooks);
      notifications.show({ color: "teal", message: "通知设置已保存" });
    },
    onError: (e) => notifications.show({ color: "red", message: errText(e) }),
  });
  const testHook = useMutation({
    mutationFn: (hook: Hook) => api.post("/system/webhooks/test", hook),
    onSuccess: () => notifications.show({ color: "teal", message: "测试消息已发送" }),
    onError: (e) => notifications.show({ color: "red", message: errText(e) }),
  });

  // -------------------------------------------------------------- tokens
  const tokens = useQuery<{ tokens: TokenRow[] }>({
    queryKey: ["tokens"],
    queryFn: () => api.get<{ tokens: TokenRow[] }>("/tokens"),
  });
  const [tokenName, setTokenName] = useState("");
  const [tokenDays, setTokenDays] = useState(90);
  const [fresh, setFresh] = useState("");
  const createToken = useMutation({
    mutationFn: () => api.post<{ token: string }>("/tokens", { name: tokenName, days: tokenDays }),
    onSuccess: (data) => {
      setFresh(data.token);
      setTokenName("");
      qc.invalidateQueries({ queryKey: ["tokens"] });
    },
    onError: (e) => notifications.show({ color: "red", message: errText(e) }),
  });
  const revoke = useMutation({
    mutationFn: (id: number) => api.del(`/tokens/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["tokens"] }),
    onError: (e) => notifications.show({ color: "red", message: errText(e) }),
  });

  const eventOptions = (hooksQuery.data?.events ?? []).map((e) => ({ value: e, label: EVENT_LABELS[e] ?? e }));

  return (
    <Grid gutter="md" className="stagger-cols">
      <Grid.Col span={12}>
        <Card className="glass" p="md" withBorder>
          <Group justify="space-between" mb="sm">
            <Group gap={8}>
              <Title order={3}>系统信息</Title>
              {meta.data && (
                <Badge size="xs" variant="light" color={meta.data.authz_ready ? "teal" : "red"}>
                  权限引擎{meta.data.authz_ready ? "就绪" : "异常"}
                </Badge>
              )}
            </Group>
            <ActionIcon variant="subtle" color="gray" size="sm" onClick={() => { void info.refetch(); void meta.refetch(); }} aria-label="刷新">
              <IconRefresh size={14} />
            </ActionIcon>
          </Group>
          {info.isError || meta.isError ? (
            <Text fz={12} c="red">读取系统信息失败：{errText(info.error ?? meta.error)}</Text>
          ) : info.isLoading ? (
            <Text fz={12} c="dimmed">读取中…</Text>
          ) : info.data ? (
            <Grid gutter="xs">
              {[
                ["面板", `${meta.data?.name ?? "Nautilus"} v${meta.data?.version ?? "?"}`],
                ["平台", info.data.platform],
                ["Python", info.data.python],
                ["Java（默认）", info.data.java],
                ["数据目录", info.data.data_dir],
                ["服务器 / 用户", `${info.data.servers} / ${info.data.users}`],
                ["运行时", meta.data?.runtime ?? "—"],
                ["扩展", meta.data?.extensions?.length ? meta.data.extensions.join(", ") : "无"],
              ].map(([label, value]) => (
                <Grid.Col key={label} span={{ base: 12, md: 4 }}>
                  <Group gap={6} wrap="nowrap" align="flex-start">
                    <Text fz={10.5} c="dimmed" style={{ width: 84, flexShrink: 0 }}>{label}</Text>
                    <Text fz={11.5} className="mono" style={{ wordBreak: "break-all" }}>{value}</Text>
                  </Group>
                </Grid.Col>
              ))}
            </Grid>
          ) : null}
        </Card>
      </Grid.Col>
      <Grid.Col span={12}>
        <Card className="glass" p="md" withBorder>
          <Group justify="space-between" mb={4}>
            <Title order={3}>Java 运行时</Title>
            <Group gap={6}>
              {(java.data?.majors ?? [8, 17, 21, 25]).map((major) => (
                <Button
                  key={major}
                  size="compact-xs"
                  variant="default"
                  disabled={!canManage || progress?.major === major}
                  loading={downloadJava.isPending && downloadJava.variables === major}
                  onClick={() => downloadJava.mutate(major)}
                >
                  下载 JDK {major}
                </Button>
              ))}
            </Group>
          </Group>
          <Text fz={11} c="dimmed" mb="sm">
            低于 1.17 需要 Java 8，1.18–1.20 需要 17，1.20.5 之后需要 21。下载来源为 Eclipse Temurin（Adoptium）。
          </Text>
          {progress && (
            <Text fz={11} className="mono" mb={6} style={{ color: progress.pct < 0 ? "var(--bad)" : "var(--dim)" }}>
              JDK {progress.major} · {progress.pct < 0 ? progress.stage : `${progress.pct}% ${progress.stage}`}
            </Text>
          )}
          <Table verticalSpacing="xs">
            <Table.Thead>
              <Table.Tr>
                <Table.Th>版本</Table.Th>
                <Table.Th>来源</Table.Th>
                <Table.Th>路径</Table.Th>
                <Table.Th />
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {(java.data?.runtimes ?? []).map((rt) => (
                <Table.Tr key={rt.path}>
                  <Table.Td>
                    <Group gap={6} wrap="nowrap">
                      <Text fz={12} className="mono">
                        {rt.version}
                      </Text>
                      <Badge size="xs" variant="default">
                        {rt.major}
                      </Badge>
                    </Group>
                  </Table.Td>
                  <Table.Td>
                    <Text fz={11} c="dimmed">
                      {rt.source === "managed" ? "面板下载" : rt.vendor || "系统"}
                    </Text>
                  </Table.Td>
                  <Table.Td>
                    <Text fz={10.5} c="dimmed" className="mono" style={{ wordBreak: "break-all" }}>
                      {rt.path}
                    </Text>
                  </Table.Td>
                  <Table.Td>
                    {rt.source === "managed" && canManage && (
                      <ActionIcon
                        variant="subtle"
                        color="red"
                        size="sm"
                        onClick={() => removeJava.mutate(rt.path)}
                        aria-label="删除"
                      >
                        <IconTrash size={13} />
                      </ActionIcon>
                    )}
                  </Table.Td>
                </Table.Tr>
              ))}
              {!java.data?.runtimes.length && (
                <Table.Tr>
                  <Table.Td colSpan={4}>
                    <Text fz={12} c="dimmed">
                      没有检测到 Java。先下载一个运行时，否则服务器无法启动。
                    </Text>
                  </Table.Td>
                </Table.Tr>
              )}
            </Table.Tbody>
          </Table>
        </Card>
      </Grid.Col>

      <Grid.Col span={{ base: 12, md: 7 }}>
        <Card className="glass" p="md" withBorder>
          <Title order={3} mb={4}>
            通知 Webhook
          </Title>
          <Text fz={11} c="dimmed" mb="sm">
            Discord 地址自动使用 embed 格式；其他地址收到扁平 JSON（panel / event / server / detail）。
          </Text>
          <Stack gap="xs">
            {hooks.map((hook, index) => (
              <Card key={hook.id ?? index} p="xs" withBorder>
                <Group justify="space-between" wrap="nowrap" mb={6}>
                  <Group gap={8} wrap="nowrap" style={{ minWidth: 0 }}>
                    <Text fz={12} fw={500}>
                      {hook.name || "webhook"}
                    </Text>
                    <Text fz={10.5} c="dimmed" className="mono" style={{ overflow: "hidden", textOverflow: "ellipsis" }}>
                      {hook.url}
                    </Text>
                  </Group>
                  <Group gap={4} wrap="nowrap">
                    <Switch
                      size="xs"
                      color="steelblue"
                      checked={hook.enabled}
                      onChange={(e) =>
                        setHooks((all) => all.map((h, i) => (i === index ? { ...h, enabled: e.currentTarget.checked } : h)))
                      }
                    />
                    <ActionIcon variant="subtle" color="gray" size="sm" onClick={() => testHook.mutate(hook)} aria-label="测试">
                      <IconSend size={13} />
                    </ActionIcon>
                    <ActionIcon
                      variant="subtle"
                      color="red"
                      size="sm"
                      onClick={() => setHooks((all) => all.filter((_, i) => i !== index))}
                      aria-label="删除"
                    >
                      <IconTrash size={13} />
                    </ActionIcon>
                  </Group>
                </Group>
                <Text fz={10.5} c="dimmed">
                  {hook.events.includes("*") ? "全部事件" : hook.events.map((e) => EVENT_LABELS[e] ?? e).join("、")}
                </Text>
              </Card>
            ))}
            {!hooks.length && (
              <Text fz={12} c="dimmed">
                还没有通知渠道。
              </Text>
            )}
          </Stack>

          <Divider my="sm" />
          <Stack gap="xs">
            <Group grow>
              <TextInput
                size="xs"
                label="名称"
                placeholder="运维群"
                value={draft.name}
                onChange={(e) => setDraft((d) => ({ ...d, name: e.currentTarget.value }))}
              />
              <Select
                size="xs"
                label="格式"
                data={[
                  { value: "auto", label: "自动识别" },
                  { value: "discord", label: "Discord Embed" },
                  { value: "json", label: "JSON" },
                ]}
                value={draft.format}
                allowDeselect={false}
                onChange={(v) => setDraft((d) => ({ ...d, format: (v as Hook["format"]) ?? "auto" }))}
              />
            </Group>
            <TextInput
              size="xs"
              label="地址"
              placeholder="https://discord.com/api/webhooks/..."
              value={draft.url}
              onChange={(e) => setDraft((d) => ({ ...d, url: e.currentTarget.value }))}
              className="mono"
            />
            <MultiSelect
              size="xs"
              label="订阅事件"
              data={eventOptions}
              value={draft.events}
              onChange={(v) => setDraft((d) => ({ ...d, events: v }))}
              searchable
            />
            <Group justify="space-between">
              <Button
                size="xs"
                variant="default"
                leftSection={<IconPlus size={12} />}
                disabled={!draft.url.trim()}
                onClick={() => {
                  setHooks((all) => [...all, { ...draft, name: draft.name || "webhook" }]);
                  setDraft({ name: "", url: "", enabled: true, events: ["server.crashed"], format: "auto" });
                }}
              >
                添加
              </Button>
              <Button size="xs" color="steelblue" loading={saveHooks.isPending} disabled={!canManage} onClick={() => saveHooks.mutate()}>
                保存通知设置
              </Button>
            </Group>
          </Stack>
        </Card>
      </Grid.Col>

      <Grid.Col span={{ base: 12, md: 5 }}>
        <Card className="glass" p="md" withBorder>
          <Title order={3} mb={4}>
            API 令牌
          </Title>
          <Text fz={11} c="dimmed" mb="sm">
            用于脚本与 CI：<Code fz={10.5}>Authorization: Bearer nlt_…</Code>。权限等同创建者，降权立即生效。
          </Text>
          <Stack gap="xs">
            <Group grow align="flex-end">
              <TextInput
                size="xs"
                label="名称"
                placeholder="backup-cron"
                value={tokenName}
                onChange={(e) => setTokenName(e.currentTarget.value)}
              />
              <NumberInput
                size="xs"
                label="有效期（天）"
                description="0 表示永久"
                value={tokenDays}
                min={0}
                max={3650}
                onChange={(v) => setTokenDays(Number(v) || 0)}
              />
            </Group>
            <Group justify="flex-end">
              <Button size="xs" color="steelblue" loading={createToken.isPending} onClick={() => createToken.mutate()}>
                生成令牌
              </Button>
            </Group>
          </Stack>

          {fresh && (
            <Card p="xs" withBorder mt="sm" style={{ borderColor: "var(--warn)" }}>
              <Text fz={11} mb={4} style={{ color: "var(--warn)" }}>
                只显示一次，请立即保存。
              </Text>
              <Group gap={6} wrap="nowrap">
                <Code fz={10.5} style={{ wordBreak: "break-all", flex: 1 }}>
                  {fresh}
                </Code>
                <ActionIcon
                  size="sm"
                  variant="subtle"
                  onClick={async () => {
                    try {
                      await navigator.clipboard.writeText(fresh);
                      notifications.show({ color: "teal", message: "已复制" });
                    } catch {
                      notifications.show({ color: "red", message: "复制失败，请手动选择" });
                    }
                  }}
                  aria-label="复制"
                >
                  <IconCopy size={13} />
                </ActionIcon>
              </Group>
            </Card>
          )}

          <Table verticalSpacing="xs" mt="sm">
            <Table.Tbody>
              {(tokens.data?.tokens ?? []).map((t) => (
                <Table.Tr key={t.id}>
                  <Table.Td>
                    <Text fz={12}>{t.name}</Text>
                    <Text fz={10.5} c="dimmed">
                      创建 {t.created_at?.slice(0, 10)} · {t.expires_at ? `到期 ${t.expires_at.slice(0, 10)}` : "永久"}
                      {t.last_used_at ? ` · 最近 ${t.last_used_at.slice(0, 10)}` : ""}
                    </Text>
                  </Table.Td>
                  <Table.Td>
                    <ActionIcon variant="subtle" color="red" size="sm" onClick={() => revoke.mutate(t.id)} aria-label="吊销">
                      <IconTrash size={13} />
                    </ActionIcon>
                  </Table.Td>
                </Table.Tr>
              ))}
              {!tokens.data?.tokens.length && (
                <Table.Tr>
                  <Table.Td>
                    <Text fz={11.5} c="dimmed">
                      还没有令牌。
                    </Text>
                  </Table.Td>
                </Table.Tr>
              )}
            </Table.Tbody>
          </Table>
          <Group gap={4} mt="xs">
            <IconCheck size={12} color="var(--dim)" />
            <Text fz={10.5} c="dimmed">
              令牌只存 SHA-256 摘要，数据库中看不到明文。
            </Text>
          </Group>
        </Card>
      </Grid.Col>

      <Grid.Col span={12}>
        <AddressSettings canManage={canManage} />
      </Grid.Col>

      <Grid.Col span={12}>
        <AiSettings canManage={canManage} />
      </Grid.Col>
    </Grid>
  );
}
