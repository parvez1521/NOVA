import type { HealthPayload, ModelActivation, ModelDescriptor, RoutingSettings, VoiceSettings, VoiceStatus } from "../types/nova";
import { browserSessionToken, nativeRuntime, sessionHeaders } from "./native";

export const apiBaseUrl = () => nativeRuntime()?.base_url ?? import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8742";
export function localFetch(path: string, options: RequestInit = {}) { return fetch(`${apiBaseUrl()}${path}`, { ...options, headers: sessionHeaders(options.headers) }); }

export async function fetchHealth(signal?: AbortSignal): Promise<HealthPayload> {
  const response = await localFetch("/api/health", { signal });
  if (!response.ok) {
    throw new Error(`Backend health request failed (${response.status})`);
  }
  return (await response.json()) as HealthPayload;
}

export function websocketUrl(): string {
  const url = new URL(apiBaseUrl());
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  url.pathname = "/ws";
  const runtime = nativeRuntime();
  const session = runtime?.session ?? browserSessionToken();
  if (session) url.searchParams.set("session", session);
  return url.toString();
}

export async function voiceStatus(settings?: VoiceSettings): Promise<VoiceStatus> {
  const response = await localFetch("/api/voice", settings ? {
    method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(settings),
  } : {});
  if (!response.ok) throw new Error("Voice settings could not be loaded or saved.");
  return response.json();
}

export async function voiceNames(): Promise<{ name: string; language: string }[]> {
  const response = await localFetch("/api/voice/voices");
  return response.ok ? response.json() : [];
}

export async function modelCatalog(refresh = false): Promise<{ models: ModelDescriptor[]; routing: RoutingSettings; activation: ModelActivation }> {
  const response = await localFetch(`/api/models${refresh ? "?refresh=true" : ""}`);
  if (!response.ok) throw new Error("Model catalog unavailable.");
  return response.json();
}

export async function saveRouting(update: Partial<RoutingSettings>): Promise<RoutingSettings> {
  const response = await localFetch("/api/routing", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(update) });
  if (!response.ok) throw new Error("Routing settings could not be saved.");
  return response.json();
}
