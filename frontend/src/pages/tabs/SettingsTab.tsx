import {
  Alert,
  Box,
  Button,
  Card,
  Divider,
  Grid,
  Group,
  NumberInput,
  Select,
  Stack,
  Switch,
  Text,
  TextInput,
  Title,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconAlertTriangle, IconDownload, IconUpload } from "@tabler/icons-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { api, ApiError } from "../../api";
import type { Server } from "../../types";

export function SettingsTab({ server, onChanged }: { server: Server; onChanged: () => void }) {
  const qc = useQueryClient();
  const [form, setForm] = useState({
    memory_mb: server.memory_mb,
    min_memory_mb: server.min_memory_mb,
    port: server.port,
    public_address: server.public_address,
    rcon_port: server.rcon_port,
    java: server.java,
    jvm_args: server.jvm_args,
    server_args: server.server_args,
    auto_start: server.auto_start,
    auto_restart: server.auto_restart,
    eula_accepted: server.eula_accepted,
  });
  const [version, setVersion] = useState(server.version);
  const fileInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    setForm({
      memory_mb: server.memory_mb,
      min_memory_mb: server.min_memory_mb,
      port: server.port,
      public_address: server.public_address,
      rcon_port: server.rcon_port,
      java: server.java,
      jvm_args: server.jvm_args,
      server_args: server.server_args,
      auto_start: server.auto_start,
      auto_restart: server.auto_restart,
      eula_accepted: server.eula_accepted,
    });
    setVersion(server.version);
  }, [server]);

  const java = useQuery<{ runtimes: { path: string; version: string; major: number; source: string; vendor: string }[]; default: string }>({
    queryKey: ["java"],
    queryFn: () => api.get("/system/java"),
    staleTime: 60_000,
    retry: false,
  });

  const versions = useQuery<{ versions: string[]; manual?: boolean }>({
    queryKey: ["versions", server.kind],
    queryFn: () => api.get(`/servers/versions/${server.kind}`),
    enabled: !["bedrock", "custom"].includes(server.kind),
    retry: false,
    staleTime: 300_000,
  });

  const save = useMutation({
    mutationFn: () => api.patch(`/servers/${server.id}`, form),
    onSuccess: () => {
      notifications.show({ color: "teal", message: "设置已保存" });
      qc.invalidateQueries({ queryKey: ["server", String(server.id)] });
      onChanged();
    },
    onError: (e) => notifications.show({ color: "red", message: (e as ApiError).detail }),
  });

  const install = useMutation({
    mutationFn: () => api.post(`/servers/${server.id}/install`, { version }),
    onSuccess: () => notifications.show({ color: "teal", message: "已开始安装，进度见控制台" }),
    onError: (e) => notifications.show({ color: "red", message: (e as ApiError).detail }),
  });

  const upload = async (file: File) => {
    try {
      await api.upload(`/servers/${server.id}/jar`, file);
      notifications.show({ color: "teal", message: `${file.name} 已上传并设为启动 jar` });
      onChanged();
    } catch (e) {
      notifications.show({ color: "red", message: (e as ApiError).detail });
    }
  };

  return (
    <Grid gutter="md">
      <Grid.Col span={{ base: 12, md: 7 }}>
        <Card className="glass" p="md" withBorder>
          <Title order={3} mb="md">
            运行参数
          </Title>
          <Stack gap="sm">
            <Group grow>
              <NumberInput
                label="最大内存 (MB)"
                value={form.memory_mb}
                onChange={(v) => setForm((f) => ({ ...f, memory_mb: Number(v) || 1024 }))}
                min={512}
                max={32768}
                step={512}
              />
              <NumberInput
                label="初始内存 (MB)"
                value={form.min_memory_mb}
                onChange={(v) => setForm((f) => ({ ...f, min_memory_mb: Number(v) || 512 }))}
                min={128}
                max={32768}
                step={256}
              />
            </Group>
            <TextInput
              label="玩家连接地址"
              description="对外进服地址，如 mc.example.com:25565；留空则用本机游戏端口。AI 助手回答“怎么进服”时会读这里"
              placeholder="留空 = 本机:25565"
              value={form.public_address}
              onChange={(e) => setForm((f) => ({ ...f, public_address: e.currentTarget.value }))}
            />
            <Group grow>
              <NumberInput
                label="游戏端口"
                value={form.port}
                onChange={(v) => setForm((f) => ({ ...f, port: Number(v) || 25565 }))}
                min={1024}
                max={65535}
              />
              <NumberInput
                label="RCON 端口"
                value={form.rcon_port}
                onChange={(v) => setForm((f) => ({ ...f, rcon_port: Number(v) || 25575 }))}
                min={1024}
                max={65535}
              />
            </Group>
            <Select
              label="Java 运行时"
              description="来自系统探测与面板下载；需要其他版本时在「系统设置」下载"
              data={[
                { value: "", label: `面板默认（${java.data?.default ?? "java"}）` },
                ...(java.data?.runtimes ?? []).map((rt) => ({
                  value: rt.path,
                  label: `${rt.version} · Java ${rt.major} · ${rt.source === "managed" ? "面板下载" : rt.vendor || "系统"}`,
                })),
              ]}
              value={(java.data?.runtimes ?? []).some((rt) => rt.path === form.java) ? form.java : ""}
              onChange={(v) => setForm((f) => ({ ...f, java: v ?? "" }))}
              searchable
              allowDeselect={false}
            />
            <TextInput
              label="自定义 Java 路径"
              value={form.java}
              onChange={(e) => setForm((f) => ({ ...f, java: e.currentTarget.value }))}
              placeholder="/usr/bin/java"
              className="mono"
            />
            <TextInput
              label="JVM 参数"
              value={form.jvm_args}
              onChange={(e) => setForm((f) => ({ ...f, jvm_args: e.currentTarget.value }))}
              placeholder="-XX:+UseG1GC -XX:MaxGCPauseMillis=200"
              className="mono"
            />
            <TextInput
              label="服务端参数"
              value={form.server_args}
              onChange={(e) => setForm((f) => ({ ...f, server_args: e.currentTarget.value }))}
              placeholder="--nogui"
            />
            <Divider my={4} />
            <Group justify="space-between">
              <Text size="sm">面板启动时自动启动</Text>
              <Switch checked={form.auto_start} onChange={(e) => setForm((f) => ({ ...f, auto_start: e.currentTarget.checked }))} color="moss" />
            </Group>
            <Group justify="space-between">
              <Text size="sm">崩溃后自动重启（指数退避，最多 10 次）</Text>
              <Switch checked={form.auto_restart} onChange={(e) => setForm((f) => ({ ...f, auto_restart: e.currentTarget.checked }))} color="moss" />
            </Group>
            <Group justify="space-between">
              <Box>
                <Text size="sm">已接受 EULA</Text>
                <Text size="10.5px" c="dimmed">
                  关闭后服务端将拒绝启动
                </Text>
              </Box>
              <Switch checked={form.eula_accepted} onChange={(e) => setForm((f) => ({ ...f, eula_accepted: e.currentTarget.checked }))} color="moss" />
            </Group>
            <Group justify="flex-end" mt="xs">
              <Button color="moss" loading={save.isPending} onClick={() => save.mutate()}>
                保存设置
              </Button>
            </Group>
          </Stack>
        </Card>
      </Grid.Col>

      <Grid.Col span={{ base: 12, md: 5 }}>
        <Stack gap="md">
          <Card className="glass" p="md" withBorder>
            <Title order={3} mb={4}>
              服务端文件
            </Title>
            <Text size="11px" c="dimmed" mb="sm">
              当前 jar：<span className="mono">{server.jar}</span>
              {server.jar_ready ? "（已就绪）" : "（缺失）"}
            </Text>
            {!["bedrock", "custom"].includes(server.kind) ? (
              <Group align="flex-end" gap="sm">
                <Select
                  label="版本"
                  data={versions.data?.versions ?? []}
                  value={version}
                  onChange={(v) => setVersion(v || "")}
                  searchable
                  style={{ flex: 1 }}
                />
                <Button
                  variant="light"
                  color="moss"
                  leftSection={<IconDownload size={14} />}
                  loading={install.isPending}
                  onClick={() => install.mutate()}
                  disabled={!version}
                >
                  下载安装
                </Button>
              </Group>
            ) : (
              <Alert color="yellow" variant="light" icon={<IconAlertTriangle size={15} />} p="xs">
                <Text size="11px">
                  该类型需手动上传 jar（Forge 安装器需先在本地运行生成 libraries 目录，基岩版为 BDS 压缩包）。
                </Text>
              </Alert>
            )}
            <Button
              mt="sm"
              variant="subtle"
              color="gray"
              leftSection={<IconUpload size={14} />}
              onClick={() => fileInput.current?.click()}
            >
              上传 jar / zip
            </Button>
            <input
              ref={fileInput}
              type="file"
              accept=".jar,.zip"
              hidden
              onChange={(e) => {
                const f = e.currentTarget.files?.[0];
                if (f) upload(f);
                e.currentTarget.value = "";
              }}
            />
          </Card>

          <Card className="glass" p="md" withBorder style={{ borderColor: "var(--bad)" }}>
            <Title order={3} mb={4} style={{ color: "var(--bad)" }}>
              危险区域
            </Title>
            <Text size="11px" c="dimmed" mb="sm">
              强制结束会立即终止进程树，可能造成存档损伤。删除服务器不可恢复。
            </Text>
            <Group>
              <Button
                variant="light"
                color="red"
                size="xs"
                onClick={async () => {
                  try {
                    await api.post(`/servers/${server.id}/kill`);
                    notifications.show({ color: "orange", message: "已强制结束" });
                    onChanged();
                  } catch (e) {
                    notifications.show({ color: "red", message: (e as ApiError).detail });
                  }
                }}
              >
                强制结束进程
              </Button>
              <Button
                variant="subtle"
                color="red"
                size="xs"
                onClick={async () => {
                  if (!window.confirm(`删除 ${server.name} 及其全部文件？此操作不可恢复。`)) return;
                  try {
                    await api.del(`/servers/${server.id}?purge=true`);
                    window.location.assign("/");
                  } catch (e) {
                    notifications.show({ color: "red", message: (e as ApiError).detail });
                  }
                }}
              >
                删除服务器
              </Button>
            </Group>
          </Card>
        </Stack>
      </Grid.Col>
    </Grid>
  );
}
