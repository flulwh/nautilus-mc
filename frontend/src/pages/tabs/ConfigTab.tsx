import {
  ActionIcon,
  Badge,
  Box,
  Button,
  Card,
  Grid,
  Group,
  Loader,
  ScrollArea,
  Select,
  Stack,
  Switch,
  Text,
  TextInput,
  Tooltip,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconDeviceFloppy, IconHistory, IconRefresh } from "@tabler/icons-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";

import { api, ApiError } from "../../api";

interface Doc {
  format: string;
  data: Record<string, unknown>;
  types?: Record<string, string>;
  path: string;
}

const FORMAT_LABEL: Record<string, string> = {
  properties: "properties",
  yaml: "YAML",
  toml: "TOML",
  json: "JSON",
  text: "文本",
};

export function ConfigTab({ serverId }: { serverId: number }) {
  const qc = useQueryClient();
  const [selected, setSelected] = useState<string>("");
  const [filter, setFilter] = useState("");
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [raw, setRaw] = useState("");
  const [mode, setMode] = useState<"form" | "source">("form");

  const files = useQuery<{ files: string[] }>({
    queryKey: ["config-files", serverId],
    queryFn: () => api.get(`/servers/${serverId}/config/files`),
  });

  useEffect(() => {
    if (!selected && files.data?.files.length) setSelected(files.data.files[0]);
  }, [files.data, selected]);

  const doc = useQuery<Doc>({
    queryKey: ["config", serverId, selected],
    queryFn: () => api.get(`/servers/${serverId}/config/${selected}`),
    enabled: !!selected,
  });

  const rawText = useQuery<{ content: string }>({
    queryKey: ["file-raw", serverId, selected],
    queryFn: () => api.get(`/servers/${serverId}/files/content?path=${encodeURIComponent(selected)}`),
    enabled: !!selected && mode === "source",
  });

  useEffect(() => {
    if (doc.data) {
      const next: Record<string, string> = {};
      for (const [k, v] of Object.entries(doc.data.data ?? {})) {
        next[k] = typeof v === "object" && v !== null ? JSON.stringify(v, null, 2) : String(v ?? "");
      }
      setDraft(next);
      // The structured form only knows how to round-trip flat scalars. YAML /
      // TOML / JSON configs (paper-global.yml, mods.toml...) contain nested
      // maps; editing them through the form used to write JSON strings where
      // maps used to be, which the server then refused to parse. Those formats
      // are edited as source, where the backend validates the syntax.
      setMode(doc.data.format === "properties" ? "form" : "source");
    }
  }, [doc.data]);

  useEffect(() => {
    if (rawText.data) setRaw(rawText.data.content);
  }, [rawText.data]);

  const backups = useQuery<{ backups: { name: string; size: number; mtime: number }[] }>({
    queryKey: ["backups", serverId, selected],
    queryFn: () => api.get(`/servers/${serverId}/config/${selected}/backups`),
    enabled: !!selected,
  });

  const saveForm = useMutation({
    mutationFn: () => {
      const payload: Record<string, unknown> = {};
      for (const [k, v] of Object.entries(draft)) {
        const type = doc.data?.types?.[k];
        if (type === "int") payload[k] = Number(v) || 0;
        else if (type === "bool") payload[k] = v === "true";
        else payload[k] = v;
      }
      return api.put(`/servers/${serverId}/config/${selected}`, { data: payload });
    },
    onSuccess: () => {
      notifications.show({ color: "teal", message: "配置已保存（已生成备份）" });
      qc.invalidateQueries({ queryKey: ["config", serverId, selected] });
      qc.invalidateQueries({ queryKey: ["backups", serverId, selected] });
    },
    onError: (e) => notifications.show({ color: "red", title: "保存失败", message: (e as ApiError).detail }),
  });

  const saveRaw = useMutation({
    mutationFn: () => api.put(`/servers/${serverId}/config/${selected}/raw`, { content: raw }),
    onSuccess: () => {
      notifications.show({ color: "teal", message: "已写入并通过语法校验" });
      qc.invalidateQueries({ queryKey: ["config", serverId, selected] });
    },
    onError: (e) => notifications.show({ color: "red", title: "校验未通过", message: (e as ApiError).detail }),
  });

  const restore = useMutation({
    mutationFn: (name: string) =>
      api.post(`/servers/${serverId}/config/${selected}/restore?backup=${encodeURIComponent(name)}`),
    onSuccess: () => {
      notifications.show({ color: "teal", message: "已从备份恢复" });
      qc.invalidateQueries({ queryKey: ["config", serverId, selected] });
    },
    onError: (e) => notifications.show({ color: "red", message: (e as ApiError).detail }),
  });

  const keys = useMemo(() => {
    const all = Object.keys(draft);
    if (!filter.trim()) return all;
    const q = filter.toLowerCase();
    return all.filter((k) => k.toLowerCase().includes(q) || String(draft[k]).toLowerCase().includes(q));
  }, [draft, filter]);

  // Only .properties has flat, typed values the form can write back safely.
  const supportsForm = doc.data?.format === "properties";

  return (
    <Grid gutter="md">
      <Grid.Col span={{ base: 12, md: 3 }}>
        <Card className="glass" p="sm" withBorder>
          <Group justify="space-between" mb={8}>
            <Text className="stat-label">配置文件</Text>
            <ActionIcon size="xs" variant="subtle" color="gray" onClick={() => files.refetch()}>
              <IconRefresh size={12} />
            </ActionIcon>
          </Group>
          <ScrollArea h="calc(100vh - 380px)" type="auto">
            <Stack gap={2}>
              {files.data?.files.map((f) => (
                <Box
                  key={f}
                  onClick={() => setSelected(f)}
                  px={9}
                  py={6}
                  style={{
                    borderRadius: 8,
                    cursor: "pointer",
                    background: selected === f ? "rgba(53,198,225,0.12)" : "transparent",
                  }}
                >
                  <Text size="11.5px" className="mono" c={selected === f ? "steelblue.2" : "dimmed"} truncate>
                    {f}
                  </Text>
                </Box>
              ))}
              {!files.data?.files.length && (
                <Text size="10.5px" c="dimmed" p="xs">
                  未发现配置文件。启动一次服务端后会自动生成。
                </Text>
              )}
            </Stack>
          </ScrollArea>
        </Card>

        <Card className="glass" p="sm" mt="sm" withBorder>
          <Group gap={6} mb={8}>
            <IconHistory size={13} color="#6b8095" />
            <Text className="stat-label">备份</Text>
            <Badge size="xs" variant="light" color="gray">
              {backups.data?.backups.length ?? 0}
            </Badge>
          </Group>
          <Stack gap={4}>
            {backups.data?.backups.slice(0, 6).map((b) => (
              <Group key={b.name} justify="space-between" gap={4}>
                <Text size="9.5px" c="dimmed" className="mono" truncate style={{ maxWidth: 130 }}>
                  {b.name.replace(/\..*$/, "")}
                </Text>
                <Tooltip label="恢复此备份">
                  <ActionIcon size="xs" variant="subtle" color="orange" onClick={() => restore.mutate(b.name)}>
                    <IconHistory size={12} />
                  </ActionIcon>
                </Tooltip>
              </Group>
            ))}
            {!backups.data?.backups.length && (
              <Text size="10.5px" c="dimmed">
                保存一次后自动生成
              </Text>
            )}
          </Stack>
        </Card>
      </Grid.Col>

      <Grid.Col span={{ base: 12, md: 9 }}>
        <Card className="glass" p="md" withBorder>
          {doc.isLoading ? (
            <Group justify="center" py="xl">
              <Loader size="sm" color="steelblue" />
            </Group>
          ) : (
            <Stack gap="sm">
              <Group justify="space-between">
                <Group gap="sm">
                  <Text fw={550} className="mono" size="sm">
                    {selected || "选择文件"}
                  </Text>
                  {doc.data && (
                    <Badge size="xs" variant="light" color="steelblue">
                      {FORMAT_LABEL[doc.data.format] ?? doc.data.format}
                    </Badge>
                  )}
                </Group>
                <Group gap={6}>
                  <Button
                    size="xs"
                    variant={mode === "form" ? "light" : "subtle"}
                    color="gray"
                    disabled={!supportsForm}
                    title={supportsForm ? undefined : "结构化编辑仅支持 .properties，嵌套格式请用源码模式"}
                    onClick={() => setMode("form")}
                  >
                    结构化
                  </Button>
                  <Button size="xs" variant={mode === "source" ? "light" : "subtle"} color="gray" onClick={() => setMode("source")}>
                    源码
                  </Button>
                  <Button
                    size="xs"
                    color="steelblue"
                    leftSection={<IconDeviceFloppy size={14} />}
                    loading={saveForm.isPending || saveRaw.isPending}
                    onClick={() => (mode === "form" && supportsForm ? saveForm.mutate() : saveRaw.mutate())}
                    disabled={!selected}
                  >
                    保存
                  </Button>
                </Group>
              </Group>

              {mode === "form" && supportsForm ? (
                <>
                  <TextInput
                    size="xs"
                    placeholder="筛选键名或值…"
                    value={filter}
                    onChange={(e) => setFilter(e.currentTarget.value)}
                  />
                  <ScrollArea h="calc(100vh - 400px)" type="auto">
                    <Stack gap={6} pr="sm">
                      {keys.map((k) => {
                        const type = doc.data?.types?.[k];
                        const enumValues = Array.isArray(type) ? type : null;
                        const value = draft[k] ?? "";
                        return (
                          <Group key={k} gap="sm" align="center" wrap="nowrap">
                            <Text size="11px" className="mono" c="dimmed" w={220} truncate title={k}>
                              {k}
                            </Text>
                            {enumValues ? (
                              <Select
                                size="xs"
                                style={{ flex: 1 }}
                                data={enumValues as unknown as string[]}
                                value={value}
                                onChange={(v) => setDraft((d) => ({ ...d, [k]: v ?? "" }))}
                              />
                            ) : type === "bool" ? (
                              <Switch
                                size="xs"
                                checked={value === "true"}
                                onChange={(e) => setDraft((d) => ({ ...d, [k]: e.currentTarget.checked ? "true" : "false" }))}
                              />
                            ) : value.includes("\n") ? (
                              <textarea
                                className="code-editor"
                                style={{ minHeight: 90, flex: 1, fontSize: 11.5 }}
                                value={value}
                                onChange={(e) => setDraft((d) => ({ ...d, [k]: e.currentTarget.value }))}
                              />
                            ) : (
                              <TextInput
                                size="xs"
                                style={{ flex: 1 }}
                                value={value}
                                onChange={(e) => setDraft((d) => ({ ...d, [k]: e.currentTarget.value }))}
                                type={type === "int" ? "number" : "text"}
                              />
                            )}
                          </Group>
                        );
                      })}
                      {!keys.length && (
                        <Text size="xs" c="dimmed" py="md">
                          没有匹配的键
                        </Text>
                      )}
                    </Stack>
                  </ScrollArea>
                </>
              ) : (
                <textarea
                  className="code-editor"
                  style={{ minHeight: "calc(100vh - 380px)" }}
                  value={raw}
                  onChange={(e) => setRaw(e.currentTarget.value)}
                  spellCheck={false}
                />
              )}
            </Stack>
          )}
        </Card>
      </Grid.Col>
    </Grid>
  );
}
