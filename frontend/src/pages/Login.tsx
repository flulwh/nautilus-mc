import { Alert, Box, Button, PasswordInput, Stack, Text, TextInput } from "@mantine/core";
import { IconAlertCircle } from "@tabler/icons-react";
import { motion } from "motion/react";
import { useState } from "react";

import { api, ApiError } from "../api";
import { EASE, staggerContainer, staggerItem } from "../motion";

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
      <motion.div
        className="login-art"
        initial="hidden"
        animate="show"
        variants={staggerContainer(0.07)}
      >
        <motion.div className="brand-mark" variants={staggerItem}>
          <span className="brand-dot" />
          Nautilus
        </motion.div>

        <motion.div className="login-body" variants={staggerItem}>
          <h1 className="hero-title">Minecraft 服务端管理面板</h1>
          <p className="hero-sub">
            单进程运行，直接管理服务器目录下的 jar 与配置文件。没有守护进程，也不依赖外部中间件。
          </p>

          <motion.dl
            className="spec-list mono"
            variants={staggerContainer(0.045)}
            initial="hidden"
            animate="show"
          >
            {[
              ["运行时", "FastAPI · Uvicorn（单 worker）"],
              ["鉴权", "Casbin RBAC · HttpOnly Cookie"],
              ["存储", "SQLite（WAL）· Alembic 迁移"],
              ["进程", "asyncio + psutil 进程树采样"],
              ["前端", "React · Vite · Mantine"],
            ].map(([k, v]) => (
              <motion.div key={k} style={{ display: "contents" }} variants={staggerItem}>
                <dt>{k}</dt>
                <dd>{v}</dd>
              </motion.div>
            ))}
          </motion.dl>
        </motion.div>

        <motion.div variants={staggerItem}>
          <Text fz={10.5} c="dimmed" className="mono">
            nautilus 0.1.0 · MIT
          </Text>
        </motion.div>
      </motion.div>

      <Box style={{ display: "grid", placeItems: "center", padding: 24 }}>
        <motion.div
          style={{ width: 320 }}
          initial={{ opacity: 0, x: 18 }}
          animate={{ opacity: 1, x: 0 }}
          transition={{ duration: 0.5, ease: EASE.snap, delay: 0.12 }}
        >
          <Text fz={13} fw={600} mb={2}>
            登录
          </Text>
          <Text fz={11.5} c="dimmed" mb={16}>
            使用账号密码登录，会话保存在 HttpOnly Cookie 中。
          </Text>

          <form onSubmit={submit}>
            <Stack gap="xs">
              <TextInput
                label="邮箱或用户名"
                placeholder="admin"
                value={identifier}
                onChange={(e) => setIdentifier(e.currentTarget.value)}
                autoFocus
                size="xs"
                required
              />
              <PasswordInput
                label="密码"
                value={password}
                onChange={(e) => setPassword(e.currentTarget.value)}
                size="xs"
                required
              />
              {error && (
                <motion.div
                  initial={{ opacity: 0, height: 0 }}
                  animate={{ opacity: 1, height: "auto" }}
                  transition={{ duration: 0.24, ease: EASE.snap }}
                >
                  <Alert color="red" variant="light" icon={<IconAlertCircle size={14} />} p={6}>
                    <Text fz={11.5}>{error}</Text>
                  </Alert>
                </motion.div>
              )}
              <Button type="submit" size="xs" loading={loading} mt={4} fullWidth>
                登录
              </Button>
            </Stack>
          </form>

          <Text fz={10.5} c="dimmed" mt={18} style={{ lineHeight: 1.7 }}>
            首次启动时初始管理员密码打印在服务端日志中。连续 5 次失败锁定账号 15 分钟。
          </Text>
        </motion.div>
      </Box>
    </div>
  );
}
