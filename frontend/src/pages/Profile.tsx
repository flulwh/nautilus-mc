import { Alert, Button, Card, Code, Group, PasswordInput, Stack, Text, Title } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconCheck, IconKey, IconShieldCheck } from "@tabler/icons-react";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { api, ApiError } from "../api";
import type { Me } from "../types";

/** 个人设置：账号信息与修改自己的密码（后端 POST /auth/password 已就绪）。 */
export function ProfilePage() {
  const me = useQuery<Me>({
    queryKey: ["me"],
    queryFn: () => api.get<Me>("/auth/me"),
    retry: false,
  });
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const submit = async () => {
    setError("");
    if (next.length < 8) {
      setError("新密码至少 8 位");
      return;
    }
    if (next !== confirm) {
      setError("两次输入的新密码不一致");
      return;
    }
    setSaving(true);
    try {
      await api.post("/auth/password", { current_password: current, new_password: next });
      setCurrent("");
      setNext("");
      setConfirm("");
      notifications.show({ color: "teal", message: "密码已修改，其它登录会话需重新登录" });
    } catch (exc) {
      setError(exc instanceof ApiError ? exc.detail : exc instanceof Error ? exc.message : String(exc));
    } finally {
      setSaving(false);
    }
  };

  const user = me.data?.user;
  const permissions = me.data?.permissions ?? [];

  return (
    <Stack gap="md" maw={720}>
      <Group gap={8}>
        <IconShieldCheck size={18} />
        <Title order={3}>个人设置</Title>
      </Group>

      <Card className="glass" p="md">
        <Title order={4} mb="sm">
          账号信息
        </Title>
        {me.isLoading && <Text fz={12} c="dimmed">读取中…</Text>}
        {user && (
          <Stack gap={6}>
            <Group gap={8}>
              <Text fz={11} c="dimmed" style={{ width: 72 }}>用户名</Text>
              <Text fz={12.5} className="mono">{user.username}</Text>
            </Group>
            <Group gap={8}>
              <Text fz={11} c="dimmed" style={{ width: 72 }}>邮箱</Text>
              <Text fz={12.5}>{user.email}</Text>
            </Group>
            <Group gap={8}>
              <Text fz={11} c="dimmed" style={{ width: 72 }}>角色</Text>
              <Text fz={12.5}>{user.role}</Text>
            </Group>
            <Group gap={8} align="flex-start">
              <Text fz={11} c="dimmed" style={{ width: 72 }}>权限</Text>
              <Group gap={4} wrap="wrap" style={{ flex: 1 }}>
                {permissions.map((p) => (
                  <Code key={p} fz={10.5}>{p}</Code>
                ))}
              </Group>
            </Group>
          </Stack>
        )}
      </Card>

      <Card className="glass" p="md">
        <Group gap={8} mb="sm">
          <IconKey size={16} />
          <Title order={4}>修改密码</Title>
        </Group>
        <Stack gap="sm" maw={380}>
          <PasswordInput
            label="当前密码"
            size="xs"
            value={current}
            onChange={(e) => setCurrent(e.currentTarget.value)}
            autoComplete="current-password"
          />
          <PasswordInput
            label="新密码"
            size="xs"
            value={next}
            onChange={(e) => setNext(e.currentTarget.value)}
            autoComplete="new-password"
          />
          <PasswordInput
            label="确认新密码"
            size="xs"
            value={confirm}
            onChange={(e) => setConfirm(e.currentTarget.value)}
            autoComplete="new-password"
            onKeyDown={(e) => {
              if (e.key === "Enter") void submit();
            }}
          />
          {error && (
            <Alert color="red" variant="light" p="xs">
              <Text fz={11.5}>{error}</Text>
            </Alert>
          )}
          <Group justify="flex-end">
            <Button
              size="compact-sm"
              onClick={() => void submit()}
              loading={saving}
              disabled={!current || !next || !confirm}
              leftSection={<IconCheck size={13} />}
            >
              保存新密码
            </Button>
          </Group>
        </Stack>
      </Card>
    </Stack>
  );
}
