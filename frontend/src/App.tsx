import {
  ActionIcon,
  Anchor,
  AppShell,
  Avatar,
  Badge,
  Box,
  Burger,
  Button,
  Divider,
  Group,
  Loader,
  Menu,
  ScrollArea,
  Stack,
  Text,
  Tooltip,
  useMantineColorScheme,
} from "@mantine/core";
import {
  IconActivity,
  IconBox,
  IconBucketDroplet,
  IconDashboard,
  IconLogout,
  IconMoon,
  IconServer2,
  IconShieldLock,
  IconSun,
  IconUserCircle,
} from "@tabler/icons-react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link, Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";

import { api, ApiError } from "./api";
import { AuditPage } from "./pages/Audit";
import { Dashboard } from "./pages/Dashboard";
import { Login } from "./pages/Login";
import { ServerDetail } from "./pages/ServerDetail";
import { UsersPage } from "./pages/Users";
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
        <Loader color="abyss" />
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

  const isAdmin = me.user.role === "admin" || me.permissions.includes("users:manage") || me.permissions.includes("*:.*");

  const logout = async () => {
    await api.logout();
    queryClient.clear();
    navigate("/login", { replace: true });
    await meQuery.refetch();
  };

  return (
    <AppShell
      navbar={{ width: 232, breakpoint: "sm", collapsed: { mobile: !navOpen } }}
      header={{ height: 58 }}
      padding="lg"
    >
      <AppShell.Header
        style={{
          background: "rgba(7,13,20,0.72)",
          backdropFilter: "blur(14px)",
          borderBottom: "1px solid var(--nautilus-line)",
        }}
      >
        <Group h="100%" px="md" justify="space-between">
          <Group gap="sm">
            <Burger opened={navOpen} onClick={() => setNavOpen((v) => !v)} hiddenFrom="sm" size="sm" />
            <Group gap={10}>
              <div className="brand-dot" />
              <Text fw={700} style={{ letterSpacing: "0.18em", fontSize: 13 }}>
                NAUTILUS
              </Text>
              <Badge variant="light" color="abyss" size="xs" radius="sm">
                0.1.0
              </Badge>
            </Group>
          </Group>

          <Group gap="xs">
            <Tooltip label={colorScheme === "dark" ? "切换到亮色" : "切换到暗色"}>
              <ActionIcon variant="subtle" color="gray" onClick={() => setColorScheme(colorScheme === "dark" ? "light" : "dark")}>
                {colorScheme === "dark" ? <IconSun size={17} /> : <IconMoon size={17} />}
              </ActionIcon>
            </Tooltip>
            <Menu shadow="md" width={210} position="bottom-end">
              <Menu.Target>
                <Group gap={8} style={{ cursor: "pointer" }}>
                  <Avatar size={28} radius="xl" color="abyss">
                    {me.user.username.slice(0, 1).toUpperCase()}
                  </Avatar>
                  <Box visibleFrom="sm">
                    <Text size="sm" fw={550} lh={1.1}>
                      {me.user.username}
                    </Text>
                    <Text size="10px" c="dimmed" tt="uppercase" style={{ letterSpacing: "0.12em" }}>
                      {me.user.role}
                    </Text>
                  </Box>
                </Group>
              </Menu.Target>
              <Menu.Dropdown>
                <Menu.Label>{me.user.email}</Menu.Label>
                <Menu.Item leftSection={<IconUserCircle size={15} />} disabled>
                  个人设置（P6）
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
        <Stack gap={4} mt={4}>
          <div className="section-label">运营</div>
          <NavLink to="/" icon={<IconDashboard size={16} />} label="总览" active={location.pathname === "/"} />
          <NavLink
            to="/servers"
            icon={<IconServer2 size={16} />}
            label="服务器"
            active={location.pathname.startsWith("/servers")}
          />
          <div className="section-label">治理</div>
          <NavLink to="/audit" icon={<IconActivity size={16} />} label="审计日志" active={location.pathname === "/audit"} />
          {isAdmin && (
            <NavLink
              to="/users"
              icon={<IconShieldLock size={16} />}
              label="用户与角色"
              active={location.pathname === "/users"}
            />
          )}
          <Divider my="sm" color="var(--nautilus-line)" />
          <div className="section-label">关于</div>
          <Anchor
            href="https://github.com/flulwh/nautilus-mc"
            target="_blank"
            className="nav-item"
            underline="never"
          >
            <IconBox size={16} />
            <span>源码仓库</span>
          </Anchor>
          <Box px={12} py={10} mt="auto">
            <Group gap={7}>
              <IconBucketDroplet size={14} color="#4f6478" />
              <Text size="10.5px" c="dimmed">
                FastAPI + Casbin + React
              </Text>
            </Group>
          </Box>
        </Stack>
      </AppShell.Navbar>

      <AppShell.Main>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/servers" element={<Navigate to="/" replace />} />
          <Route path="/servers/:id" element={<ServerDetail />} />
          <Route path="/audit" element={<AuditPage />} />
          <Route path="/users" element={isAdmin ? <UsersPage /> : <Navigate to="/" replace />} />
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
