import test from "node:test";
import assert from "node:assert/strict";
import { encodeWav, energy, SilenceDetector } from "../src/voice/audio.ts";
import { MicrophoneCapture, microphoneError } from "../src/voice/microphone.ts";
import { microphoneLabel } from "../src/voice/status.ts";

test("mono PCM16 WAV header, rate and clipping", () => {
  const bytes = encodeWav(new Float32Array([-2, 0, 2]));
  const view = new DataView(bytes.buffer);
  assert.equal(view.getUint32(24, true), 16000);
  assert.equal(view.getUint16(22, true), 1);
  assert.equal(view.getInt16(44, true), -32768);
  assert.equal(view.getInt16(48, true), 32767);
});

test("VAD waits for speech before silence; does not continuously listen", () => {
  const vad = new SilenceDetector(1200);
  assert.equal(vad.update(0, 4000), false);
  assert.equal(vad.update(0.2, 5000), false);
  assert.equal(vad.update(0, 6100), false);
  assert.equal(vad.update(0, 6300), true);
  assert.equal(energy(new Float32Array([0.5, -0.5])), 0.5);
});

test("permission rejection is structured and leaves the mic off", async () => {
  const states = [];
  Object.defineProperty(globalThis, "navigator", { configurable: true, value: { mediaDevices: {
    getUserMedia: async () => { throw new DOMException("denied", "NotAllowedError"); },
  } } });
  const microphone = new MicrophoneCapture((state) => states.push(state));
  await assert.rejects(microphone.start({ deviceId: "", autoStop: false, silenceMs: 1200, maxSeconds: 60 }, () => {}), { code: "MIC_PERMISSION_DENIED" });
  assert.deepEqual(states, ["requesting", "error"]);
  microphone.cancel();
  assert.equal(microphone.state, "off");
  assert.equal(microphoneError(new DOMException("missing", "NotFoundError")).code, "MIC_UNAVAILABLE");
});

test("releasing while permission is pending closes late microphone tracks", async () => {
  let resolve;
  let stopped = false;
  Object.defineProperty(globalThis, "navigator", { configurable: true, value: { mediaDevices: {
    getUserMedia: () => new Promise((done) => { resolve = done; }),
  } } });
  const microphone = new MicrophoneCapture(() => {});
  const starting = microphone.start({ deviceId: "", autoStop: false, silenceMs: 1200, maxSeconds: 60 }, () => {});
  assert.equal(await microphone.stop(), null);
  resolve({ getTracks: () => [{ stop: () => { stopped = true; } }] });
  assert.equal(await starting, false);
  assert.equal(stopped, true);
  assert.equal(microphone.state, "off");
});

test("microphone readiness is independent from active recording and backend connectivity", () => {
  const options = { voice_enabled: true, stt_enabled: true };
  assert.equal(microphoneLabel("MIC OFF", options, true, true, { microphone_permission: 3 }), "MIC READY");
  assert.equal(microphoneLabel("MIC OFF", options, true, true, { microphone_permission: 2 }), "MIC DENIED");
  assert.equal(microphoneLabel("MIC OFF", options, false, true, { microphone_permission: 3 }), "MIC READY");
  assert.equal(microphoneLabel("MIC OFF", options, false, true, { microphone_state: "NOT_DETERMINED" }), "MIC PERMISSION NEEDED");
  assert.equal(microphoneLabel("MIC OFF", null, false, true, null), "MIC CHECKING");
  assert.equal(microphoneLabel("MIC OFF", options, true, true, { microphone_permission: 3 }), "MIC READY");
  assert.equal(microphoneLabel("MIC LISTENING", options, true, true, { microphone_permission: 3 }), "MIC LISTENING");
});
