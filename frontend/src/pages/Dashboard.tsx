import {
  Box,
  Button,
  Code,
  Divider,
  Group,
  Loader,
  Modal,
  NumberInput,
  Select,
  Stack,
  Switch,
  Text,
  TextInput,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconPlus } from "@tabler/icons-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";

import { api, ApiError } from "../api";
import { ServerTable } from "../components/ServerTable";
import { hasPerm } from "../perm";
import type { Server } from "../types";

const KINDS = [
  { value: "paper", label: "Paper · 插件服（推荐）" },
  { value: "purpur", label: "Purpur · Paper 分支" },
  { value: "vanilla", label: "Vanilla · 原版" },
  { value: "fabric", label: "Fabric · 模组服" },
  { value: "forge", label: "Forge · 模组服（自动安装）" },
  { value: "neoforge", label: "NeoForge · 模组服（自动安装）" },
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
  const canControl = !!me.data && hasPerm(me.data.permissions, "server", "control");

  const servers = useQuery<Server[]>({
    queryKey: ["servers"],
    queryFn: () => api.get<Server[]>("/servers"),
    refetchInterval: 4000,
  });

  const versions = useQuery<{ versions: string[]; manual?: boolean }>({
    queryKey: ["versions", kind],
    queryFn: () => api.get(`/servers/versions/${kind}`),
    enabled: opened && !["bedrock", "custom"].includes(kind),
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
        install: !!version && !["bedrock", "custom"].includes(kind),
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
    <Stack gap="sm">
      <Group justify="space-between" align="center">
        <Group gap={8} align="baseline">
          <Text fz={16} fw={600}>
            服务器
          </Text>
          <Text fz={11} c="dimmed">
            {stats.total} 个，{stats.running} 个运行中 · 每 4s 刷新
          </Text>
        </Group>
        <Button size="xs" leftSection={<IconPlus size={13} />} onClick={() => setOpened(true)}>
          新建
        </Button>
      </Group>

      <div className="stat-strip">
        <div>
          <div className="stat-label">服务器</div>
          <div className="stat-value">{stats.total}</div>
        </div>
        <div>
          <div className="stat-label">运行中</div>
          <div className="stat-value">{stats.running}</div>
        </div>
        <div>
          <div className="stat-label">在线玩家</div>
          <div className="stat-value">{stats.players}</div>
        </div>
        <div>
          <div className="stat-label">服务端内存</div>
          <div className="stat-value">
            {stats.mem.toFixed(0)}
            <span style={{ fontSize: 11, color: "var(--dim)", marginLeft: 3 }}>MB</span>
          </div>
        </div>
      </div>

      {servers.isLoading ? (
        <Group justify="center" py="lg">
          <Loader size="xs" color="gray" />
        </Group>
      ) : servers.data?.length ? (
        <ServerTable
          servers={servers.data}
          canControl={canControl}
          busyId={busyId}
          onStart={(s) => control.mutate({ id: s.id, action: "start" })}
          onStop={(s) => control.mutate({ id: s.id, action: "stop" })}
        />
      ) : (
        <Box className="glass" p={20}>
          <Text fz={12.5}>还没有服务器。</Text>
          <Text fz={12} c="dimmed" mt={6} maw={560}>
            Paper / Purpur / 原版 / Fabric 的 jar 由面板从官方源下载并按哈希校验；Forge 与基岩版需要手动上传。
          </Text>
          <Button size="xs" mt={12} leftSection={<IconPlus size={13} />} onClick={() => setOpened(true)}>
            新建服务器
          </Button>
        </Box>
      )}

      <Modal opened={opened} onClose={() => setOpened(false)} title="新建服务器" size="md">
        <Stack gap="sm">
          <TextInput
            label="名称"
            placeholder="survival-main"
            value={name}
            onChange={(e) => setName(e.currentTarget.value)}
            description="字母、数字、点、横线与下划线"
            size="xs"
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
            size="xs"
          />
          {["bedrock", "custom"].includes(kind) ? (
            <Text fz={11.5} c="dimmed">
              该类型没有稳定的公开分发 API，创建后在详情页手动上传 jar。
            </Text>
          ) : (
            <Select
              label="游戏版本"
              placeholder={versions.isLoading ? "加载中…" : "选择版本"}
              data={versions.data?.versions ?? []}
              value={version}
              onChange={(v) => setVersion(v || "")}
              searchable
              size="xs"
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
            size="xs"
          />
          <Divider />
          <Group justify="space-between" align="flex-start" wrap="nowrap">
            <Box>
              <Text fz={12}>接受 Minecraft EULA</Text>
              <Text fz={11} c="dimmed">
                启动服务端必须同意 Mojang 最终用户许可协议。
              </Text>
            </Box>
            <Switch checked={eula} onChange={(e) => setEula(e.currentTarget.checked)} size="xs" />
          </Group>
          <Group justify="flex-end" gap="xs" mt={4}>
            <Button variant="default" size="xs" onClick={() => setOpened(false)}>
              取消
            </Button>
            <Button
              size="xs"
              loading={create.isPending}
              disabled={name.trim().length < 2 || (!version && !["bedrock", "custom"].includes(kind))}
              onClick={() => create.mutate()}
            >
              创建并安装
            </Button>
          </Group>
          <Text fz={10.5} c="dimmed">
            安装进度写在 <Code fz={10.5}>data/logs/</Code> 与详情页。
          </Text>
        </Stack>
      </Modal>
    </Stack>
  );
}
