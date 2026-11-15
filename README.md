<p align="center">
  <img src="assets/banner.svg" alt="Binaural Voice — English Edition. Speech, with a sense of space." width="100%">
</p>

<p align="center">
  <a href="https://github.com/martusha89/binaural-voice-english/actions/workflows/tests.yml"><img src="https://github.com/martusha89/binaural-voice-english/actions/workflows/tests.yml/badge.svg" alt="Offline test status"></a>
  <img src="https://img.shields.io/badge/Python-3.10%2B-9b7bea?style=flat-square" alt="Python 3.10 or newer">
  <a href="LICENSE"><img src="https://img.shields.io/badge/Code-AGPL--3.0-7964b4?style=flat-square" alt="Code: AGPL-3.0; additional attribution term in NOTICE"></a>
  <a href="https://doi.org/10.5281/zenodo.4297951"><img src="https://img.shields.io/badge/HRIR_data-CC_BY_4.0-31998f?style=flat-square" alt="HRIR data: CC BY 4.0"></a>
</p>

<p align="center">
  <strong>English</strong> · <a href="README.zh-CN.md">简体中文</a> · <a href="README.ja.md">日本語</a>
</p>

<p align="center">
  <a href="#quick-start">Quick start</a> · <a href="#position-tags">Position tags</a> · <a href="#application-and-agent-integration">For developers</a> · <a href="#limits-and-safe-use">Safe use</a> · <a href="#license-attribution-and-dataset">Credits</a>
</p>

---

## Give your voice somewhere to be

