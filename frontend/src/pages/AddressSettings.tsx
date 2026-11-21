import {
  Alert,
  Badge,
  Button,
  Card,
  Code,
  Group,
  NumberInput,
  Select,
  Stack,
  Text,
  TextInput,
  Title,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconCopy, IconWorld } from "@tabler/icons-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { api, ApiError } from "../api";
import type { AddressConfig } from "../types";

function errText(exc: unknown): string {
  return exc instanceof ApiError ? exc.detail : exc instanceof Error ? exc.message : String(exc);
}

export function AddressSettings({ canManage }: { canManage: boolean }) {
  const qc = useQueryClient();
  const query = useQuery<AddressConfig>({
    queryKey: ["addresses"],
    queryFn: () => api.get<AddressConfig>("/system/addresses"),
    retry: false,
  });
  const [form, setForm] = useState<AddressConfig | null>(null);

  useEffect(() => {
    if (query.data) setForm(query.data);
  }, [query.data]);

  const save = useMutation({
    mutationFn: (payload: Record<string, unknown>) => api.put<AddressConfig>("/system/addresses", payload),
    onSuccess: (data) => {
      setForm(data);
      qc.invalidateQueries({ queryKey: ["addresses"] });
      notifications.show({ color: "teal", message: "地址配置已保存" });
    },
    onError: (exc) => notifications.show({ color: "red", message: errText(exc) }),
  });

  if (query.isError) {
    return (
      <Card className="glass" p="md" withBorder>
        <Group justify="space-between">
          <Text fz={11.5} c="red">
            读取地址配置失败：{errText(query.error)}
          </Text>
          <Button size="compact-xs" variant="default" onClick={() => query.refetch()}>
            重试
          </Button>
        </Group>
      </Card>
    );
  }
  if (!form) {
    return (
      <Card className="glass" p="md" withBorder>
        <Text fz={11.5} c="dimmed">读取地址配置中…</Text>
      </Card>
    );
  }

  const set = <K extends keyof AddressConfig>(key: K, value: AddressConfig[K]) => setForm({ ...form, [key]: value });
  const copy = (text: string) => {
    void navigator.clipboard.writeText(text).then(
      () => notifications.show({ color: "teal", message: "已复制到剪贴板" }),
      () => notifications.show({ color: "red", message: "复制失败，请手动选中" }),
    );
  };

  return (
    <Card className="glass" p="md" withBorder>
      <Group justify="space-between" mb={4}>
        <Group gap={8}>
          <IconWorld size={15} />
          <Title order={3}>地址配置</Title>
          {form.inferred ? (
            <Badge size="xs" color="gray" variant="light">自动推断</Badge>
          ) : (
            <Badge size="xs" color="teal" variant="light">已配置</Badge>
          )}
        </Group>
      </Group>
      <Text fz={11} c="dimmed" mb="sm">
        面板地址用于生成可分享的链接；MCP 地址给外部 agent（Claude Desktop / Cursor）连接面板。
        每台服务器的进服地址在「服务器 → 设置」里单独填。
      </Text>

      <Stack gap="xs">
        <TextInput
          label="面板对外地址"
          description={form.inferred ? `未配置时按访问地址推断：${form.effective_panel_url}` : `当前生效：${form.effective_panel_url}`}
          placeholder="https://mc.example.com"
          value={form.panel_url}
          onChange={(e) => set("panel_url", e.currentTarget.value)}
          disabled={!canManage}
        />
        <Group grow align="flex-end">
          <Select
            label="MCP 传输模式"
            description="stdio 由客户端拉起；http 常驻监听，需自行放到反代后"
            data={[
              { value: "stdio", label: "stdio（本地客户端拉起）" },
              { value: "http", label: "streamable-http（常驻服务）" },
            ]}
            value={form.mcp_mode}
            onChange={(v) => set("mcp_mode", (v ?? "stdio") as AddressConfig["mcp_mode"])}
            disabled={!canManage}
          />
          <TextInput
            label="MCP 监听地址"
            value={form.mcp_host}
            onChange={(e) => set("mcp_host", e.currentTarget.value)}
            disabled={!canManage || form.mcp_mode !== "http"}
          />
          <NumberInput
            label="MCP 端口"
            value={form.mcp_port}
            onChange={(v) => set("mcp_port", typeof v === "number" ? v : 8765)}
            min={1024}
            max={65535}
            disabled={!canManage || form.mcp_mode !== "http"}
          />
        </Group>

        {form.mcp_exposed && form.mcp_mode === "http" && (
          <Alert color="orange" variant="light" p="xs">
            <Text fz={11}>
              MCP 监听在非本机地址，懂得调用工具的人等于拥有这台面板的全部权限。请只在可信内网使用，
              并通过防火墙或反向代理限制来源。
            </Text>
          </Alert>
        )}

        <Text fz={10.5} c="dimmed">MCP 客户端配置片段：</Text>
        <Code block style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere", maxHeight: 150, overflow: "auto" }}>
          {form.mcp_client_example}
        </Code>
        <Group gap={6}>
          <Button size="compact-xs" variant="default" leftSection={<IconCopy size={12} />} onClick={() => copy(form.mcp_client_example)}>
            复制配置
          </Button>
          <Button
            size="compact-xs"
            color="moss"
            disabled={!canManage}
            loading={save.isPending}
            onClick={() =>
              save.mutate({
                panel_url: form.panel_url,
                mcp_mode: form.mcp_mode,
                mcp_host: form.mcp_host,
                mcp_port: form.mcp_port,
              })
            }
          >
            保存
          </Button>
          <Text fz={10.5} c="dimmed">改完 MCP 模式需重启 MCP 进程才生效。</Text>
        </Group>
      </Stack>
    </Card>
  );
}
