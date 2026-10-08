import { nativeCommand } from "./native";
import { nativeListen, NativeEvents, traceNativeEventRegistrations } from "./nativeEvents";

// Opt-in packaged test (--event-smoke). No microphone, transcript or task payload.
export async function runNativeEventSmoke() {
  traceNativeEventRegistrations(true);
  let passed = true;
  let received = 0;
  for (let cycle = 0; cycle < 2; cycle++) {
    const cleanups: (() => void)[] = [];
    let timer: ReturnType<typeof setTimeout> | undefined;
    try {
      let resolve!: () => void;
      const ready = new Promise<void>((done, reject) => {
        resolve = done;
        timer = setTimeout(() => reject(new Error("Native subscription probe timed out")), 5000);
      });
      void ready.catch(() => undefined);
      for (const name of Object.values(NativeEvents)) {
        cleanups.push(await nativeListen<{ type: string }>(name, event => {
          if (name === NativeEvents.voice && event.type === "subscription.smoke") { received++; resolve(); }
        }));
      }
      await nativeCommand("event_subscription_probe");
      await ready;
    } catch { passed = false; }
    finally { clearTimeout(timer); for (const cleanup of cleanups) await cleanup(); }
    const before = received;
    await nativeCommand("event_subscription_probe");
    await new Promise(resolve => setTimeout(resolve, 100));
    if (received !== before || received !== cycle + 1) passed = false;
  }
  traceNativeEventRegistrations(false);
  await nativeCommand("event_subscription_probe", { completed: passed });
}
