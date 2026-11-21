export interface Runtime {
  id: number;
  state: "stopped" | "starting" | "running" | "stopping" | "crashed";
  pid: number | null;
  started_at: number | null;
  uptime: number;
  players: string[];
  player_count: number;
  last_exit_code: number | null;
  restarts: number;
  cpu: number;
  rss_mb: number;
  install_pct: number;
  last_error: string;
}

export interface Server {
  id: number;
  name: string;
  kind: string;
  version: string;
  jar: string;
  java: string;
  memory_mb: number;
  min_memory_mb: number;
  port: number;
  public_address: string;
  rcon_port: number;
  rcon_enabled: boolean;
  jvm_args: string;
  server_args: string;
  auto_start: boolean;
  auto_restart: boolean;
  eula_accepted: boolean;
  created_at: string;
  runtime: Runtime;
  disk_bytes?: number;
  jar_ready?: boolean;
  directory?: string;
  files?: string[];
}

export interface Me {
  user: {
    id: number;
    username: string;
    email: string;
    role: string;
    is_active: boolean;
    last_login_at: string | null;
  };
  permissions: string[];
  roles: string[];
}

export interface MetricPoint {
  ts: number;
  cpu: number;
  rss_mb: number;
  threads: number;
  players: number;
}

export interface LogLine {
  line: string;
  ts: number;
  level?: string;
}

export interface FileEntry {
  name: string;
  path: string;
  is_dir: boolean;
  size: number;
  mtime: number;
  editable: boolean;
  format: string;
}

export interface PluginItem {
  filename: string;
  size: number;
  mtime: number;
  enabled: boolean;
  name?: string;
  version?: string;
  main?: string;
  api?: string;
  broken?: boolean;
}

export interface MarketItem {
  source: string;
  id: string;
  slug: string;
  name: string;
  description: string;
  downloads: number;
  author: string;
  icon?: string | null;
  categories?: string[];
  page: string;
}

export interface AuditEntry {
  id: number;
  ts: string;
  actor: string;
  action: string;
  target: string;
  detail: Record<string, unknown>;
  ip: string;
  ok: boolean;
}

export interface BackupArchive {
  name: string;
  scope: "world" | "config" | "full";
  label: string;
  created_at: number;
  bytes: number;
  entries: number;
  includes: string[];
}

export interface BackupList {
  archives: BackupArchive[];
  total_bytes: number;
  keep: number;
  scopes: string[];
  world_dirs: string[];
  running: boolean;
  in_progress: boolean;
}

export interface PlayerEntry {
  uuid: string;
  name: string;
  level?: number;
  bypassesPlayerLimit?: boolean;
  created?: string;
  expires?: string;
  reason?: string;
  source?: string;
}

export interface PlayerLists {
  online: string[];
  whitelist: PlayerEntry[];
  ops: PlayerEntry[];
  bans: PlayerEntry[];
  whitelist_enabled: boolean;
  online_mode: boolean;
  state: string;
}

export interface ScheduledTask {
  id: number;
  server_id: number;
  name: string;
  kind: "restart" | "start" | "stop" | "kill" | "backup" | "command";
  cron: string;
  payload: string;
  enabled: boolean;
  last_run: string | null;
  last_status: string;
  last_result: string;
  created_at: string;
  next_run: string | null;
}

export interface TaskList {
  tasks: ScheduledTask[];
  kinds: Record<string, string>;
  timezone: string | null;
}

export interface UserRow {
  id: number;
  username: string;
  email: string;
  role: string;
  is_active: boolean;
  created_at: string | null;
  last_login_at: string | null;
}

export interface AiConfig {
  enabled: boolean;
  base_url: string;
  model: string;
  api_key: string;
  api_key_masked: string;
  has_key: boolean;
  configured: boolean;
  max_steps: number;
  auto_approve: boolean;
  presets: { label: string; base_url: string; model: string }[];
}

export interface AiTool {
  name: string;
  summary: string;
  dangerous: boolean;
  mutates: boolean;
  perm: string;
}

export interface AddressConfig {
  panel_url: string;
  mcp_mode: "stdio" | "http";
  mcp_host: string;
  mcp_port: number;
  effective_panel_url: string;
  inferred: boolean;
  mcp_exposed: boolean;
  mcp_client_example: string;
}
