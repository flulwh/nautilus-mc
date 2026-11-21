import {
  Badge,
  Button,
  Card,
  Grid,
  Group,
  NumberInput,
  Select,
  Stack,
  Switch,
  Table,
  Text,
  TextInput,
  Title,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconPlus, IconTrash } from "@tabler/icons-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { api, ApiError } from "../../api";

interface World {
  name: string;
  size: number;
  modified: number;
  active: boolean;
  pending: boolean;
  has_nether: boolean;
  has_end: boolean;
}

interface Field {
  type: "text" | "int" | "bool" | "enum";
  label: string;
  options?: string[];
  min?: number;
  max?: number;
}

interface WorldPayload {
  worlds: World[];
  settings: Record<string, string | number | boolean>;
  fields: Record<string, Field>;
  running: boolean;
}

const mb = (bytes: number) => (bytes >= 1073741824 ? `${(bytes / 1073741824).toFixed(2)} GB` : `${Math.max(1, Math.round(bytes / 1048576))} MB`);

export function WorldsTab({ serverId }: { serverId: number }) {
  const qc = useQueryClient();
  const key = ["worlds", String(serverId)];
  const [values, setValues] = useState<Record<string, string | number | boolean>>({});
  const [draft, setDraft] = useState({ name: "", seed: "", level_type: "minecraft:normal" });

  const data = useQuery<WorldPayload>({
    queryKey: key,
    queryFn: () => api.get<WorldPayload>(`/servers/${serverId}/worlds`),
    refetchInterval: 15000,
  });

  useEffect(() => {
    if (data.data) setValues(data.data.settings);
  }, [data.data]);

  const refresh = () => {
    qc.invalidateQueries({ queryKey: key });
    qc.invalidateQueries({ queryKey: ["server", String(serverId)] });
  };
  const fail = (e: unknown) => notifications.show({ color: "red", message: e instanceof ApiError ? e.detail : "操作失败" });

  const save = useMutation({
    mutationFn: () => api.put(`/servers/${serverId}/worlds/settings`, values),
    onSuccess: () => {
      notifications.show({ color: "teal", message: "已写入 server.properties，重启后生效" });
      refresh();
    },
    onError: fail,
  });

  const create = useMutation({
    mutationFn: () => api.post(`/servers/${serverId}/worlds`, draft),
    onSuccess: () => {
      notifications.show({ color: "teal", message: "已切换世界，下次启动时生成" });
      setDraft({ name: "", seed: "", level_type: "minecraft:normal" });
      refresh();
    },
    onError: fail,
  });

  const activate = useMutation({
    mutationFn: (name: string) => api.post(`/servers/${serverId}/worlds/${encodeURIComponent(name)}/activate`),
    onSuccess: refresh,
    onError: fail,
  });

  const remove = useMutation({
    mutationFn: (name: string) => api.del(`/servers/${serverId}/worlds/${encodeURIComponent(name)}`),
    onSuccess: refresh,
    onError: fail,
  });

  const fields = data.data?.fields ?? {};
  const running = data.data?.running ?? false;
  const worlds = data.data?.worlds ?? [];

  const renderField = (name: string, meta: Field) => {
    const value = values[name];
    if (meta.type === "bool") {
      return (
        <Group key={name} justify="space-between" wrap="nowrap">
          <Text fz={12}>{meta.label}</Text>
          <Switch
            size="xs"
            color="moss"
            checked={Boolean(value)}
            disabled={running}
            onChange={(e) => setValues((v) => ({ ...v, [name]: e.currentTarget.checked }))}
          />
        </Group>
      );
    }
    if (meta.type === "enum") {
      return (
        <Select
          key={name}
          size="xs"
          label={meta.label}
          data={meta.options ?? []}
          value={String(value ?? "")}
          disabled={running}
          allowDeselect={false}
          onChange={(v) => setValues((old) => ({ ...old, [name]: v ?? "" }))}
        />
      );
    }
    if (meta.type === "int") {
      return (
        <NumberInput
          key={name}
          size="xs"
          label={meta.label}
          value={Number(value ?? 0)}
          min={meta.min}
          max={meta.max}
          disabled={running}
          onChange={(v) => setValues((old) => ({ ...old, [name]: Number(v) || 0 }))}
        />
      );
    }
    return (
      <TextInput
        key={name}
        size="xs"
        label={meta.label}
        value={String(value ?? "")}
        disabled={running}
        onChange={(e) => setValues((old) => ({ ...old, [name]: e.currentTarget.value }))}
      />
    );
  };

  return (
    <Grid gutter="md">
      <Grid.Col span={{ base: 12, md: 7 }}>
        <Card className="glass" p="md" withBorder>
          <Group justify="space-between" mb={8}>
            <Title order={3}>世界</Title>
            {running && (
              <Text fz={11} c="dimmed">
                运行中，操作已禁用
              </Text>
            )}
          </Group>
          <Table verticalSpacing="xs" highlightOnHover>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>名称</Table.Th>
                <Table.Th>大小</Table.Th>
                <Table.Th>维度</Table.Th>
                <Table.Th />
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {worlds.map((w) => (
                <Table.Tr key={w.name}>
                  <Table.Td>
                    <Group gap={6} wrap="nowrap">
                      <span className="mono" style={{ fontSize: 12 }}>
                        {w.name}
                      </span>
                      {w.active && (
                        <Badge size="xs" variant="default">
                          使用中
                        </Badge>
                      )}
                      {w.pending && (
                        <Text fz={10.5} c="dimmed">
                          待生成
                        </Text>
                      )}
                    </Group>
                  </Table.Td>
                  <Table.Td>
                    <Text fz={11.5} c="dimmed" className="mono">
                      {w.pending ? "—" : mb(w.size)}
                    </Text>
                  </Table.Td>
                  <Table.Td>
                    <Text fz={11} c="dimmed">
                      {[w.has_nether && "下界", w.has_end && "末地"].filter(Boolean).join(" / ") || "主世界"}
                    </Text>
                  </Table.Td>
                  <Table.Td>
                    <Group gap={4} justify="flex-end" wrap="nowrap">
                      {!w.active && !w.pending && (
                        <Button
                          size="compact-xs"
                          variant="default"
                          disabled={running}
                          loading={activate.isPending}
                          onClick={() => activate.mutate(w.name)}
                        >
                          切换
                        </Button>
                      )}
                      {!w.active && (
                        <Button
                          size="compact-xs"
                          variant="subtle"
                          color="red"
                          disabled={running}
                          leftSection={<IconTrash size={11} />}
                          onClick={() => {
                            if (!window.confirm(`把世界 ${w.name} 移到 .nautilus/world-trash/？`)) return;
                            remove.mutate(w.name);
                          }}
                        >
                          删除
                        </Button>
                      )}
                    </Group>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
          <Text fz={10.5} c="dimmed" mt={8}>
            删除是移动到回收目录，不是永久删除；当前使用的世界不能删。
          </Text>
        </Card>

        <Card className="glass" p="md" withBorder mt="md">
          <Title order={3} mb={4}>
            新建世界
          </Title>
          <Text fz={11} c="dimmed" mb="sm">
            创建后会切换为当前世界，首次启动时生成地形。
          </Text>
          <Stack gap="xs">
            <TextInput
              size="xs"
              label="名称"
              placeholder="survival-s2"
              value={draft.name}
              onChange={(e) => setDraft((d) => ({ ...d, name: e.currentTarget.value }))}
            />
            <Group grow>
              <TextInput
                size="xs"
                label="种子"
                description="留空随机"
                value={draft.seed}
                onChange={(e) => setDraft((d) => ({ ...d, seed: e.currentTarget.value }))}
              />
              <Select
                size="xs"
                label="地形"
                data={fields["level-type"]?.options ?? ["minecraft:normal"]}
                value={draft.level_type}
                allowDeselect={false}
                onChange={(v) => setDraft((d) => ({ ...d, level_type: v ?? "minecraft:normal" }))}
              />
            </Group>
            <Group justify="flex-end">
              <Button
                size="xs"
                variant="default"
                leftSection={<IconPlus size={12} />}
                loading={create.isPending}
                disabled={running || !draft.name.trim()}
                onClick={() => create.mutate()}
              >
                创建并切换
              </Button>
            </Group>
          </Stack>
        </Card>
      </Grid.Col>

      <Grid.Col span={{ base: 12, md: 5 }}>
        <Card className="glass" p="md" withBorder>
          <Title order={3} mb={4}>
            玩法设置
          </Title>
          <Text fz={11} c="dimmed" mb="sm">
            写入 server.properties，重启服务器后生效。
          </Text>
          <Stack gap="xs">
            {Object.entries(fields)
              .filter(([name]) => name !== "level-name")
              .map(([name, meta]) => renderField(name, meta))}
          </Stack>
          <Group justify="flex-end" mt="md">
            <Button size="xs" color="moss" loading={save.isPending} disabled={running} onClick={() => save.mutate()}>
              保存
            </Button>
          </Group>
        </Card>
      </Grid.Col>
    </Grid>
  );
}
