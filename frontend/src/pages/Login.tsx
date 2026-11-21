import { Alert, Box, Button, Group, Paper, PasswordInput, Stack, Text, TextInput, ThemeIcon, Title } from "@mantine/core";
import { IconAlertCircle, IconArrowRight, IconServer2, IconShieldCheck, IconTerminal2 } from "@tabler/icons-react";
import { useState } from "react";

import { api, ApiError } from "../api";

export function Login({ onSuccess, authError }: { onSuccess: () => void; authError?: string }) {
  const [identifier, setIdentifier] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState(authError || "");
  const [loading, setLoading] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      await api.login(identifier.trim(), password);
      onSuccess();
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "登录失败，请稍后重试");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="login-shell">
      <div className="login-art">
        <div>
          <div className="brand-mark">
            <span className="brand-dot" />
            Nautilus
          </div>
          <Box mt={84}>
            <h1 className="hero-title">
              把 Minecraft 服务端
              <br />
              装进一个安静的控制台
            </h1>
            <p className="hero-sub">
              进程监管、实时日志、结构化配置、插件市场与细粒度 RBAC —— 全部落在一个
              FastAPI 进程里。没有守护进程，没有外部中间件，也没有你不需要的复杂度。
            </p>
            <div className="hero-metrics">
              <div className="hero-metric">
                <strong>5</strong>
                <span>服务端类型</span>
              </div>
              <div className="hero-metric">
                <strong>4</strong>
                <span>内置角色</span>
              </div>
              <div className="hero-metric">
                <strong>2</strong>
                <span>插件源</span>
              </div>
            </div>
          </Box>
        </div>
        <Group gap="xl">
          {[
            { icon: <IconTerminal2 size={15} />, label: "RCON 控制台" },
            { icon: <IconServer2 size={15} />, label: "进程树监控" },
            { icon: <IconShieldCheck size={15} />, label: "Casbin 鉴权" },
          ].map((item) => (
            <Group key={item.label} gap={7}>
              <ThemeIcon variant="light" color="abyss" size={26} radius="md">
                {item.icon}
              </ThemeIcon>
              <Text size="xs" c="dimmed">
                {item.label}
              </Text>
            </Group>
          ))}
        </Group>
      </div>

      <Box style={{ display: "grid", placeItems: "center", padding: 32 }}>
        <Paper w={392} p={34} radius="lg" className="glass">
          <Stack gap={6} mb={26}>
            <Text className="stat-label">Sign in</Text>
            <Title order={2}>登录控制台</Title>
            <Text size="sm" c="dimmed">
              使用账号登录。会话保存在 HttpOnly Cookie 中，前端不接触任何令牌。
            </Text>
          </Stack>

          <form onSubmit={submit}>
            <Stack gap="md">
              <TextInput
                label="邮箱或用户名"
                placeholder="admin"
                value={identifier}
                onChange={(e) => setIdentifier(e.currentTarget.value)}
                autoFocus
                size="md"
                required
              />
              <PasswordInput
                label="密码"
                placeholder="••••••••••"
                value={password}
                onChange={(e) => setPassword(e.currentTarget.value)}
                size="md"
                required
              />
              {error && (
                <Alert color="red" variant="light" icon={<IconAlertCircle size={16} />} p="xs">
                  <Text size="xs">{error}</Text>
                </Alert>
              )}
              <Button
                type="submit"
                size="md"
                color="abyss"
                loading={loading}
                rightSection={<IconArrowRight size={16} />}
                mt={4}
              >
                进入面板
              </Button>
            </Stack>
          </form>

          <Text size="10.5px" c="dimmed" mt={22} ta="center" style={{ lineHeight: 1.7 }}>
            首次启动时，初始管理员密码会打印在服务端日志中。
            <br />
            连续 5 次失败将锁定该账号 15 分钟。
          </Text>
        </Paper>
      </Box>
    </div>
  );
}
