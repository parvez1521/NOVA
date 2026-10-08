import test from "node:test";
import assert from "node:assert/strict";
import { registerHooks } from "node:module";
import { readFileSync } from "node:fs";
import ts from "typescript";
import { mockIPC } from "@tauri-apps/api/mocks";

// The existing runner uses Node's TypeScript stripping. These classes also use
// parameter properties, so transpile the real source without a second test copy.
registerHooks({
  resolve(specifier, context, next) {
    if (specifier.startsWith(".") && !/\.[a-z]+$/i.test(specifier)) return next(`${specifier}.ts`, context);
    return next(specifier, context);
  },
  load(url, context, next) {
    if (!url.endsWith(".ts")) return next(url, context);
    return { format: "module", shortCircuit: true, source: ts.transpileModule(readFileSync(new URL(url), "utf8"), {
      compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
    }).outputText };
  },
});
globalThis.window = { crypto: globalThis.crypto };
globalThis.isTauri = true;
globalThis.document = { documentElement: { classList: { add() {} } } };
const { NativeEvents, nativeListen, assertValidEventName, traceNativeEventRegistrations } = await import("../src/services/nativeEvents.ts");
const { initializeNative } = await import("../src/services/native.ts");
const { LocalWakeProvider } = await import("../src/voice/wake.ts");
const { NativeMicrophoneCapture } = await import("../src/voice/nativeMicrophone.ts");

const options = { wake_provider: "apple_speech", followup_window_seconds: 5, max_recording_seconds: 60 };
const flush = () => new Promise(resolve => setImmediate(resolve));
function deferred() { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; }

function fixture({ ready = true, listenError = false, startError = false, listenGate, stopGate } = {}) {
  const calls = [], listeners = new Map(), states = [], errors = [];
  const deliver = payload => {
    for (const [id, name] of listeners) if (name === NativeEvents.voice) {
      window.__TAURI_INTERNALS__.runCallback(id, { event: name, id, payload });
    }
  };
  mockIPC(async (command, args) => {
    calls.push({ command, args });
    if (command === "runtime_info") return { base_url: "http://127.0.0.1:8742", session: "test-only" };
    if (command === "plugin:event|listen") {
      if (listenError) throw new Error("invalid args `event` for command `listen`");
      if (listenGate) await listenGate.promise;
      listeners.set(args.handler, args.event); return args.handler;
    }
    if (command === "plugin:event|unlisten") { listeners.delete(args.eventId); return; }
    if (command === "voice_status") return { apple_speech_available: true };
    if (command === "voice_command") {
      const action = args.options.action;
      if (action === "start") {
        if (startError) throw new Error("private native serialization detail");
        if (ready) deliver({ type: "ready", mode: "passive" });
      } else if (action === "stop" && stopGate) await stopGate.promise;
    }
  });
  globalThis.fetch = async () => new Response(JSON.stringify([{ name: "whisper_vad", available: true }]));
  const provider = new LocalWakeProvider(options, state => states.push(state), error => errors.push(error));
  return { provider, calls, listeners, states, errors, deliver };
}

test("valid internal event names and all centralized constants are accepted", async () => {
  const f = fixture();
  for (const name of ["nova:wake:starting", "nova/wake-started_1", ...Object.values(NativeEvents)]) assertValidEventName(name);
  for (const name of Object.values(NativeEvents)) { const cleanup = await nativeListen(name, () => {}); cleanup(); }
  await flush(); assert.equal(f.listeners.size, 0);
});

test("invalid names are rejected before Tauri IPC", async () => {
  const f = fixture();
  for (const value of ["", null, {}, "native.voice", "a,b", "a(b)", '"name"', "a\nb", "a\n", "{\"event\":1}"]) {
    await assert.rejects(nativeListen(value, () => {}), /NOVA_INVALID_NATIVE_EVENT/);
  }
  assert.equal(f.calls.length, 0);
});

test("UI labels cannot be event identifiers", async () => {
  const f = fixture();
  for (const label of ["WAKE STARTING", "MIC OFF", "Ready when you are", "Wake Starting", "STARTING"]) {
    await assert.rejects(nativeListen(label, () => {}), /NOVA_(INVALID|UNKNOWN)_NATIVE_EVENT/);
  }
  assert.equal(f.calls.length, 0);
});

