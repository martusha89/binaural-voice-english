"""Generate timestamped ElevenLabs speech and render headphone binaural WAV.

Based on binaural-voice by Saekisui - https://github.com/Saekisui/binaural-voice
AGPL-3.0 with the additional attribution term in NOTICE.
"""
import argparse
import base64
import binascii
import json
import math
import os
import re
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import wave

from binaural_voice import ALIASES, TAGS, render

TAG = re.compile(r"\[(" + "|".join((*TAGS, *ALIASES)) + r")\]")
FORMATS = ("pcm_16000", "pcm_22050", "pcm_24000", "pcm_44100", "pcm_48000")


def split_tags(text):
    """Strip position tags, preserving performance tags and character offsets."""
    tts, marks, last = "", [], 0
    for match in TAG.finditer(text):
        tts += text[last:match.start()]
        tag = match.group(1)
        marks.append((ALIASES.get(tag, tag), len(tts)))
        last = match.end()
    return tts + text[last:], marks


def aligned_cues(text, marks, alignment):
    """Reject mismatched alignment rather than silently randomizing movement."""
    if not isinstance(alignment, dict):
        raise ValueError("ElevenLabs returned no character alignment")
    starts = alignment.get("character_start_times_seconds", [])
    chars = alignment.get("characters", [])
    ends = alignment.get("character_end_times_seconds", [])
    if not text or not all(isinstance(v, list) for v in (starts, chars, ends)):
        raise ValueError("ElevenLabs returned invalid character alignment")
    if len(starts) != len(text) or len(ends) != len(text) or chars != list(text):
        raise ValueError("ElevenLabs character alignment does not match the script")
    if any(isinstance(t, bool) or not isinstance(t, (int, float)) or not math.isfinite(t) or t < 0 for t in starts + ends):
        raise ValueError("ElevenLabs returned invalid alignment times")
    if any(a > b for a, b in zip(starts, starts[1:])) or any(a > b for a, b in zip(starts, ends)):
        raise ValueError("ElevenLabs returned inconsistent alignment times")
    return [{"tag": tag, "t": starts[at] if at < len(starts) else ends[-1]} for tag, at in marks]


def speak(text, output, audio_format="pcm_24000"):
    if not isinstance(text, str) or not 1 <= len(text) <= 5000:
        raise ValueError("Script must contain 1–5000 characters, including tags")
    tts, marks = split_tags(text)
    if not tts.strip():
        raise ValueError("Script must contain spoken text")
    if len(marks) > 1000:
        raise ValueError("Script must contain at most 1000 position tags")
    if audio_format not in FORMATS:
        raise ValueError("Unsupported ELEVENLABS_FORMAT; choose " + ", ".join(FORMATS))
    key, voice = os.environ.get("ELEVENLABS_API_KEY"), os.environ.get("ELEVENLABS_VOICE_ID")
    if not key or not voice:
        raise ValueError("Set ELEVENLABS_API_KEY and ELEVENLABS_VOICE_ID before generating speech")
    voice = urllib.parse.quote(voice, safe="")
    request = urllib.request.Request(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice}/with-timestamps?output_format={audio_format}",
        data=json.dumps({"text": tts, "model_id": "eleven_v3"}).encode(),
        headers={"Content-Type": "application/json", "xi-api-key": key},
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            data = json.load(response)
    except urllib.error.HTTPError as exc:
        raise ValueError(f"ElevenLabs request failed (HTTP {exc.code}); check account access and quota") from None
    if not isinstance(data, dict):
        raise ValueError("ElevenLabs returned an invalid response")
    cues = aligned_cues(tts, marks, data.get("alignment"))
    try:
        pcm = base64.b64decode(data["audio_base64"], validate=True)
    except (KeyError, TypeError, binascii.Error):
        raise ValueError("ElevenLabs returned invalid PCM audio") from None
    fs = int(audio_format.split("_")[1])
    if len(pcm) % 2 or not fs // 100 <= len(pcm) // 2 <= fs * 600:
        raise ValueError("ElevenLabs PCM duration must be between 0.01 and 600 seconds")
    with tempfile.TemporaryDirectory(prefix="binaural-voice-") as directory:
        source = os.path.join(directory, "speech.wav")
        with wave.open(source, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(fs)
            wav.writeframes(pcm)
        render(source, output, cues)
    return cues


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("text", help="Script with optional [left], [right], [behind], [front], [close], [away] tags")
    parser.add_argument("output", help="Output stereo WAV")
    parser.add_argument("--format", choices=FORMATS, default=os.environ.get("ELEVENLABS_FORMAT", "pcm_24000"), help="PCM format (default: ELEVENLABS_FORMAT or pcm_24000); availability depends on your plan")
    args = parser.parse_args(argv)
    try:
        cues = speak(args.text, args.output, args.format)
    except (ValueError, OSError, EOFError) as exc:
        parser.error(str(exc))
    print("cues:", json.dumps(cues))


if __name__ == "__main__":
    main()
