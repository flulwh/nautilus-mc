import {
  ActionIcon,
  Anchor,
  Breadcrumbs,
  Button,
  Group,
  Modal,
  Stack,
  Table,
  Text,
  TextInput,
  Tooltip,
  ScrollArea,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import {
  IconArrowUp,
  IconDownload,
  IconFile,
  IconFolder,
  IconFolderPlus,
  IconPencil,
  IconRefresh,
  IconTrash,
} from "@tabler/icons-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { api, ApiError } from "../../api";
import type { FileEntry } from "../../types";

function human(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1048576) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1048576).toFixed(1)} MB`;
}

export function FilesTab({ serverId }: { serverId: number }) {
  const qc = useQueryClient();
  const [path, setPath] = useState("");
  const [editing, setEditing] = useState<{ path: string; content: string } | null>(null);
  const [saving, setSaving] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);

  const list = useQuery<{ path: string; entries: FileEntry[]; parent: string | null }>({
    queryKey: ["files", serverId, path],
    queryFn: () => api.get(`/servers/${serverId}/files?path=${encodeURIComponent(path)}`),
  });

  useEffect(() => {
    if (list.data && list.data.path !== path) setPath(list.data.path);
  }, [list.data]);

  const mkdir = useMutation({
    mutationFn: async () => {
      const name = window.prompt("目录名称");
      if (!name) throw new Error("cancelled");
      return api.post(`/servers/${serverId}/files/mkdir`, { path: path ? `${path}/${name}` : name });
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["files", serverId] }),
    onError: (e) => {
      if ((e as Error).message !== "cancelled")
        notifications.show({ color: "red", title: "创建失败", message: (e as Error).message });
    },
  });

  const remove = useMutation({
    mutationFn: (entryPath: string) =>
      api.del(`/servers/${serverId}/files?path=${encodeURIComponent(entryPath)}&recursive=true`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["files", serverId] }),
    onError: (e) => notifications.show({ color: "red", title: "删除失败", message: (e as Error).message }),
  });

  const open = async (entry: FileEntry) => {
    if (entry.is_dir) {
      setPath(entry.path);
      return;
    }
    if (!entry.editable) {
      notifications.show({ color: "yellow", message: "该文件不支持在线编辑，请下载后查看" });
      return;
    }
    try {
      const res = await api.get<{ content: string }>(
        `/servers/${serverId}/files/content?path=${encodeURIComponent(entry.path)}`,
      );
      setEditing({ path: entry.path, content: res.content });
    } catch (e) {
      notifications.show({ color: "red", title: "读取失败", message: (e as ApiError).detail });
    }
  };

  const save = async () => {
    if (!editing) return;
    setSaving(true);
    try {
      await api.put(`/servers/${serverId}/files/content`, { path: editing.path, content: editing.content });
      notifications.show({ color: "teal", message: "已保存" });
      setEditing(null);
      qc.invalidateQueries({ queryKey: ["files", serverId] });
    } catch (e) {
      notifications.show({ color: "red", title: "保存失败", message: (e as ApiError).detail });
    } finally {
      setSaving(false);
    }
  };

  const upload = async (file: File) => {
    try {
      await api.upload(`/servers/${serverId}/files/upload?path=${encodeURIComponent(path)}`, file);
      notifications.show({ color: "teal", message: `${file.name} 已上传` });
      qc.invalidateQueries({ queryKey: ["files", serverId] });
    } catch (e) {
      notifications.show({ color: "red", title: "上传失败", message: (e as ApiError).detail });
    }
  };

  const crumbs = path ? path.split("/") : [];

  return (
    <Stack gap="sm">
      <Group justify="space-between">
        <Group gap={6}>
          <Breadcrumbs separator="/" separatorMargin={4}>
            <Anchor size="xs" c="dimmed" onClick={() => setPath("")} style={{ cursor: "pointer" }}>
              根目录
            </Anchor>
            {crumbs.map((c, i) => (
              <Anchor
                key={`${c}-${i}`}
                size="xs"
                c={i === crumbs.length - 1 ? "abyss.3" : "dimmed"}
                onClick={() => setPath(crumbs.slice(0, i + 1).join("/"))}
                style={{ cursor: "pointer" }}
              >
                {c}
              </Anchor>
            ))}
          </Breadcrumbs>
          {path && (
            <ActionIcon size="xs" variant="subtle" color="gray" onClick={() => setPath(crumbs.slice(0, -1).join("/"))}>
              <IconArrowUp size={13} />
            </ActionIcon>
          )}
        </Group>
        <Group gap={6}>
          <Button size="xs" variant="light" color="gray" leftSection={<IconFolderPlus size={14} />} onClick={() => mkdir.mutate()}>
            新建目录
          </Button>
          <Button size="xs" variant="light" color="abyss" leftSection={<IconArrowUp size={14} />} onClick={() => fileInput.current?.click()}>
            上传
          </Button>
          <ActionIcon variant="subtle" color="gray" onClick={() => qc.invalidateQueries({ queryKey: ["files", serverId] })}>
            <IconRefresh size={15} />
          </ActionIcon>
          <input
            ref={fileInput}
            type="file"
            hidden
            onChange={(e) => {
              const f = e.currentTarget.files?.[0];
              if (f) upload(f);
              e.currentTarget.value = "";
            }}
          />
        </Group>
      </Group>

      <ScrollArea h="calc(100vh - 330px)" type="auto">
        <Table verticalSpacing={4} highlightOnHover>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>名称</Table.Th>
              <Table.Th w={90}>大小</Table.Th>
              <Table.Th w={130}>修改时间</Table.Th>
              <Table.Th w={80} />
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {list.data?.entries.map((entry) => (
              <Table.Tr key={entry.path} className="file-row" onClick={() => open(entry)}>
                <Table.Td>
                  <Group gap={7} wrap="nowrap">
                    {entry.is_dir ? <IconFolder size={15} color="#e8b13a" /> : <IconFile size={15} color="#5d8496" />}
                    <Text size="xs" className="mono">
                      {entry.name}
                    </Text>
                  </Group>
                </Table.Td>
                <Table.Td>
                  <Text size="10.5px" c="dimmed" className="tabular">
                    {entry.is_dir ? "—" : human(entry.size)}
                  </Text>
                </Table.Td>
                <Table.Td>
                  <Text size="10.5px" c="dimmed" className="tabular">
                    {new Date(entry.mtime * 1000).toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" })}
                  </Text>
                </Table.Td>
                <Table.Td w={104}>
                  <Group gap={2} justify="flex-end" wrap="nowrap" onClick={(e) => e.stopPropagation()}>
                    {!entry.is_dir && (
                      <>
                        <Tooltip label="编辑">
                          <ActionIcon size="sm" variant="subtle" color="gray" onClick={() => open(entry)}>
                            <IconPencil size={13} />
                          </ActionIcon>
                        </Tooltip>
                        <Tooltip label="下载">
                          <ActionIcon
                            size="sm"
                            variant="subtle"
                            color="gray"
                            component="a"
                            href={`/api/servers/${serverId}/files/download?path=${encodeURIComponent(entry.path)}`}
                          >
                            <IconDownload size={13} />
                          </ActionIcon>
                        </Tooltip>
                      </>
                    )}
                    <Tooltip label="删除">
                      <ActionIcon
                        size="sm"
                        variant="subtle"
                        color="red"
                        onClick={() => {
                          if (window.confirm(`删除 ${entry.name}？`)) remove.mutate(entry.path);
                        }}
                      >
                        <IconTrash size={13} />
                      </ActionIcon>
                    </Tooltip>
                  </Group>
                </Table.Td>
              </Table.Tr>
            ))}
            {!list.data?.entries.length && (
              <Table.Tr>
                <Table.Td colSpan={4}>
                  <Text size="xs" c="dimmed" py="md" ta="center">
                    目录为空
                  </Text>
                </Table.Td>
              </Table.Tr>
            )}
          </Table.Tbody>
        </Table>
      </ScrollArea>

      <Modal opened={!!editing} onClose={() => setEditing(null)} title={<span className="mono">{editing?.path}</span>} size="xl">
        <Stack gap="sm">
          <textarea
            className="code-editor"
            value={editing?.content ?? ""}
            onChange={(e) => setEditing((v) => (v ? { ...v, content: e.target.value } : v))}
            spellCheck={false}
          />
          <Group justify="flex-end">
            <Button variant="subtle" color="gray" onClick={() => setEditing(null)}>
              取消
            </Button>
            <Button color="abyss" loading={saving} onClick={save}>
              保存
            </Button>
          </Group>
        </Stack>
      </Modal>
    </Stack>
  );
}