test("user transcripts, commands and model output cannot be event identifiers", async () => {
  const f = fixture();
  for (const transcript of ["Hey nova", "Hello nova", "Open Safari", "hello", "nova:user:arbitrary"]) {
    await assert.rejects(nativeListen(transcript, () => {}), /NOVA_(INVALID|UNKNOWN)_NATIVE_EVENT/);
  }
  assert.equal(f.calls.length, 0);
});

test("registration diagnostic contains only the centralized name, never payloads", async t => {
  const logs = []; t.mock.method(console, "debug", (...args) => logs.push(args));
  traceNativeEventRegistrations(true); t.after(() => traceNativeEventRegistrations(false));
  const f = fixture();
  const cleanup = await nativeListen(NativeEvents.voice, () => {});
  f.deliver({ type: "audio", audio: "private-audio" });
  assert.deepEqual(logs, [[NativeEvents.voice]]); cleanup(); await flush();
});

test("wake initializes only after native readiness and uses the centralized voice event", async () => {
  const f = fixture({ ready: false }); await initializeNative();
  let finished = false; const starting = f.provider.start().then(() => { finished = true; });
  await flush();
  assert.deepEqual(f.states, ["STARTING"]); assert.equal(finished, false);
  const registration = f.calls.findIndex(call => call.command === "plugin:event|listen");
  const start = f.calls.findIndex(call => call.args?.options?.action === "start");
  assert.ok(registration < start);
  assert.equal(f.calls[registration].args.event, NativeEvents.voice);
  f.deliver({ type: "ready", mode: "passive" }); await starting;
  assert.deepEqual(f.states, ["STARTING", "PASSIVE"]);
  await f.provider.stop(); await flush();
});

test("wake registration failure produces a clean unavailable status and never starts the engine", async () => {
  const f = fixture({ listenError: true });
  await assert.rejects(f.provider.start(), /Wake word unavailable/);
  assert.equal(f.states.at(-1), "UNAVAILABLE");
  assert.ok(f.errors.every(error => !error.includes("invalid args")));
  assert.equal(f.calls.filter(call => call.args?.options?.action === "start").length, 0);
  assert.equal(f.listeners.size, 0); await f.provider.stop();
});

test("native command failure cleans the subscription and retains unavailable rather than OFF", async () => {
  const f = fixture({ startError: true });
  await assert.rejects(f.provider.start(), /Wake word unavailable/);
  await flush(); assert.equal(f.listeners.size, 0);
  assert.equal(f.states.at(-1), "UNAVAILABLE");
  assert.ok(!f.errors[0].includes("serialization")); await f.provider.stop();
});

test("native initialization error is handled without leaking native details", async () => {
  const f = fixture({ ready: false }); const starting = f.provider.start(); await flush();
  f.deliver({ type: "error", code: "MIC_PERMISSION_DENIED" });
  await assert.rejects(starting, /Wake word unavailable/); await flush();
  assert.equal(f.states.at(-1), "UNAVAILABLE"); assert.equal(f.listeners.size, 0); await f.provider.stop();
});

test("native engine failure after readiness retains unavailable and removes the listener", async () => {
  const f = fixture(); await f.provider.start();
  f.deliver({ type: "error", code: "LOCAL_WAKE_UNAVAILABLE" }); await flush();
  assert.equal(f.states.at(-1), "UNAVAILABLE"); assert.equal(f.listeners.size, 0);
  await f.provider.stop();
});

test("missing native readiness times out to a clean failure", async t => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const f = fixture({ ready: false }); const starting = f.provider.start(); await flush();
  t.mock.timers.tick(30_000);
  await assert.rejects(starting, /Wake word unavailable/); await flush();
  assert.equal(f.states.at(-1), "UNAVAILABLE"); assert.equal(f.listeners.size, 0); await f.provider.stop();
});

