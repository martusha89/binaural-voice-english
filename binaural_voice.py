"""Render a WAV as moving KU100 near-field binaural stereo. Use headphones.

Based on binaural-voice by Saekisui - https://github.com/Saekisui/binaural-voice
AGPL-3.0 with the additional attribution term in NOTICE; HRIR data: CC BY 4.0.
"""
import argparse
import json
import math
import os
import random
import numpy as np
from scipy.io import wavfile
from scipy.signal import fftconvolve, resample_poly

# Azimuth is counterclockwise: front=0, left=90, behind=180, right=270.
PLACES = {"left": (90, 0.25), "right": (270, 0.25), "behind": (180, 0.31), "front": (0, 0.42)}
ALIASES = {"左耳": "left", "右耳": "right", "脑后": "behind", "面前": "front", "贴近": "close", "退开": "away"}
TAGS = tuple(PLACES) + ("close", "away")
NEAR, FAR = 0.25, 0.5
HALF_TURN = 4.5  # Voiced seconds per half turn.
DIST_MOVE = 1.5  # Voiced seconds per distance change.
DRIFT_DEG, DRIFT_M = 5, 0.015
# Restore the official gains removed by per-distance dataset normalization.
GAINS = [1.00, 0.33, 0.25, 0.16, 0.095]


def validate_cues(cues, duration):
    """Accept at most 1000 position cues within the source audio."""
    if not isinstance(cues, list) or len(cues) > 1000:
        raise ValueError("Cues must be a JSON array with at most 1000 entries")
    result = []
    for cue in cues:
        if not isinstance(cue, dict) or set(cue) != {"t", "tag"}:
            raise ValueError("Each cue must contain exactly 't' and 'tag'")
        t, tag = cue["t"], cue["tag"]
        if not isinstance(tag, str):
            raise ValueError("Cue tag must be a string")
        tag = ALIASES.get(tag, tag)
        if tag not in TAGS:
            raise ValueError("Unknown position tag: " + tag)
        if isinstance(t, bool) or not isinstance(t, (int, float)) or not math.isfinite(t) or not 0 <= t <= duration:
            raise ValueError("Cue times must be finite seconds within the audio duration")
        result.append({"t": t, "tag": tag})
    return result