English edition of [Saekisui/binaural-voice](https://github.com/Saekisui/binaural-voice). Converts mono speech into **binaural stereo**, using measured Neumann KU100 **near-field** head-related impulse responses (HRIRs), starting at 25 cm. Position tags let the voice move gradually around your head as it speaks.

| Close enough to feel spatial | Movement that follows speech | Your voice, your workflow |
| :--- | :--- | :--- |
| Measured **25 cm near-field HRIRs**, not just left/right volume panning. | Routes advance while speech is sounding, rather than jumping across pauses. | Render an existing WAV locally, or generate tagged speech with **ElevenLabs**. |

> [!IMPORTANT]
> **Headphones required.** Start at a comfortable volume. Keep the output stereo—mono delivery removes the binaural effect.

This project is a Python command-line tool, **not a native MCP server**. You can call it from your application or an agent's local tool runner.

## Quick start

### 1 · Set up once

```bash
git clone https://github.com/martusha89/binaural-voice-english.git
cd binaural-voice-english
```

Use Python 3.10 or newer. From the repository directory:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip setuptools
python -m pip install -r requirements.txt
python binaural_voice.py --help
python speak.py --help
```

<details>
<summary><strong>Windows setup</strong></summary>

In Command Prompt:

```bat
py -3 -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip setuptools
python -m pip install -r requirements.txt
```

</details>

FFmpeg is optional, for converting other audio formats. **Rendering existing WAVs needs no account or network access.** Optional ElevenLabs synthesis sends your script to the provider and uses your credits.

### 2 · Choose your route

#### A. You already have audio — local rendering

Render an existing TTS recording or other voice WAV:

```bash
ffmpeg -i voice.mp3 -ac 1 voice.wav
python binaural_voice.py voice.wav out.wav
```

Want to direct the movement? Use a different, fresh output filename:

```bash
python binaural_voice.py voice.wav directed.wav '[{"t":0,"tag":"right"},{"t":2.4,"tag":"behind"}]'
```

Each `t` is the time in seconds at which movement toward the tag begins; use a recording longer than the last cue. No cues? A short movement pattern is chosen for you.

#### B. Start with words — ElevenLabs synthesis

```bash
export ELEVENLABS_API_KEY='your-api-key'
export ELEVENLABS_VOICE_ID='your-voice-id'
python speak.py "[right][whispers] Take a moment. There's no hurry. [behind][whispers] Let the rest of the room fall quiet, just for a little while. [left][whispers] That's better. Stay a moment longer." speech.wav
```

In PowerShell, set environment variables using `$env:ELEVENLABS_API_KEY = 'your-api-key'` and `$env:ELEVENLABS_VOICE_ID = 'your-voice-id'`. Keep credentials out of scripts and source control.

<details>
<summary><strong>ElevenLabs formats, limits and billing</strong></summary>

The default format is `pcm_24000`. Use `--format pcm_44100` or `ELEVENLABS_FORMAT=pcm_44100` for 44.1 kHz if supported by your ElevenLabs plan. Available CLI formats are 16, 22.05, 24, 44.1 and 48 kHz PCM; account availability varies. The model is `eleven_v3`. Scripts are limited to 5000 characters including tags; provider limits may be lower. The same duration/frame limits apply to generated audio. Provider responses are limited to 12 MiB, and encoded PCM size is checked before decoding. The output path is checked before requesting paid speech, then checked again during rendering; a concurrent file creation or later validation failure can still occur after a charge.

</details>

### 3 · Keep it stereo

The result is a stereo WAV. For a smaller audio attachment, convert your chosen output with FFmpeg:

```bash
ffmpeg -i out.wav -c:a aac -b:a 192k out.m4a
```

Use a delivery format that preserves stereo: a service that downmixes voice notes to mono loses the binaural effect.

> [!TIP]
> **Choose a fresh filename each time.** Existing output files are deliberately protected from overwriting.

## Position tags

| English tag | Compatible Chinese tag | Destination |
|---|---|---|
| `[left]` / `[right]` | `[左耳]` / `[右耳]` | Beside the ear, 25 cm |
| `[behind]` | `[脑后]` | Behind the head, about 31 cm |
| `[front]` | `[面前]` | In front, about 42 cm |
| `[close]` | `[贴近]` | Distance only: move to 25 cm |
| `[away]` | `[退开]` | Distance only: move to 50 cm |

Tags are lowercase and case-sensitive. Use bare names such as `"right"` in cue JSON. Chinese names remain accepted in both scripts and JSON.

- A position tag at the start sets the starting position. In mid-sentence it begins a gradual move.
- Ear-to-ear movement passes behind the head rather than across the face.
- A half turn takes 4.5 voiced seconds. Short recordings may accelerate the motion, but it remains smooth.
- A new cue redirects from the current position. Distance-only tags do not interrupt the azimuth track.
- A destination tag restores that place's default distance. `[close]` has no effect if already at the minimum 25 cm.
- Without tags, the renderer randomly stays beside an ear, crosses between ears (with a rear stop for longer clips), approaches from behind, or approaches from the front. Subtle drift and random selection mean renders are not bit-for-bit repeatable.

## Application and agent integration

1. Remove position tags before synthesis and record their character offsets in the stripped text. Keep performance tags such as `[whispers]` for the TTS model.
2. Request audio and character alignment from `/v1/text-to-speech/{voice_id}/with-timestamps`.
3. Map each position tag to the start time of the following character. A trailing tag maps to the final character's end.
4. Render the mono WAV with `binaural_voice.py`, or import `render(input_path, output_path, cues)`.

The import-safe `speak.split_tags(text)` and `speak.aligned_cues(text, marks, alignment, duration=None)` functions implement these steps. Alignment must match the exact script characters, with finite, ordered start and end timestamps. `speak()` checks every timestamp against decoded audio duration, even when no position tags are present. It tolerates up to 50 ms past the audio end for provider rounding and clamps those times to the audio end; pass `duration` when using the helper directly for the same check. A mismatch stops with an error rather than silently substituting random movement. A provider request may already have incurred a charge if its response fails validation.

An agent tool description can say:

> The listener is wearing headphones. Optional position tags are [left], [right], [behind], [front], [close], and [away]. Position tags are removed before speech synthesis. At the start, a position tag sets your location; later tags move you gradually while you speak. Ear-to-ear motion passes behind the listener. Place tags where the words call for movement, and omit them for a random movement pattern.

Call the CLI using an argument array rather than constructing a shell command from generated text. The application must handle permissions, credentials, file delivery, and any MCP wrapper itself. This is offline rendering, not a real-time call processor. Upstream reports roughly one second to render 16 seconds of speech on an M3 Pro Mac; performance depends on your machine.

## Design notes from upstream listening tests

<details>
<summary><strong>Why near-field measurements and speech-aware movement?</strong></summary>

- Upstream preferred measured near-field KU100 rendering in informal blind comparisons against Web Audio `PannerNode` HRTF and browser Resonance Audio. This is a subjective observation, not a universal perceptual guarantee. Those browser paths lack the same measured near-field response; distance attenuation alone does not reproduce it.
- Moving during silence hides the journey and makes the next phrase sound as if it jumped. Speech energy therefore controls movement progress: 10 ms frames, a 5% peak RMS threshold, gaps shorter than 0.4 seconds bridged, and a 0.2-second smoothing window.
- Constant sinusoidal ear-to-ear panning sounds mechanical. Short routes with pauses and subtle local drift sound more natural to the upstream author.
- Upstream observed inaccurate initial `eleven_v3` timestamps around opening performance tags; later timestamps were closer to the audio. Speech energy detects pauses independently of provider timestamps.
- Each source-distance SOFA dataset was normalized separately. The official gain factors must be restored for natural distance differences.
- Several short random routes avoid giving every short voice message the same opening choreography.

</details>

## Tuning

Constants near the top of `binaural_voice.py` control the default positions and distances (`PLACES`), voiced seconds per half turn (`HALF_TURN`), distance transition time (`DIST_MOVE`), and subtle drift (`DRIFT_DEG`, `DRIFT_M`). Random routes are defined by `random_cues()` inside `render()`. Rendering uses 1024-sample Hann windows with 50% overlap, azimuth rounded to the nearest measured degree, distance interpolation, and gain-corrected HRIR convolution. Output loudness is matched to the source and peaks limited to about −1 dBFS. Silence remains silence.

## Limits and safe use

| Input / output | Supported range |
| :--- | :--- |
| Input | Mono or stereo WAV; stereo is averaged to mono |
| Sample rate | 8–96 kHz |
| Duration | 0.01–120 seconds **and** at most 3,000,000 frames |
| Input file | At most 64 MiB; metadata checked before loading samples |
| Output | 16-bit stereo WAV at the input sample rate, plus a short convolution tail |
| Position cues | At most 1000; unknown tags and malformed input are rejected |
| File protection | Existing paths—including hard links and symlinks—are refused; final creation is exclusive |

Both duration and frame limits apply: at 48 kHz the maximum is **62.5 seconds**, and at 96 kHz it is **31.25 seconds**.

<details>
<summary><strong>Supported WAV encodings</strong></summary>

Little-endian RIFF WAV with PCM 8/16/24/32-bit or IEEE float 32/64-bit audio, including these subtypes in WAVE_FORMAT_EXTENSIBLE. Float samples must be finite and within [-1, 1]. Compressed WAV, RF64 and big-endian RIFX are refused; convert them with FFmpeg first.

</details>

### What to expect

- Headphones are required; only the horizontal plane is modeled, with no elevation.
- The nearest measured distance is 25 cm.
- Generic dummy-head responses are not personalized to your ears. Some people may hear front/back reversals.
- Clean TTS whispers do not recreate a real ASMR performer's mouth and breath sounds.
- At 24 kHz, the highest representable frequency is 12 kHz.
- A live ElevenLabs synthesis → alignment → stereo rendering smoke test has passed, with listener confirmation. This is not a controlled listening study or a guarantee for every voice, account, or listener.

## Offline checks

```bash
python -m unittest discover -s tests -v
```

Tests use synthetic WAVs and mocked provider responses, with no paid requests or credentials required.

These resource limits bound local processing but do not provide a service sandbox. A hosted or MCP wrapper must additionally restrict input/output directories, enforce per-job memory/time limits, and control access to paid generation.

## License, attribution and dataset

Based on binaural-voice by Saekisui - https://github.com/Saekisui/binaural-voice

The current source is **GNU AGPL-3.0 with the additional section 7(b) attribution term** in [NOTICE](NOTICE); see [LICENSE](LICENSE). Preserve the required attribution in source and appropriate legal notices. When distributing modified versions or offering modified software over a network, follow the AGPL's corresponding-source requirements. Older revisions through `1ea1816` were MIT; that is not the license of the current upstream edition used here.

The data in `hrir/` remains under **CC BY 4.0**, separately from the code:

> J. M. Arend, A. Neidhardt, C. Pörschmann. *Spherical Near-Field (NF) HRIR Compilation of the Neumann KU100.* Zenodo, 2020. [doi:10.5281/zenodo.4297951](https://doi.org/10.5281/zenodo.4297951).

`hrir/ku100_nearfield_circ360.npz` is a format conversion of `NFHRIR_CIRC360_SOFA`: horizontal plane, 1° spacing, distances 0.25 / 0.5 / 0.75 / 1 / 1.5 m, 48 kHz, 128 samples per response. Upstream converted SOFA to float32 NumPy NPZ without otherwise modifying the measurements. The official gain table comes from `NF_Datasets_Gains_infos.pdf` in the same record. This English edition leaves the bundled data unchanged.

Related papers:

- J. M. Arend, A. Neidhardt, C. Pörschmann, “Measurement and Perceptual Evaluation of a Spherical Near-Field HRTF Set,” *29th Tonmeistertagung*, 2016.
- C. Pörschmann, J. M. Arend, A. Neidhardt, “A Spherical Near-Field HRTF Set for Auralization and Psychoacoustic Research,” *142nd AES Convention*, 2017.

<details>
<summary><strong>Reproduce the original dataset format conversion</strong></summary>

Install `h5py` separately and run beside the extracted SOFA files:

```python
import h5py
import numpy as np
responses = []
for distance in ["025", "050", "075", "100", "150"]:
    with h5py.File(f"HRIR_CIRC360_NF{distance}.sofa") as source:
        responses.append(source["Data.IR"][:])
np.savez_compressed(
    "hrir/ku100_nearfield_circ360.npz",
    ir=np.stack(responses).astype(np.float32), fs=48000,
    dists=np.array([0.25, 0.5, 0.75, 1.0, 1.5]),
)
```

</details>

---

<p align="center"><strong>One voice. A little more room.</strong><br>English edition maintained by <a href="https://github.com/martusha89">martusha89</a> · Original renderer by <a href="https://github.com/Saekisui">Saekisui</a></p>
