#!/usr/bin/env python3
"""
Cut a film_s1.py master take into per-state stimulus clips.

    python3 tools/crop_s1.py TAKE_3m.mov film_log_3m_XXXX.json -o clips_3m

Finds the slate (triple 1568 Hz beep) in the take's audio to anchor the log's
monotonic timestamps to video time, then cuts every [start, end] span.
SOUND IS KEPT (the robot's designed sfx are part of the stimulus, decision
2026-08-08); the marker beeps sit exactly at the span boundaries, so the
default trim window (--trim-start 0.6 / --trim-end 0.15) excludes them while
keeping >= 0.5 s of stillness inside each clip. Use --mute to strip audio.

If auto-detection fails (noisy room, low volume), read the slate time off the
waveform (QuickTime/Audacity: the first triple beep) and pass --slate SECONDS.

Requires ffmpeg on PATH. Output: <shot>_<distance>_p<pass>.mp4 (H.264, no
audio) plus a cutlist.txt for the record.
"""
import argparse
import json
import math
import os
import struct
import subprocess
import sys
import tempfile
import wave

RATE = 8000  # analysis sample rate; plenty for tones under 2 kHz


def extract_audio(video, out_wav):
    r = subprocess.run(
        ["ffmpeg", "-y", "-i", video, "-vn", "-ac", "1", "-ar", str(RATE),
         "-c:a", "pcm_s16le", out_wav],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if r.returncode != 0 or not os.path.exists(out_wav):
        sys.exit("ffmpeg could not extract audio -- is ffmpeg installed and "
                 "does the take have an audio track? (If the camera recorded "
                 "no audio, re-run with --slate <seconds> read off some other "
                 "sync event.)")


def goertzel_track(wav_path, freq, win=0.025):
    """Per-window normalized power at `freq` -> list of (t, ratio)."""
    with wave.open(wav_path, "rb") as w:
        n = w.getnframes()
        raw = w.readframes(n)
    samples = struct.unpack(f"<{n}h", raw)
    N = int(RATE * win)
    k = int(0.5 + N * freq / RATE)
    wr = 2.0 * math.cos(2.0 * math.pi * k / N)
    out = []
    for start in range(0, n - N, N):
        s0 = s1 = s2 = 0.0
        energy = 1e-9
        for i in range(start, start + N):
            x = samples[i]
            energy += x * x
            s0 = x + wr * s1 - s2
            s2, s1 = s1, s0
        power = s1 * s1 + s2 * s2 - wr * s1 * s2
        out.append((start / RATE, power / energy))
    return out


def find_slate(wav_path, freq):
    """First cluster of >=3 tone bursts ~0.22 s apart -> time of FIRST burst."""
    track = goertzel_track(wav_path, freq)
    ratios = sorted(r for _, r in track)
    floor = ratios[int(0.9 * len(ratios))]  # 90th percentile as noise floor
    thresh = max(floor * 6, ratios[-1] * 0.25)
    hot = [t for t, r in track if r > thresh]
    # collapse consecutive windows into burst onsets
    bursts = []
    for t in hot:
        if not bursts or t - bursts[-1][-1] > 0.06:
            bursts.append([t])
        else:
            bursts[-1].append(t)
    onsets = [b[0] for b in bursts]
    for i in range(len(onsets) - 2):
        d1, d2 = onsets[i + 1] - onsets[i], onsets[i + 2] - onsets[i + 1]
        if 0.12 < d1 < 0.45 and 0.12 < d2 < 0.45:
            return onsets[i]
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("take")
    ap.add_argument("log")
    ap.add_argument("-o", "--outdir", default="clips_out")
    ap.add_argument("--slate", type=float, default=None,
                    help="slate time in the video, seconds (skips detection)")
    ap.add_argument("--pass", dest="which_pass", type=int, default=None,
                    help="export only this pass (default: all)")
    ap.add_argument("--trim-start", type=float, default=0.6,
                    help="seconds cut from span start, past the START beep "
                         "(default 0.6; the take holds 1.2 s stillness there)")
    ap.add_argument("--trim-end", type=float, default=0.15,
                    help="seconds cut before span end, ahead of the END beep")
    ap.add_argument("--mute", action="store_true",
                    help="strip audio (default keeps it -- sfx are stimulus)")
    ap.add_argument("--crf", type=int, default=18)
    a = ap.parse_args()

    meta = json.load(open(a.log))
    events = meta["events"]
    slate_ev = next(e for e in events if e["event"] == "slate")

    if a.slate is not None:
        slate_video = a.slate
    else:
        with tempfile.TemporaryDirectory() as td:
            wav = os.path.join(td, "a.wav")
            print("extracting audio + searching for the slate "
                  f"({meta['tones']['slate'][0]:.0f} Hz x3)...")
            extract_audio(a.take, wav)
            slate_video = find_slate(wav, meta["tones"]["slate"][0])
        if slate_video is None:
            sys.exit("slate not found in audio. Read it off the waveform and "
                     "re-run with --slate SECONDS.")
        print(f"slate at {slate_video:.2f} s in the take")

    offset = slate_video - slate_ev["t_mono"]

    spans = {}
    for e in events:
        if e["event"] in ("start", "end"):
            spans.setdefault((e["shot"], e["pass_"]), {})[e["event"]] = \
                e["t_mono"] + offset

    os.makedirs(a.outdir, exist_ok=True)
    cutlist = []
    for (shot, pas), se in sorted(spans.items(), key=lambda kv: kv[1]["start"]):
        if "start" not in se or "end" not in se:
            print(f"  !! {shot} p{pas}: incomplete span, skipped")
            continue
        if a.which_pass and pas != a.which_pass:
            continue
        t0, t1 = se["start"] + a.trim_start, se["end"] - a.trim_end
        out = os.path.join(a.outdir,
                           f"{shot}_{meta['distance']}_p{pas}.mp4")
        audio = ["-an"] if a.mute else ["-c:a", "aac", "-b:a", "192k"]
        r = subprocess.run(
            ["ffmpeg", "-y", "-ss", f"{t0:.3f}", "-to", f"{t1:.3f}",
             "-i", a.take] + audio + ["-c:v", "libx264", "-crf", str(a.crf),
             "-preset", "slow", "-pix_fmt", "yuv420p",
             "-movflags", "+faststart", out],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        ok = r.returncode == 0
        print(f"  {shot:14} p{pas}  {t0:8.2f} -> {t1:8.2f}  "
              f"({t1-t0:5.1f} s)  {'ok' if ok else 'FFMPEG FAILED'}")
        cutlist.append(f"{shot}\tp{pas}\t{t0:.3f}\t{t1:.3f}\t{out}")

    with open(os.path.join(a.outdir, "cutlist.txt"), "w") as fh:
        fh.write(f"take={a.take}\nlog={a.log}\nslate_video={slate_video:.3f}\n"
                 f"offset={offset:.3f}\n")
        fh.write("\n".join(cutlist) + "\n")

    print(f"\n{len(cutlist)} clips -> {a.outdir}/  "
          f"({'audio stripped' if a.mute else 'audio KEPT -- sfx are stimulus'})")
    print("Before locking: LISTEN to every clip -- designed sfx audible, NO "
          "marker beep leaked at either end; check the LED reads on a laptop "
          "AND a phone (S1/S5B carry their state in the light alone); pick the "
          "cleanest pass per state; rename to neutral codes (no state names in "
          "filenames/URLs); for loops trim to whole cycles if desired: "
          f"{meta.get('led_cycles')}")


if __name__ == "__main__":
    main()
