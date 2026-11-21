import {
  ActionIcon,
  Badge,
  Button,
  Card,
  Divider,
  Grid,
  Group,
  Modal,
  ScrollArea,
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
  IconBan,
  IconCrown,
  IconShieldCheck,
  IconUserPlus,
  IconUserX,
  IconUsers,
} from "@tabler/icons-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, ApiError } from "../../api";
import type { PlayerLists } from "../../types";

type Pending = { type: "ban" | "kick"; name: string } | null;

function isOnline(state: string): boolean {
  return state === "running" || state === "starting";
}

export function PlayersTab({ serverId }: { serverId: number }) {
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [pending, setPending] = useState<Pending>(null);
  const [reason, setReason] = useState("");

  const players = useQuery<PlayerLists>({
    queryKey: ["players", serverId],
    queryFn: () => api.get<PlayerLists>(`/servers/${serverId}/players`),
    refetchInterval: 8000,
  });

  const apply = (data: Partial<PlayerLists> & { applied?: boolean }) => {
    qc.setQueryData<PlayerLists>(["players", serverId], (old) => (old ? { ...old, ...data } : old));
  };

  const onError = (e: unknown) =>
    notifications.show({ color: "red", title: "操作失败", message: (e as ApiError).detail });

  const afterWrite = (data: Partial<PlayerLists> & { applied?: boolean }, okMessage: string) => {
    apply(data);
    notifications.show({
      color: data.applied === false ? "yellow" : "teal",
      message: data.applied === false ? `${okMessage}（已写入文件，重启后生效）` : okMessage,
    });
  };

  const addWhitelist = useMutation({
    mutationFn: (value: string) => api.post<PlayerLists & { applied?: boolean }>(`/servers/${serverId}/players/whitelist`, { name: value }),
    onSuccess: (data: PlayerLists & { applied?: boolean }) => {
      setName("");
      afterWrite(data, "已加入白名单");
    },
    onError,
  });

  const removeWhitelist = useMutation({
    mutationFn: (value: string) =>
      api.del<PlayerLists & { applied?: boolean }>(`/servers/${serverId}/players/whitelist/${encodeURIComponent(value)}`),
    onSuccess: (data: PlayerLists & { applied?: boolean }) => afterWrite(data, "已移出白名单"),
    onError,
  });

  const makeOp = useMutation({
    mutationFn: (value: string) => api.post<PlayerLists & { applied?: boolean }>(`/servers/${serverId}/players/ops`, { name: value, level: 4 }),
    onSuccess: (data: PlayerLists & { applied?: boolean }) => afterWrite(data, "已设为管理员"),
    onError,
  });

  const removeOp = useMutation({
    mutationFn: (value: string) => api.del<PlayerLists & { applied?: boolean }>(`/servers/${serverId}/players/ops/${encodeURIComponent(value)}`),
    onSuccess: (data: PlayerLists & { applied?: boolean }) => afterWrite(data, "已取消管理员"),
    onError,
  });

  const unban = useMutation({
    mutationFn: (value: string) => api.del<PlayerLists & { applied?: boolean }>(`/servers/${serverId}/players/bans/${encodeURIComponent(value)}`),
    onSuccess: (data: PlayerLists & { applied?: boolean }) => afterWrite(data, "已解除封禁"),
    onError,
  });

  const whitelistMode = useMutation({
    mutationFn: (enabled: boolean) => api.post<PlayerLists & { applied?: boolean }>(`/servers/${serverId}/players/whitelist-mode`, { enabled }),
    onSuccess: (data: PlayerLists & { applied?: boolean; enabled?: boolean }) =>
      notifications.show({
        color: data.applied === false ? "yellow" : "teal",
        message: (data.enabled ?? data.whitelist_enabled) ? "白名单已开启" : "白名单已关闭",
      }),
    onError,
  });

  const moderate = useMutation({
    mutationFn: (input: { type: "ban" | "kick"; name: string; reason: string }) =>
      input.type === "ban"
        ? api.post(`/servers/${serverId}/players/bans`, { name: input.name, reason: input.reason, kick: true })
        : api.post(`/servers/${serverId}/players/kick`, { name: input.name, reason: input.reason }),
    onSuccess: (data, input) => {
      setPending(null);
      setReason("");
      if (input.type === "ban") {
        apply(data as PlayerLists);
        notifications.show({ color: "teal", message: `${input.name} 已封禁` });
      } else {
        notifications.show({ color: "teal", message: `${input.name} 已被踢出` });
      }
    },
    onError,
  });

  const data = players.data;
  const running = isOnline(data?.state ?? "");

  return (
    <Grid gutter="md">
      <Grid.Col span={{ base: 12, md: 6 }}>
        <Stack gap="md">
          <Card className="glass" p="md" withBorder>
            <Group justify="space-between" mb="sm">
              <Group gap={8}>
                <IconUsers size={16} color="#5d8496" />
                <Title order={4}>在线玩家</Title>
                <Badge size="sm" variant="light" color="teal">
                  {data?.online.length ?? 0}
                </Badge>
              </Group>
              <Badge size="xs" variant="light" color={data?.online_mode ? "blue" : "gray"}>
                {data?.online_mode ? "正版验证" : "离线模式"}
              </Badge>
            </Group>
            {!running ? (
              <Text size="xs" c="dimmed">
                服务器未运行。启动后这里会实时显示在线玩家，可直接踢出、授权或封禁。
              </Text>
            ) : data?.online.length ? (
              <Stack gap={4}>
                {data.online.map((player) => (
                  <Group key={player} justify="space-between" className="player-row">
                    <Group gap={8}>
                      <span className="pulse-dot" />
                      <Text size="sm" className="mono">
                        {player}
                      </Text>
                    </Group>
                    <Group gap={2} wrap="nowrap">
                      <Tooltip label="设为管理员">
                        <ActionIcon variant="subtle" color="yellow" onClick={() => makeOp.mutate(player)}>
                          <IconCrown size={15} />
                        </ActionIcon>
                      </Tooltip>
                      <Tooltip label="踢出">
                        <ActionIcon
                          variant="subtle"
                          color="orange"
                          onClick={() => {
                            setReason("Kicked by an operator");
                            setPending({ type: "kick", name: player });
                          }}
                        >
                          <IconUserX size={15} />
                        </ActionIcon>
                      </Tooltip>
                      <Tooltip label="封禁">
                        <ActionIcon
                          variant="subtle"
                          color="red"
                          onClick={() => {
                            setReason("Banned by an operator");
                            setPending({ type: "ban", name: player });
                          }}
                        >
                          <IconBan size={15} />
                        </ActionIcon>
                      </Tooltip>
                    </Group>
                  </Group>
                ))}
              </Stack>
            ) : (
              <Text size="xs" c="dimmed">
                暂时没有玩家在线。
              </Text>
            )}
          </Card>

          <Card className="glass" p="md" withBorder>
            <Group justify="space-between" mb="xs">
              <Group gap={8}>
                <IconShieldCheck size={16} color="#5d8496" />
                <Title order={4}>白名单</Title>
                <Badge size="sm" variant="light" color="moss">
                  {data?.whitelist.length ?? 0}
                </Badge>
              </Group>
              <Group gap={8}>
                <Text size="11px" c="dimmed">
                  仅白名单可进入
                </Text>
                <Switch
                  size="sm"
                  color="moss"
                  checked={!!data?.whitelist_enabled}
                  disabled={whitelistMode.isPending}
                  onChange={(e) => whitelistMode.mutate(e.currentTarget.checked)}
                />
              </Group>
            </Group>
            <Group gap="xs" align="flex-end" mb="sm">
              <TextInput
                placeholder="玩家名"
                value={name}
                onChange={(e) => setName(e.currentTarget.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && name.trim().length >= 2) addWhitelist.mutate(name.trim());
                }}
                style={{ flex: 1 }}
                maxLength={16}
              />
              <Button
                leftSection={<IconUserPlus size={14} />}
                color="moss"
                variant="light"
                loading={addWhitelist.isPending}
                disabled={name.trim().length < 2}
                onClick={() => addWhitelist.mutate(name.trim())}
              >
                添加
              </Button>
            </Group>
            <ScrollArea h={220} type="hover" scrollbars="y" offsetScrollbars>
              {data?.whitelist.length ? (
                <Table verticalSpacing={4} horizontalSpacing="sm">
                  <Table.Tbody>
                    {data.whitelist.map((entry) => (
                      <Table.Tr key={entry.uuid}>
                        <Table.Td>
                          <Text size="sm" className="mono">
                            {entry.name}
                          </Text>
                          <Text size="9.5px" c="dimmed" className="mono">
                            {entry.uuid}
                          </Text>
                        </Table.Td>
                        <Table.Td w={40}>
                          <Tooltip label="移出白名单">
                            <ActionIcon
                              variant="subtle"
                              color="red"
                              onClick={() => removeWhitelist.mutate(entry.name)}
                            >
                              <IconUserX size={14} />
                            </ActionIcon>
                          </Tooltip>
                        </Table.Td>
                      </Table.Tr>
                    ))}
                  </Table.Tbody>
                </Table>
              ) : (
                <Text size="xs" c="dimmed" py="sm">
                  白名单为空。开启开关后，只有列表里的玩家能进入。
                </Text>
              )}
            </ScrollArea>
          </Card>
        </Stack>
      </Grid.Col>

      <Grid.Col span={{ base: 12, md: 6 }}>
        <Stack gap="md">
          <Card className="glass" p="md" withBorder>
            <Group justify="space-between" mb="sm">
              <Group gap={8}>
                <IconCrown size={16} color="#c9a227" />
                <Title order={4}>管理员</Title>
                <Badge size="sm" variant="light" color="yellow">
                  {data?.ops.length ?? 0}
                </Badge>
              </Group>
              <Text size="11px" c="dimmed">
                ops.json · 等级 4
              </Text>
            </Group>
            <Divider mb="sm" />
            {data?.ops.length ? (
              <Stack gap={4}>
                {data.ops.map((entry) => (
                  <Group key={entry.uuid} justify="space-between">
                    <Group gap={8}>
                      <IconCrown size={13} color="#c9a227" />
                      <Text size="sm" className="mono">
                        {entry.name}
                      </Text>
                      <Badge size="xs" variant="light" color="yellow">
                        Lv.{entry.level ?? 4}
                      </Badge>
                    </Group>
                    <Button size="compact-xs" variant="subtle" color="red" onClick={() => removeOp.mutate(entry.name)}>
                      取消
                    </Button>
                  </Group>
                ))}
              </Stack>
            ) : (
              <Text size="xs" c="dimmed">
                还没有管理员。在「在线玩家」里点皇冠图标即可授权。
              </Text>
            )}
          </Card>

          <Card className="glass" p="md" withBorder>
            <Group justify="space-between" mb="sm">
              <Group gap={8}>
                <IconBan size={16} color="#e2566b" />
                <Title order={4}>封禁名单</Title>
                <Badge size="sm" variant="light" color="red">
                  {data?.bans.length ?? 0}
                </Badge>
              </Group>
              <Text size="11px" c="dimmed">
                banned-players.json
              </Text>
            </Group>
            <Divider mb="sm" />
            <ScrollArea h={260} type="hover" scrollbars="y" offsetScrollbars>
              {data?.bans.length ? (
                <Stack gap={6}>
                  {data.bans.map((entry) => (
                    <Group key={entry.uuid} justify="space-between" align="flex-start" wrap="nowrap">
                      <Stack gap={0} style={{ minWidth: 0 }}>
                        <Text size="sm" className="mono">
                          {entry.name}
                        </Text>
                        <Text size="10px" c="dimmed" truncate>
                          {entry.reason || "未填写原因"} · {entry.created ?? ""}
                        </Text>
                      </Stack>
                      <Button size="compact-xs" variant="subtle" color="teal" onClick={() => unban.mutate(entry.name)}>
                        解封
                      </Button>
                    </Group>
                  ))}
                </Stack>
              ) : (
                <Text size="xs" c="dimmed">
                  没有封禁记录。
                </Text>
              )}
            </ScrollArea>
          </Card>
        </Stack>
      </Grid.Col>

      <Modal
        opened={!!pending}
        onClose={() => setPending(null)}
        title={pending?.type === "ban" ? `封禁 ${pending.name}` : `踢出 ${pending?.name ?? ""}`}
        size="sm"
      >
        <Stack gap="md">
          <TextInput
            label="原因"
            value={reason}
            onChange={(e) => setReason(e.currentTarget.value)}
            maxLength={200}
          />
          <Text size="11px" c="dimmed">
            {pending?.type === "ban"
              ? "封禁会写入 banned-players.json，并在服务器运行时立即生效。"
              : "踢出只是断开当前连接，玩家可以立即重新进入。"}
          </Text>
          <Group justify="flex-end">
            <Button variant="subtle" color="gray" onClick={() => setPending(null)}>
              取消
            </Button>
            <Button
              color={pending?.type === "ban" ? "red" : "orange"}
              loading={moderate.isPending}
              onClick={() =>
                pending && moderate.mutate({ type: pending.type, name: pending.name, reason: reason || "Kicked by an operator" })
              }
            >
              确认
            </Button>
          </Group>
        </Stack>
      </Modal>
    </Grid>
  );
}
