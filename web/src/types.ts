export interface User {
  id: string;
  username: string;
  display_name: string;
  role: "admin" | "platform_admin" | "group_admin" | "operator" | "viewer";
}
export interface Auth {
  user: User;
  csrf_token: string;
}
export interface List<T> {
  items: T[];
  total: number;
  page?: number;
  page_size?: number;
}
export interface Agent {
  id: string;
  hostname: string;
  ip: string;
  version: string;
  platform: string;
  capabilities: string[];
  capability_details?: Record<
    string,
    {
      available: boolean;
      reason?: string;
      tools?: {
        name?: string;
        version?: string;
        sha256?: string;
        path?: string;
        reason?: string;
      }[];
    }
  >;
  labels: Record<string, unknown>;
  status: string;
  cpu_percent: number;
  memory_percent: number;
  load_average: number;
  last_seen_at: string;
  resources: {
    processes?: {
      pid: number;
      name: string;
      cpu_percent?: number;
      uid?: number;
      start_time_ticks?: number;
    }[];
    memory_total_bytes?: number;
    memory_used_bytes?: number;
  };
}
export interface Task {
  version?: number;
  analysis_identity?: {
    analysis_job_id: string;
    analysis_run: number;
    input_artifact_id: string;
    input_digest: string;
  };
  priority?: number;
  permissions?: {
    read: boolean;
    operate: boolean;
    share: boolean;
    delete: boolean;
    reanalyze: boolean;
  };
  id: string;
  name: string;
  kind: string;
  target_agent_id?: string;
  agent_hostname?: string;
  params: {
    pid?: number;
    duration_seconds?: number;
    frequency_hz?: number;
    [key: string]: unknown;
  };
  group_id?: string;
  parent_task_id?: string;
  children?: Task[];
  composite?: {
    progress_percent?: number;
    partial_success?: boolean;
    nodes?: {
      key: string;
      task_id?: string;
      depends_on?: string[];
      required?: boolean;
      status?: string;
    }[];
  };
  collection_status: string;
  analysis_status: string;
  creator_id: string;
  created_at: string;
  updated_at: string;
  request_id?: string;
  error_code?: string;
  error_message?: string;
  attempts?: Attempt[];
  artifacts?: Artifact[];
  events?: TaskEvent[];
  suggestions?: Suggestion[];
}
export interface Attempt {
  id: string;
  attempt: number;
  status: string;
  started_at?: string;
  finished_at?: string;
  error_code?: string;
  error_message?: string;
}
export interface Artifact {
  task_id?: string;
  attempt_id?: string;
  status?: string;
  format?: string;
  expires_at?: string;
  retention_class?: string;
  download_allowed?: boolean;
  id: string;
  kind: string;
  name: string;
  size: number;
  sha256: string;
  content_type: string;
  created_at: string;
}
export interface TaskEvent {
  task_id?: string;
  attempt_id?: string;
  sequence: string;
  type: string;
  source: string;
  payload: Record<string, unknown>;
  created_at: string;
}
export interface Suggestion {
  id?: string;
  title: string;
  content: string;
  severity: string;
  confidence: number;
  evidence: Record<string, unknown>;
  engine?: string;
  version?: string;
  feedback?: string;
  input_digest?: string;
}
export interface FlameNode {
  name: string;
  value: number;
  self: number;
  children?: FlameNode[];
}
export interface AnalysisResult {
  schema_version: number;
  kind: string;
  total_samples: number;
  unique_stacks: number;
  node_count: number;
  max_depth: number;
  flamegraph: FlameNode;
  top_functions: {
    name: string;
    self: number;
    total: number;
    self_percent: number;
    total_percent: number;
  }[];
  summary: {
    duration_seconds: number | null;
    frequency_hz: number | null;
    unit: string;
    source: string;
    limitations: string[];
  };
  suggestions: Suggestion[];
  sections?: Record<string, Record<string, unknown>>;
}
export interface TaskKind {
  id: string;
  display_name: string;
  description: string;
  runner: string;
  requires_pid: boolean;
  default_duration_seconds: number;
  max_duration_seconds: number;
  default_frequency_hz: number;
  max_frequency_hz: number;
  analysis_pipeline?: string;
  input_format?: string;
  input_formats?: string[];
  supported_targets?: string[];
  parameter_schema?: import("./task-schema").KindSchema["parameter_schema"];
  parameters?: import("./task-schema").KindSchema["parameters"];
}
export interface Overview {
  agents_online: number;
  agents_total: number;
  tasks_total: number;
  tasks_running: number;
  tasks_success: number;
  success_rate: number;
  storage_bytes: number;
  trend: { time: string; total: number; success: number }[];
  recent_tasks: Task[];
  collection_counts: Record<string, number>;
  analysis_counts: Record<string, number>;
}
export interface Schedule {
  id: string;
  name: string;
  interval_minutes: number;
  cron?: string;
  template_version?: number;
  timezone: string;
  template: {
    name: string;
    kind: string;
    target_agent_id: string;
    parameters: Record<string, unknown>;
  };
  enabled: boolean;
  next_run_at: string;
  last_run_at?: string;
  created_at: string;
}
