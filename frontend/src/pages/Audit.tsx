import { Badge, Box, Card, Group, Pagination, Select, Stack, Table, Text, TextInput, Title } from "@mantine/core";
import { IconActivity } from "@tabler/icons-react";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "../api";
import type { AuditEntry } from "../types";

const ACTION_COLORS: Record<string, string> = {
  server: "abyss",
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
  const limit = 30;

  const audit = useQuery<{ total: number; entries: AuditEntry[] }>({
    queryKey: ["audit", page, action, actor],
    queryFn: () =>
      api.get(`/audit?limit=${limit}&offset=${(page - 1) * limit}&action=${encodeURIComponent(action)}&actor=${encodeURIComponent(actor)}`),
  });

  return (
    <Stack gap="lg">
      <Group justify="space-between" align="flex-end">
        <Box>
          <Text className="stat-label">Audit trail</Text>
          <Title order={2} mt={2}>
            审计日志
          </Title>
          <Text size="11.5px" c="dimmed" mt={4}>
            所有写操作都会落库：谁、何时、从哪个 IP、对谁做了什么。该表只追加，不提供修改接口。
          </Text>
        </Box>
        <Group gap="xs">
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
        <Table verticalSpacing="xs">
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
                  <Text size="11px" className="mono" c="dimmed" truncate>
                    {e.target || "—"}
                  </Text>
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

        <Group justify="space-between" mt="md">
          <Text size="10.5px" c="dimmed">
            共 {audit.data?.total ?? 0} 条
          </Text>
          <Pagination
            size="xs"
            total={Math.max(1, Math.ceil((audit.data?.total ?? 0) / limit))}
            value={page}
            onChange={setPage}
            color="abyss"
          />
        </Group>
      </Card>
    </Stack>
  );
}
