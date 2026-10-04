// Vendored from StackChan/tools/voice-lab; see docs/VOICE.md. Only transport is adapted.
// Energy-only endpoint heuristic, not semantic VAD or emotion recognition.
// The recorder keeps everything from the button click, including the opening.
class VoiceEndpoint {
  constructor({silenceMs = 2200, start = .02, continuing = .008} = {}) {
    this.silenceMs = silenceMs;
    this.start = start;
    this.continuing = continuing;
    this.candidate = null;
    this.lastSound = null;
    this.heard = false;
  }
  update(rms, now) {
    if (!this.heard) {
      if (this.candidate === null && rms >= this.start) this.candidate = now;
      if (this.candidate !== null) {
        if (rms >= this.continuing) {
          this.lastSound = now;
          if (now - this.candidate >= 450) this.heard = true;
        } else if (now - this.lastSound > 150) {
          this.candidate = null;
        }
      }
    } else if (rms >= this.continuing) this.lastSound = now;
    const quietMs = this.heard ? now - this.lastSound : 0;
    return {heard: this.heard, quietMs, stop: this.heard && quietMs >= this.silenceMs};
  }
}
if (typeof module !== 'undefined') module.exports = VoiceEndpoint;