def render(src, dst, tag_cues=None):
    """Render up to ten minutes of WAV audio, averaging stereo input to mono.

    Motion advances during speech; azimuth and distance follow independent
    eased tracks. Overlapping Hann windows are convolved with interpolated HRIRs.
    """
    if os.path.realpath(src) == os.path.realpath(dst):
        raise ValueError("Input and output paths must differ")
    tag_cues = [] if tag_cues is None else tag_cues
    fs, x = wavfile.read(src)
    if np.issubdtype(x.dtype, np.integer):
        x = ((x.astype(np.float64) - 128) / 128 if x.dtype == np.uint8
             else x.astype(np.float64) / np.iinfo(x.dtype).max)
    x = x.astype(np.float64)
    if x.ndim not in (1, 2) or (x.ndim == 2 and x.shape[1] not in (1, 2)):
        raise ValueError("WAV must contain mono or stereo audio")
    if not 8000 <= fs <= 96000:
        raise ValueError("WAV sample rate must be between 8000 and 96000 Hz")
    if len(x) < fs // 100 or len(x) > fs * 600:
        raise ValueError("WAV duration must be between 0.01 and 600 seconds")
    if not np.all(np.isfinite(x)):
        raise ValueError("WAV samples must be finite")
    if x.ndim > 1:
        x = x.mean(axis=1)

    tag_cues = validate_cues(tag_cues, len(x) / fs)
    hop = fs // 100
    rms = np.array([np.sqrt((x[i:i + hop] ** 2).mean()) for i in range(0, len(x), hop)])
    voiced = rms > rms.max() * 0.05
    i = 0
    while i < len(voiced):
        j = i
        while j < len(voiced) and not voiced[j]:
            j += 1
        if 0 < i and j < len(voiced) and j - i < 40:
            voiced[i:j] = True
        i = max(j, i + 1)
    speed = np.convolve(voiced.astype(float), np.ones(20) / 20, mode="full")[9:9 + len(voiced)]
    tau = np.concatenate([[0], np.cumsum(speed)]) * 0.01
    speech_time = lambda t: np.interp(t, np.arange(len(tau)) * 0.01, tau)
    T = tau[-1]

    def random_cues():
        ear = random.choice(["left", "right"])
        other = "right" if ear == "left" else "left"
        kind = random.choices(["stay", "cross", "from-behind", "from-front"], weights=[1, 2, 1, 1])[0]
        if kind == "stay":
            return [(0, ear)]
        if kind == "cross":
            return [(0, ear), (0.2 * T, "behind"), (0.6 * T, other)] if T > 6 else [(0, ear), (0.3 * T, other)]
        return [(0, "behind" if kind == "from-behind" else "front"), (0.35 * T, ear)]

    cues = [(float(speech_time(c["t"])), c["tag"]) for c in tag_cues if c["tag"] in PLACES or c["tag"] in ("close", "away")]
    if not cues:
        cues = random_cues()
    cues.sort(key=lambda c: c[0])
    if cues[0][1] in PLACES and cues[0][0] < 0.3:
        start = PLACES[cues.pop(0)[1]]
    else:
        start = PLACES[random.choice(["left", "right"])]

    def az_path(a0, a1):
        a0 %= 360
        short = (a1 - a0 + 180) % 360 - 180
        return short if abs(short) <= 150 else a1 - a0

    az_segs, d_segs = [], []
    def track_at(segs, s, init):
        active = [g for g in segs if g[0] <= s]
        if not active:
            return init
        s0, s1, v0, dv = active[-1]
        return v0 + dv * (1 - np.cos(np.pi * min(1.0, (s - s0) / (s1 - s0)))) / 2
    pos_at = lambda s: (track_at(az_segs, s, start[0]), track_at(d_segs, s, start[1]))

    for s, tag in cues:
        az, d = pos_at(s)
        if tag in PLACES:
            target_az, target_d = PLACES[tag]
            da = az_path(az, target_az)
            nominal = abs(da) / 180 * HALF_TURN if abs(da) > 1 else DIST_MOVE
        else:
            target_d, da, nominal = (NEAR if tag == "close" else FAR), 0.0, DIST_MOVE
        dur = max(min(nominal, 0.9 * (T - s)), 0.6 * nominal)
        if abs(da) > 1:
            az_segs.append((s, s + dur, az, da))
        if abs(target_d - d) > 0.01:
            d_segs.append((s, s + dur, d, target_d - d))
    phase = random.uniform(0, 2 * np.pi)

    def position(t):
        az, d = pos_at(speech_time(t))
        return (az + DRIFT_DEG * np.sin(2 * np.pi * t / 4.3 + phase) + 2 * np.sin(2 * np.pi * t / 1.9 + 2 * phase),
                d + DRIFT_M * np.sin(2 * np.pi * t / 3.7 + 3 * phase))

    data = np.load(os.path.join(os.path.dirname(os.path.abspath(__file__)), "hrir", "ku100_nearfield_circ360.npz"))
    dists = data["dists"]
    H = resample_poly(data["ir"].astype(np.float64), fs, int(data["fs"]), axis=3) * np.array(GAINS)[:, None, None, None]

    def hrir_at(az, d):
        a = int(round(az)) % 360
        d = min(max(d, dists[0]), dists[-1])
        k = min(np.searchsorted(dists, d, side="right") - 1, len(dists) - 2)
        w = (d - dists[k]) / (dists[k + 1] - dists[k])
        return (1 - w) * H[k, a] + w * H[k + 1, a]

    N, Hop = 1024, 512
    win = np.hanning(N + 1)[:N]
    L = H.shape[3]
    out = np.zeros((len(x) + 2 * N + L, 2))
    for s in range(-Hop, len(x), Hop):
        seg = np.zeros(N)
        lo, hi = max(s, 0), min(s + N, len(x))
        seg[lo - s:hi - s] = x[lo:hi]
        h = hrir_at(*position((s + N / 2) / fs))
        for e in (0, 1):
            y = fftconvolve(seg * win, h[e])
            out[s + Hop:s + Hop + len(y), e] += y
    out = out[Hop:Hop + len(x) + L]

    if np.any(out):
        out *= np.sqrt((x ** 2).mean() * 2 / (out ** 2).mean())
        out *= min(1.0, 0.89 / np.abs(out).max())
    wavfile.write(dst, fs, np.int16(out * 32767))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("input", help="Input mono/stereo WAV (8–96 kHz, 0.01–600 seconds)")
    parser.add_argument("output", help="Output 16-bit stereo WAV; use headphones")
    parser.add_argument("cues", nargs="?", default="[]", help='JSON array, e.g. [{"t":0,"tag":"right"},{"t":2.4,"tag":"behind"}]')
    args = parser.parse_args(argv)
    try:
        render(args.input, args.output, json.loads(args.cues))
    except (ValueError, OSError, EOFError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
