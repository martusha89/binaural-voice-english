# binaural-voice: TTS at your ear and around your head

**English** | [简体中文](README.zh-CN.md) | [日本語](README.ja.md)

English edition of [Saekisui/binaural-voice](https://github.com/Saekisui/binaural-voice). Converts mono speech into **binaural stereo**, using measured Neumann KU100 **near-field** head-related impulse responses (HRIRs), starting at 25 cm. Position tags let the voice move gradually around your head as it speaks.

**Listen with headphones.** The near-ear bass response and large differences between ears come from measurements, rather than volume panning alone. Movement advances during speech and pauses during silence. Without position tags, a short movement pattern is chosen randomly.

This project is a Python command-line tool, **not a native MCP server**. You can call it from your application or an agent's local tool runner.

## Setup

Use Python 3.10 or newer. From the repository directory:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python binaural_voice.py --help
python speak.py --help
```

On Windows, use `py -3 -m venv .venv`, then `.venv\Scripts\activate` in Command Prompt. FFmpeg is optional, for converting other audio formats. Rendering existing WAVs requires no account or network access. `speak.py` sends your script to ElevenLabs and uses your paid account's credits; check its current pricing and voice access first.

## Quick start

Render an existing TTS recording or other voice WAV:

```bash
ffmpeg -i voice.mp3 -ac 1 voice.wav
python binaural_voice.py voice.wav out.wav
python binaural_voice.py voice.wav out.wav '[{"t":0,"tag":"right"},{"t":2.4,"tag":"behind"}]'
```

Each `t` is the time in seconds at which movement toward the tag begins. Cue times must fall within the recording. The renderer accepts mono or stereo WAVs (stereo is averaged to mono), 8–96 kHz, from 0.01 seconds to 10 minutes. It writes 16-bit stereo WAV at the input sample rate, including the short convolution tail. Input and output paths must differ. At most 1000 cues are accepted; unknown tags and malformed input produce English errors.

To generate and render a tagged script with ElevenLabs:

```bash
export ELEVENLABS_API_KEY='your-api-key'
export ELEVENLABS_VOICE_ID='your-voice-id'
python speak.py "[right][whispers] Don't move. [behind] I'm right behind you. [left] And now I'm here." out.wav
```

In PowerShell, set environment variables using `$env:ELEVENLABS_API_KEY = 'your-api-key'` and `$env:ELEVENLABS_VOICE_ID = 'your-voice-id'`. Keep credentials out of scripts and source control.

The default format is `pcm_24000`. Use `--format pcm_44100` or `ELEVENLABS_FORMAT=pcm_44100` for 44.1 kHz if supported by your ElevenLabs plan. Available CLI formats are 16, 22.05, 24, 44.1 and 48 kHz PCM; account availability varies. The model is `eleven_v3`. Scripts are limited to 5000 characters including tags; provider limits may be lower. For a stereo audio attachment:

```bash
ffmpeg -i out.wav -c:a aac -b:a 192k out.m4a
```

Use a delivery format that preserves stereo: a service that downmixes voice notes to mono loses the binaural effect.

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

The import-safe `speak.split_tags(text)` and `speak.aligned_cues(text, marks, alignment)` functions implement these steps. Alignment must match the exact script characters, with finite, ordered timestamps. A mismatch stops with an error rather than silently substituting random movement. A provider request may already have incurred a charge if its response fails validation.

An agent tool description can say:

> The listener is wearing headphones. Optional position tags are [left], [right], [behind], [front], [close], and [away]. Position tags are removed before speech synthesis. At the start, a position tag sets your location; later tags move you gradually while you speak. Ear-to-ear motion passes behind the listener. Place tags where the words call for movement, and omit them for a random movement pattern.

Call the CLI using an argument array rather than constructing a shell command from generated text. The application must handle permissions, credentials, file delivery, and any MCP wrapper itself. This is offline rendering, not a real-time call processor. Upstream reports roughly one second to render 16 seconds of speech on an M3 Pro Mac; performance depends on your machine.

## Design notes from upstream listening tests

- Upstream preferred measured near-field KU100 rendering in informal blind comparisons against Web Audio `PannerNode` HRTF and browser Resonance Audio. This is a subjective observation, not a universal perceptual guarantee. Those browser paths lack the same measured near-field response; distance attenuation alone does not reproduce it.
- Moving during silence hides the journey and makes the next phrase sound as if it jumped. Speech energy therefore controls movement progress: 10 ms frames, a 5% peak RMS threshold, gaps shorter than 0.4 seconds bridged, and a 0.2-second smoothing window.
- Constant sinusoidal ear-to-ear panning sounds mechanical. Short routes with pauses and subtle local drift sound more natural to the upstream author.
- Upstream observed inaccurate initial `eleven_v3` timestamps around opening performance tags; later timestamps were closer to the audio. Speech energy detects pauses independently of provider timestamps.
- Each source-distance SOFA dataset was normalized separately. The official gain factors must be restored for natural distance differences.
- Several short random routes avoid giving every short voice message the same opening choreography.

## Tuning

Constants near the top of `binaural_voice.py` control the default positions and distances (`PLACES`), voiced seconds per half turn (`HALF_TURN`), distance transition time (`DIST_MOVE`), and subtle drift (`DRIFT_DEG`, `DRIFT_M`). Random routes are defined by `random_cues()` inside `render()`. Rendering uses 1024-sample Hann windows with 50% overlap, azimuth rounded to the nearest measured degree, distance interpolation, and gain-corrected HRIR convolution. Output loudness is matched to the source and peaks limited to about −1 dBFS. Silence remains silence.

## Limitations

- Headphones are required; only the horizontal plane is modeled, with no elevation.
- The nearest measured distance is 25 cm.
- Generic dummy-head responses are not personalized to your ears. Some people may hear front/back reversals.
- Clean TTS whispers do not recreate a real ASMR performer's mouth and breath sounds.
- At 24 kHz, the highest representable frequency is 12 kHz.
- This edition has offline automated checks; it does not claim a new listening study or live ElevenLabs verification.

## Offline checks

```bash
python -m unittest discover -s tests -v
```

Tests use synthetic WAVs and mocked provider responses, with no paid requests or credentials required.

## License, attribution and dataset

Based on binaural-voice by Saekisui - https://github.com/Saekisui/binaural-voice

The current source is **GNU AGPL-3.0 with the additional section 7(b) attribution term** in [NOTICE](NOTICE); see [LICENSE](LICENSE). Preserve the required attribution in source and appropriate legal notices. When distributing modified versions or offering modified software over a network, follow the AGPL's corresponding-source requirements. Older revisions through `1ea1816` were MIT; that is not the license of the current upstream edition used here.

The data in `hrir/` remains under **CC BY 4.0**, separately from the code:

> J. M. Arend, A. Neidhardt, C. Pörschmann. *Spherical Near-Field (NF) HRIR Compilation of the Neumann KU100.* Zenodo, 2020. [doi:10.5281/zenodo.4297951](https://doi.org/10.5281/zenodo.4297951).

`hrir/ku100_nearfield_circ360.npz` is a format conversion of `NFHRIR_CIRC360_SOFA`: horizontal plane, 1° spacing, distances 0.25 / 0.5 / 0.75 / 1 / 1.5 m, 48 kHz, 128 samples per response. Upstream converted SOFA to float32 NumPy NPZ without otherwise modifying the measurements. The official gain table comes from `NF_Datasets_Gains_infos.pdf` in the same record. This English edition leaves the bundled data unchanged.

Related papers:

- J. M. Arend, A. Neidhardt, C. Pörschmann, “Measurement and Perceptual Evaluation of a Spherical Near-Field HRTF Set,” *29th Tonmeistertagung*, 2016.
- C. Pörschmann, J. M. Arend, A. Neidhardt, “A Spherical Near-Field HRTF Set for Auralization and Psychoacoustic Research,” *142nd AES Convention*, 2017.

To repeat the original format conversion, install `h5py` separately and run beside the extracted SOFA files:

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
