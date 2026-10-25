/** The things the server owns, as shared resources. Topic names match what the server's change feed sends. */
import { api } from "./api";
import { resource } from "./store";

export const planRange = (start: string, days: number) =>
  resource(`plan:${start}:${days}`, ["plan"], async () => (await api.plan(start, days)).events);

export const dayNote = (date: string) => resource(`day:${date}`, ["day_notes"], async () => (await api.day(date)).note);

export const memoryFiles = () => resource("memory:files", ["memory"], async () => (await api.memoryFiles()).files);
export const memoryFile = (key: string) => resource(`memory:${key}`, ["memory"], () => api.memoryFile(key));
export const memoryLog = () => resource("memory:log", ["memory_log", "memory"], async () => (await api.memoryLog(80)).changes);

export const memoryOverview = () => resource("memory:overview", ["memory"], () => api.memoryOverview());
export const proposalsRes = () => resource("memory:proposals", ["proposals", "memory"], async () => (await api.proposals()).proposals);

export const settingsRes = () => resource("settings", ["settings"], async () => (await api.settings()).proactive);
export const devicesRes = () => resource("devices", ["devices"], async () => (await api.devices()).devices);
