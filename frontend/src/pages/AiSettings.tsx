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

interface FoundModel {
  id: string;
  label: string;
  size: number | null;
  parameter_size: string;
  quantization: string;
}

interface ModelList {
  ok: boolean;
  models: FoundModel[];
  count: number;
  source: string;
  error?: string;
  hint?: string;
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
  const [models, setModels] = useState<FoundModel[]>([]);
  const [modelNote, setModelNote] = useState("");

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

  const discover = useMutation({
    mutationFn: ({ baseUrl, key }: { baseUrl: string; key: string | undefined }) =>
      api.post<ModelList>("/ai/models", { base_url: baseUrl, api_key: key }),
    onSuccess: (data) => {
      setModels(data.models);
      setModelNote(
        data.ok
          ? `找到 ${data.count} 个模型（${data.source === "ollama" ? "Ollama" : "OpenAI 兼容接口"}）`
          : `获取失败：${data.error ?? "未知错误"}${data.hint ? `。${data.hint}` : ""}`,
      );
    },
    onError: (exc) => {
      setModels([]);
      setModelNote(`获取失败：${errText(exc)}`);
    },
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

  if (config.isError) {
    return (
      <Card className="glass" p="md" withBorder>
        <Group justify="space-between">
          <Text fz={11.5} c="red">
            读取 AI 配置失败：{errText(config.error)}
          </Text>
          <Button size="compact-xs" variant="default" onClick={() => config.refetch()}>
            重试
          </Button>
        </Group>
        <Text fz={10.5} c="dimmed" mt={6}>
          若提示接口不存在，说明面板进程还是旧版本，重启面板后再试。
        </Text>
      </Card>
    );
  }

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
    if (!hit) return;
    setForm({ ...form, base_url: hit.base_url, model: hit.model });
    setModels([]);
    setModelNote("");
    // 换服务后旧密钥不能发给新地址；本地 Ollama 直接自动列一次。
    if (label?.includes("Ollama")) discover.mutate({ baseUrl: hit.base_url, key: "" });
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
        <Group gap={6} align="flex-end" wrap="nowrap">
          <TextInput
            label="模型名"
            size="xs"
            placeholder="deepseek-chat"
            value={form.model}
            onChange={(e) => set("model", e.currentTarget.value)}
            disabled={!canManage}
            style={{ flex: 1, minWidth: 0 }}
          />
          <Button
            size="compact-sm"
            variant="default"
            loading={discover.isPending}
            disabled={!canManage || !form.base_url.trim()}
            onClick={() =>
              discover.mutate({
                baseUrl: form.base_url,
                // 已存密钥且没输入新值时沿用，否则按输入（空串=不用密钥，本地 Ollama）。
                key: form.has_key && !apiKey.trim() ? undefined : apiKey.trim(),
              })
            }
          >
            获取模型列表
          </Button>
        </Group>
        {models.length > 0 && (
          <Select
            size="xs"
            placeholder={`从 ${models.length} 个已发现模型中选择`}
            data={models.map((m) => ({ value: m.id, label: m.label }))}
            value={models.some((m) => m.id === form.model) ? form.model : null}
            onChange={(value) => value && set("model", value)}
            disabled={!canManage}
            allowDeselect={false}
          />
        )}
        {modelNote && (
          <Text fz={10.5} c={models.length ? "dimmed" : "red"} style={{ whiteSpace: "pre-wrap" }}>
            {modelNote}
          </Text>
        )}
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
            disabled={!canManage || !form.model.trim()}
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
