import {
  ActionIcon,
  Badge,
  Box,
  Button,
  Card,
  Grid,
  Group,
  Modal,
  ScrollArea,
  SegmentedControl,
  Stack,
  Switch,
  Table,
  Text,
  TextInput,
  Tooltip,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconDownload, IconSearch, IconTrash, IconVersions } from "@tabler/icons-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { api, ApiError } from "../../api";
import { Skeleton } from "../../components/AsyncState";
import { useConfirm } from "../../components/Confirm";
import type { MarketItem, PluginItem } from "../../types";

interface VersionRow {
  id: string;
  name: string;
  channel: string;
  downloads: number;
  date: string;
  filename: string;
  installable: boolean;
}

function human(n: number): string {
  if (n > 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n > 1000) return `${(n / 1000).toFixed(1)}k`;
  return String(n);
}

export function PluginsTab({ serverId }: { serverId: number }) {
  const qc = useQueryClient();
  const confirm = useConfirm();
  const [source, setSource] = useState("modrinth");
  const [query, setQuery] = useState("essentials");
  const [submitted, setSubmitted] = useState("essentials");
  const [offset, setOffset] = useState(0);
  const [checkUpdates, setCheckUpdates] = useState(false);
  const [picking, setPicking] = useState<MarketItem | null>(null);
  const PAGE = 20;

  const installed = useQuery<{ installed: PluginItem[]; folder: string; kind: string }>({
    queryKey: ["plugins", serverId],
    queryFn: () => api.get(`/servers/${serverId}/plugins`),
  });

  /** Vanilla has no plugin loader — only datapacks. Keep the wording honest. */
  const kind = installed.data?.kind ?? "";
  const noun = kind === "fabric" || kind === "forge" ? "模组" : kind === "vanilla" ? "数据包" : "插件";
  const allowsHangar = kind !== "vanilla" && kind !== "bedrock";

  useEffect(() => {
    if (!allowsHangar && source === "hangar") setSource("modrinth");
  }, [allowsHangar, source]);

  // 搜索翻页：offset 变化时重新取一页；换关键词/来源时重置到第一页。
  useEffect(() => setOffset(0), [submitted, source]);

  const search = useQuery<{ results: MarketItem[] }>({
    queryKey: ["plugin-search", serverId, source, submitted, offset],
    queryFn: () =>
      api.get(
        `/servers/${serverId}/plugins/search?q=${encodeURIComponent(submitted)}&source=${source}&limit=${PAGE}&offset=${offset}`,
      ),
    enabled: submitted.length > 0,
    retry: false,
  });

  const updates = useQuery<{ updates: { filename: string; installed: string; latest: string; project: string }[] }>({
    queryKey: ["plugin-updates", serverId],
    queryFn: () => api.get(`/servers/${serverId}/plugins/updates`),
    enabled: checkUpdates,
    retry: false,
  });
  const updatable = new Set((updates.data?.updates ?? []).map((u) => u.filename));

  const versions = useQuery<{ versions: VersionRow[] }>({
    queryKey: ["plugin-versions", serverId, picking?.source, picking?.id],
    queryFn: () => api.get(`/servers/${serverId}/plugins/${picking?.source}/${picking?.id}/versions`),
    enabled: !!picking,
    retry: false,
  });

  const install = useMutation({
    mutationFn: (v: { project: string; source: string; version_id: string }) =>
      api.post(`/servers/${serverId}/plugins/install`, v),
    onSuccess: (res: unknown) => {
      const r = res as { filename: string };
      notifications.show({ color: "teal", title: "安装完成", message: r.filename });
      setPicking(null);
      qc.invalidateQueries({ queryKey: ["plugins", serverId] });
    },
    onError: (e) => notifications.show({ color: "red", title: "安装失败", message: (e as ApiError).detail }),
  });

  const toggle = useMutation({
    mutationFn: (v: { filename: string; enabled: boolean }) =>
      api.post(`/servers/${serverId}/plugins/toggle`, v),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["plugins", serverId] }),
    onError: (e) => notifications.show({ color: "red", message: (e as ApiError).detail }),
  });

  const remove = useMutation({
    mutationFn: (filename: string) =>
      api.del(`/servers/${serverId}/plugins?filename=${encodeURIComponent(filename)}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["plugins", serverId] }),
    onError: (e) => notifications.show({ color: "red", message: (e as ApiError).detail }),
  });

  return (
    <Grid gutter="md">
      <Grid.Col span={{ base: 12, lg: 5 }}>
        <Card className="glass" p="md" withBorder>
          <Group justify="space-between" mb="sm">
            <Box>
              <Text className="stat-label">已安装</Text>
              <Text size="sm" fw={550} mt={2}>
                {installed.data?.folder ?? "plugins"} 目录 · {installed.data?.installed.length ?? 0} 项
              </Text>
            </Box>
            <Group gap={6}>
              {updatable.size > 0 && (
                <Badge variant="light" color="orange" size="xs">
                  {updatable.size} 个可更新
                </Badge>
              )}
              <Tooltip label="对照 Modrinth 检查已装插件是否有新版本">
                <Button
                  size="compact-xs"
                  variant="default"
                  leftSection={<IconVersions size={12} />}
                  loading={updates.isFetching}
                  onClick={() => {
                    setCheckUpdates(true);
                    void updates.refetch();
                  }}
                >
                  检查更新
                </Button>
              </Tooltip>
              <Badge variant="light" color="steelblue" size="xs">
                {installed.data?.kind}
              </Badge>
            </Group>
          </Group>
          <ScrollArea h="calc(100vh - 294px)" type="hover" scrollbars="y" offsetScrollbars>
            <Table verticalSpacing={6}>
              <Table.Tbody>
                {installed.data?.installed.map((p) => (
                  <Table.Tr key={p.filename}>
                    <Table.Td>
                      <Group gap={8} wrap="nowrap">
                        <Switch
                          size="xs"
                          checked={p.enabled}
                          onChange={(e) => toggle.mutate({ filename: p.filename, enabled: e.currentTarget.checked })}
                        />
                        <Box style={{ minWidth: 0 }}>
                          <Text size="xs" fw={520} truncate>
                            {p.name || p.filename}
                          </Text>
                          <Group gap={4} wrap="nowrap">
                            <Text size="10px" c="dimmed" className="mono" truncate>
                              {p.version ? `v${p.version}` : p.filename}
                              {p.api ? ` · api ${p.api}` : ""}
                            </Text>
                            {updatable.has(p.filename) && (
                              <Tooltip
                                label={`可更新到 ${updates.data?.updates.find((u) => u.filename === p.filename)?.latest ?? "新版"}`}
                              >
                                <Badge size="xs" color="orange" variant="light">
                                  新版本
                                </Badge>
                              </Tooltip>
                            )}
                          </Group>
                        </Box>
                      </Group>
                    </Table.Td>
                    <Table.Td w={34}>
                      <Tooltip label="删除">
                        <ActionIcon
                          size="sm"
                          variant="subtle"
                          color="red"
                          onClick={async () => {
                            if (await confirm({ title: "删除插件", message: `删除 ${p.filename}？重启服务器后生效。`, danger: true })) {
                              remove.mutate(p.filename);
                            }
                          }}
                        >
                          <IconTrash size={13} />
                        </ActionIcon>
                      </Tooltip>
                    </Table.Td>
                  </Table.Tr>
                ))}
                {!installed.data?.installed.length && (
                  <Table.Tr>
                    <Table.Td>
                      <Text size="xs" c="dimmed" py="md">
                        还没有{noun}。从右侧市场安装，或把文件拖到「文件」页上传。
                      </Text>
                    </Table.Td>
                  </Table.Tr>
                )}
              </Table.Tbody>
            </Table>
          </ScrollArea>
        </Card>
      </Grid.Col>

      <Grid.Col span={{ base: 12, lg: 7 }}>
        <Card className="glass" p="md" withBorder>
          <Group justify="space-between" mb="sm" wrap="nowrap">
            <TextInput
              style={{ flex: 1 }}
              size="xs"
              placeholder={`搜索${noun}…`}
              leftSection={<IconSearch size={13} />}
              value={query}
              onChange={(e) => setQuery(e.currentTarget.value)}
              onKeyDown={(e) => e.key === "Enter" && setSubmitted(query.trim())}
            />
            <SegmentedControl
              size="xs"
              value={source}
              onChange={setSource}
              data={
                allowsHangar
                  ? [
                      { value: "modrinth", label: "Modrinth" },
                      { value: "hangar", label: "Hangar" },
                    ]
                  : [{ value: "modrinth", label: "Modrinth" }]
              }
            />
          </Group>

          {search.isLoading && (
            <Stack gap={0} py="sm" aria-busy="true">
              {Array.from({ length: 4 }, (_, i) => (
                <div key={i} style={{ padding: "9px 4px" }}>
                  <Skeleton h={12} w={`${70 - i * 8}%`} />
                  <Skeleton h={9} w="40%" />
                </div>
              ))}
            </Stack>
          )}
          {search.isError && (
            <Text size="xs" c="red.4" py="md">
              插件源暂不可用：{(search.error as ApiError)?.detail}
            </Text>
          )}

          <ScrollArea h="calc(100vh - 294px)" type="hover" scrollbars="y" offsetScrollbars>
            <Stack gap={6} pe={6}>
              {search.data?.results.map((item) => (
                <Box
                  key={`${item.source}-${item.id}`}
                  p="sm"
                  style={{ border: "1px solid var(--line)", borderRadius: 2 }}
                >
                  <Group justify="space-between" wrap="nowrap" align="flex-start">
                    <Box style={{ minWidth: 0 }}>
                      <Group gap={8} mb={3}>
                        <Text size="xs" fw={600}>
                          {item.name}
                        </Text>
                        <Badge size="xs" variant="light" color="gray">
                          {human(item.downloads)} 下载
                        </Badge>
                      </Group>
                      <Text size="10.5px" c="dimmed" lineClamp={2}>
                        {item.description}
                      </Text>
                    </Box>
                    <Button
                      size="compact-xs"
                      variant="light"
                      color="steelblue"
                      leftSection={<IconVersions size={12} />}
                      onClick={() => setPicking(item)}
                    >
                      安装
                    </Button>
                  </Group>
                </Box>
              ))}
              {search.data && !search.data.results.length && (
                <Text size="xs" c="dimmed" py="md">
                  {search.error
                    ? (search.error as ApiError)?.detail
                    : kind === "vanilla"
                      ? "没有结果。Vanilla 服务端只能通过数据包扩展，试试搜索「terralith」「incendium」这类数据包。"
                      : "没有结果，换个关键词试试。"}
                </Text>
              )}
              {submitted && (
                <Group justify="space-between" wrap="nowrap">
                  <Text fz={10} c="dimmed" className="mono">
                    第 {Math.floor(offset / PAGE) + 1} 页
                  </Text>
                  <Group gap={4}>
                    <Button
                      size="compact-xs"
                      variant="default"
                      disabled={offset === 0 || search.isFetching}
                      onClick={() => setOffset((o) => Math.max(0, o - PAGE))}
                    >
                      上一页
                    </Button>
                    <Button
                      size="compact-xs"
                      variant="default"
                      disabled={(search.data?.results.length ?? 0) < PAGE || search.isFetching}
                      onClick={() => setOffset((o) => o + PAGE)}
                    >
                      下一页
                    </Button>
                  </Group>
                </Group>
              )}
            </Stack>
          </ScrollArea>
        </Card>
      </Grid.Col>

      <Modal opened={!!picking} onClose={() => setPicking(null)} title={`安装 ${picking?.name ?? ""}`} size="lg">
        {versions.isLoading ? (
          <Stack gap={0} py="sm" aria-busy="true">
            {Array.from({ length: 5 }, (_, i) => (
              <div key={i} style={{ padding: "8px 4px" }}>
                <Skeleton h={12} w={`${60 + (i % 3) * 12}%`} />
              </div>
            ))}
          </Stack>
        ) : (
          <Stack gap="xs">
            <Text size="xs" c="dimmed">
              版本已按当前服务端类型（{installed.data?.kind}）与游戏版本过滤；下载后会校验哈希。
            </Text>
            <ScrollArea h={320} type="auto">
              <Table verticalSpacing={4}>
                <Table.Tbody>
                  {versions.data?.versions.map((v) => (
                    <Table.Tr key={v.id}>
                      <Table.Td>
                        <Text size="xs" className="mono">
                          {v.name}
                        </Text>
                        <Text size="10px" c="dimmed">
                          {v.filename || "—"} · {v.channel}
                        </Text>
                      </Table.Td>
                      <Table.Td w={90}>
                        <Button
                          size="compact-xs"
                          variant="light"
                          color="steelblue"
                          leftSection={<IconDownload size={12} />}
                          disabled={!v.installable}
                          loading={install.isPending}
                          onClick={() =>
                            install.mutate({
                              project: picking?.id ?? "",
                              source: picking?.source ?? "modrinth",
                              version_id: v.id,
                            })
                          }
                        >
                          安装
                        </Button>
                      </Table.Td>
                    </Table.Tr>
                  ))}
                  {!versions.data?.versions.length && (
                    <Table.Tr>
                      <Table.Td>
                        <Text size="xs" c="dimmed" py="sm">
                          没有适配当前服务端的版本
                        </Text>
                      </Table.Td>
                    </Table.Tr>
                  )}
                </Table.Tbody>
              </Table>
            </ScrollArea>
          </Stack>
        )}
      </Modal>
    </Grid>
  );
}