test("wake detection, follow-up and passive state are driven by native events", async () => {
  const f = fixture(); let wakes = 0; f.provider.on_wake(() => { wakes++; });
  await f.provider.start(); f.deliver({ type: "wake.detected" });
  assert.equal(wakes, 1); assert.equal(f.states.at(-1), "LISTENING");
  f.provider.followup(); assert.equal(f.states.at(-1), "LISTENING");
  f.deliver({ type: "ready", mode: "followup" }); assert.equal(f.states.at(-1), "FOLLOWUP");
  f.provider.passive(); assert.equal(f.states.at(-1), "FOLLOWUP");
  f.deliver({ type: "ready", mode: "passive" }); assert.equal(f.states.at(-1), "PASSIVE");
  await f.provider.stop(); await flush();
});

test("cleanup removes the listener and ignores late wake events", async () => {
  const f = fixture(); let wakes = 0; f.provider.on_wake(() => { wakes++; });
  await f.provider.start(); await f.provider.stop(); await flush();
  assert.equal(f.listeners.size, 0); f.deliver({ type: "wake.detected" });
  assert.equal(wakes, 0); assert.equal(f.states.at(-1), "OFF");
});

test("duplicate starts and reinitialization do not duplicate listeners", async () => {
  const f = fixture();
  const first = f.provider.start(); assert.equal(first, f.provider.start()); await first;
  await f.provider.start(); assert.equal(f.listeners.size, 1);
  await f.provider.stop(); await flush(); await f.provider.start();
  assert.equal(f.listeners.size, 1);
  assert.equal(f.calls.filter(call => call.command === "plugin:event|listen").length, 2);
  await f.provider.stop(); await flush();
});

test("unmount during pending registration cleans late subscriptions and does not start native voice", async () => {
  const gate = deferred(); const f = fixture({ listenGate: gate });
  const starting = f.provider.start(); await flush(); const stopping = f.provider.stop();
  gate.resolve(); await Promise.all([starting, stopping]); await flush();
  assert.equal(f.listeners.size, 0);
  assert.equal(f.calls.filter(call => call.args?.options?.action === "start").length, 0);
  assert.equal(f.states.at(-1), "OFF");
});

test("unmount while waiting for readiness cancels initialization promptly", async () => {
  const f = fixture({ ready: false }); const starting = f.provider.start(); await flush();
  await f.provider.stop(); await starting; await flush();
  assert.equal(f.listeners.size, 0); assert.equal(f.states.at(-1), "OFF");
});

test("replacement provider waits for old teardown before registering", async () => {
  const gate = deferred(); const f = fixture({ stopGate: gate }); await f.provider.start();
  const stopping = f.provider.stop();
  const replacement = new LocalWakeProvider(options, () => {}, () => {});
  const starting = replacement.start(); await flush();
  assert.equal(f.calls.filter(call => call.command === "plugin:event|listen").length, 1);
  gate.resolve(); await stopping; await starting; await flush();
  assert.equal(f.listeners.size, 1); await replacement.stop(); await flush();
});

test("microphone capture registers the same shared voice channel and cancels cleanly", async () => {
  const f = fixture(); const capture = new NativeMicrophoneCapture(() => {});
  await capture.start({ adoptExisting: true, deviceId: "", autoStop: true, maxSeconds: 60, silenceMs: 1200 }, () => {});
  assert.equal(f.calls.find(call => call.command === "plugin:event|listen").args.event, NativeEvents.voice);
  capture.cancel(); await flush(); assert.equal(f.listeners.size, 0);
});

test("microphone registration failure gives a clean status", async () => {
  fixture({ listenError: true }); const capture = new NativeMicrophoneCapture(() => {});
  await assert.rejects(capture.start({ deviceId: "", autoStop: true, maxSeconds: 60, silenceMs: 1200 }, () => {}),
    error => error.code === "MIC_CAPTURE_FAILED" && !error.message.includes("invalid args"));
  assert.equal(capture.state, "error"); capture.cancel(); await flush();
});

test("cancelled microphone registration removes the late subscription", async () => {
  const gate = deferred(); const f = fixture({ listenGate: gate });
  const capture = new NativeMicrophoneCapture(() => {});
  const starting = capture.start({ deviceId: "", autoStop: true, maxSeconds: 60, silenceMs: 1200 }, () => {});
  capture.cancel(); gate.resolve(); assert.equal(await starting, false); await flush();
  assert.equal(f.listeners.size, 0); assert.equal(capture.state, "off");
});
