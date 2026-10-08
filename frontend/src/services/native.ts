import { invoke, isTauri } from "@tauri-apps/api/core";
import { getCurrentWindow } from "@tauri-apps/api/window";
export { nativeListen, NativeEvents } from "./nativeEvents";

export const nativeAvailable = isTauri();
export const nativeWindowLabel = (() => {
  if (!nativeAvailable) return "browser";
  try { return getCurrentWindow().label; } catch { return "main"; }
})();
export interface NativeRuntime { base_url: string; session: string; status: string; restarts: number; pid: number | null; data_directory: string; event_smoke?: boolean }
export interface NativePreferences { always_on_top: boolean; hotkey: string; hotkey_enabled: boolean; mode: string; position?: [number, number] | null; lock_position: boolean }
export interface NativePermissionDiagnostics {
  microphone: "GRANTED" | "DENIED" | "NOT_DETERMINED";
  speech_recognition: "GRANTED" | "DENIED" | "NOT_DETERMINED" | "UNAVAILABLE";
  accessibility: "GRANTED" | "DENIED";
  screen_recording: "GRANTED" | "DENIED";
  active_app_path: string;
  bundle_identity: string;
  signing_identity: string;
  designated_requirement: string;
  helper_path: string;
  helper_identity: string;
  voice_identity: string;
  runtime_identity: string;
  permission_owners: Record<string, string>;
}
let runtime: NativeRuntime | null = null;
let browserSession: string | null = null;

function initializeBrowserSession() {
  if (nativeAvailable || typeof window === "undefined") return;
  const hash = new URLSearchParams(window.location.hash.replace(/^#/, ""));
  const supplied = hash.get("nova_session");
  if (supplied) {
    browserSession = supplied;
    window.sessionStorage.setItem("nova.session", supplied);
    window.history.replaceState(null, "", `${window.location.pathname}${window.location.search}`);
  } else {
    browserSession = window.sessionStorage.getItem("nova.session");
  }
}
export async function initializeNative() {
  if (nativeAvailable) {
    runtime = await invoke<NativeRuntime>("runtime_info");
    document.documentElement.classList.add("native-app");
    if (runtime.event_smoke && nativeWindowLabel !== "companion") await (await import("./nativeEventSmoke")).runNativeEventSmoke();
  } else initializeBrowserSession();
}
export const nativeRuntime = () => runtime;
export const browserSessionToken = () => browserSession;
export const nativeCommand = <T,>(command: string, args?: Record<string, unknown>) => invoke<T>(command, args);
export const nativePermissionDiagnostics = () => nativeCommand<NativePermissionDiagnostics>("permission_diagnostics");
export function sessionHeaders(headers?: HeadersInit) {
  const result = new Headers(headers);
  const session = runtime?.session ?? browserSession;
  if (session) result.set("x-nova-session", session);
  return result;
}
