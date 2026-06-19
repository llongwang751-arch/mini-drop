import { create } from "zustand";
import { api } from "../api/client";
import type { Agent, AuditEvent, Task, TaskRequest } from "../types";

interface DashboardState {
  agents: Agent[];
  tasks: Task[];
  audit: AuditEvent[];
  selectedTask: Task | null;
  error: string;
  refresh: () => Promise<void>;
  selectTask: (id: string) => Promise<void>;
  clearSelectedTask: () => void;
  createTask: (body: TaskRequest) => Promise<void>;
  planTask: (text: string) => Promise<Task>;
  loadContinuous: (agentId: string, start?: string, end?: string) => Promise<Task[]>;
}

const toTimestamp = (value?: string) => (value ? String(new Date(value).getTime() / 1000) : undefined);

export const useDashboardStore = create<DashboardState>()((set, get) => ({
  agents: [],
  tasks: [],
  audit: [],
  selectedTask: null,
  error: "",

  refresh: async () => {
    try {
      const [agents, tasks, audit] = await Promise.all([
        api<Agent[]>("/agents"),
        api<Task[]>("/tasks"),
        api<AuditEvent[]>("/audit"),
      ]);
      const selectedId = get().selectedTask?.id;
      const selectedTask = selectedId ? await api<Task>(`/tasks/${selectedId}`) : null;
      set({ agents, tasks, audit, selectedTask, error: "" });
    } catch (error) {
      set({ error: error instanceof Error ? error.message : String(error) });
    }
  },

  selectTask: async (id: string) => {
    const selectedTask = await api<Task>(`/tasks/${id}`);
    set({ selectedTask });
  },

  clearSelectedTask: () => set({ selectedTask: null }),

  createTask: async (body: TaskRequest) => {
    await api<Task>("/tasks", { method: "POST", body: JSON.stringify(body) });
    await get().refresh();
  },

  planTask: async (text: string) => {
    const response = await api<{ task: Task }>("/natural-language", {
      method: "POST",
      body: JSON.stringify({ text }),
    });
    await get().refresh();
    await get().selectTask(response.task.id);
    return response.task;
  },

  loadContinuous: async (agentId: string, start?: string, end?: string) => {
    const query = new URLSearchParams();
    const startTime = toTimestamp(start);
    const endTime = toTimestamp(end);
    if (startTime) query.set("start", startTime);
    if (endTime) query.set("end", endTime);
    return api<Task[]>(`/continuous/${agentId}?${query.toString()}`);
  },
}));
