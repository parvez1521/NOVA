// Runs only during an explicit push-to-talk recording. Output is silence.
class NovaPCMRecorder extends AudioWorkletProcessor {
  constructor() {
    super();
    this.samples = [];
    this.port.onmessage = () => {
      this.flush();
      this.port.postMessage({ done: true });
    };
  }
  flush() {
    if (this.samples.length) {
      const data = new Float32Array(this.samples);
      this.port.postMessage({ samples: data }, [data.buffer]);
      this.samples = [];
    }
  }
  process(inputs) {
    const channels = inputs[0];
    if (channels?.length) {
      for (let i = 0; i < channels[0].length; i++) {
        let sample = 0;
        for (const channel of channels) sample += channel[i];
        this.samples.push(sample / channels.length);
      }
      if (this.samples.length >= 2048) this.flush();
    }
    return true;
  }
}
registerProcessor("nova-pcm", NovaPCMRecorder);
