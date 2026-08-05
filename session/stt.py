#!/usr/bin/env python3
"""
stt.py — PTT recording + Whisper, with the researcher's keyboard as an equal path.

TWO SOURCES, ONE ENTRY POINT. Whisper and the web UI's text box both produce the
same `transcript:<text>` event, and neither is privileged. That matters more than
it looks: a manual path bolted on as an error handler tends to be wired somewhere
slightly different, so it takes a slightly different route through the state
machine and behaves differently under STOP or a timeout -- and it is used at
exactly the moments a session is already going badly. Same door, same rules.

The recording is bracketed by the CoreS3 button but happens on the LAPTOP mic:
16 kHz 16-bit mono is 32 kB/s and the serial link carries ~14 kB/s, so streaming
the board's mic was never an option (see INTERACTION_SPEC.md).

  pip install sounddevice faster-whisper

Both are optional. Without them the module still loads and manual entry still
works, so the flow can be tested before either is installed -- which is the
whole reason the manual path is not an afterthought.

Standalone check:
  python3 stt.py            # record 3 s, transcribe, print
"""
from __future__ import annotations
import os, sys, threading, time, wave

from session.session_flow import transcript_usable   # same rules everywhere

SR = 16000
LANG = os.environ.get("NOTICEBOT_LANG", "en")   # PIN IT. Auto-detect on two
                                                # seconds of speech is a coin toss
MODEL = os.environ.get("NOTICEBOT_WHISPER", "small")
MIN_SECONDS = 2.0        # pad short clips: very short input makes Whisper
                         # hallucinate filler rather than return nothing

# Biases decoding toward the words that can actually matter here. This is the
# single biggest win for short imperatives -- without it "watch the mug" comes
# back as "watch the mud" about as often as not.
PROMPT = os.environ.get(
    "NOTICEBOT_STT_PROMPT",
    "Short spoken requests to a desk robot about watching objects: "
    "watch, look at, keep an eye on, tell me when, the mug, the cup, the door, "
    "the window, the plant, the laptop, the notebook, the bottle, the shelf.")


