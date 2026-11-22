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
  IconSearch,
  IconTrash,
} from "@tabler/icons-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { api, ApiError } from "../../api";
import { ListEmpty, ListError, ListLoading } from "../../components/AsyncState";
import { useConfirm } from "../../components/Confirm";
import type { FileEntry } from "../../types";

function human(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1048576) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1048576).toFixed(1)} MB`;
}

export function FilesTab({ serverId }: { serverId: number }) {
  const qc = useQueryClient();
  const confirm = useConfirm();
  const [path, setPath] = useState("");
  const [editing, setEditing] = useState<{ path: string; content: string } | null>(null);
  const [saving, setSaving] = useState(false);
  const [search, setSearch] = useState("");
  const [showHidden, setShowHidden] = useState(false);
  const [mkdirOpen, setMkdirOpen] = useState(false);
  const [mkdirName, setMkdirName] = useState("");
  const [renaming, setRenaming] = useState<FileEntry | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const fileInput = useRef<HTMLInputElement>(null);

  const list = useQuery<{ path: string; entries: FileEntry[]; parent: string | null }>({
    queryKey: ["files", serverId, path, showHidden],
    queryFn: () =>
      api.get(
        `/servers/${serverId}/files?path=${encodeURIComponent(path)}&hidden=${showHidden ? "true" : "false"}`,
      ),
  });

  useEffect(() => {
    if (list.data && list.data.path !== path) setPath(list.data.path);
  }, [list.data]);

  const mkdir = useMutation({
    mutationFn: (name: string) =>
      api.post(`/servers/${serverId}/files/mkdir`, { path: path ? `${path}/${name}` : name }),
    onSuccess: () => {
      setMkdirOpen(false);
      setMkdirName("");
      qc.invalidateQueries({ queryKey: ["files", serverId] });
    },
    onError: (e) => notifications.show({ color: "red", title: "创建失败", message: (e as ApiError).detail ?? (e as Error).message }),
  });

  const rename = useMutation({
    mutationFn: ({ entryPath, name }: { entryPath: string; name: string }) =>
      api.post(`/servers/${serverId}/files/rename`, { path: entryPath, name }),
    onSuccess: () => {
      setRenaming(null);
      qc.invalidateQueries({ queryKey: ["files", serverId] });
    },
    onError: (e) => notifications.show({ color: "red", title: "重命名失败", message: (e as ApiError).detail ?? (e as Error).message }),
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
  const shown = (list.data?.entries ?? []).filter((e) =>
    search ? e.name.toLowerCase().includes(search.toLowerCase()) : true,
  );

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
                c={i === crumbs.length - 1 ? "steelblue.5" : "dimmed"}
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
          <TextInput
            size="xs"
            placeholder="筛选当前目录…"
            leftSection={<IconSearch size={13} />}
            value={search}
            onChange={(e) => setSearch(e.currentTarget.value)}
            style={{ width: 170 }}
          />
          <Tooltip label={showHidden ? "隐藏文件：显示" : "隐藏文件：隐藏"}>
            <ActionIcon
              variant={showHidden ? "filled" : "subtle"}
              color="gray"
              size="sm"
              onClick={() => setShowHidden((v) => !v)}
            >
              <IconFolder size={14} />
            </ActionIcon>
          </Tooltip>
          <Button size="xs" variant="light" color="gray" leftSection={<IconFolderPlus size={14} />} onClick={() => setMkdirOpen(true)}>
            新建目录
          </Button>
          <Button size="xs" variant="light" color="steelblue" leftSection={<IconArrowUp size={14} />} onClick={() => fileInput.current?.click()}>
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

      {list.isError ? (
        <ListError error={list.error} onRetry={() => void list.refetch()} />
      ) : list.isLoading ? (
        <ListLoading label="读取目录…" />
      ) : !shown.length ? (
        <ListEmpty label={search ? "没有匹配的文件" : "目录为空"} hint={search ? "换个关键词试试" : undefined} />
      ) : (
      <ScrollArea h="calc(100vh - 330px)" type="auto">
        <Table verticalSpacing={4} highlightOnHover>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>名称</Table.Th>
              <Table.Th w={90}>大小</Table.Th>
              <Table.Th w={130}>修改时间</Table.Th>
              <Table.Th w={140} />
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {shown.map((entry) => (
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
                <Table.Td w={140}>
                  <Group gap={2} justify="flex-end" wrap="nowrap" onClick={(e) => e.stopPropagation()}>
                    <Tooltip label="重命名">
                      <ActionIcon
                        size="sm"
                        variant="subtle"
                        color="gray"
                        onClick={() => {
                          setRenaming(entry);
                          setRenameValue(entry.name);
                        }}
                      >
                        <IconPencil size={13} />
                      </ActionIcon>
                    </Tooltip>
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
                        onClick={async () => {
                          if (await confirm({ title: "删除文件", message: `删除 ${entry.name}？此操作不可恢复。`, danger: true })) {
                            remove.mutate(entry.path);
                          }
                        }}
                      >
                        <IconTrash size={13} />
                      </ActionIcon>
                    </Tooltip>
                  </Group>
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </ScrollArea>
      )}

      <Modal opened={mkdirOpen} onClose={() => setMkdirOpen(false)} title="新建目录" size="xs">
        <Stack gap="sm">
          <TextInput
            label="目录名"
            data-autofocus
            size="xs"
            value={mkdirName}
            onChange={(e) => setMkdirName(e.currentTarget.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && mkdirName.trim()) mkdir.mutate(mkdirName.trim());
            }}
          />
          <Group justify="flex-end">
            <Button size="compact-sm" variant="default" onClick={() => setMkdirOpen(false)}>
              取消
            </Button>
            <Button size="compact-sm" loading={mkdir.isPending} disabled={!mkdirName.trim()} onClick={() => mkdir.mutate(mkdirName.trim())}>
              创建
            </Button>
          </Group>
        </Stack>
      </Modal>

      <Modal opened={!!renaming} onClose={() => setRenaming(null)} title="重命名" size="xs">
        <Stack gap="sm">
          <Text fz={10.5} c="dimmed" className="mono" truncate>
            {renaming?.path}
          </Text>
          <TextInput
            label="新名称"
            data-autofocus
            size="xs"
            value={renameValue}
            onChange={(e) => setRenameValue(e.currentTarget.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && renaming && renameValue.trim()) {
                rename.mutate({ entryPath: renaming.path, name: renameValue.trim() });
              }
            }}
          />
          <Group justify="flex-end">
            <Button size="compact-sm" variant="default" onClick={() => setRenaming(null)}>
              取消
            </Button>
            <Button
              size="compact-sm"
              loading={rename.isPending}
              disabled={!renameValue.trim() || renameValue === renaming?.name}
              onClick={() => renaming && rename.mutate({ entryPath: renaming.path, name: renameValue.trim() })}
            >
              保存
            </Button>
          </Group>
        </Stack>
      </Modal>

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
            <Button color="steelblue" loading={saving} onClick={save}>
              保存
            </Button>
          </Group>
        </Stack>
      </Modal>
    </Stack>
  );
}
