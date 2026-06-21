export type StatusValue = "PENDING" | "RUNNING" | "UPLOADING" | "DONE" | "FAILED";
export type Collector = "perf" | "ebpf" | "pyspy";

export interface Agent {
  id: string;
  hostname: string;
  version: string;
  last_seen: number;
  online: boolean;
}

export interface TaskTransition {
  id: number;
  from_status: StatusValue | null;
  to_status: StatusValue;
  reason: string;
  created_at: number;
}

export interface FlameNode {
  name: string;
  value: number;
  children?: FlameNode[];
}

export interface TopFrame {
  name: string;
  samples: number;
}

export interface HistogramBucket {
  bucket: string;
  count: number;
}

export interface Attribution {
  conclusion: string;
  evidence: Record<string, unknown>;
  rule: string;
}

export interface ProfileResult {
  flamegraph: FlameNode;
  top: TopFrame[];
  histogram?: HistogramBucket[];
  attribution: Attribution[];
  collector_meta: { backend: string; degraded?: boolean; reason?: string; stderr?: string; collector?: string };
  fingerprint: string;
}

export interface Task {
  id: string;
  agent_id: string;
  pid: number;
  duration: number;
  rate: number;
  collector: Collector;
  continuous: boolean;
  status: StatusValue;
  reason: string;
  created_at: number;
  updated_at: number;
  result?: ProfileResult | null;
  transitions?: TaskTransition[];
}

export interface TaskRequest {
  agent_id: string;
  pid: number;
  duration: number;
  rate: number;
  collector: Collector;
  continuous: boolean;
}

export interface AuditEvent {
  id: number;
  kind: string;
  subject: string;
  message: string;
  created_at: number;
}
