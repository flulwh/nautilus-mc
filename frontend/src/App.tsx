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
import { useEffect, useState } from "react";
import { Link, Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";

import { api, ApiError } from "./api";
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
      {icon}
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
      navbar={{ width: 200, breakpoint: "sm", collapsed: { mobile: !navOpen } }}
      header={{ height: 46 }}
      padding="md"
    >
      <AppShell.Header
        style={{
          background: "var(--panel)",
          borderBottom: "1px solid var(--line)",
        }}
      >
        <Group h="100%" px="md" justify="space-between">
          <Group gap="sm">
            <Burger opened={navOpen} onClick={() => setNavOpen((v) => !v)} hiddenFrom="sm" size="sm" />
            <Group gap={8} align="center">
              <div className="brand-dot" />
              <Text fz={13} fw={600}>
                Nautilus
              </Text>
              <Text fz={11} c="dimmed" className="mono">
                v0.1.0
              </Text>
            </Group>
          </Group>

          <Group gap="xs">
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
            <Menu width={200} position="bottom-end">
              <Menu.Target>
                <Group gap={7} style={{ cursor: "pointer" }}>
                  <Avatar size={24} radius={2} color="steelblue">
                    {me.user.username.slice(0, 1).toUpperCase()}
                  </Avatar>
                  <Box visibleFrom="sm">
                    <Text size="sm" lh={1.2}>
                      {me.user.username}
                    </Text>
                    <Text size="11px" c="dimmed" lh={1.2}>
                      {me.user.role}
                    </Text>
                  </Box>
                </Group>
              </Menu.Target>
              <Menu.Dropdown>
                <Menu.Label>{me.user.email}</Menu.Label>
                <Menu.Item
                  leftSection={<IconUserCircle size={15} />}
                  onClick={() => navigate("/profile")}
                >
                  个人设置
                </Menu.Item>
                <Menu.Divider />
                <Menu.Item color="red" leftSection={<IconLogout size={15} />} onClick={logout}>
                  退出登录
                </Menu.Item>
              </Menu.Dropdown>
            </Menu>
          </Group>
        </Group>
      </AppShell.Header>

      <AppShell.Navbar className="shell-nav" p="sm">
        <Stack gap={2} mt={2} h="100%">
          <div className="section-label">运营</div>
          <NavLink to="/" icon={<IconDashboard size={14} />} label="总览" active={location.pathname === "/"} />
          <NavLink
            to="/servers"
            icon={<IconServer2 size={14} />}
            label="服务器"
            active={location.pathname.startsWith("/servers")}
          />
          <NavLink
            to="/assistant"
            icon={<IconRobot size={14} />}
            label="AI 助手"
            active={location.pathname === "/assistant"}
          />
          <div className="section-label">治理</div>
          {canAudit && (
            <NavLink to="/audit" icon={<IconActivity size={14} />} label="审计日志" active={location.pathname === "/audit"} />
          )}
          {isAdmin && (
            <>
              <NavLink
                to="/users"
                icon={<IconShieldLock size={14} />}
                label="用户与角色"
                active={location.pathname === "/users"}
              />
              <NavLink
                to="/settings"
                icon={<IconAdjustments size={14} />}
                label="系统设置"
                active={location.pathname === "/settings"}
              />
            </>
          )}
          <Box mt="auto" px={10} py={8}>
            <Anchor
              href="https://github.com/flulwh/nautilus-mc"
              target="_blank"
              underline="never"
              fz={11}
              c="dimmed"
            >
              源码仓库
            </Anchor>
            <Text fz={10.5} c="dimmed" mt={2} className="mono">
              fastapi · casbin
            </Text>
          </Box>
        </Stack>
      </AppShell.Navbar>

      <AppShell.Main>
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
