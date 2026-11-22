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
  Modal,
  ScrollArea,
  Stack,
  Text,
  Textarea,
  TextInput,
  Tooltip,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import {
  IconAlertTriangle,
  IconArrowUp,
  IconBrain,
  IconCheck,
  IconMessage,
  IconPencil,
  IconPlayerStop,
  IconPlus,
  IconRefresh,
  IconRobot,
  IconRoute,
  IconTerminal2,
  IconTrash,
  IconX,
} from "@tabler/icons-react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { motion } from "motion/react";
import { useEffect, useRef, useState } from "react";

import { api, ApiError } from "../api";
import { EASE } from "../motion";
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
  startedAt?: number;
  finishedAt?: number;
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
  key: string;
  role: "user" | "assistant" | "note";
  text: string;
  thinking?: string;
  thinkingDone?: boolean;
  tools: ToolChip[];
  plan?: PlanView;
  pending?: Pending[];
  error?: string;
  stopped?: boolean;
  busy?: boolean;
  startedAt?: number;
  usage?: Record<string, unknown>;
}

interface ConversationMeta {
  id: string;
  title: string;
  preview: string;
  messages: number;
  idle: number;
  compactions: number;
}

interface HistoryEvent {
  type: string;
  text?: string;
  id?: string;
  name?: string;
  args?: Record<string, unknown>;
  preview?: string;
  failed?: boolean;
}

/** 服务端记录的单步轨迹。 */
interface TraceStep {
  seq: number;
  kind: "model" | "tool" | "plan" | "approval" | "note";
  name: string;
  status: "running" | "done" | "failed" | "waiting" | "stopped";
  at: number;
  duration_ms: number | null;
  args?: unknown;
  result: string;
  detail: string;
}

interface RunSummary {
  run_id: string;
  started_at: number;
  finished_at: number | null;
  status: string;
  model: string;
  prompt: string;
  steps: number;
  tools: number;
  failed: number;
  duration_ms: number;
}

interface RunDetail extends Omit<RunSummary, "steps" | "tools" | "failed" | "duration_ms"> {
  conversation_id: string;
  usage?: Record<string, unknown> | null;
  steps: TraceStep[];
}

type RunStatus = "ready" | "submitted" | "thinking" | "streaming" | "error" | "stopped";

const STATUS: Record<RunStatus, { label: string; color: string }> = {
  ready: { label: "就绪", color: "gray" },
  submitted: { label: "连接中", color: "blue" },
  thinking: { label: "思考中", color: "grape" },
  streaming: { label: "生成中", color: "teal" },
  error: { label: "出错", color: "red" },
  stopped: { label: "已停止", color: "orange" },
};

let bubbleSeq = 0;
const nextKey = () => `b${++bubbleSeq}`;

function relative(idle: number): string {
  if (idle < 60) return "刚刚";
  if (idle < 3600) return `${Math.floor(idle / 60)} 分钟前`;
  if (idle < 86400) return `${Math.floor(idle / 3600)} 小时前`;
  return `${Math.floor(idle / 86400)} 天前`;
}

const TRACE_STEP: Record<string, { label: string; color: string; icon: "model" | "tool" | "plan" | "approval" | "note" }> = {
  model: { label: "模型推理", color: "grape", icon: "model" },
  tool: { label: "工具", color: "teal", icon: "tool" },
  plan: { label: "任务清单", color: "blue", icon: "plan" },
  approval: { label: "等待确认", color: "orange", icon: "approval" },
  note: { label: "上下文", color: "gray", icon: "note" },
};

const TRACE_STATUS: Record<string, { label: string; color: string }> = {
  running: { label: "进行中", color: "blue" },
  done: { label: "完成", color: "teal" },
  failed: { label: "失败", color: "red" },
  waiting: { label: "待确认", color: "orange" },
  stopped: { label: "已停止", color: "gray" },
  done_idle: { label: "完成", color: "gray" },
};

function duration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "";
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

