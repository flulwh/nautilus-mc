/** Typed API client for the Nautilus REST surface (cookie session, no tokens in JS). */

export class ApiError extends Error {
  status: number;
  detail: string;
  constructor(status: number, detail: string) {
    super(detail);
    this.status = status;
    this.detail = detail;
  }
}

const BASE = "/api";

function describe(payload: unknown): string {
  if (!payload) return "请求失败";
  const p = payload as Record<string, unknown>;
  if (typeof p.detail === "string") return p.detail;
  if (Array.isArray(p.detail)) {
    return p.detail
      .map((d) => {
        const item = d as Record<string, unknown>;
        return typeof item.msg === "string" ? item.msg : JSON.stringify(item);
      })
      .join("；");
  }
  return JSON.stringify(payload).slice(0, 240);
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(BASE + path, {
    credentials: "include",
    headers: {
      ...(init.body && !(init.body instanceof FormData) ? { "Content-Type": "application/json" } : {}),
      ...(init.headers || {}),
    },
    ...init,
  });

  if (res.status === 401) {
    window.dispatchEvent(new Event("nautilus:unauthorized"));
    throw new ApiError(401, "会话已过期，请重新登录");
  }
  if (!res.ok) {
    let body: unknown = null;
    try {
      body = await res.json();
    } catch {
      body = await res.text();
    }
    throw new ApiError(res.status, describe(body));
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) }),
  put: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "PUT", body: body === undefined ? undefined : JSON.stringify(body) }),
  patch: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "PATCH", body: body === undefined ? undefined : JSON.stringify(body) }),
  del: <T>(path: string) => request<T>(path, { method: "DELETE" }),
  async login(identifier: string, password: string) {
    const res = await fetch(`${BASE}/auth/signin`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ identifier, password }),
    });
    if (!res.ok) {
      let text = "登录失败";
      try {
        text = describe(await res.json());
      } catch {
        /* ignore */
      }
      throw new ApiError(res.status, res.status === 400 ? "账号或密码不正确" : text);
    }
  },
  async logout() {
    await fetch(`${BASE}/auth/logout`, { method: "POST", credentials: "include" });
  },
  async upload<T>(path: string, file: File): Promise<T> {
    const fd = new FormData();
    fd.append("file", file);
    return request<T>(path, { method: "POST", body: fd });
  },
};
