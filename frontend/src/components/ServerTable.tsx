import { ActionIcon, Tooltip } from "@mantine/core";
import { IconPlayerPlay, IconPlayerStop } from "@tabler/icons-react";
import { useNavigate } from "react-router-dom";

import type { Server } from "../types";
import { StatusBadge } from "./StatusBadge";

function uptime(seconds: number): string {
  if (!seconds) return "—";
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (d) return `${d}d ${h}h`;
  if (h) return `${h}h ${m}m`;
  return `${m}m`;
}

export function ServerTable({
  servers,
  canControl,
  busyId,
  onStart,
  onStop,
}: {
  servers: Server[];
  canControl: boolean;
  busyId: number | null;
  onStart: (server: Server) => void;
  onStop: (server: Server) => void;
}) {
  const navigate = useNavigate();

  return (
    <table className="srv-table">
      <thead>
        <tr>
          <th>名称</th>
          <th>状态</th>
          <th>端口</th>
          <th>玩家</th>
          <th>CPU</th>
          <th>内存</th>
          <th>运行</th>
          <th style={{ width: 68 }} />
        </tr>
      </thead>
      <tbody>
        {servers.map((server) => {
          const rt = server.runtime;
          const running = rt.state === "running" || rt.state === "starting";
          const installing = rt.install_pct >= 0 && rt.install_pct < 100;
          const memPct = Math.min(100, (rt.rss_mb / Math.max(1, server.memory_mb)) * 100);

          return (
            <tr key={server.id} onClick={() => navigate(`/servers/${server.id}`)}>
              <td>
                <div className="srv-name">
                  {server.name}
                  {!server.jar_ready && !installing && (
                    <span style={{ color: "var(--bad)", fontWeight: 400 }}> · 缺 jar</span>
                  )}
                </div>
                <div className="srv-sub mono">
                  {server.kind} {server.version || "—"} · #{server.id}
                </div>
              </td>
              <td>
                <StatusBadge state={rt.state} />
                {rt.state === "crashed" && (
                  <div className="srv-sub" style={{ color: "var(--bad)" }}>
                    {rt.last_error || `退出码 ${rt.last_exit_code}`}
                  </div>
                )}
              </td>
              <td className="mono">{server.port}</td>
              <td className="num">{rt.player_count}</td>
              <td className="num">{rt.pid ? `${rt.cpu.toFixed(0)}%` : "—"}</td>
              <td className="num">
                {installing ? (
                  <span className="mono" style={{ color: "var(--accent-text)" }}>
                    安装 {rt.install_pct}%
                  </span>
                ) : rt.pid ? (
                  <span style={{ color: memPct > 85 ? "var(--bad)" : undefined }}>
                    {rt.rss_mb.toFixed(0)} / {server.memory_mb} MB
                  </span>
                ) : (
                  "—"
                )}
              </td>
              <td className="num mono">{uptime(rt.uptime)}</td>
              <td onClick={(e) => e.stopPropagation()}>
                {canControl && (
                  <Tooltip label={running ? "停止" : "启动"}>
                    <ActionIcon
                      variant="default"
                      size="sm"
                      loading={busyId === server.id}
                      onClick={() => (running ? onStop(server) : onStart(server))}
                    >
                      {running ? <IconPlayerStop size={13} /> : <IconPlayerPlay size={13} />}
                    </ActionIcon>
                  </Tooltip>
                )}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
