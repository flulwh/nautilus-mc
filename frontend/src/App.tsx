import {
  ActionIcon,
  Anchor,
  AppShell,
  Avatar,
  Box,
  Burger,
  Button,
  Group,
  Loader,
  Menu,
  Stack,
  Text,
  Tooltip,
  useMantineColorScheme,
} from "@mantine/core";
import {
  IconActivity,
  IconAdjustments,
  IconDashboard,
  IconLogout,
  IconMoon,
  IconRobot,
  IconServer2,
  IconShieldLock,
  IconSun,
  IconUserCircle,
} from "@tabler/icons-react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { AnimatePresence, motion } from "motion/react";
import { useEffect, useState } from "react";
import { Link, Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";

import { api, ApiError } from "./api";
import { pageVariants, staggerContainer, staggerItem } from "./motion";
import { AssistantPage } from "./pages/Assistant";
import { AuditPage } from "./pages/Audit";
import { Dashboard } from "./pages/Dashboard";
import { Login } from "./pages/Login";
import { ProfilePage } from "./pages/Profile";
import { ServerDetail } from "./pages/ServerDetail";
import { SystemPage } from "./pages/System";
import { UsersPage } from "./pages/Users";
import { hasPerm, isAdmin as isAdminUser } from "./perm";
import type { Me } from "./types";

function NavLink({ to, icon, label, active }: { to: string; icon: React.ReactNode; label: string; active: boolean }) {
  return (
    <Anchor component={Link} to={to} className="nav-item" data-active={active} underline="never">
      {/* A single shared indicator that physically slides between items. */}
      {active && (
        <motion.span
          layoutId="nav-active"
          className="nav-indicator"
          transition={{ type: "spring", stiffness: 460, damping: 38, mass: 0.7 }}
        />
      )}
      <span className="nav-icon">{icon}</span>
      <span>{label}</span>
    </Anchor>
  );
}

export function App() {
  const location = useLocation();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [navOpen, setNavOpen] = useState(false);
  const { colorScheme, setColorScheme } = useMantineColorScheme();

  const meQuery = useQuery<Me>({
    queryKey: ["me"],
    queryFn: () => api.get<Me>("/auth/me"),
    retry: false,
    staleTime: 60_000,
    // On the login screen there is no session yet; asking would log a pointless
    // 401 in the browser console on every visit.
    enabled: location.pathname !== "/login",
  });

  useEffect(() => {
    const onUnauthorized = () => queryClient.invalidateQueries({ queryKey: ["me"] });
    window.addEventListener("nautilus:unauthorized", onUnauthorized);
    return () => window.removeEventListener("nautilus:unauthorized", onUnauthorized);
  }, [queryClient]);

  if (meQuery.isLoading) {
    return (
      <Group h="100vh" justify="center">
        <Loader color="steelblue" />
      </Group>
    );
  }

  const me = meQuery.data;
  const authError = meQuery.error as ApiError | null;

  if (!me) {
    if (location.pathname !== "/login") {
      return <Navigate to="/login" replace state={{ from: location.pathname }} />;
    }
    return <Login onSuccess={() => meQuery.refetch()} authError={authError?.status === 401 ? "" : authError?.detail} />;
  }

  if (location.pathname === "/login") return <Navigate to="/" replace />;

  const isAdmin = isAdminUser(me.user.role, me.permissions);
  const canAudit = hasPerm(me.permissions, "audit", "read");

  const logout = async () => {
    await api.logout();
    queryClient.clear();
    navigate("/login", { replace: true });
    await meQuery.refetch();
  };

  return (
    <AppShell
      navbar={{ width: 240, breakpoint: "sm", collapsed: { mobile: !navOpen } }}
      header={{ height: { base: 46, sm: 0 } }}
      padding="md"
    >
      {/* Desktop has no top bar — everything lives in the sidebar, like Codex.
          The header only exists on mobile, to host the burger. */}
      <AppShell.Header
        hiddenFrom="sm"
        style={{
          background: "var(--panel)",
          borderBottom: "1px solid var(--line)",
        }}
      >
        <Group h="100%" px="md" gap="sm">
          <Burger opened={navOpen} onClick={() => setNavOpen((v) => !v)} size="sm" />
          <div className="brand-dot" />
          <Text fz={13} fw={600}>
            Nautilus
          </Text>
        </Group>
      </AppShell.Header>

      <AppShell.Navbar className="shell-nav">
        <motion.div
          variants={staggerContainer(0.04)}
          initial="hidden"
          animate="show"
          style={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column" }}
        >
          <motion.div className="side-brand" variants={staggerItem}>
            <div className="brand-dot" />
            Nautilus
            <Text fz={10.5} c="dimmed" className="mono" fw={400}>
              v0.1.0
            </Text>
          </motion.div>

          <Box px="sm" style={{ flex: 1, minHeight: 0, overflowY: "auto" }}>
            <Stack gap={2}>
              <motion.div variants={staggerItem} className="section-label">
                运营
              </motion.div>
              <motion.div variants={staggerItem}>
                <NavLink to="/" icon={<IconDashboard size={14} />} label="总览" active={location.pathname === "/"} />
              </motion.div>
              <motion.div variants={staggerItem}>
                <NavLink
                  to="/servers"
                  icon={<IconServer2 size={14} />}
                  label="服务器"
                  active={location.pathname.startsWith("/servers")}
                />
              </motion.div>
              <motion.div variants={staggerItem}>
                <NavLink
                  to="/assistant"
                  icon={<IconRobot size={14} />}
                  label="AI 助手"
                  active={location.pathname === "/assistant"}
                />
              </motion.div>
              <motion.div variants={staggerItem} className="section-label">
                治理
              </motion.div>
              {canAudit && (
                <motion.div variants={staggerItem}>
                  <NavLink to="/audit" icon={<IconActivity size={14} />} label="审计日志" active={location.pathname === "/audit"} />
                </motion.div>
              )}
              {isAdmin && (
                <>
                  <motion.div variants={staggerItem}>
                    <NavLink
                      to="/users"
                      icon={<IconShieldLock size={14} />}
                      label="用户与角色"
                      active={location.pathname === "/users"}
                    />
                  </motion.div>
                  <motion.div variants={staggerItem}>
                    <NavLink
                      to="/settings"
                      icon={<IconAdjustments size={14} />}
                      label="系统设置"
                      active={location.pathname === "/settings"}
                    />
                  </motion.div>
                </>
              )}
              <motion.div variants={staggerItem}>
                <Anchor
                  href="https://github.com/flulwh/nautilus-mc"
                  target="_blank"
                  underline="never"
                  fz={10.5}
                  c="dimmed"
                  ml={10}
                  mt={10}
                  style={{ display: "block" }}
                >
                  源码仓库
                </Anchor>
              </motion.div>
            </Stack>
          </Box>

          {/* Sidebar footer: user menu and theme switch — Codex puts the
              account affordance at the bottom of the rail. */}
          <motion.div
            variants={staggerItem}
            className="side-foot"
            style={{ padding: "var(--mantine-spacing-sm)" }}
          >
            <Group justify="space-between" wrap="nowrap">
              <Menu width={200} position="top-start">
                <Menu.Target>
                  <Group gap={7} style={{ cursor: "pointer", minWidth: 0 }}>
                    <Avatar size={26} radius={6} color="steelblue">
                      {me.user.username.slice(0, 1).toUpperCase()}
                    </Avatar>
                    <Box style={{ minWidth: 0 }}>
                      <Text size="sm" lh={1.2} truncate>
                        {me.user.username}
                      </Text>
                      <Text size="11px" c="dimmed" lh={1.2} truncate>
                        {me.user.role}
                      </Text>
                    </Box>
                  </Group>
                </Menu.Target>
                <Menu.Dropdown>
                  <Menu.Label>{me.user.email}</Menu.Label>
                  <Menu.Item leftSection={<IconUserCircle size={15} />} onClick={() => navigate("/profile")}>
                    个人设置
                  </Menu.Item>
                  <Menu.Divider />
                  <Menu.Item color="red" leftSection={<IconLogout size={15} />} onClick={logout}>
                    退出登录
                  </Menu.Item>
                </Menu.Dropdown>
              </Menu>
              <Tooltip label={colorScheme === "dark" ? "切换亮色" : "切换暗色"}>
                <ActionIcon
                  variant="subtle"
                  color="gray"
                  size="sm"
                  onClick={() => setColorScheme(colorScheme === "dark" ? "light" : "dark")}
                >
                  {colorScheme === "dark" ? <IconSun size={15} /> : <IconMoon size={15} />}
                </ActionIcon>
              </Tooltip>
            </Group>
          </motion.div>
        </motion.div>
      </AppShell.Navbar>

      <AppShell.Main className="shell-main">
        {/* A one-shot light bar across the top edge marks a route change. */}
        <AnimatePresence mode="wait" initial={false}>
          <motion.div
            key={location.pathname}
            className="route-flash"
            initial={{ opacity: 0.9, scaleX: 0.3 }}
            animate={{ opacity: 0, scaleX: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.52, ease: [0.2, 0.9, 0.25, 1] }}
          />
        </AnimatePresence>
        <AnimatePresence mode="wait" initial={false}>
          <motion.div
            key={location.pathname}
            variants={pageVariants}
            initial="initial"
            animate="animate"
            exit="exit"
          >
            <Routes>
              <Route path="/" element={<Dashboard />} />
              <Route path="/servers" element={<Navigate to="/" replace />} />
              <Route path="/servers/:id" element={<ServerDetail />} />
              <Route path="/assistant" element={<AssistantPage />} />
              <Route path="/profile" element={<ProfilePage />} />
              <Route path="/audit" element={canAudit ? <AuditPage /> : <Navigate to="/" replace />} />
              <Route path="/users" element={isAdmin ? <UsersPage /> : <Navigate to="/" replace />} />
              <Route path="/settings" element={isAdmin ? <SystemPage /> : <Navigate to="/" replace />} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          </motion.div>
        </AnimatePresence>
      </AppShell.Main>
    </AppShell>
  );
}

export function PermissionHint({ children }: { children: React.ReactNode }) {
  return (
    <Button variant="light" color="gray" size="xs" disabled>
      {children}
    </Button>
  );
}
