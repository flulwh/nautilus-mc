import {
  ActionIcon,
  Alert,
  Badge,
  Box,
  Button,
  Card,
  Code,
  Group,
  Loader,
  ScrollArea,
  Stack,
  Text,
  Textarea,
  Tooltip,
} from "@mantine/core";
import {
  IconAlertTriangle,
  IconArrowUp,
  IconCheck,
  IconPlayerStop,
  IconRefresh,
  IconRobot,
  IconTerminal2,
  IconX,
} from "@tabler/icons-react";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { api, ApiError } from "../api";
import type { AiConfig } from "../types";

/** 工具说明来自 docstring，会带 markdown 记号；界面上按纯文本显示。 */
function plain(text?: string): string {
  return (text ?? "").replace(/\*\*/g, "").replace(/`/g, "");
}

interface ToolChip {
  id: string;
  name: string;
  args?: Record<string, unknown>;
  preview?: string;
  status: "running" | "done" | "failed" | "waiting";
}

interface PlanView {
  title: string;
  steps: { text: string; status: "pending" | "doing" | "done" | "blocked" }[];
}

interface Pending {
  id: string;
  name: string;
  args?: Record<string, unknown>;
  summary?: string;
}

interface Bubble {
  role: "user" | "assistant";
  text: string;
  tools: ToolChip[];
  plan?: PlanView;
  pending?: Pending[];
  error?: string;
  stopped?: boolean;
}

/** 逐个读出 SSE 的 `data: {...}` 事件。EventSource 不支持 POST，只能手搓。 */
async function* streamPost(path: string, body: unknown, signal: AbortSignal) {
  const res = await fetch(`/api${path}`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok) {
    const text = await res.text();
    let message = text;
    try {
      message = (JSON.parse(text) as { detail?: string }).detail ?? text;
    } catch {
      /* 纯文本错误 */
    }
    throw new ApiError(res.status, message);
  }
  if (!res.body) throw new ApiError(0, "浏览器不支持流式响应");

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let cut = buffer.indexOf("\n\n");
    while (cut >= 0) {
      const chunk = buffer.slice(0, cut);
      buffer = buffer.slice(cut + 2);
      const line = chunk.split("\n").find((l) => l.startsWith("data: "));
      if (line) {
        try {
          yield JSON.parse(line.slice(6)) as Record<string, never>;
        } catch {
          /* 半包或空行，忽略 */
        }
      }
      cut = buffer.indexOf("\n\n");
    }
  }
}

export function AssistantPage() {
  const config = useQuery<AiConfig>({
    queryKey: ["ai", "config"],
    queryFn: () => api.get<AiConfig>("/ai/config"),
    retry: false,
  });

  const [bubbles, setBubbles] = useState<Bubble[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const conversation = useRef<string | null>(null);
  const abort = useRef<AbortController | null>(null);
  const viewport = useRef<HTMLDivElement>(null);

  useEffect(() => {
    viewport.current?.scrollTo({ top: viewport.current.scrollHeight, behavior: "smooth" });
  }, [bubbles]);

  const configured = Boolean(config.data?.configured);
  const enabled = Boolean(config.data?.enabled);

  const patchLast = (fn: (b: Bubble) => Bubble) =>
    setBubbles((prev) => prev.map((b, i) => (i === prev.length - 1 ? fn(b) : b)));

  /** 消费事件流，把增量写进最后一条助手气泡。 */
  const consume = async (path: string, body: unknown) => {
    const controller = new AbortController();
    abort.current = controller;
    setBusy(true);
    try {
      for await (const event of streamPost(path, body, controller.signal)) {
        const type = String(event.type ?? "");
        if (type === "open") {
          conversation.current = String(event.conversation_id ?? "") || conversation.current;
        } else if (type === "text") {
          patchLast((b) => ({ ...b, text: b.text + String(event.delta ?? "") }));
        } else if (type === "tool_call") {
          patchLast((b) => ({
            ...b,
            tools: [
              ...b.tools,
              {
                id: String(event.id ?? ""),
                name: String(event.name ?? ""),
                args: (event.args ?? {}) as Record<string, unknown>,
                status: event.dangerous ? "waiting" : "running",
              },
            ],
          }));
        } else if (type === "tool_result") {
          patchLast((b) => ({
            ...b,
            tools: b.tools.map((t) =>
              t.id === event.id ? { ...t, preview: String(event.preview ?? ""), status: event.failed ? "failed" : "done" } : t,
            ),
          }));
        } else if (type === "plan") {
          patchLast((b) => ({ ...b, plan: event.plan as PlanView }));
        } else if (type === "approval_required") {
          const items = (event.items ?? []) as unknown as Pending[];
          patchLast((b) => ({
            ...b,
            pending: items,
            tools: b.tools.map((t) => (items.some((i) => i.id === t.id) ? { ...t, status: "waiting" } : t)),
          }));
        } else if (type === "cancelled") {
          patchLast((b) => ({ ...b, stopped: true }));
        } else if (type === "error") {
          patchLast((b) => ({ ...b, error: String(event.message ?? "未知错误") }));
        } else if (type === "done") {
          const pending = (event.pending ?? null) as Pending[] | null;
          const finalText = String(event.text ?? "");
          // 正常情况下增量已经把文本拼好了；done 里的全文只当兜底，
          // 否则会把同一段话再追加一遍。
          patchLast((b) => ({ ...b, text: b.text || finalText, pending: pending ?? b.pending }));
        }
      }
    } catch (exc) {
      if ((exc as Error).name !== "AbortError") {
        patchLast((b) => ({ ...b, error: exc instanceof Error ? exc.message : String(exc) }));
      }
    } finally {
      setBusy(false);
      abort.current = null;
    }
  };

  const send = async () => {
    const text = input.trim();
    if (!text || busy) return;
    setInput("");
    setBubbles((prev) => [...prev, { role: "user", text, tools: [] }, { role: "assistant", text: "", tools: [] }]);
    await consume("/ai/chat", { message: text, conversation_id: conversation.current });
  };

  const decide = async (approved: boolean) => {
    const cid = conversation.current;
    const last = bubbles[bubbles.length - 1];
    const pending = last?.pending ?? [];
    if (!cid || !pending.length) return;
    const approvals: Record<string, boolean> = {};
    pending.forEach((item) => {
      approvals[item.id] = approved;
    });
    patchLast((b) => ({ ...b, pending: undefined }));
    await consume("/ai/approve", { conversation_id: cid, approvals });
  };

  const stop = () => {
    const cid = conversation.current;
    if (cid) {
      api.post("/ai/cancel", { conversation_id: cid }).catch(() => undefined);
    }
    abort.current?.abort();
  };

  const reset = () => {
    if (conversation.current) {
      api.del(`/ai/conversations/${conversation.current}`).catch(() => undefined);
    }
    conversation.current = null;
    setBubbles([]);
  };

  if (config.isError) {
    return (
      <Alert color="red" variant="light" title="无法读取 AI 配置" icon={<IconAlertTriangle size={16} />}>
        <Stack gap={6}>
          <Text fz={12}>{config.error instanceof ApiError ? config.error.detail : "请求 AI 配置失败"}</Text>
          <Text fz={11} c="dimmed">
            如果提示接口不存在，说明面板进程还是旧版本——重新运行启动脚本（或重启面板）加载新代码后再试。
          </Text>
          <Button size="compact-xs" variant="default" onClick={() => config.refetch()} leftSection={<IconRefresh size={12} />}>
            重试
          </Button>
        </Stack>
      </Alert>
    );
  }

  if (config.isLoading) {
    return (
      <Group justify="center" p="xl">
        <Loader size="sm" />
      </Group>
    );
  }

  if (!enabled || !configured) {
    return (
      <Alert color="gray" variant="light" title="AI 助手还没启用" icon={<IconRobot size={16} />}>
        <Text fz={12}>
          {!enabled
            ? "去「系统设置 -> AI 助手」打开开关，填上模型端点和 API Key 就能用了。"
            : "模型端点、模型名或 API Key 还没填全，去「系统设置 -> AI 助手」补齐。"}
        </Text>
      </Alert>
    );
  }

  return (
    <Card className="glass" p={0} withBorder style={{ height: "calc(100vh - 96px)", display: "flex", flexDirection: "column" }}>
      <Group justify="space-between" px="md" py="xs" style={{ borderBottom: "1px solid var(--line)" }}>
        <Group gap={8}>
          <IconRobot size={15} />
          <Text fz={12.5} fw={600}>
            AI 助手
          </Text>
          <Badge size="xs" variant="light" color="gray">
            {config.data?.model}
          </Badge>
          {!config.data?.auto_approve && (
            <Badge size="xs" variant="light" color="orange">
              危险操作需确认
            </Badge>
          )}
        </Group>
        <Tooltip label="清空对话">
          <ActionIcon variant="subtle" color="gray" size="sm" onClick={reset} disabled={!bubbles.length}>
            <IconRefresh size={14} />
          </ActionIcon>
        </Tooltip>
      </Group>

      <ScrollArea style={{ flex: 1 }} viewportRef={viewport} px="md" py="sm">
        {!bubbles.length && (
          <Stack gap={6} mt="10vh" align="center">
            <IconRobot size={26} style={{ opacity: 0.35 }} />
            <Text fz={12} c="dimmed">
              直接用中文说要做什么，比如「把 1 号服重启一下」「给 2 号服装个 EssentialsX」「世界备份留一份」
            </Text>
            <Group gap={6} mt={4}>
              {["列出所有服务器", "1 号服现在几个人", "给所有服做一份世界备份"].map((hint) => (
                <Button key={hint} size="compact-xs" variant="default" onClick={() => setInput(hint)}>
                  {hint}
                </Button>
              ))}
            </Group>
          </Stack>
        )}

        <Stack gap="sm">
          {bubbles.map((bubble, index) => (
            <Box key={index}>
              {bubble.role === "user" ? (
                <Group justify="flex-end">
                  <Card p="xs" radius="sm" bg="var(--panel)" withBorder style={{ maxWidth: "78%" }}>
                    <Text fz={12.5} style={{ whiteSpace: "pre-wrap" }}>
                      {bubble.text}
                    </Text>
                  </Card>
                </Group>
              ) : (
                <Stack gap={6}>
                  {bubble.plan && bubble.plan.steps.length > 0 && (
                    <Box p="xs" style={{ borderLeft: "2px solid var(--line)" }}>
                      <Text fz={11} fw={600}>
                        {bubble.plan.title}
                      </Text>
                      {bubble.plan.steps.map((step, i) => (
                        <Group key={i} gap={6} wrap="nowrap">
                          <Text fz={10.5} c="dimmed" className="mono">
                            {step.status === "done"
                              ? "x"
                              : step.status === "doing"
                                ? ">"
                                : step.status === "blocked"
                                  ? "!"
                                  : "-"}
                          </Text>
                          <Text fz={11} c={step.status === "done" ? "dimmed" : undefined} td={step.status === "done" ? "line-through" : undefined}>
                            {step.text}
                          </Text>
                        </Group>
                      ))}
                    </Box>
                  )}

                  {bubble.tools.map((tool) => (
                    <Group key={tool.id || tool.name} gap={6} wrap="nowrap" align="flex-start">
                      {tool.status === "running" ? (
                        <Loader size={11} />
                      ) : tool.status === "failed" ? (
                        <IconX size={12} color="var(--red, #e03131)" />
                      ) : tool.status === "waiting" ? (
                        <IconAlertTriangle size={12} color="var(--orange, #f08c00)" />
                      ) : (
                        <IconCheck size={12} color="var(--dim)" />
                      )}
                      <Stack gap={2} style={{ minWidth: 0 }}>
                        <Group gap={6}>
                          <Text fz={11} className="mono">
                            {tool.name}
                          </Text>
                          {tool.args && Object.keys(tool.args).length > 0 && (
                            <Text fz={10.5} c="dimmed" className="mono" truncate>
                              {JSON.stringify(tool.args)}
                            </Text>
                          )}
                        </Group>
                        {tool.preview && (
                          <Code
                            block
                            fz={10.5}
                            style={{
                              maxHeight: 150,
                              overflow: "auto",
                              background: "var(--panel)",
                              padding: 6,
                              // 工具结果是长 JSON，必须换行，否则单行横向溢出被裁掉
                              whiteSpace: "pre-wrap",
                              wordBreak: "break-all",
                            }}
                          >
                            {tool.preview}
                          </Code>
                        )}
                      </Stack>
                    </Group>
                  ))}

                  {bubble.text && (
                    <Text fz={12.5} style={{ whiteSpace: "pre-wrap" }}>
                      {bubble.text}
                    </Text>
                  )}

                  {bubble.pending && bubble.pending.length > 0 && (
                    <Alert
                      color="orange"
                      variant="light"
                      icon={<IconAlertTriangle size={15} />}
                      title="这一步需要你确认"
                      p="xs"
                    >
                      <Stack gap={4}>
                        {bubble.pending.map((item) => (
                          <Text key={item.id} fz={11.5}>
                            <Code fz={11}>{item.name}</Code> {plain(item.summary)}
                            {item.args && Object.keys(item.args).length > 0 && (
                              <Text fz={10.5} c="dimmed" className="mono">
                                {JSON.stringify(item.args)}
                              </Text>
                            )}
                          </Text>
                        ))}
                        <Group gap={6} mt={4}>
                          <Button size="compact-xs" color="orange" onClick={() => decide(true)} disabled={busy}>
                            确认执行
                          </Button>
                          <Button size="compact-xs" variant="default" onClick={() => decide(false)} disabled={busy}>
                            取消
                          </Button>
                        </Group>
                      </Stack>
                    </Alert>
                  )}

                  {bubble.stopped && (
                    <Text fz={11} c="dimmed">
                      已停止
                    </Text>
                  )}

                  {bubble.error && (
                    <Alert color="red" variant="light" p="xs">
                      <Text fz={11.5}>{bubble.error}</Text>
                    </Alert>
                  )}
                </Stack>
              )}
            </Box>
          ))}
        </Stack>
      </ScrollArea>

      <Box px="md" py="sm" style={{ borderTop: "1px solid var(--line)" }}>
        <Group gap="xs" align="flex-end">
          <Textarea
            style={{ flex: 1 }}
            autosize
            minRows={1}
            maxRows={6}
            size="sm"
            placeholder="说要做什么，Enter 发送 / Shift+Enter 换行"
            value={input}
            onChange={(e) => setInput(e.currentTarget.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                void send();
              }
            }}
          />
          {busy ? (
            <ActionIcon variant="default" size="lg" onClick={stop} aria-label="停止">
              <IconPlayerStop size={15} />
            </ActionIcon>
          ) : (
            <ActionIcon variant="filled" size="lg" onClick={() => void send()} disabled={!input.trim()} aria-label="发送">
              <IconArrowUp size={15} />
            </ActionIcon>
          )}
        </Group>
        <Group gap={4} mt={6}>
          <IconTerminal2 size={11} color="var(--dim)" />
          <Text fz={10.5} c="dimmed">
            助手用的就是你的账号权限；每次工具调用都进审计日志。
          </Text>
        </Group>
      </Box>
    </Card>
  );
}
