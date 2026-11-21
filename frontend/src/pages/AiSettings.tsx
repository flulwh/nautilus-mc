import {
  Alert,
  Badge,
  Button,
  Card,
  Group,
  NumberInput,
  PasswordInput,
  Select,
  Stack,
  Switch,
  Text,
  TextInput,
  Title,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconPlugConnected, IconRobot } from "@tabler/icons-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { api, ApiError } from "../api";
import type { AiConfig } from "../types";

function errText(exc: unknown): string {
  if (exc instanceof ApiError) return exc.detail;
  return exc instanceof Error ? exc.message : String(exc);
}

export function AiSettings({ canManage }: { canManage: boolean }) {
  const qc = useQueryClient();
  const config = useQuery<AiConfig>({
    queryKey: ["ai", "config"],
    queryFn: () => api.get<AiConfig>("/ai/config"),
    retry: false,
  });

  const [form, setForm] = useState<AiConfig | null>(null);
  const [preset, setPreset] = useState<string | null>(null);
  const [apiKey, setApiKey] = useState("");

  useEffect(() => {
    if (config.data) setForm(config.data);
  }, [config.data]);

  const save = useMutation({
    mutationFn: (payload: Record<string, unknown>) => api.put<AiConfig>("/ai/config", payload),
    onSuccess: (data) => {
      setForm(data);
      setApiKey("");
      qc.invalidateQueries({ queryKey: ["ai"] });
      notifications.show({ color: "teal", message: "AI 配置已保存" });
    },
    onError: (exc) => notifications.show({ color: "red", message: errText(exc) }),
  });

  const test = useMutation({
    mutationFn: () => api.post<{ ok: boolean; reply?: string; error?: string; model?: string }>("/ai/config/test"),
    onSuccess: (data) => {
      if (data.ok) {
        notifications.show({ color: "teal", message: `连接正常，模型回了：${data.reply ?? "OK"}` });
      } else {
        notifications.show({ color: "red", title: "连不上模型", message: data.error ?? "未知错误", autoClose: 8000 });
      }
    },
    onError: (exc) => notifications.show({ color: "red", message: errText(exc) }),
  });

  if (!form) {
    return (
      <Card className="glass" p="md" withBorder>
        <Text fz={11.5} c="dimmed">
          读取 AI 配置中…
        </Text>
      </Card>
    );
  }

  const set = <K extends keyof AiConfig>(key: K, value: AiConfig[K]) => setForm({ ...form, [key]: value });

  const applyPreset = (label: string | null) => {
    setPreset(label);
    const hit = form.presets.find((p) => p.label === label);
    if (hit) setForm({ ...form, base_url: hit.base_url, model: hit.model });
  };

  return (
    <Card className="glass" p="md" withBorder>
      <Group justify="space-between" mb={4}>
        <Group gap={8}>
          <IconRobot size={15} />
          <Title order={3}>AI 助手</Title>
          {form.enabled ? (
            <Badge size="xs" color="teal" variant="light">
              已启用
            </Badge>
          ) : (
            <Badge size="xs" color="gray" variant="light">
              未启用
            </Badge>
          )}
        </Group>
        <Switch
          size="sm"
          checked={form.enabled}
          disabled={!canManage}
          onChange={(e) => set("enabled", e.currentTarget.checked)}
          label="启用"
        />
      </Group>
      <Text fz={11} c="dimmed" mb="sm">
        任意兼容 OpenAI 协议的服务都行（DeepSeek / 通义 / Kimi / 智谱 / 本地 Ollama）。助手用当前登录账号的权限操作面板，
        每次工具调用都会写审计日志。
      </Text>

      <Stack gap="xs">
        <Select
          label="快速填充"
          size="xs"
          placeholder="选一个常见服务"
          data={form.presets.map((p) => ({ value: p.label, label: p.label }))}
          value={preset}
          onChange={applyPreset}
          disabled={!canManage}
          clearable
        />
        <TextInput
          label="模型端点 (base_url)"
          size="xs"
          placeholder="https://api.deepseek.com/v1"
          value={form.base_url}
          onChange={(e) => set("base_url", e.currentTarget.value)}
          disabled={!canManage}
        />
        <TextInput
          label="模型名"
          size="xs"
          placeholder="deepseek-chat"
          value={form.model}
          onChange={(e) => set("model", e.currentTarget.value)}
          disabled={!canManage}
        />
        <PasswordInput
          label="API Key"
          size="xs"
          placeholder={form.has_key ? `已保存 ${form.api_key_masked}（留空则不修改）` : "sk-..."}
          value={apiKey}
          onChange={(e) => setApiKey(e.currentTarget.value)}
          disabled={!canManage}
        />
        <Group grow align="flex-end">
          <NumberInput
            label="单轮最多几次工具往返"
            size="xs"
            min={1}
            max={40}
            value={form.max_steps}
            onChange={(v) => set("max_steps", typeof v === "number" ? v : 12)}
            disabled={!canManage}
          />
        </Group>
        <Switch
          size="sm"
          checked={form.auto_approve}
          disabled={!canManage}
          onChange={(e) => set("auto_approve", e.currentTarget.checked)}
          label="危险操作自动执行（关掉则每次都要点确认）"
        />

        {form.auto_approve && (
          <Alert color="orange" variant="light" p="xs">
            <Text fz={11}>
              关掉确认之后，AI 删除世界、覆盖配置文件、强杀进程都不会再问你。只在完全信任的场景下打开。
            </Text>
          </Alert>
        )}

        <Group gap={6} mt={4}>
          <Button
            size="compact-sm"
            loading={save.isPending}
            disabled={!canManage}
            onClick={() => {
              const payload: Record<string, unknown> = {
                enabled: form.enabled,
                base_url: form.base_url,
                model: form.model,
                max_steps: form.max_steps,
                auto_approve: form.auto_approve,
              };
              if (apiKey.trim()) payload.api_key = apiKey.trim();
              save.mutate(payload);
            }}
          >
            保存
          </Button>
          <Button
            size="compact-sm"
            variant="default"
            leftSection={<IconPlugConnected size={13} />}
            loading={test.isPending}
            disabled={!canManage || !form.has_key}
            onClick={() => test.mutate()}
          >
            测试连接
          </Button>
          <Text fz={10.5} c="dimmed">
            密钥加密后存库，页面只回显尾号。
          </Text>
        </Group>
      </Stack>
    </Card>
  );
}
