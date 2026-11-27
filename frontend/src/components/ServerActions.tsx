import { Badge, Button, Group, Menu, Modal, Stack, TextInput } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import {
  IconBroadcast,
  IconDeviceFloppy,
  IconDotsVertical,
  IconReload,
  IconShieldCheck,
  IconShieldOff,
} from "@tabler/icons-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, ApiError } from "../api";
import { useConfirm } from "./Confirm";

interface PlayerLists {
  whitelist_enabled: boolean;
  whitelist: { name?: string }[];
}

/**
 * 「更多操作」菜单：保存/广播/重载/维护模式。全部走 /actions/* 端点，
 * 与控制台指令共用 server:console 权限；接管模式下依赖 RCON，未启用时后端会给出明确报错。
 */
export function ServerActionsMenu({ serverId }: { serverId: number }) {
  const confirm = useConfirm();
  const qc = useQueryClient();
  const [broadcastOpen, setBroadcastOpen] = useState(false);
  const [message, setMessage] = useState("");

  const players = useQuery<PlayerLists>({
    queryKey: ["players", String(serverId)],
    queryFn: () => api.get<PlayerLists>(`/servers/${serverId}/players`),
    staleTime: 15_000,
  });
  const maintenance = !!players.data?.whitelist_enabled;
  const emptyWhitelist = (players.data?.whitelist.length ?? 0) === 0;

  const run = useMutation({
    mutationFn: ({ action, body }: { action: string; body?: unknown }) =>
      api.post<Record<string, unknown>>(`/servers/${serverId}/actions/${action}`, body),
    onSuccess: (res, vars) => {
      if (vars.action === "maintenance") {
        const kicked = Array.isArray(res.kicked) ? (res.kicked as string[]) : [];
        notifications.show({
          color: "blue",
          title: res.enabled ? "维护模式已开启" : "维护模式已解除",
          message: kicked.length ? `已踢出：${kicked.join("、")}` : "白名单准入状态已更新",
        });
        players.refetch();
      } else if (vars.action === "broadcast") {
        notifications.show({ color: "blue", title: "公告已发送", message: String(res.message ?? "") });
      } else {
        notifications.show({ color: "blue", title: vars.action === "save" ? "世界已保存" : "配置已重载", message: String(res.output ?? "").slice(0, 120) || "完成" });
      }
      qc.invalidateQueries({ queryKey: ["server", String(serverId)] });
    },
    onError: (e) => notifications.show({ color: "red", title: "操作失败", message: (e as ApiError).detail }),
  });

  const doMaintenance = async (enabled: boolean) => {
    const ok = await confirm({
      title: enabled ? "开启维护模式" : "解除维护模式",
      message: enabled
        ? emptyWhitelist
          ? "白名单当前为空——开启后所有在线玩家都会被踢下线。仍要继续？"
          : "将先保存世界，然后启用白名单准入并踢出不在白名单的在线玩家（OP 豁免）。"
        : "将关闭白名单准入，写回 server.properties。",
      confirmLabel: enabled ? "开启" : "解除",
      danger: enabled,
    });
    if (ok) run.mutate({ action: "maintenance", body: { enabled, kick_outsiders: true } });
  };

  const doReload = async () => {
    const ok = await confirm({
      title: "重载配置",
      message: "执行 /reload。多数插件热重载不稳定，卡顿时优先用重启。",
      confirmLabel: "重载",
      danger: true,
    });
    if (ok) run.mutate({ action: "reload" });
  };

  return (
    <>
      <Menu trigger="click" position="bottom-end" shadow="md" width={190}>
        <Menu.Target>
          <Button size="xs" variant="default" leftSection={<IconDotsVertical size={12} />} disabled={run.isPending}>
            更多操作
          </Button>
        </Menu.Target>
        <Menu.Dropdown>
          <Menu.Item leftSection={<IconDeviceFloppy size={13} />} onClick={() => run.mutate({ action: "save" })}>
            保存世界
          </Menu.Item>
          <Menu.Item
            leftSection={<IconBroadcast size={13} />}
            onClick={() => {
              setMessage("");
              setBroadcastOpen(true);
            }}
          >
            广播公告
          </Menu.Item>
          <Menu.Item leftSection={<IconReload size={13} />} onClick={doReload}>
            重载配置
          </Menu.Item>
          <Menu.Divider />
          <Menu.Item
            leftSection={maintenance ? <IconShieldOff size={13} /> : <IconShieldCheck size={13} />}
            disabled={maintenance && emptyWhitelist}
            onClick={() => doMaintenance(!maintenance)}
          >
            <Group gap={6}>
              {maintenance ? "解除维护模式" : "开启维护模式"}
              {maintenance && <Badge size="xs" color="yellow" variant="light">维护中</Badge>}
            </Group>
          </Menu.Item>
        </Menu.Dropdown>
      </Menu>

      <Modal opened={broadcastOpen} onClose={() => setBroadcastOpen(false)} title="广播公告" centered>
        <Stack gap="sm">
          <TextInput
            label="公告内容"
            placeholder="例如：今晚 8 点领地战集合"
            maxLength={240}
            value={message}
            onChange={(e) => setMessage(e.currentTarget.value)}
            description="以 say 发送到聊天栏；换行会被压成空格。"
          />
          <Group justify="flex-end">
            <Button variant="subtle" color="gray" onClick={() => setBroadcastOpen(false)}>
              取消
            </Button>
            <Button
              color="steelblue"
              disabled={!message.trim()}
              loading={run.isPending}
              onClick={() => {
                run.mutate({ action: "broadcast", body: { message } });
                setBroadcastOpen(false);
              }}
            >
              发送
            </Button>
          </Group>
        </Stack>
      </Modal>
    </>
  );
}
