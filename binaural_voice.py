"""Render a WAV as moving KU100 near-field binaural stereo. Use headphones.

Based on binaural-voice by Saekisui - https://github.com/Saekisui/binaural-voice
AGPL-3.0 with the additional attribution term in NOTICE; HRIR data: CC BY 4.0.
"""
import argparse
import json
import math
import os
import random
import stat
import struct
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
MAX_SECONDS = 120
MAX_FRAMES = 3_000_000
MAX_WAV_BYTES = 64 * 1024 * 1024


def preflight_output(path):
    """Reject existing destinations, including dangling links, before expensive work.

    This is an early usability check; exclusive creation at write time is required.
    """
    if os.path.lexists(path):
        raise ValueError("Output already exists; choose a fresh filename")
    parent = os.path.dirname(os.path.abspath(path))
    if not os.path.isdir(parent):
        raise ValueError("Output directory does not exist")
    if not os.access(parent, os.W_OK):
        raise ValueError("Output directory is not writable")


def preflight_wav(source):
    """Inspect bounded little-endian RIFF metadata before SciPy allocates samples."""
    info = os.fstat(source.fileno())
    if not stat.S_ISREG(info.st_mode) or not 12 <= info.st_size <= MAX_WAV_BYTES:
        raise ValueError("Input must be a regular WAV file of at most 64 MiB")
    header = source.read(12)
    if header[:4] != b"RIFF" or header[8:] != b"WAVE":
        raise ValueError("Only little-endian RIFF WAV is supported")
    end = struct.unpack_from("<I", header, 4)[0] + 8
    if not 12 <= end <= info.st_size:
        raise ValueError("Invalid or truncated WAV container")
    fmt = None
    frames = None
    while source.tell() < end:
        if end - source.tell() < 8:
            raise ValueError("Truncated WAV chunk header")
        chunk, size = struct.unpack("<4sI", source.read(8))
        chunk_end = source.tell() + size
        # SciPy and Python's wave writer may omit padding after final odd data.
        if chunk_end > end or (chunk_end + (size & 1) > end and chunk != b"data"):
            raise ValueError("Truncated WAV chunk")
        if chunk == b"fmt ":
            if fmt is not None or size < 16:
                raise ValueError("Invalid WAV format chunk")
            raw = source.read(min(size, 40))
            kind, channels, fs, rate, block, bits = struct.unpack_from("<HHIIHH", raw)
            if kind == 0xFFFE:
                if size < 40 or struct.unpack_from("<H", raw, 16)[0] < 22:
                    raise ValueError("Invalid extensible WAV format")
                if raw[28:40] != bytes.fromhex("00001000800000aa00389b71"):
                    raise ValueError("Unsupported extensible WAV subtype")
                kind = struct.unpack_from("<I", raw, 24)[0]
                valid_bits = struct.unpack_from("<H", raw, 18)[0]
                if not 1 <= valid_bits <= bits:
                    raise ValueError("Invalid extensible WAV bit depth")
            if channels not in (1, 2) or not 8000 <= fs <= 96000:
                raise ValueError("WAV must be mono/stereo at 8000–96000 Hz")
            if not ((kind == 1 and bits in (8, 16, 24, 32)) or (kind == 3 and bits in (32, 64))):
                raise ValueError("WAV must use PCM 8/16/24/32-bit or IEEE float 32/64-bit")
            if block != channels * (bits // 8) or rate != fs * block:
                raise ValueError("Inconsistent WAV byte rate or block alignment")
            fmt = (fs, channels, block)
        elif chunk == b"data":
            if fmt is None or frames is not None or size % fmt[2]:
                raise ValueError("Invalid WAV data chunk")
            frames = size // fmt[2]
            if not math.ceil(fmt[0] / 100) <= frames <= min(fmt[0] * MAX_SECONDS, MAX_FRAMES):
                raise ValueError("WAV must be 0.01–120 seconds and at most 3,000,000 frames")
        source.seek(min(chunk_end + (size & 1), end))
    if frames is None:
        raise ValueError("WAV has no audio data")
    source.seek(0)
    return fmt[0], fmt[1], frames


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
        if isinstance(t, bool) or not isinstance(t, (int, float)) or not 0 <= t <= duration:
            raise ValueError("Cue times must be finite seconds within the audio duration")
        result.append({"t": t, "tag": tag})
    return result


def render(src, dst, tag_cues=None):
    """Render bounded WAV audio, averaging stereo input to mono.

    Motion advances during speech; azimuth and distance follow independent
    eased tracks. Overlapping Hann windows are convolved with interpolated HRIRs.
    """
    preflight_output(dst)
    if os.path.realpath(src) == os.path.realpath(dst):
        raise ValueError("Input and output paths must differ")
    tag_cues = [] if tag_cues is None else tag_cues
    with open(src, "rb") as source:
        expected_fs, channels, frames = preflight_wav(source)
        fs, x = wavfile.read(source)
    if fs != expected_fs or len(x) != frames or x.ndim not in (1, 2) or (1 if x.ndim == 1 else x.shape[1]) != channels:
        raise ValueError("Decoded WAV does not match its metadata")
    if np.issubdtype(x.dtype, np.floating) and np.any(np.abs(x) > 1):
        raise ValueError("Floating-point WAV samples must be within [-1, 1]")
    if np.issubdtype(x.dtype, np.integer):
        x = ((x.astype(np.float64) - 128) / 128 if x.dtype == np.uint8
             else x.astype(np.float64) / np.iinfo(x.dtype).max)
    x = x.astype(np.float64)
    if x.ndim not in (1, 2) or (x.ndim == 2 and x.shape[1] not in (1, 2)):
        raise ValueError("WAV must contain mono or stereo audio")
    if not 8000 <= fs <= 96000:
        raise ValueError("WAV sample rate must be between 8000 and 96000 Hz")
    if not math.ceil(fs / 100) <= len(x) <= min(fs * MAX_SECONDS, MAX_FRAMES):
        raise ValueError("WAV must be 0.01–120 seconds and at most 3,000,000 frames")
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

    if not np.all(np.isfinite(out)):
        raise ValueError("Rendered audio contains nonfinite samples")
    if np.any(out):
        out *= np.sqrt((x ** 2).mean() * 2 / (out ** 2).mean())
        out *= min(1.0, 0.89 / np.abs(out).max())
    if not np.all(np.isfinite(out)):
        raise ValueError("Rendered audio contains nonfinite samples")
    # Exclusive creation refuses existing files and symlinks even after a race.
    with open(dst, "xb") as destination:
        wavfile.write(destination, fs, np.int16(out * 32767))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("input", help="Input RIFF WAV (8–96 kHz, 0.01–120 seconds, at most 3,000,000 frames / 64 MiB)")
    parser.add_argument("output", help="New output 16-bit stereo WAV; existing paths are refused; use headphones")
    parser.add_argument("cues", nargs="?", default="[]", help='JSON array, e.g. [{"t":0,"tag":"right"},{"t":2.4,"tag":"behind"}]')
    args = parser.parse_args(argv)
    try:
        render(args.input, args.output, json.loads(args.cues))
    except (ValueError, OSError, EOFError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
