import { listen } from "@tauri-apps/api/event";
import names from "./nativeEvents.json" with { type: "json" };

export type NativeEventName = string & { readonly __nativeEventName: unique symbol };

export function assertValidEventName(value: unknown): asserts value is string {
  if (typeof value !== "string" || !value.length || /[^A-Za-z0-9_:/-]/.test(value)) {
    // Never include an arbitrary input: it might contain a transcript or private data.
    throw new Error("NOVA_INVALID_NATIVE_EVENT: Expected a non-empty internal event identifier.");
  }
}

for (const name of Object.values(names)) assertValidEventName(name);
export const NativeEvents = Object.freeze(names) as Readonly<Record<keyof typeof names, NativeEventName>>;
let registrationDiagnostics = false;
export function traceNativeEventRegistrations(enabled: boolean) { registrationDiagnostics = enabled; }

export async function nativeListen<T>(name: NativeEventName, callback: (value: T) => void) {
  assertValidEventName(name);
  if (!Object.values(NativeEvents).includes(name)) {
    throw new Error("NOVA_UNKNOWN_NATIVE_EVENT: Use the centralized NativeEvents contract.");
  }
  // Sanitized registration diagnostic: event identifier only, never the payload.
  if (registrationDiagnostics) console.debug(name);
  return listen<T>(name, event => callback(event.payload));
}
