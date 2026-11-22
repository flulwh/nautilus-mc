import { Badge, Box, Button, Card, Code, Group, Pagination, Select, Stack, Table, Text, TextInput } from "@mantine/core";
import { IconActivity, IconDownload } from "@tabler/icons-react";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "../api";
import { SkeletonRows } from "../components/AsyncState";
import type { AuditEntry } from "../types";

const ACTION_COLORS: Record<string, string> = {
  server: "steelblue",
  auth: "teal",
  user: "grape",
  config: "orange",
  file: "gray",
  plugin: "cyan",
};

export function AuditPage() {
  const [page, setPage] = useState(1);
  const [action, setAction] = useState("");
  const [actor, setActor] = useState("");
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const limit = 30;

  const audit = useQuery<{ total: number; entries: AuditEntry[] }>({
    queryKey: ["audit", page, action, actor],
    queryFn: () =>
      api.get(`/audit?limit=${limit}&offset=${(page - 1) * limit}&action=${encodeURIComponent(action)}&actor=${encodeURIComponent(actor)}`),
  });

  const toggleDetail = (id: number) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const exportCsv = () => {
    const rows = audit.data?.entries ?? [];
    const cell = (v: unknown) => `"${String(v ?? "").replace(/"/g, '""')}"`;
    const lines = [
      ["时间", "操作者", "动作", "目标", "来源IP", "结果", "详情"]
        .map(cell)
        .join(","),
      ...rows.map((e) =>
        [
          e.ts ? new Date(e.ts).toLocaleString("zh-CN") : "",
          e.actor,
          e.action,
          e.target,
          e.ip,
          e.ok ? "成功" : "失败",
          JSON.stringify(e.detail ?? {}),
        ]
          .map(cell)
          .join(","),
      ),
    ];
    const blob = new Blob(["\ufeff" + lines.join("\r\n")], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `nautilus-audit-page${page}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <Stack gap="sm">
      <Group justify="space-between" align="flex-end">
        <Box>
          <Text fz={16} fw={600}>
            审计日志
          </Text>
          <Text fz={11} c="dimmed" mt={2}>
            所有写操作都会落库：谁、何时、从哪个 IP、对谁做了什么。该表只追加，不提供修改接口。
          </Text>
        </Box>
        <Group gap="xs">
          <Button
            size="xs"
            variant="default"
            leftSection={<IconDownload size={13} />}
            onClick={exportCsv}
            disabled={!audit.data?.entries.length}
          >
            导出本页 CSV
          </Button>
          <TextInput
            size="xs"
            placeholder="操作者"
            value={actor}
            onChange={(e) => {
              setActor(e.currentTarget.value);
              setPage(1);
            }}
          />
          <Select
            size="xs"
            placeholder="操作类型"
            clearable
            value={action || null}
            onChange={(v) => {
              setAction(v || "");
              setPage(1);
            }}
            data={["server.", "auth.", "user.", "config.", "file.", "plugin."].map((v) => ({ value: v, label: v }))}
          />
        </Group>
      </Group>

      <Card className="glass" p="md" withBorder>
        {audit.isLoading ? (
          <SkeletonRows rows={8} />
        ) : (
        <Table verticalSpacing="xs" className="stagger-rows-alt">
          <Table.Thead>
            <Table.Tr>
              <Table.Th w={150}>时间</Table.Th>
              <Table.Th w={120}>操作者</Table.Th>
              <Table.Th w={170}>动作</Table.Th>
              <Table.Th>目标</Table.Th>
              <Table.Th w={130}>来源 IP</Table.Th>
              <Table.Th w={70}>结果</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {audit.data?.entries.map((e) => (
              <Table.Tr key={e.id}>
                <Table.Td>
                  <Text size="10.5px" c="dimmed" className="tabular">
                    {e.ts ? new Date(e.ts).toLocaleString("zh-CN") : "—"}
                  </Text>
                </Table.Td>
                <Table.Td>
                  <Text size="11px">{e.actor}</Text>
                </Table.Td>
                <Table.Td>
                  <Badge size="xs" variant="light" color={ACTION_COLORS[e.action.split(".")[0]] ?? "gray"}>
                    {e.action}
                  </Badge>
                </Table.Td>
                <Table.Td>
                  <Text
                    size="11px"
                    className="mono"
                    c={expanded.has(e.id) ? "steelblue" : "dimmed"}
                    truncate
                    style={{ cursor: e.detail && Object.keys(e.detail).length ? "pointer" : undefined }}
                    onClick={() => toggleDetail(e.id)}
                  >
                    {e.target || "—"}
                    {e.detail && Object.keys(e.detail).length > 0 && (expanded.has(e.id) ? " 收起" : " 详情")}
                  </Text>
                  {expanded.has(e.id) && e.detail && Object.keys(e.detail).length > 0 && (
                    <Code
                      block
                      fz={10}
                      mt={4}
                      style={{ background: "var(--panel-2)", padding: 6, whiteSpace: "pre-wrap", wordBreak: "break-all", maxHeight: 220, overflow: "auto" }}
                    >
                      {JSON.stringify(e.detail, null, 2)}
                    </Code>
                  )}
                </Table.Td>
                <Table.Td>
                  <Text size="10.5px" c="dimmed" className="mono">
                    {e.ip || "—"}
                  </Text>
                </Table.Td>
                <Table.Td>
                  <Text size="10.5px" c={e.ok ? "teal.3" : "red.4"}>
                    {e.ok ? "成功" : "失败"}
                  </Text>
                </Table.Td>
              </Table.Tr>
            ))}
            {!audit.data?.entries.length && (
              <Table.Tr>
                <Table.Td colSpan={6}>
                  <Stack align="center" py="lg" gap={6}>
                    <IconActivity size={22} color="#3d5568" />
                    <Text size="xs" c="dimmed">
                      暂无记录
                    </Text>
                  </Stack>
                </Table.Td>
              </Table.Tr>
            )}
          </Table.Tbody>
        </Table>
        )}

        <Group justify="space-between" mt="md">
          <Text size="10.5px" c="dimmed">
            共 {audit.data?.total ?? 0} 条
          </Text>
          <Pagination
            size="xs"
            total={Math.max(1, Math.ceil((audit.data?.total ?? 0) / limit))}
            value={page}
            onChange={setPage}
            color="steelblue"
          />
        </Group>
      </Card>
    </Stack>
  );
}
