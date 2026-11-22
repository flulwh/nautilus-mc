import {
  ActionIcon,
  Alert,
  Badge,
  Box,
  Button,
  Card,
  Group,
  Modal,
  Progress,
  ScrollArea,
  Select,
  Stack,
  Table,
  Text,
  TextInput,
  Title,
  Tooltip,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import {
  IconAlertTriangle,
  IconArchive,
  IconDownload,
  IconHistory,
  IconTrash,
} from "@tabler/icons-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, ApiError } from "../../api";
import { useConfirm } from "../../components/Confirm";
import type { BackupArchive, BackupList } from "../../types";
import { useSocket } from "../../useSocket";

const SCOPES = [
  { value: "world", label: "世界存档（推荐）" },
  { value: "config", label: "仅配置文件" },
  { value: "full", label: "全部文件（含 jar）" },
];

const SCOPE_LABEL: Record<string, string> = {
  world: "世界",
  config: "配置",
  full: "全部",
};

function human(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1048576) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1073741824) return `${(bytes / 1048576).toFixed(1)} MB`;
  return `${(bytes / 1073741824).toFixed(2)} GB`;
}

function when(ts: number): string {
  return new Date(ts * 1000).toLocaleString("zh-CN", { hour12: false });
}

export function BackupsTab({ serverId }: { serverId: number }) {
  const qc = useQueryClient();
  const confirm = useConfirm();
  const [scope, setScope] = useState("world");
  const [label, setLabel] = useState("");
  const [restoring, setRestoring] = useState<BackupArchive | null>(null);
  const [progress, setProgress] = useState<{ pct: number; stage: string } | null>(null);

  const list = useQuery<BackupList>({
    queryKey: ["backups", serverId],
    queryFn: () => api.get<BackupList>(`/servers/${serverId}/backups`),
    refetchInterval: (query) => (query.state.data?.in_progress ? 1000 : false),
  });

  useSocket(`/ws/servers/${serverId}`, (msg) => {
    if (msg.t !== "backup") return;
    const pct = Number(msg.pct);
    setProgress({ pct, stage: String(msg.stage ?? "") });
    if (!msg.running) {
      qc.invalidateQueries({ queryKey: ["backups", serverId] });
    }
  });

  const create = useMutation({
    mutationFn: () => api.post(`/servers/${serverId}/backups`, { scope, label: label.trim() }),
    onSuccess: () => {
      notifications.show({ color: "teal", title: "已开始备份", message: "完成后会出现在下方列表" });
      setLabel("");
      qc.invalidateQueries({ queryKey: ["backups", serverId] });
    },
    onError: (e) => notifications.show({ color: "red", title: "备份失败", message: (e as ApiError).detail }),
  });

  const restore = useMutation({
    mutationFn: (name: string) => api.post(`/servers/${serverId}/backups/${encodeURIComponent(name)}/restore`),
    onSuccess: () => {
      notifications.show({ color: "teal", title: "恢复完成", message: "原文件已挪到 .nautilus/pre-restore 作为安全副本" });
      setRestoring(null);
      qc.invalidateQueries({ queryKey: ["backups", serverId] });
      qc.invalidateQueries({ queryKey: ["files", serverId] });
    },
    onError: (e) => notifications.show({ color: "red", title: "恢复失败", message: (e as ApiError).detail }),
  });

  const remove = useMutation({
    mutationFn: (name: string) => api.del(`/servers/${serverId}/backups/${encodeURIComponent(name)}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["backups", serverId] }),
    onError: (e) => notifications.show({ color: "red", message: (e as ApiError).detail }),
  });

  const data = list.data;
  const busy = create.isPending || !!data?.in_progress;

  return (
    <Stack gap="md">
      <Card className="glass" p="md" withBorder>
        <Group justify="space-between" align="flex-end" wrap="nowrap">
          <Group align="flex-end" gap="sm" style={{ flex: 1 }}>
            <Select
              label="备份范围"
              data={SCOPES}
              value={scope}
              onChange={(v) => setScope(v || "world")}
              allowDeselect={false}
              style={{ width: 200 }}
              disabled={busy}
            />
            <TextInput
              label="备注"
              placeholder="例如：升级前"
              value={label}
              onChange={(e) => setLabel(e.currentTarget.value)}
              style={{ flex: 1 }}
              maxLength={40}
              disabled={busy}
            />
            <Button
              color="steelblue"
              leftSection={<IconArchive size={15} />}
              loading={busy}
              onClick={() => create.mutate()}
            >
              立即备份
            </Button>
          </Group>
          <Text size="11px" c="dimmed" className="mono">
            保留最近 {data?.keep ?? 10} 份
          </Text>
        </Group>

        {data?.world_dirs?.length ? (
          <Text size="11px" c="dimmed" mt="sm">
            世界目录：<span className="mono">{data.world_dirs.join(", ")}</span>
          </Text>
        ) : (
          <Text size="11px" c="dimmed" mt="sm">
            还没有世界存档。启动一次服务器后会自动生成 <span className="mono">world/</span>。
          </Text>
        )}

        {busy && (
          <Stack gap={4} mt="sm">
            <Progress
              value={progress && progress.pct >= 0 ? progress.pct : 100}
              animated={!progress || progress.pct < 0}
              striped
              color="steelblue"
              size="sm"
            />
            <Text size="10.5px" c="dimmed" className="mono">
              {progress ? `${progress.stage}${progress.pct >= 0 ? ` · ${progress.pct}%` : ""}` : "正在准备…"}
            </Text>
          </Stack>
        )}
      </Card>

      <Alert color="blue" variant="light" icon={<IconHistory size={15} />} p="xs">
        <Text size="11px">
          恢复备份前必须先停止服务器（JVM 持有区块文件）。恢复是「覆盖式」的：被替换的原文件会挪到{" "}
          <span className="mono">.nautilus/pre-restore/</span>，出问题可手工回滚。
        </Text>
      </Alert>

      <Card className="glass" p={0} withBorder>
        {data?.archives.length ? (
          <ScrollArea h="calc(100vh - 384px)" type="hover" scrollbars="y" offsetScrollbars>
            <Table verticalSpacing="sm" horizontalSpacing="md" highlightOnHover>
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>备份</Table.Th>
                  <Table.Th w={90}>范围</Table.Th>
                  <Table.Th w={100}>大小</Table.Th>
                  <Table.Th w={150}>时间</Table.Th>
                  <Table.Th w={110} />
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {data.archives.map((a) => (
                  <Table.Tr key={a.name}>
                    <Table.Td>
                      <Group gap={6} wrap="nowrap">
                        <IconArchive size={15} color="var(--dim)" />
                        <Stack gap={0} style={{ minWidth: 0 }}>
                          <Text size="sm" className="mono" truncate>
                            {a.label || a.name}
                          </Text>
                          <Text size="10px" c="dimmed" className="mono" truncate>
                            {a.name}
                          </Text>
                        </Stack>
                      </Group>
                    </Table.Td>
                    <Table.Td>
                      <Badge size="sm" variant="light" color={a.scope === "full" ? "orange" : "steelblue"}>
                        {SCOPE_LABEL[a.scope] ?? a.scope}
                      </Badge>
                    </Table.Td>
                    <Table.Td>
                      <Text size="xs" className="mono">
                        {human(a.bytes)}
                      </Text>
                      <Text size="10px" c="dimmed">
                        {a.entries} 个文件
                      </Text>
                    </Table.Td>
                    <Table.Td>
                      <Text size="xs">{when(a.created_at)}</Text>
                    </Table.Td>
                    <Table.Td>
                      <Group gap={2} justify="flex-end" wrap="nowrap">
                        <Tooltip label="下载">
                          <ActionIcon
                            variant="subtle"
                            color="gray"
                            component="a"
                            href={`/api/servers/${serverId}/backups/${encodeURIComponent(a.name)}/download`}
                            download
                          >
                            <IconDownload size={15} />
                          </ActionIcon>
                        </Tooltip>
                        <Tooltip label={data.running ? "请先停止服务器" : "恢复此备份"}>
                          <ActionIcon
                            variant="subtle"
                            color="orange"
                            disabled={data.running}
                            onClick={() => setRestoring(a)}
                          >
                            <IconHistory size={15} />
                          </ActionIcon>
                        </Tooltip>
                        <Tooltip label="删除">
                          <ActionIcon
                            variant="subtle"
                            color="red"
                            onClick={async () => {
                              if (await confirm({ title: "删除备份", message: `删除备份 ${a.name}？此操作不可恢复。`, danger: true })) {
                                remove.mutate(a.name);
                              }
                            }}
                          >
                            <IconTrash size={15} />
                          </ActionIcon>
                        </Tooltip>
                      </Group>
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </ScrollArea>
        ) : (
          <Box py={16}>
            <Text fz={12}>还没有备份。</Text>
            <Text fz={11.5} c="dimmed" mt={4}>
              备份会打包世界存档和 server.properties；升级版本或装插件前先做一次。
            </Text>
          </Box>
        )}
      </Card>

      <Modal opened={!!restoring} onClose={() => setRestoring(null)} title="恢复备份" size="md">
        <Stack gap="md">
          <Alert color="orange" variant="light" icon={<IconAlertTriangle size={15} />} p="xs">
            <Text size="11px">
              将用 <span className="mono">{restoring?.name}</span> 覆盖当前服务器目录中的同名文件。此操作不可撤销，
              但被替换的文件会保留在 <span className="mono">.nautilus/pre-restore/</span>。
            </Text>
          </Alert>
          <Text size="xs" c="dimmed">
            范围：{SCOPE_LABEL[restoring?.scope ?? "world"]} · {restoring ? human(restoring.bytes) : ""} · 包含{" "}
            {restoring?.includes.slice(0, 6).join("、")}
            {(restoring?.includes.length ?? 0) > 6 ? " 等" : ""}
          </Text>
          <Group justify="flex-end">
            <Button variant="subtle" color="gray" onClick={() => setRestoring(null)}>
              取消
            </Button>
            <Button
              color="orange"
              loading={restore.isPending}
              onClick={() => restoring && restore.mutate(restoring.name)}
            >
              确认恢复
            </Button>
          </Group>
        </Stack>
      </Modal>
    </Stack>
  );
}
