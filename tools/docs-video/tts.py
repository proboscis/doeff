# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Makes the narration of a page's video with OpenAI's text-to-speech API.

Reads src/pages/<page>/narration.json (a list of {"id", "text"}), has OpenAI's speech endpoint say each line,
writes public/<page>/<id>.wav, and writes src/pages/<page>/durations.json (seconds per line), which narrate()
in src/kit/narrated.tsx reads to stretch each headline's slot to its line. The voice is always a hosted model:
no local TTS model is used, by the maintainer's decision (2026-10-25).

Usage (from tools/docs-video): OPENAI_API_KEY=... uv run tts.py <page> [<line id>...]
  Line ids given: say only those lines again and keep the other durations.
"""

import concurrent.futures
import io
import json
import os
import re
import sys
import urllib.error
import urllib.request
import wave
from pathlib import Path

ENDPOINT = "https://api.openai.com/v1/audio/speech"
MODEL = "gpt-4o-mini-tts"
VOICE = "marin"
INSTRUCTIONS = (
    "You are the narrator of a short technical explainer video about a Python library. Speak in a calm, clear, "
    "friendly voice at an even, unhurried pace, like a good conference talk. Read code names plainly."
)

# How the scripts' words are spelled for the voice: the library's name, the decorator, and single letters.
SPOKEN = [
    (r"\bdoeff\b", "doe-eff"),
    (r"\bat-do\b", "at do"),
    (r"\bb's k\b", "bee's kay"),
    (r"\bk\b", "kay"),
    (r"\bfor a\.", "for ay."),
    (r"\bI/O\b", "I O"),
]


def spoken(text: str) -> str:
    """Spell a script line the way the voice should say it (the script keeps the written form)."""
    for pattern, said in SPOKEN:
        text = re.sub(pattern, said, text)
    return text


def say(key: str, text: str) -> bytes:
    """Get one line of narration as WAV bytes from the speech endpoint."""
    body = json.dumps(
        {
            "model": MODEL,
            "voice": VOICE,
            "input": spoken(text),
            "instructions": INSTRUCTIONS,
            "response_format": "wav",
        }
    ).encode()
    request = urllib.request.Request(
        ENDPOINT,
        data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return response.read()
    except urllib.error.HTTPError as error:
        raise SystemExit(f"speech endpoint: HTTP {error.code}: {error.read()[:300]!r}") from error


def seconds_of(audio: bytes) -> float:
    """The length of a WAV in seconds, so the line's headline slot can be stretched to fit it."""
    with wave.open(io.BytesIO(audio)) as reader:
        frames = reader.getnframes()
        # The endpoint streams its WAV, so the header may not carry the length; count the data instead.
        if frames in (
            0,
            0x7FFFFFFF,
        ) or frames * reader.getsampwidth() * reader.getnchannels() > len(audio):
            frames = (len(audio) - 44) // (reader.getsampwidth() * reader.getnchannels())
        return round(frames / reader.getframerate(), 2)


def main() -> None:
    """Say the lines of one page and record how long each one lasts."""
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise SystemExit("OPENAI_API_KEY is not set")
    page, only = sys.argv[1], set(sys.argv[2:])
    here = Path(__file__).resolve().parent
    source = here / "src" / "pages" / page
    out = here / "public" / page
    out.mkdir(parents=True, exist_ok=True)
    durations = source / "durations.json"
    seconds = json.loads(durations.read_text()) if only and durations.exists() else {}
    lines = [
        line
        for line in json.loads((source / "narration.json").read_text())
        if not only or line["id"] in only
    ]
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        audios = list(pool.map(lambda line: say(key, line["text"]), lines))
    for line, audio in zip(lines, audios, strict=True):
        (out / f"{line['id']}.wav").write_bytes(audio)
        seconds[line["id"]] = seconds_of(audio)
        print(line["id"], seconds[line["id"]], flush=True)
    order = [line["id"] for line in json.loads((source / "narration.json").read_text())]
    durations.write_text(
        json.dumps({i: seconds[i] for i in order if i in seconds}, indent=1) + "\n"
    )
    print("total", round(sum(seconds.values()), 1), "s in", len(seconds), "lines")


if __name__ == "__main__":
    main()
