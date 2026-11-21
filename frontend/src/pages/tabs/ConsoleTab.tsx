import { ActionIcon, Box, Group, Text, TextInput } from "@mantine/core";
import { FitAddon } from "@xterm/addon-fit";
import { Terminal } from "@xterm/xterm";
import { IconCornerDownLeft, IconTrash } from "@tabler/icons-react";
import { useEffect, useRef, useState } from "react";

import { api } from "../../api";
import { useSocket } from "../../useSocket";

const LEVEL_COLOR: Record<string, string> = {
  warn: "\x1b[33m",
  error: "\x1b[31m",
};

export function ConsoleTab({ serverId, canSend }: { serverId: number; canSend: boolean }) {
  const hostRef = useRef<HTMLDivElement>(null);
  const termRef = useRef<Terminal | null>(null);
  const [command, setCommand] = useState("");
  const [history, setHistory] = useState<string[]>([]);
  const [histIndex, setHistIndex] = useState(-1);

  useEffect(() => {
    if (!hostRef.current) return;
    const term = new Terminal({
      convertEol: true,
      cursorBlink: false,
      disableStdin: true,
      fontSize: 12.5,
      lineHeight: 1.5,
      scrollback: 6000,
      fontFamily: '"JetBrains Mono", "Cascadia Code", Consolas, monospace',
      theme: {
        background: "#050a0e",
        foreground: "#c7d7e4",
        black: "#0b141c",
        brightBlack: "#5a7286",
        red: "#ef8093",
        green: "#5ed6a4",
        yellow: "#e8c26a",
        blue: "#6aa9ff",
        magenta: "#c79bf0",
        cyan: "#4dd4e8",
        white: "#dfe9f2",
      },
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(hostRef.current);
    fit.fit();
    termRef.current = term;

    const observer = new ResizeObserver(() => {
      try {
        fit.fit();
      } catch {
        /* container detached */
      }
    });
    observer.observe(hostRef.current);
    return () => {
      observer.disconnect();
      term.dispose();
      termRef.current = null;
    };
  }, []);

  useSocket(`/ws/servers/${serverId}`, (msg) => {
    const term = termRef.current;
    if (!term) return;
    if (msg.t === "history") {
      term.clear();
      for (const item of (msg.lines as { line: string }[]) ?? []) term.writeln(item.line);
    } else if (msg.t === "log") {
      const color = LEVEL_COLOR[String(msg.level ?? "info")] ?? "";
      term.writeln(`${color}${String(msg.line ?? "")}${color ? "\x1b[0m" : ""}`);
    } else if (msg.t === "state") {
      term.writeln(`\x1b[90m· 状态 ${String(msg.state)}\x1b[0m`);
    }
  });

  const send = async () => {
    const cmd = command.trim();
    if (!cmd) return;
    setCommand("");
    setHistory((h) => [cmd, ...h].slice(0, 60));
    setHistIndex(-1);
    termRef.current?.writeln(`\x1b[36m> ${cmd}\x1b[0m`);
    try {
      const res = await api.post<{ output: string }>(`/servers/${serverId}/command`, { command: cmd });
      if (res?.output) termRef.current?.writeln(res.output);
    } catch (err) {
      termRef.current?.writeln(`\x1b[31m[nautilus] ${(err as Error).message}\x1b[0m`);
    }
  };

  return (
    <Box>
      <div className="console-wrap">
        <div className="console-head">
          <Group gap={6}>
            <span className="status-dot running" />
            <span>live console</span>
          </Group>
          <Box style={{ flex: 1 }} />
          <Text size="10.5px" c="dimmed">
            ws · stdout + rcon
          </Text>
          <ActionIcon size="xs" variant="subtle" color="gray" onClick={() => termRef.current?.clear()} title="清屏">
            <IconTrash size={13} />
          </ActionIcon>
        </div>
        <div className="console-body">
          <div ref={hostRef} style={{ height: "100%" }} />
        </div>
        {canSend && (
          <div className="console-input">
            <TextInput
              value={command}
              onChange={(e) => setCommand(e.currentTarget.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") send();
                if (e.key === "ArrowUp" && history.length) {
                  const next = Math.min(history.length - 1, histIndex + 1);
                  setHistIndex(next);
                  setCommand(history[next]);
                }
                if (e.key === "ArrowDown") {
                  const next = Math.max(-1, histIndex - 1);
                  setHistIndex(next);
                  setCommand(next < 0 ? "" : history[next]);
                }
              }}
              placeholder="输入指令，例如 list / say hello / whitelist add Steve"
              variant="unstyled"
              styles={{ input: { color: "#cfe0ee", fontFamily: "var(--mantine-font-family-monospace)", fontSize: 12.5 } }}
              leftSection={<Text size="xs" c="abyss.4" className="mono">/</Text>}
              rightSection={
                <ActionIcon variant="subtle" color="abyss" onClick={send}>
                  <IconCornerDownLeft size={14} />
                </ActionIcon>
              }
              px="md"
              py={8}
            />
          </div>
        )}
      </div>
    </Box>
  );
}