function clock(at: number): string {
  if (!at) return "";
  const d = new Date(at);
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}:${String(d.getSeconds()).padStart(2, "0")}`;
}

/** 历史事件还原成气泡；历史里的工具都已结束，不会显示“运行中”。 */
function eventsToBubbles(events: HistoryEvent[]): Bubble[] {
  const out: Bubble[] = [];
  const assistant = () => {
    if (out.length && out[out.length - 1].role === "assistant") return out[out.length - 1];
    const bubble: Bubble = { key: nextKey(), role: "assistant", text: "", tools: [] };
    out.push(bubble);
    return bubble;
  };
  for (const event of events) {
    if (event.type === "user") {
      out.push({ key: nextKey(), role: "user", text: event.text ?? "", tools: [] });
    } else if (event.type === "note") {
      out.push({ key: nextKey(), role: "note", text: event.text ?? "", tools: [] });
    } else if (event.type === "thinking") {
      const bubble = assistant();
      bubble.thinking = (bubble.thinking ?? "") + (event.text ?? "");
      bubble.thinkingDone = true;
    } else if (event.type === "text") {
      const bubble = assistant();
      bubble.thinkingDone = true;
      bubble.text += event.text ?? "";
    } else if (event.type === "tool_call") {
      assistant().tools.push({
        id: String(event.id ?? ""),
        name: String(event.name ?? ""),
        args: event.args,
        status: "done",
      });
    } else if (event.type === "tool_result") {
      const tool = assistant().tools.find((t) => t.id === event.id);
      if (tool) {
        tool.preview = event.preview;
        tool.status = event.failed ? "failed" : "done";
      }
    }
  }
  return out;
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
  const qc = useQueryClient();
  const config = useQuery<AiConfig>({
    queryKey: ["ai", "config"],
    queryFn: () => api.get<AiConfig>("/ai/config"),
    retry: false,
  });
  const sessions = useQuery<{ conversations: ConversationMeta[] }>({
    queryKey: ["ai", "conversations"],
    queryFn: () => api.get<{ conversations: ConversationMeta[] }>("/ai/conversations"),
    retry: false,
  });

  const [bubbles, setBubbles] = useState<Bubble[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<RunStatus>("ready");
  const [activeId, setActiveId] = useState<string | null>(null);
  const [stage, setStage] = useState("");
  const [compaction, setCompaction] = useState("");
  const [renameTarget, setRenameTarget] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [thinkingFolded, setThinkingFolded] = useState(false);
  const [traceOpen, setTraceOpen] = useState(false);
  const [traceRuns, setTraceRuns] = useState<RunSummary[]>([]);
  const [traceRun, setTraceRun] = useState<RunDetail | null>(null);
  const [traceLoading, setTraceLoading] = useState(false);
  const abort = useRef<AbortController | null>(null);
  const activeRef = useRef<string | null>(null);
  const lastPrompt = useRef("");
  const viewport = useRef<HTMLDivElement>(null);

  useEffect(() => {
    viewport.current?.scrollTo({ top: viewport.current.scrollHeight, behavior: "smooth" });
  }, [bubbles, status]);

  const configured = Boolean(config.data?.configured);
  const enabled = Boolean(config.data?.enabled);

  const setActive = (id: string | null) => {
    activeRef.current = id;
    setActiveId(id);
  };

  const patchLast = (fn: (b: Bubble) => Bubble) =>
    setBubbles((prev) => prev.map((b, i) => (i === prev.length - 1 ? fn(b) : b)));

  /** 消费事件流；所有事件都落到气泡或顶部状态上，不留“静默期”。 */
  const consume = async (path: string, body: unknown) => {
    const controller = new AbortController();
    abort.current = controller;
    setBusy(true);
    setStatus("submitted");
    setStage("正在连接模型…");
    try {
      for await (const event of streamPost(path, body, controller.signal)) {
        const type = String(event.type ?? "");
        if (type === "open") {
          const cid = String(event.conversation_id ?? "") || activeRef.current;
          if (cid && cid !== activeRef.current) {
            setActive(cid);
            void qc.invalidateQueries({ queryKey: ["ai", "conversations"] });
          }
        } else if (type === "started") {
          // 模型调用正式开始：立刻从“连接中”切到“思考中”，不等首字节。
          setStatus("thinking");
          setStage("模型正在思考…");
          patchLast((b) => ({ ...b, busy: true, startedAt: b.startedAt ?? Date.now() }));
        } else if (type === "thinking") {
          setStatus("thinking");
          setStage("模型正在思考…");
          patchLast((b) => ({ ...b, thinking: (b.thinking ?? "") + String(event.delta ?? ""), busy: true }));
        } else if (type === "text") {
          setStatus("streaming");
          setStage("");
          patchLast((b) => ({ ...b, thinkingDone: true, text: b.text + String(event.delta ?? "") }));
        } else if (type === "tool_call") {
          setStage(`正在执行 ${String(event.name ?? "")}`);
          patchLast((b) => ({ ...b, thinkingDone: true }));
          patchLast((b) => ({
            ...b,
            tools: [
              ...b.tools,
              {
                id: String(event.id ?? ""),
                name: String(event.name ?? ""),
                args: (event.args ?? {}) as Record<string, unknown>,
                status: event.dangerous ? "waiting" : "running",
                startedAt: Date.now(),
              },
            ],
          }));
        } else if (type === "tool_result") {
          patchLast((b) => ({
            ...b,
            tools: b.tools.map((t) =>
              t.id === event.id
                ? { ...t, preview: String(event.preview ?? ""), status: event.failed ? "failed" : "done", finishedAt: Date.now() }
                : t,
            ),
          }));
        } else if (type === "plan") {
          patchLast((b) => ({ ...b, plan: event.plan as PlanView }));
        } else if (type === "approval_required") {
          const items = (event.items ?? []) as unknown as Pending[];
          setStage("等待你确认");
          patchLast((b) => ({
            ...b,
            pending: items,
            tools: b.tools.map((t) => (items.some((i) => i.id === t.id) ? { ...t, status: "waiting" } : t)),
          }));
        } else if (type === "compacted") {
          setCompaction(String(event.summary ?? ""));
          setBubbles((prev) => [
            ...prev,
            { key: nextKey(), role: "note", text: `已压缩旧上下文，省下 ${Number(event.saved_chars ?? 0)} 字符`, tools: [] },
          ]);
        } else if (type === "cancelled") {
          setStatus("stopped");
          setStage("");
          patchLast((b) => ({ ...b, stopped: true, busy: false, thinkingDone: true }));
        } else if (type === "error") {
          setStatus("error");
          setStage("");
          patchLast((b) => ({ ...b, error: String(event.message ?? "未知错误"), busy: false, thinkingDone: true }));
        } else if (type === "done") {
          const pending = (event.pending ?? null) as Pending[] | null;
          const finalText = String(event.text ?? "");
          setStatus("ready");
          setStage("");
          patchLast((b) => ({
            ...b,
            text: b.text || finalText,
            busy: false,
            thinkingDone: true,
            pending: pending ?? b.pending,
            usage: (event.usage ?? b.usage) as Record<string, unknown> | undefined,
          }));
          void qc.invalidateQueries({ queryKey: ["ai", "conversations"] });
        }
      }
    } catch (exc) {
      if ((exc as Error).name !== "AbortError") {
        setStatus("error");
        setStage("");
        patchLast((b) => ({ ...b, error: exc instanceof Error ? exc.message : String(exc) }));
      } else {
        setStatus("stopped");
        setStage("");
      }
    } finally {
      setBusy(false);
      abort.current = null;
    }
  };

  const send = async (retry?: string) => {
    const text = retry ?? input.trim();
    if (!text || busy) return;
    setInput("");
    lastPrompt.current = text;
    setCompaction("");
    // 立即（同一帧）插入用户气泡与“正在思考”的助手骨架，不等网络往返。
    // 这样点击发送的瞬间就有可见反馈。
    setStatus("submitted");
    setStage("正在连接模型…");
    setBusy(true);
    setBubbles((prev) => [
      ...prev,
      { key: nextKey(), role: "user", text, tools: [] },
      { key: nextKey(), role: "assistant", text: "", tools: [], busy: true, startedAt: Date.now() },
    ]);
    await consume("/ai/chat", { message: text, conversation_id: activeRef.current });
  };

  const decide = async (approved: boolean) => {
    const cid = activeRef.current;
    const last = bubbles[bubbles.length - 1];
    const pending = last?.pending ?? [];
    if (!cid || !pending.length) return;
    const approvals: Record<string, boolean> = {};
    pending.forEach((item) => {
      approvals[item.id] = approved;
    });
    setBusy(true);
    setStatus("submitted");
    setStage("正在继续…");
    patchLast((b) => ({ ...b, pending: undefined, busy: true, error: undefined }));
    await consume("/ai/approve", { conversation_id: cid, approvals });
  };

  const stop = () => {
    const cid = activeRef.current;
    if (cid) void api.post("/ai/cancel", { conversation_id: cid }).catch(() => undefined);
    abort.current?.abort();
  };

  const newChat = () => {
    if (busy) stop();
    setActive(null);
    setBubbles([]);
    setCompaction("");
    setStage("");
    setStatus("ready");
    setInput("");
  };

  const switchChat = async (id: string) => {
    if (id === activeRef.current) return;
    if (busy) stop();
    try {
      const data = await api.get<{ events: HistoryEvent[] }>(`/ai/conversations/${id}/messages`);
      setActive(id);
      setBubbles(eventsToBubbles(data.events));
      setCompaction("");
      setStage("");
      setStatus("ready");
    } catch (exc) {
      notifications.show({ color: "red", message: `读取会话失败：${exc instanceof Error ? exc.message : String(exc)}` });
    }
  };

  const deleteChat = async (id: string) => {
    try {
      await api.del(`/ai/conversations/${id}`);
      if (activeRef.current === id) newChat();
      await qc.invalidateQueries({ queryKey: ["ai", "conversations"] });
    } catch (exc) {
      notifications.show({ color: "red", message: `删除会话失败：${exc instanceof Error ? exc.message : String(exc)}` });
    }
  };

  const submitRename = async () => {
    if (!renameTarget || !renameValue.trim()) return;
    try {
      await api.patch(`/ai/conversations/${renameTarget}`, { title: renameValue.trim() });
      await qc.invalidateQueries({ queryKey: ["ai", "conversations"] });
      setRenameTarget(null);
    } catch (exc) {
      notifications.show({ color: "red", message: `重命名失败：${exc instanceof Error ? exc.message : String(exc)}` });
    }
  };

  const compactNow = async () => {
    const cid = activeRef.current;
    if (!cid || busy) return;
    try {
      const out = await api.post<{ compacted: boolean; summary?: string; saved_chars?: number; reason?: string }>(
        `/ai/conversations/${cid}/compact`,
        { focus: "" },
      );
      notifications.show({
        color: "teal",
        message: out.compacted ? `已压缩，省下 ${out.saved_chars ?? 0} 字符` : (out.reason ?? "无需压缩"),
      });
      await switchChatRefresh(cid);
    } catch (exc) {
      notifications.show({ color: "red", message: `压缩失败：${exc instanceof Error ? exc.message : String(exc)}` });
    }
  };

  const switchChatRefresh = async (id: string) => {
    try {
      const data = await api.get<{ events: HistoryEvent[] }>(`/ai/conversations/${id}/messages`);
      setBubbles(eventsToBubbles(data.events));
      await qc.invalidateQueries({ queryKey: ["ai", "conversations"] });
    } catch {
      /* 切换失败的提示已在别处给出 */
    }
  };

  const togglePreview = (id: string) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const loadRun = async (runId: string) => {
    setTraceLoading(true);
    try {
      const data = await api.get<{ run: RunDetail }>(`/ai/runs/${runId}`);
      setTraceRun(data.run);
    } catch (exc) {
      notifications.show({ color: "red", message: `读取轨迹失败：${exc instanceof Error ? exc.message : String(exc)}` });
    } finally {
      setTraceLoading(false);
    }
  };

  const openTrace = async () => {
    const cid = activeRef.current;
    setTraceOpen(true);
    if (!cid) return;
    setTraceLoading(true);
    try {
      const data = await api.get<{ runs: RunSummary[] }>(`/ai/conversations/${cid}/runs`);
      setTraceRuns(data.runs);
      if (data.runs.length > 0) await loadRun(data.runs[0].run_id);
    } catch (exc) {
      notifications.show({ color: "red", message: `读取运行记录失败：${exc instanceof Error ? exc.message : String(exc)}` });
    } finally {
      setTraceLoading(false);
    }
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

  const items = sessions.data?.conversations ?? [];
  const info = STATUS[status];

  return (
    <Group align="stretch" gap="xs" wrap="nowrap" style={{ height: "calc(100vh - 96px)" }}>
      <Card className="glass" p={0} withBorder style={{ width: 208, display: "flex", flexDirection: "column", flexShrink: 0 }}>
        <Group justify="space-between" px="xs" py={8} style={{ borderBottom: "1px solid var(--line)" }}>
          <Text fz={11} fw={600}>
            会话
          </Text>
          <Tooltip label="新对话">
            <ActionIcon variant="subtle" size="sm" onClick={newChat} aria-label="新对话">
              <IconPlus size={14} />
            </ActionIcon>
          </Tooltip>
        </Group>
        <ScrollArea style={{ flex: 1 }} px={6} py={6}>
          <Stack gap={2}>
            {!items.length && (
              <Text fz={10.5} c="dimmed" p={6}>
                还没有历史会话
              </Text>
            )}
            {items.map((item, idx) => (
              <motion.div
                key={item.id}
                initial={{ opacity: 0, x: -8 }}
                animate={{ opacity: 1, x: 0 }}
                transition={{ duration: 0.26, ease: EASE.snap, delay: Math.min(idx, 10) * 0.03 }}
              >
              <Group
                gap={2}
                wrap="nowrap"
                p={5}
                className="conv-item"
                style={{
                  cursor: "pointer",
                  background: item.id === activeId ? "var(--panel-2, var(--panel))" : undefined,
                  borderLeft: item.id === activeId ? "2px solid var(--accent, var(--dim))" : "2px solid transparent",
                }}
                onClick={() => void switchChat(item.id)}
              >
                <IconMessage size={12} style={{ flexShrink: 0, opacity: 0.6 }} />
                <Stack gap={0} style={{ flex: 1, minWidth: 0 }}>
                  <Text fz={11} truncate>
                    {item.title || "新对话"}
                  </Text>
                  <Text fz={9.5} c="dimmed" truncate>
                    {relative(item.idle)} · {item.messages} 条{item.compactions ? ` · 压缩${item.compactions}` : ""}
                  </Text>
                </Stack>
                <ActionIcon
                  variant="subtle"
                  color="gray"
                  size="xs"
                  aria-label="重命名"
                  onClick={(e) => {
                    e.stopPropagation();
                    setRenameTarget(item.id);
                    setRenameValue(item.title);
                  }}
                >
                  <IconPencil size={11} />
                </ActionIcon>
                <ActionIcon
                  variant="subtle"
                  color="red"
                  size="xs"
                  aria-label="删除"
                  onClick={(e) => {
                    e.stopPropagation();
                    void deleteChat(item.id);
                  }}
                >
                  <IconTrash size={11} />
                </ActionIcon>
              </Group>
              </motion.div>
            ))}
          </Stack>
        </ScrollArea>
      </Card>

      <Card className="glass" p={0} withBorder style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column" }}>
        <Group justify="space-between" px="md" py="xs" style={{ borderBottom: "1px solid var(--line)" }}>
          <Group gap={8} wrap="nowrap">
            <IconRobot size={15} />
            <Text fz={12.5} fw={600}>
              AI 助手
            </Text>
            <Badge size="xs" variant="light" color="gray">
              {config.data?.model}
            </Badge>
            <Badge size="xs" variant="light" color={info.color}>
              {info.label}
            </Badge>
            {stage && (
              <Group gap={5} wrap="nowrap" style={{ maxWidth: 240 }}>
                <span className="stage-led" aria-hidden="true" />
                <Text fz={10.5} c="dimmed" truncate>
                  {stage}
                </Text>
              </Group>
            )}
          </Group>
          <Group gap={4}>
            {!config.data?.auto_approve && (
              <Badge size="xs" variant="light" color="orange">
                危险操作需确认
              </Badge>
            )}
            <Tooltip label="查看执行轨迹">
              <ActionIcon variant="subtle" color="gray" size="sm" onClick={() => void openTrace()} disabled={!activeId} aria-label="执行轨迹">
                <IconRoute size={14} />
              </ActionIcon>
            </Tooltip>
            <Tooltip label="压缩当前会话上下文">
              <ActionIcon variant="subtle" color="gray" size="sm" onClick={() => void compactNow()} disabled={!activeId || busy} aria-label="压缩上下文">
                <IconRefresh size={14} />
              </ActionIcon>
            </Tooltip>
            <Tooltip label="清空对话">
              <ActionIcon variant="subtle" color="gray" size="sm" onClick={newChat} aria-label="清空对话">
                <IconX size={14} />
              </ActionIcon>
            </Tooltip>
          </Group>
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
            {bubbles.map((bubble) => (
              <motion.div
                key={bubble.key}
                className="msg-enter"
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.32, ease: EASE.snap }}
              >
                {bubble.role === "user" ? (
                  <Group justify="flex-end">
                    <Card p="xs" radius="sm" bg="var(--panel)" withBorder style={{ maxWidth: "78%" }}>
                      <Text fz={12.5} style={{ whiteSpace: "pre-wrap" }}>
                        {bubble.text}
                      </Text>
                    </Card>
                  </Group>
                ) : bubble.role === "note" ? (
                  <Text fz={10.5} c="dimmed" py={2}>
                    — {bubble.text}
                  </Text>
                ) : (
                  <Stack gap={6}>
                    {bubble.thinking && (
                      <motion.div
                        className="thinking-block"
                        style={{
                          padding: "var(--mantine-spacing-xs)",
                          borderLeft: "2px solid var(--accent, var(--dim))",
                          background: "var(--panel-2, var(--panel))",
                          opacity: 0.92,
                        }}
                        layout
                      >
                        <Group
                          gap={6}
                          wrap="nowrap"
                          style={{ cursor: "pointer", userSelect: "none" }}
                          onClick={() => setThinkingFolded((v) => !v)}
                        >
                          <IconBrain size={12} color="var(--dim)" />
                          <Text fz={10.5} fw={600} c="dimmed">
                            思考过程
                          </Text>
                          {bubble.thinkingDone ? (
                            <Text fz={9.5} c="dimmed">
                              已完成
                            </Text>
                          ) : (
                            <Text fz={9.5} c="dimmed" className="thinking-dots">
                              思考中
                            </Text>
                          )}
                          <Text fz={9.5} c="dimmed" style={{ marginLeft: "auto" }}>
                            {thinkingFolded ? "展开" : "收起"}
                          </Text>
                        </Group>
                        {!thinkingFolded && (
                          <Text
                            fz={11}
                            c="dimmed"
                            mt={4}
                            style={{ whiteSpace: "pre-wrap", wordBreak: "break-word", fontStyle: "italic", lineHeight: 1.5 }}
                          >
                            {bubble.thinking}
                            {!bubble.thinkingDone && <span className="chat-caret" />}
                          </Text>
                        )}
                      </motion.div>
                    )}

                    {bubble.busy && !bubble.thinking && !bubble.text && (
                      <Group gap={6} wrap="nowrap">
                        <IconBrain size={13} color="var(--dim)" className="thinking-pulse" />
                        <Text fz={11} c="dimmed" className="thinking-dots">
                          正在思考
                        </Text>
                      </Group>
                    )}

                    {bubble.plan && bubble.plan.steps.length > 0 && (
                      <motion.div
                        className="plan-block"
                        style={{ padding: "var(--mantine-spacing-xs)", borderLeft: "2px solid var(--line)" }}
                        layout
                      >
                        <Text fz={11} fw={600}>
                          {bubble.plan.title}
                        </Text>
                        {bubble.plan.steps.map((step, i) => (
                          <motion.div
                            key={i}
                            initial={{ opacity: 0, x: -6 }}
                            animate={{ opacity: 1, x: 0 }}
                            transition={{ duration: 0.26, ease: EASE.snap, delay: i * 0.05 }}
                          >
                            <Group gap={6} wrap="nowrap">
                              <Text
                                fz={10.5}
                                c={step.status === "blocked" ? "red" : step.status === "doing" ? "var(--accent-text)" : "dimmed"}
                                className="mono"
                                style={
                                  step.status === "doing"
                                    ? { animation: "thinking-pulse 1.3s ease-in-out infinite" }
                                    : undefined
                                }
                              >
                                {step.status === "done" ? "x" : step.status === "doing" ? ">" : step.status === "blocked" ? "!" : "-"}
                              </Text>
                              <Text
                                fz={11}
                                c={step.status === "done" ? "dimmed" : undefined}
                                td={step.status === "done" ? "line-through" : undefined}
                              >
                                {step.text}
                              </Text>
                            </Group>
                          </motion.div>
                        ))}
                      </motion.div>
                    )}

                    {bubble.tools.map((tool, toolIndex) => {
                      const open = expanded.has(tool.id);
                      const elapsed =
                        tool.startedAt && tool.finishedAt ? `${Math.max(0, tool.finishedAt - tool.startedAt)}ms` : "";
                      const word =
                        tool.status === "running"
                          ? "执行中"
                          : tool.status === "waiting"
                            ? "待确认"
                            : tool.status === "failed"
                              ? "失败"
                              : "完成";
                      return (
                        <motion.div
                          key={tool.id || tool.name}
                          className={`tool-enter ${tool.status === "running" ? "trace-running" : ""}`}
                          style={{ "--step-delay": `${toolIndex * 60}ms` } as React.CSSProperties}
                          layout
                        >
                        <Group gap={6} wrap="nowrap" align="flex-start">
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
                              <Text fz={10} c="dimmed">
                                {word}
                                {elapsed ? ` · ${elapsed}` : ""}
                              </Text>
                            </Group>
                            {tool.args && Object.keys(tool.args).length > 0 && (
                              <Text fz={10.5} c="dimmed" className="mono" truncate>
                                {JSON.stringify(tool.args)}
                              </Text>
                            )}
                            {tool.preview && (
                              <Box>
                                <Code
                                  block
                                  fz={10.5}
                                  style={{
                                    maxHeight: open ? 320 : 96,
                                    overflow: "auto",
                                    background: "var(--panel)",
                                    padding: 6,
                                    whiteSpace: "pre-wrap",
                                    wordBreak: "break-all",
                                  }}
                                >
                                  {open ? tool.preview : tool.preview.slice(0, 260)}
                                </Code>
                                {tool.preview.length > 260 && (
                                  <Text
                                    fz={10}
                                    c="dimmed"
                                    style={{ cursor: "pointer", userSelect: "none" }}
                                    onClick={() => togglePreview(tool.id)}
                                  >
                                    {open ? "收起" : "展开结果"}
                                  </Text>
                                )}
                              </Box>
                            )}
                          </Stack>
                        </Group>
                        </motion.div>
                      );
                    })}

                    {bubble.text && (
                      <Text fz={12.5} style={{ whiteSpace: "pre-wrap" }}>
                        {bubble.text}
                        {status === "streaming" && <span className="chat-caret" />}
                      </Text>
                    )}

                    {bubble.pending && bubble.pending.length > 0 && (
                      <Alert color="orange" variant="light" icon={<IconAlertTriangle size={15} />} title="这一步需要你确认" p="xs">
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
                            <Button size="compact-xs" color="orange" onClick={() => void decide(true)} disabled={busy}>
                              确认执行
                            </Button>
                            <Button size="compact-xs" variant="default" onClick={() => void decide(false)} disabled={busy}>
                              取消
                            </Button>
                          </Group>
                        </Stack>
                      </Alert>
                    )}

                    {bubble.usage && (
                      <Text fz={10} c="dimmed">
                        输入 {Number(bubble.usage.input_tokens ?? 0)} / 输出 {Number(bubble.usage.output_tokens ?? 0)} token ·{" "}
                        {Number(bubble.usage.requests ?? 0)} 次模型请求
                      </Text>
                    )}

                    {bubble.stopped && (
                      <Text fz={11} c="dimmed">
                        已停止；已完成的操作保留在会话里
                      </Text>
                    )}

                    {bubble.error && (
                      <Alert color="red" variant="light" p="xs">
                        <Stack gap={6}>
                          <Text fz={11.5}>{bubble.error}</Text>
                          {lastPrompt.current && (
                            <Button size="compact-xs" variant="default" onClick={() => void send(lastPrompt.current)} disabled={busy}>
                              重试
                            </Button>
                          )}
                        </Stack>
                      </Alert>
                    )}
                  </Stack>
                )}
              </motion.div>
            ))}
          </Stack>
        </ScrollArea>

        <Box px="md" py="sm" style={{ borderTop: "1px solid var(--line)" }}>
          {compaction && (
            <Text fz={10} c="dimmed" mb={4} lineClamp={1}>
              上下文摘要：{compaction}
            </Text>
          )}
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
              <ActionIcon variant="default" size="lg" onClick={stop} aria-label="停止" className="btn-busy">
                <IconPlayerStop size={15} />
              </ActionIcon>
            ) : (
              <ActionIcon
                variant="filled"
                size="lg"
                onClick={() => void send()}
                disabled={!input.trim()}
                aria-label="发送"
                className="btn-press"
              >
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

      <Modal opened={Boolean(renameTarget)} onClose={() => setRenameTarget(null)} title="重命名会话" size="xs">
        <Stack gap="xs">
          <TextInput
            data-autofocus
            size="xs"
            value={renameValue}
            maxLength={28}
            onChange={(e) => setRenameValue(e.currentTarget.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") void submitRename();
            }}
          />
          <Group justify="flex-end">
            <Button size="compact-xs" variant="default" onClick={() => setRenameTarget(null)}>
              取消
            </Button>
            <Button size="compact-xs" onClick={() => void submitRename()} disabled={!renameValue.trim()}>
              保存
            </Button>
          </Group>
        </Stack>
      </Modal>

      <Modal
        opened={traceOpen}
        onClose={() => setTraceOpen(false)}
        title="执行轨迹 · Agent 每一步都记录在这里"
        size="lg"
        styles={{ body: { paddingTop: 8 } }}
      >
        <Stack gap="sm">
          <Text fz={11} c="dimmed">
            耗时是服务端实测；工具参数与结果原样保留，和审计日志一致。审批续跑会接到同一次运行后面。
          </Text>
          {traceRuns.length === 0 && !traceLoading && (
            <Text fz={12} c="dimmed" ta="center" py="xl">
              这个会话还没有运行记录。发一条消息后再来看。
            </Text>
          )}
          {traceRuns.length > 0 && (
            <Group gap={4} wrap="wrap">
              {traceRuns.map((run, i) => {
                const badge = TRACE_STATUS[run.status] ?? TRACE_STATUS.running;
                return (
                  <Button
                    key={run.run_id}
                    size="compact-xs"
                    variant={traceRun?.run_id === run.run_id ? "filled" : "default"}
                    color={traceRun?.run_id === run.run_id ? badge.color : "gray"}
                    onClick={() => void loadRun(run.run_id)}
                  >
                    #{traceRuns.length - i} · {clock(run.started_at)} · {run.tools} 工具 · {badge.label}
                  </Button>
                );
              })}
            </Group>
          )}

          {traceLoading && <Loader size="sm" />}

          {traceRun && !traceLoading && (
            <>
              <Group gap={8} wrap="wrap" p="xs" style={{ background: "var(--panel-2, var(--panel))", borderRadius: 4 }}>
                <Badge size="xs" variant="light" color={(TRACE_STATUS[traceRun.status] ?? TRACE_STATUS.running).color}>
                  {(TRACE_STATUS[traceRun.status] ?? TRACE_STATUS.running).label}
                </Badge>
                <Text fz={10.5} c="dimmed" className="mono">
                  {traceRun.model}
                </Text>
                <Text fz={10.5} c="dimmed">
                  {traceRun.steps.length} 步 · {duration(traceRun.steps.reduce((n, s) => n + (s.duration_ms ?? 0), 0))} 总耗时
                </Text>
                {traceRun.usage && (
                  <Text fz={10.5} c="dimmed">
                    输入 {Number(traceRun.usage.input_tokens ?? 0)} / 输出 {Number(traceRun.usage.output_tokens ?? 0)} token
                  </Text>
                )}
              </Group>
              {traceRun.prompt && (
                <Text fz={11} truncate title={traceRun.prompt}>
                  <Text component="span" c="dimmed">
                    提问：
                  </Text>
                  {traceRun.prompt}
                </Text>
              )}

              <Stack
                gap={0}
                className="trace-rail"
                style={{ borderLeft: "1px solid var(--line)", marginLeft: 6 }}
              >
                {traceRun.steps.map((step) => {
                  const meta = TRACE_STEP[step.kind] ?? TRACE_STEP.note;
                  const stat = TRACE_STATUS[step.status] ?? TRACE_STATUS.running;
                  const key = `trace-${step.seq}`;
                  const open = expanded.has(key);
                  const hasPayload = Boolean(step.detail || step.result || (step.args && JSON.stringify(step.args) !== "{}"));
                  return (
                    <motion.div
                      key={key}
                      className={`trace-step ${step.status === "running" ? "trace-running" : ""}`}
                      style={{ "--step-delay": `${Math.min(step.seq, 12) * 55}ms` } as React.CSSProperties}
                    >
                    <Box pl="sm" py={6} style={{ position: "relative" }}>
                      <Box
                        style={{
                          position: "absolute",
                          left: -5,
                          top: 12,
                          width: 9,
                          height: 9,
                          borderRadius: "50%",
                          background:
                            step.status === "failed"
                              ? "var(--red, #e03131)"
                              : step.status === "running" || step.status === "waiting"
                                ? "var(--orange, #f08c00)"
                                : "var(--accent, var(--dim))",
                          border: "2px solid var(--panel)",
                        }}
                      />
                      <Group gap={6} wrap="nowrap">
                        <Text fz={9.5} c="dimmed" className="mono" style={{ width: 18 }}>
                          {String(step.seq).padStart(2, "0")}
                        </Text>
                        <Badge size="xs" variant="light" color={meta.color}>
                          {meta.label}
                        </Badge>
                        <Text fz={11} className="mono" truncate>
                          {step.name || meta.label}
                        </Text>
                        <Text fz={9.5} c="dimmed">
                          {clock(step.at)}
                        </Text>
                        <Badge size="xs" variant="light" color={step.status === "done" ? "gray" : stat.color}>
                          {stat.label}
                        </Badge>
                        {step.duration_ms !== null && (
                          <Text fz={9.5} c="dimmed" style={{ marginLeft: "auto" }} className="mono">
                            {duration(step.duration_ms)}
                          </Text>
                        )}
                      </Group>
                      {step.detail && (
                        <Text fz={10.5} c="dimmed" pl={24} style={{ wordBreak: "break-word" }}>
                          {step.detail}
                        </Text>
                      )}
                      {hasPayload && (
                        <>
                          <Text
                            fz={10}
                            c="dimmed"
                            pl={24}
                            style={{ cursor: "pointer", userSelect: "none" }}
                            onClick={() => togglePreview(key)}
                          >
                            {open ? "收起详情" : "查看详情 · 参数/结果"}
                          </Text>
                          {open && (
                            <Box pl={24} mt={4}>
                              {step.args !== undefined && step.args !== null && (
                                <Text fz={10} c="dimmed" mb={4}>
                                  参数
                                </Text>
                              )}
                              {step.args !== undefined && step.args !== null && (
                                <Code
                                  block
                                  fz={10}
                                  style={{ background: "var(--panel)", padding: 6, whiteSpace: "pre-wrap", wordBreak: "break-all", maxHeight: 200, overflow: "auto" }}
                                >
                                  {JSON.stringify(step.args, null, 2)}
                                </Code>
                              )}
                              {step.result && (
                                <>
                                  <Text fz={10} c="dimmed" mt={6} mb={4}>
                                    结果
                                  </Text>
                                  <Code
                                    block
                                    fz={10}
                                    style={{ background: "var(--panel)", padding: 6, whiteSpace: "pre-wrap", wordBreak: "break-all", maxHeight: 260, overflow: "auto" }}
                                  >
                                    {step.result}
                                  </Code>
                                </>
                              )}
                            </Box>
                          )}
                        </>
                      )}
                    </Box>
                    </motion.div>
                  );
                })}
              </Stack>
            </>
          )}
        </Stack>
      </Modal>
    </Group>
  );
}