class Recorder:
    """Captures while the button is held. Nothing clever: one buffer, one thread."""

    def __init__(self, sr=SR):
        self.sr = sr
        self._frames = []
        self._stream = None
        self.level = 0          # 0-100, for the CoreS3 bar
        self.available = False
        try:
            import sounddevice  # noqa: F401
            self.available = True
        except Exception as e:
            print(f"[stt] sounddevice unavailable ({e}); manual entry only")

    def start(self):
        if not self.available:
            return False
        import sounddevice as sd
        import numpy as np
        self._frames = []

        def cb(indata, frames, t, status):
            self._frames.append(indata.copy())
            self.level = int(min(100, float(np.abs(indata).max()) * 300))

        self._stream = sd.InputStream(samplerate=self.sr, channels=1,
                                      dtype="int16", callback=cb)
        self._stream.start()
        return True

    def stop(self, path="/tmp/noticebot_ptt.wav"):
        """-> wav path, or None if nothing was captured."""
        if self._stream is None:
            return None
        import numpy as np
        self._stream.stop(); self._stream.close(); self._stream = None
        self.level = 0
        if not self._frames:
            return None
        audio = np.concatenate(self._frames, axis=0)
        need = int(MIN_SECONDS * self.sr)
        if len(audio) < need:
            audio = np.concatenate([audio, np.zeros((need - len(audio), 1),
                                                    dtype=audio.dtype)])
        with wave.open(path, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(self.sr)
            w.writeframes(audio.tobytes())
        return path


class Whisper:
    """Loaded lazily and off the main thread: the first call pulls the model, and
    doing that while a participant is mid-request would stall the whole loop."""

    def __init__(self, model=MODEL):
        self.name = model
        self._m = None
        self._lock = threading.Lock()

    def _model(self):
        with self._lock:
            if self._m is None:
                from faster_whisper import WhisperModel
                print(f"[stt] loading whisper '{self.name}' ...")
                self._m = WhisperModel(self.name, device="auto",
                                       compute_type="int8")
        return self._m

    def transcribe(self, wav):
        """-> (text, no_speech_prob, avg_logprob). Raises if unavailable."""
        segs, info = self._model().transcribe(
            wav, language=LANG, initial_prompt=PROMPT,
            vad_filter=True, beam_size=5)
        segs = list(segs)
        text = " ".join(s.text.strip() for s in segs).strip()
        nsp = max((getattr(s, "no_speech_prob", 0.0) for s in segs), default=1.0)
        alp = min((getattr(s, "avg_logprob", 0.0) for s in segs), default=-9.0)
        return text, nsp, alp


class STT:
    """Ties them together. `on_transcript(text, source)` is called from a worker
    thread; the loop should treat it as an event, not do work in it."""

    def __init__(self, on_transcript, enabled=True, record_dir=None):
        self.on_transcript = on_transcript
        self.record_dir = record_dir
        if self.record_dir:
            os.makedirs(self.record_dir, exist_ok=True)
        self.rec = Recorder()
        self.whisper = Whisper() if enabled else None
        self.last = None            # (text, ok, why, source) for the web UI
        self.busy = False           # a transcription is in flight

    def warm(self):
        """Load the model NOW, in the background, before anyone presses anything.

        Why this is not optional: the first transcribe pulls and initialises the
        model, which takes far longer than STT_TIMEOUT_S. So the first PTT of every
        session failed to S8 and the second worked -- and it looked like a flaky
        button rather than a cold cache, because the delay is invisible from the
        outside. Warming at startup moves that cost to where there is nobody
        waiting on it.
        """
        if self.whisper is None:
            return
        def work():
            try:
                self.whisper._model()
                print("[stt] whisper ready")
            except Exception as e:
                print(f"[stt] whisper unavailable ({e}); manual entry still works")
        threading.Thread(target=work, daemon=True).start()

    @property
    def level(self):
        return self.rec.level

    def start(self):
        return self.rec.start()

    def stop_and_transcribe(self):
        """Called on PTT release. Returns immediately; the result arrives on the
        callback. If anything is missing -- no mic, no whisper, no speech -- it
        reports an empty transcript rather than nothing at all, so the state
        machine's own timeout is not the thing that has to notice."""
        wav_path = None
        if self.record_dir:
            stamp = time.strftime("%Y%m%d_%H%M%S_") + f"{time.time_ns() % 1_000_000_000:09d}"
            wav_path = os.path.join(self.record_dir, f"ptt_{stamp}.wav")
        self.busy = True            # the flow must not time out while this runs

        def work():
            # rec.stop() RUNS HERE, not on the caller's thread.
            #
            # It closes a PortAudio stream, and that is a C call that can block
            # indefinitely -- a mic whose permission was never granted, a device
            # yanked mid-session, a sample rate the driver would not take. This
            # used to run inline, which meant it ran on the MAIN loop, which meant
            # a stuck microphone froze the servo scheduler, the CoreS3 poll and
            # the researcher's keys all at once. Ctrl-C could not even reach it:
            # the interpreter cannot deliver a signal while a C extension holds
            # the thread, so the only way out was killing the process -- with
            # torque still on.
            #
            # Off here, the worst case is a thread that never finishes. The flow's
            # STT_HARD_TIMEOUT_S ceiling then does its job and the session
            # continues into S8, which is what it is for.
            text, nsp, alp = "", 1.0, -9.0
            try:
                wav = self.rec.stop(wav_path or "/tmp/noticebot_ptt.wav")
                if wav and self.whisper:
                    try:
                        text, nsp, alp = self.whisper.transcribe(wav)
                    except Exception as e:
                        print(f"[stt] transcribe failed: {e}")
                ok, why = transcript_usable(text, nsp, alp)
                self.last = (text, ok, why, "whisper")
                print(f"[stt] {text!r}  usable={ok} ({why})  "
                      f"no_speech={nsp:.2f} logprob={alp:.2f}")
                self.on_transcript(text, "whisper")
            finally:
                self.busy = False   # in a finally: a crash here must not wedge
                                    # the flow into never timing out

        threading.Thread(target=work, daemon=True).start()

    def manual(self, text):
        """The researcher typed it. Goes through the SAME door as Whisper, so it
        obeys the same usability rules and the same state machine path."""
        ok, why = transcript_usable(text)
        self.last = (text, ok, why, "manual")
        print(f"[stt] manual {text!r} usable={ok} ({why})")
        self.on_transcript(text, "manual")


if __name__ == "__main__":
    got = threading.Event()
    s = STT(lambda t, src: (print(f"-> {src}: {t!r}"), got.set()))
    if not s.start():
        sys.exit("no microphone available")
    print("recording 3 s -- say something like 'watch the mug'")
    for _ in range(30):
        time.sleep(0.1)
        print(f"\r  level {s.level:3d}", end="", flush=True)
    print()
    s.stop_and_transcribe()
    got.wait(60)
