/** Every call the UI makes to the server, in one place. The server is the only owner of state: callers write through
 *  here and then let the sync feed (or an explicit refresh) bring the new truth back. */
import { ipc } from "./ipc";
import type {
  DayResponse,
  DeviceRow,
  EventInput,
  ArmCommand,
  ArmSnapshot,
  HistoryMessage,
  MemoryChange,
  MemoryFile,
  MemoryFileInfo,
  MemoryOverview,
  MemoryProposal,
  PlanResponse,
  PlanWrite,
  ProactiveSettings,
} from "./types";

const q = (o: Record<string, string | number>) => new URLSearchParams(Object.entries(o).map(([k, v]) => [k, String(v)])).toString();

export const api = {
  arm: () => ipc.api<ArmSnapshot>("GET", "/arm"),
  armCommand: (c: ArmCommand) => ipc.api<{ ok: boolean }>("POST", "/arm/command", c),
  plan: (start: string, days: number) => ipc.api<PlanResponse>("GET", `/plan?${q({ start, days })}`),
  planAdd: (e: EventInput) => ipc.api<PlanWrite>("POST", "/plan", e),
  planEdit: (id: string, patch: Partial<EventInput>) => ipc.api<PlanWrite>("PATCH", `/plan/${id}`, patch),
  planStatus: (id: string, status: "planned" | "done" | "skipped") => ipc.api<PlanWrite>("POST", `/plan/${id}/status`, { status }),
  planRemove: (id: string) => ipc.api<{ ok: boolean }>("DELETE", `/plan/${id}`),
  day: (date: string) => ipc.api<DayResponse>("GET", `/day/${date}`),
  setNote: (date: string, text: string) => ipc.api<DayResponse>("PUT", `/day/${date}`, { text }),

  memoryFiles: () => ipc.api<{ files: MemoryFileInfo[] }>("GET", "/memory"),
  memoryFile: (key: string) => ipc.api<MemoryFile>("GET", `/memory/${key}`),
  memoryEdit: (key: string, action: "add" | "replace" | "remove", text: string, match = "") =>
    ipc.api<{ applied: number; rejected: { why: string }[] }>("POST", `/memory/${key}`, { action, text, match }),
  memoryLog: (last = 60) => ipc.api<{ changes: MemoryChange[] }>("GET", `/memory/log?last=${last}`),
  memoryUndo: (n: number) => ipc.api<{ file: string; action: string }>("POST", `/memory/log/${n}/undo`),

  memoryOverview: () => ipc.api<MemoryOverview>("GET", "/memory/overview"),
  proposals: () => ipc.api<{ proposals: MemoryProposal[] }>("GET", "/memory/proposals"),
  answerProposal: (id: string, verdict: "accept" | "reject") => ipc.api<{ ok: boolean; applied: number }>("POST", `/memory/proposals/${id}/${verdict}`),

  settings: () => ipc.api<{ proactive: ProactiveSettings }>("GET", "/settings"),
  saveSettings: (patch: Partial<ProactiveSettings>) => ipc.api<{ proactive: ProactiveSettings }>("PUT", "/settings", patch),

  devices: () => ipc.api<{ devices: DeviceRow[] }>("GET", "/devices"),
  revoke: (id: string) => ipc.api<{ ok: boolean }>("DELETE", `/devices/${id}`),
  pairCode: () => ipc.api<{ code: string; expires_in: number }>("POST", "/devices/pair-code"),

  history: (n = 80) => ipc.api<{ messages: HistoryMessage[] }>("GET", `/chat/history?n=${n}`),
};
