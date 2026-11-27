import {
  Badge,
  Box,
  Button,
  Card,
  Group,
  Modal,
  PasswordInput,
  Select,
  Stack,
  Switch,
  Table,
  Text,
  TextInput,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconPlus, IconTrash, IconUserShield } from "@tabler/icons-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, ApiError } from "../api";
import { SkeletonRows } from "../components/AsyncState";
import { useConfirm } from "../components/Confirm";
import { ROLES } from "../generated/contract";
import type { Me, UserRow } from "../types";

const ROLE_HINT: Record<string, string> = {
  admin: "全部权限，可管理用户、删除服务器",
  operator: "启停、控制台、文件、配置、插件与安装",
  moderator: "只读 + 控制台指令",
  viewer: "只读查看",
};

export function UsersPage() {
  const qc = useQueryClient();
  const confirm = useConfirm();
  const [opened, setOpened] = useState(false);
  const [form, setForm] = useState({ username: "", email: "", password: "", role: "viewer" });

  const me = useQuery<Me>({ queryKey: ["me"], queryFn: () => api.get<Me>("/auth/me"), staleTime: 60_000 });
  const users = useQuery<UserRow[]>({ queryKey: ["users"], queryFn: () => api.get<UserRow[]>("/users") });

  const create = useMutation({
    mutationFn: () => api.post("/users", form),
    onSuccess: () => {
      notifications.show({ color: "teal", message: "用户已创建" });
      setOpened(false);
      setForm({ username: "", email: "", password: "", role: "viewer" });
      qc.invalidateQueries({ queryKey: ["users"] });
    },
    onError: (e) => notifications.show({ color: "red", title: "创建失败", message: (e as ApiError).detail }),
  });

  const patch = useMutation({
    mutationFn: ({ id, body }: { id: number; body: Record<string, unknown> }) => api.patch(`/users/${id}`, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["users"] }),
    onError: (e) => notifications.show({ color: "red", message: (e as ApiError).detail }),
  });

  const remove = useMutation({
    mutationFn: (id: number) => api.del(`/users/${id}`),
    onSuccess: () => {
      notifications.show({ color: "orange", message: "用户已删除" });
      qc.invalidateQueries({ queryKey: ["users"] });
    },
    onError: (e) => notifications.show({ color: "red", message: (e as ApiError).detail }),
  });

  return (
    <Stack gap="sm">
      <Group justify="space-between" align="flex-end">
        <Box>
          <Text fz={16} fw={600}>
            用户与角色
          </Text>
          <Text fz={11} c="dimmed" mt={2}>
            角色到权限的映射由 Casbin 策略表驱动，支持角色继承（admin → operator → moderator → viewer）。
          </Text>
        </Box>
        <Button size="xs" leftSection={<IconPlus size={13} />} onClick={() => setOpened(true)}>
          新建用户
        </Button>
      </Group>

      <Card className="glass" p="md" withBorder>
        {users.isLoading ? (
          <SkeletonRows rows={6} />
        ) : (
        <Table verticalSpacing="xs" className="stagger-rows">
          <Table.Thead>
            <Table.Tr>
              <Table.Th>用户</Table.Th>
              <Table.Th w={170}>角色</Table.Th>
              <Table.Th w={110}>状态</Table.Th>
              <Table.Th w={170}>最近登录</Table.Th>
              <Table.Th w={60} />
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {users.data?.map((u) => {
              const self = u.id === me.data?.user.id;
              return (
                <Table.Tr key={u.id}>
                  <Table.Td>
                    <Group gap={10} wrap="nowrap">
                      <Box
                        style={{
                          width: 30,
                          height: 30,
                          borderRadius: 8,
                          display: "grid",
                          placeItems: "center",
                          background: "var(--panel-2)",
                          color: "var(--text)",
                          fontSize: 12,
                          fontWeight: 600,
                        }}
                      >
                        {u.username.slice(0, 1).toUpperCase()}
                      </Box>
                      <Box>
                        <Group gap={6}>
                          <Text size="sm" fw={520}>
                            {u.username}
                          </Text>
                          {self && (
                            <Badge size="xs" variant="light" color="gray">
                              你
                            </Badge>
                          )}
                        </Group>
                        <Text size="10.5px" c="dimmed" className="mono">
                          {u.email}
                        </Text>
                      </Box>
                    </Group>
                  </Table.Td>
                  <Table.Td>
                    <Select
                      size="xs"
                      value={u.role}
                      data={ROLES.map((r) => ({ value: r, label: r }))}
                      onChange={(role) => role && patch.mutate({ id: u.id, body: { role } })}
                      disabled={self}
                      allowDeselect={false}
                    />
                  </Table.Td>
                  <Table.Td>
                    <Switch
                      size="xs"
                      checked={u.is_active}
                      disabled={self}
                      onChange={(e) => patch.mutate({ id: u.id, body: { is_active: e.currentTarget.checked } })}
                      label={u.is_active ? "启用" : "禁用"}
                      styles={{ label: { fontSize: 11 } }}
                    />
                  </Table.Td>
                  <Table.Td>
                    <Text size="10.5px" c="dimmed" className="tabular">
                      {u.last_login_at ? new Date(u.last_login_at).toLocaleString("zh-CN") : "从未"}
                    </Text>
                  </Table.Td>
                  <Table.Td>
                    <Button
                      size="compact-xs"
                      variant="subtle"
                      color="red"
                      disabled={self}
                      onClick={async () => {
                        if (await confirm({ title: "删除用户", message: `删除用户 ${u.username}？`, danger: true })) {
                          remove.mutate(u.id);
                        }
                      }}
                    >
                      <IconTrash size={13} />
                    </Button>
                  </Table.Td>
                </Table.Tr>
              );
            })}
          </Table.Tbody>
        </Table>
        )}
      </Card>

      <Card className="glass" p="md" withBorder>
        <Group gap={8} mb={8}>
          <IconUserShield size={15} color="var(--dim)" />
          <Text className="stat-label">角色说明</Text>
        </Group>
        <Stack gap={4}>
          {Object.entries(ROLE_HINT).map(([role, hint]) => (
            <Group key={role} gap={10}>
              <Badge size="xs" variant="light" color="steelblue" w={92}>
                {role}
              </Badge>
              <Text size="11px" c="dimmed">
                {hint}
              </Text>
            </Group>
          ))}
        </Stack>
      </Card>

      <Modal opened={opened} onClose={() => setOpened(false)} title="新建用户">
        <Stack gap="sm">
          <TextInput
            label="用户名"
            value={form.username}
            onChange={(e) => setForm((f) => ({ ...f, username: e.currentTarget.value }))}
            required
          />
          <TextInput
            label="邮箱（登录标识）"
            value={form.email}
            onChange={(e) => setForm((f) => ({ ...f, email: e.currentTarget.value }))}
            placeholder="someone@example.com"
            required
          />
          <PasswordInput
            label="初始密码"
            description="至少 10 位，需包含字母与数字"
            value={form.password}
            onChange={(e) => setForm((f) => ({ ...f, password: e.currentTarget.value }))}
            required
          />
          <Select
            label="角色"
            data={ROLES.map((r) => ({ value: r, label: r }))}
            value={form.role}
            onChange={(v) => setForm((f) => ({ ...f, role: v || "viewer" }))}
            allowDeselect={false}
          />
          <Group justify="flex-end" mt="xs">
            <Button variant="subtle" color="gray" onClick={() => setOpened(false)}>
              取消
            </Button>
            <Button
              color="steelblue"
              loading={create.isPending}
              disabled={!form.username || !form.email || form.password.length < 10}
              onClick={() => create.mutate()}
            >
              创建
            </Button>
          </Group>
        </Stack>
      </Modal>
    </Stack>
  );
}
