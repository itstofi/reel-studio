// Audio tap: hands every 128-sample block of the mic to the page (raw PCM, no browser processing).
class RecTap extends AudioWorkletProcessor {
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (ch) this.port.postMessage(ch.slice(0));
    return true;
  }
}
registerProcessor('rec-tap', RecTap);
