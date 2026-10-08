import { request } from "./persistence";
import type { ComputerSettings, SystemPermissions } from "../types/computer";

export const computerApi = {
  settings: () => request<ComputerSettings>("/computer"),
  save: (settings: ComputerSettings) => request<ComputerSettings>("/computer", "PUT", settings),
  permissions: () => request<SystemPermissions>("/computer/permissions"),
  requestPermission: (kind: string) => request(`/computer/permissions/${kind}/request`, "POST"),
  history: () => request<{ id: string; goal: string; status: string; summary: string }[]>("/computer/tasks"),
};
