import {
  ActionIcon,
  Badge,
  Box,
  Button,
  Card,
  Group,
  Modal,
  ScrollArea,
  Select,
  Stack,
  Switch,
  Table,
  Text,
  TextInput,
  Title,
  Tooltip,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import {
  IconBolt,
  IconCalendarTime,
  IconPencil,
  IconPlus,
  IconTrash,
} from "@tabler/icons-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, ApiError } from "../../api";
import { useConfirm } from "../../components/Confirm";
import type { ScheduledTask, TaskList } from "../../types";

const KIND_META: Record<string, { label: string; color: string }> = {
  restart: { label: "重启", color: "orange" },
  start: { label: "启动", color: "teal" },
  stop: { label: "停止", color: "gray" },
  kill: { label: "强杀", color: "red" },
  backup: { label: "备份", color: "steelblue" },
  command: { label: "指令", color: "blue" },
};

const PRESETS = [
  { cron: "0 4 * * *", label: "每天 04:00" },
  { cron: "0 */6 * * *", label: "每 6 小时" },
  { cron: "30 3 * * 1", label: "每周一 03:30" },
  { cron: "*/15 * * * *", label: "每 15 分钟" },
];

type Draft = {
  id?: number;
  name: string;
  kind: string;
  cron: string;
  payload: string;
  enabled: boolean;
};

const EMPTY: Draft = { name: "", kind: "restart", cron: "0 4 * * *", payload: "", enabled: true };

function fmt(iso: string | null): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString("zh-CN", { hour12: false, month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}

export function TasksTab({ serverId }: { serverId: number }) {
  const qc = useQueryClient();
  const confirm = useConfirm();
  const [draft, setDraft] = useState<Draft | null>(null);

  const list = useQuery<TaskList>({
    queryKey: ["tasks", serverId],
    queryFn: () => api.get<TaskList>(`/servers/${serverId}/tasks`),
    refetchInterval: 15000,
  });

  const invalidate = () => qc.invalidateQueries({ queryKey: ["tasks", serverId] });
  const onError = (e: unknown) =>
    notifications.show({ color: "red", title: "操作失败", message: (e as ApiError).detail });

  const save = useMutation({
    mutationFn: (value: Draft) => {
      const body = {
        name: value.name.trim(),
        kind: value.kind,
        cron: value.cron.trim(),
        payload: value.payload.trim(),
        enabled: value.enabled,
      };
      return value.id
        ? api.patch<ScheduledTask>(`/servers/${serverId}/tasks/${value.id}`, body)
        : api.post<ScheduledTask>(`/servers/${serverId}/tasks`, body);
    },
    onSuccess: (_data, value) => {
      notifications.show({ color: "teal", message: value.id ? "任务已更新" : "任务已创建" });
      setDraft(null);
      invalidate();
    },
    onError,
  });

  const toggle = useMutation({
    mutationFn: (task: ScheduledTask) =>
      api.patch(`/servers/${serverId}/tasks/${task.id}`, { enabled: !task.enabled }),
    onSuccess: invalidate,
    onError,
  });

  const runNow = useMutation({
    mutationFn: (task: ScheduledTask) => api.post<{ status: string; result: string }>(`/servers/${serverId}/tasks/${task.id}/run`),
    onSuccess: (data) => {
      notifications.show({
        color: data.status === "ok" ? "teal" : "red",
        title: data.status === "ok" ? "执行完成" : "执行失败",
        message: data.result || "已触发",
      });
      invalidate();
    },
    onError,
  });

  const remove = useMutation({
    mutationFn: (task: ScheduledTask) => api.del(`/servers/${serverId}/tasks/${task.id}`),
    onSuccess: () => {
      notifications.show({ color: "teal", message: "任务已删除" });
      invalidate();
    },
    onError,
  });

  const tasks = list.data?.tasks ?? [];
  const draftValid =
    !!draft &&
    draft.name.trim().length >= 1 &&
    draft.cron.trim().split(/\s+/).length === 5 &&
    (draft.kind !== "command" || draft.payload.trim().length > 0);

  return (
    <Stack gap="md">
      <Card className="glass" p="md" withBorder>
        <Group justify="space-between" align="flex-start">
          <Stack gap={2}>
            <Group gap={8}>
              <IconCalendarTime size={16} color="#5d8496" />
              <Title order={4}>定时任务</Title>
              <Badge size="sm" variant="light" color="steelblue">
                {tasks.length}
              </Badge>
            </Group>
            <Text size="11px" c="dimmed">
              cron 使用 5 段格式（分 时 日 月 周），按面板时区{" "}
              <span className="mono">{list.data?.timezone ?? "面板默认"}</span> 解释。
            </Text>
          </Stack>
          <Button color="steelblue" leftSection={<IconPlus size={15} />} onClick={() => setDraft({ ...EMPTY })}>
            新建任务
          </Button>
        </Group>
      </Card>

      <Card className="glass" p={0} withBorder>
        {tasks.length ? (
          <ScrollArea h="calc(100vh - 380px)" type="hover" scrollbars="y" offsetScrollbars>
            <Table verticalSpacing="sm" horizontalSpacing="md" highlightOnHover>
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>任务</Table.Th>
                  <Table.Th w={80}>类型</Table.Th>
                  <Table.Th w={130}>计划</Table.Th>
                  <Table.Th w={120}>下次执行</Table.Th>
                  <Table.Th w={150}>上次结果</Table.Th>
                  <Table.Th w={54}>启用</Table.Th>
                  <Table.Th w={96} />
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {tasks.map((task) => {
                  const meta = KIND_META[task.kind] ?? { label: task.kind, color: "gray" };
                  return (
                    <Table.Tr key={task.id}>
                      <Table.Td>
                        <Text size="sm" fw={550}>
                          {task.name}
                        </Text>
                        {task.payload && task.kind !== "backup" ? (
                          <Text size="10px" c="dimmed" className="mono" truncate maw={260}>
                            {task.payload}
                          </Text>
                        ) : null}
                      </Table.Td>
                      <Table.Td>
                        <Badge size="sm" variant="light" color={meta.color}>
                          {meta.label}
                        </Badge>
                      </Table.Td>
                      <Table.Td>
                        <Text size="xs" className="mono">
                          {task.cron}
                        </Text>
                      </Table.Td>
                      <Table.Td>
                        <Text size="xs" c={task.enabled ? undefined : "dimmed"}>
                          {task.enabled ? fmt(task.next_run) : "已停用"}
                        </Text>
                      </Table.Td>
                      <Table.Td>
                        {task.last_status ? (
                          <Tooltip label={task.last_result || "无输出"} multiline maw={360}>
                            <Stack gap={0}>
                              <Badge size="xs" variant="light" color={task.last_status === "ok" ? "teal" : "red"}>
                                {task.last_status === "ok" ? "成功" : "失败"}
                              </Badge>
                              <Text size="9.5px" c="dimmed">
                                {fmt(task.last_run)}
                              </Text>
                            </Stack>
                          </Tooltip>
                        ) : (
                          <Text size="xs" c="dimmed">
                            从未执行
                          </Text>
                        )}
                      </Table.Td>
                      <Table.Td>
                        <Switch
                          size="sm"
                          color="steelblue"
                          checked={task.enabled}
                          onChange={() => toggle.mutate(task)}
                        />
                      </Table.Td>
                      <Table.Td>
                        <Group gap={2} justify="flex-end" wrap="nowrap">
                          <Tooltip label="立即执行">
                            <ActionIcon variant="subtle" color="teal" loading={runNow.isPending} onClick={() => runNow.mutate(task)}>
                              <IconBolt size={15} />
                            </ActionIcon>
                          </Tooltip>
                          <Tooltip label="编辑">
                            <ActionIcon
                              variant="subtle"
                              color="gray"
                              onClick={() =>
                                setDraft({
                                  id: task.id,
                                  name: task.name,
                                  kind: task.kind,
                                  cron: task.cron,
                                  payload: task.payload,
                                  enabled: task.enabled,
                                })
                              }
                            >
                              <IconPencil size={15} />
                            </ActionIcon>
                          </Tooltip>
                          <Tooltip label="删除">
                            <ActionIcon
                              variant="subtle"
                              color="red"
                              onClick={async () => {
                                if (await confirm({ title: "删除任务", message: `删除任务「${task.name}」？`, danger: true })) {
                                  remove.mutate(task);
                                }
                              }}
                            >
                              <IconTrash size={15} />
                            </ActionIcon>
                          </Tooltip>
                        </Group>
                      </Table.Td>
                    </Table.Tr>
                  );
                })}
              </Table.Tbody>
            </Table>
          </ScrollArea>
        ) : (
          <Box py={16}>
            <Text fz={12}>还没有定时任务。</Text>
            <Text fz={11.5} c="dimmed" mt={4}>
              常见组合：凌晨自动备份世界、每周重启一次释放内存、整点广播提示。点右上角「新建任务」开始。
            </Text>
          </Box>
        )}
      </Card>

      <Modal opened={!!draft} onClose={() => setDraft(null)} title={draft?.id ? "编辑任务" : "新建定时任务"} size="md">
        {draft && (
          <Stack gap="md">
            <TextInput
              label="名称"
              placeholder="例如：凌晨自动备份"
              value={draft.name}
              onChange={(e) => setDraft({ ...draft, name: e.currentTarget.value })}
              maxLength={60}
              required
            />
            <Select
              label="动作"
              data={[
                { value: "backup", label: "创建备份" },
                { value: "restart", label: "重启服务器" },
                { value: "start", label: "启动服务器" },
                { value: "stop", label: "停止服务器" },
                { value: "kill", label: "强制结束进程" },
                { value: "command", label: "执行控制台指令" },
              ]}
              value={draft.kind}
              onChange={(v) => setDraft({ ...draft, kind: v || "backup" })}
              allowDeselect={false}
            />

            {draft.kind === "backup" ? (
              <Select
                label="备份范围"
                data={[
                  { value: "world", label: "世界存档（推荐）" },
                  { value: "config", label: "仅配置文件" },
                  { value: "full", label: "全部文件" },
                ]}
                value={draft.payload || "world"}
                onChange={(v) => setDraft({ ...draft, payload: v || "world" })}
                allowDeselect={false}
              />
            ) : draft.kind === "command" ? (
              <TextInput
                label="指令"
                description="不需要斜杠，例如 say 服务器将在 5 分钟后重启"
                value={draft.payload}
                onChange={(e) => setDraft({ ...draft, payload: e.currentTarget.value })}
                className="mono"
                maxLength={1000}
                required
              />
            ) : null}

            <Stack gap={6}>
              <TextInput
                label="cron 表达式"
                description="分 时 日 月 周"
                value={draft.cron}
                onChange={(e) => setDraft({ ...draft, cron: e.currentTarget.value })}
                className="mono"
                error={draft.cron.trim().split(/\s+/).length === 5 ? undefined : "需要 5 个字段"}
              />
              <Group gap={6}>
                {PRESETS.map((preset) => (
                  <Button
                    key={preset.cron}
                    size="compact-xs"
                    variant={draft.cron === preset.cron ? "light" : "subtle"}
                    color="steelblue"
                    onClick={() => setDraft({ ...draft, cron: preset.cron })}
                  >
                    {preset.label}
                  </Button>
                ))}
              </Group>
            </Stack>

            <Group justify="space-between">
              <Text size="sm">启用</Text>
              <Switch
                checked={draft.enabled}
                onChange={(e) => setDraft({ ...draft, enabled: e.currentTarget.checked })}
                color="steelblue"
              />
            </Group>

            <Group justify="flex-end">
              <Button variant="subtle" color="gray" onClick={() => setDraft(null)}>
                取消
              </Button>
              <Button color="steelblue" loading={save.isPending} disabled={!draftValid} onClick={() => save.mutate(draft)}>
                {draft.id ? "保存" : "创建"}
              </Button>
            </Group>
          </Stack>
        )}
      </Modal>
    </Stack>
  );
}
