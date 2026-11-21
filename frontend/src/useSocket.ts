import { useEffect, useRef } from "react";

type Handler = (msg: Record<string, unknown>) => void;

/** Cookie-authenticated WebSocket with exponential-backoff reconnect. */
export function useSocket(path: string | null, onMessage: Handler) {
  const handler = useRef(onMessage);
  handler.current = onMessage;

  useEffect(() => {
    if (!path) return;
    let ws: WebSocket | null = null;
    let timer: number | undefined;
    let attempt = 0;
    let closed = false;

    const connect = () => {
      const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
      ws = new WebSocket(`${proto}//${window.location.host}${path}`);
      ws.onopen = () => {
        attempt = 0;
      };
      ws.onmessage = (ev) => {
        try {
          handler.current(JSON.parse(ev.data));
        } catch {
          /* ignore malformed frames */
        }
      };
      ws.onclose = (ev) => {
        if (closed || ev.code === 4401) return;
        attempt = Math.min(attempt + 1, 6);
        timer = window.setTimeout(connect, Math.min(15000, 500 * 2 ** attempt));
      };
      ws.onerror = () => ws?.close();
    };

    connect();
    return () => {
      closed = true;
      if (timer) window.clearTimeout(timer);
      ws?.close();
    };
  }, [path]);
}
