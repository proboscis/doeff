"""Tests for the chapter videos of the docs (section 23 of initiative doeff-publish).

A chapter page writes a video as `[![<description>](assets/videos/<name>.jpg)](assets/videos/<name>.mp4)`. On the GitHub repo screen the single-frame image becomes a
link to the mp4, and on the docs site a hook (tools/docs_site/hooks.py) replaces it with a video element. Videos are made with tools/docs-video.

1. The mp4 and the single-frame image that the page points to exist, and the mp4 is within the size limit — so the videos in the repo do not make clones too heavy.
   For a page that turns the video's opening into a GIF (`assets/videos/<name>.gif`), the single-frame image of the same name (the site's poster) also exists, and the GIF is
   within the size limit. GitHub does not play the repo's mp4 but animates a GIF as an image.
2. The hook replaces the link with a video element that uses that image as the poster.
3. Narration voice is made with the OpenAI text-to-speech API, not a local speech-synthesis model. If this is reverted, fix this test and tools/docs-video/tts.py
   in the same change.
4. At the head of the sections for the 3 article videos, a video service thumbnail link and a one-line click-through figure follow, and the hook replaces the link with a player.
   The README has the GitHub-attached video as a bare single line (the GitHub screen turns it into a player), and has a link to the article.
5. Every mp4 in docs/assets/videos has its subtitle files (<name>.vtt and <name>.srt) next to it, and they say every line of its narration.json once, in order,
   within the video.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
ARTICLE = DOCS / "articles" / "algebraic-effects-in-python.md"
README = ROOT / "README.md"
TTS = ROOT / "tools" / "docs-video" / "tts.py"
VIDEO_LINK = re.compile(r"\[!\[[^\]]*\]\(([^)\s]+\.(?:jpg|gif))\)\]\(([^)\s]+\.mp4)\)")
MAX_VIDEO_BYTES = 6_000_000
MAX_GIF_BYTES = 3_000_000
# Packages that make voice locally (run a model on the local machine). tts.py neither imports nor depends on them.
LOCAL_TTS_PACKAGES = (
    "kokoro",
    "kokoro_onnx",
    "onnxruntime",
    "piper",
    "TTS",
    "torch",
    "transformers",
    "bark",
    "styletts2",
    "melo",
    "f5_tts",
    "pyttsx3",
)


def _video_links() -> list[tuple[Path, Path, Path]]:
    """List of (page, image, mp4) for the video links written in docs pages — to check existence and size."""
    return [
        (page, (page.parent / poster).resolve(), (page.parent / video).resolve())
        for page in sorted(DOCS.rglob("*.md"))
        for poster, video in VIDEO_LINK.findall(page.read_text(encoding="utf-8"))
    ]


def test_the_home_page_has_its_video() -> None:
    pages = {page.relative_to(DOCS).as_posix() for page, _, _ in _video_links()}
    assert "index.md" in pages, "docs/index.md has no video link at the top"


def test_every_video_link_points_at_files_within_the_size_limit() -> None:
    for page, poster, video in _video_links():
        where = page.relative_to(ROOT)
        if poster.suffix == ".gif":
            assert poster.is_file(), f"{where}: GIF {poster.relative_to(ROOT)} is missing"
            gif_size = poster.stat().st_size
            assert gif_size <= MAX_GIF_BYTES, (
                f"{where}: {poster.name} is {gif_size} bytes — limit {MAX_GIF_BYTES}. Remake it shorter and narrower with tools/docs-video/gif.mjs"
            )
            poster = poster.with_suffix(".jpg")
        assert poster.is_file(), f"{where}: single-frame image {poster.relative_to(ROOT)} is missing"
        assert video.is_file(), f"{where}: video {video.relative_to(ROOT)} is missing"
        size = video.stat().st_size
        assert size <= MAX_VIDEO_BYTES, (
            f"{where}: {video.name} is {size} bytes — limit {MAX_VIDEO_BYTES}. Raise crf in tools/docs-video/render.mjs and remake it"
        )


def _hooks():
    """To load the hook module without installing mkdocs (mkdocs types are imported only for type checking)."""
    spec = importlib.util.spec_from_file_location(
        "docs_site_hooks", ROOT / "tools" / "docs_site" / "hooks.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_hook_turns_a_video_link_into_a_player() -> None:
    content = (
        '<p><a href="../assets/videos/home.mp4"><img alt="A &quot;narrated&quot; video" '
        'src="../assets/videos/home.jpg" /></a></p>'
    )
    out = _hooks().videos_to_players(content)
    assert out.startswith("<p><video "), out
    assert 'src="../assets/videos/home.mp4"' in out
    assert 'poster="../assets/videos/home.jpg"' in out
    assert "controls" in out
    assert "<a href" not in out


def test_the_hook_puts_the_still_as_the_poster_of_a_gif_link() -> None:
    content = (
        '<p><a href="../assets/videos/home.mp4"><img alt="A video" '
        'src="../assets/videos/home.gif" /></a></p>'
    )
    out = _hooks().videos_to_players(content)
    assert out.startswith("<p><video "), out
    assert 'src="../assets/videos/home.mp4"' in out
    assert 'poster="../assets/videos/home.jpg"' in out


def test_the_hook_leaves_other_links_alone() -> None:
    content = '<p><a href="01-getting-started/"><img alt="x" src="a.png" /></a></p>'
    assert _hooks().videos_to_players(content) == content


def test_the_hook_turns_a_youtube_link_into_a_player() -> None:
    content = (
        '<p><a href="https://youtu.be/Ao6Gc1uvmtw"><img alt="A &quot;narrated&quot; video" '
        'src="https://img.youtube.com/vi/Ao6Gc1uvmtw/maxresdefault.jpg" /></a></p>'
    )
    out = _hooks().youtube_to_iframes(content)
    assert out.startswith("<p><iframe "), out
    assert 'src="https://www.youtube.com/embed/Ao6Gc1uvmtw"' in out
    assert 'title="A &quot;narrated&quot; video"' in out
    assert 'referrerpolicy="strict-origin-when-cross-origin"' in out
    assert "<a href" not in out


def test_the_hook_leaves_a_youtube_link_inside_a_sentence_alone() -> None:
    content = '<p>See <a href="https://youtu.be/Ao6Gc1uvmtw">the video</a>.</p>'
    assert _hooks().youtube_to_iframes(content) == content


def test_the_three_article_videos_sit_above_their_figures() -> None:
    article = ARTICLE.read_text(encoding="utf-8")
    for video_id, figure in (
        ("WaLFlWe4obs", "article-opening"),
        ("tqdLeDZpeOM", "monte-carlo"),
        ("rmzj6AWnlKQ", "hundred-fights"),
    ):
        pair = re.compile(
            rf"^\[!\[[^\]]+\]\(https://img\.youtube\.com/vi/{video_id}/maxresdefault\.jpg\)\]\(https://youtu\.be/{video_id}\)\n\n"
            rf"\[!\[[^\]]+\]\(\.\./assets/figures/{figure}\.gif\)\]",
            re.MULTILINE,
        )
        assert pair.search(article), f"the article has no line for video {video_id} above figure {figure}"


def test_the_readme_has_the_attached_video_and_the_article_link() -> None:
    readme = README.read_text(encoding="utf-8")
    assert re.search(r"^https://github\.com/user-attachments/assets/[0-9a-f-]+$", readme, re.MULTILINE), (
        "README has no bare single line for the GitHub-attached video"
    )
    assert "docs/articles/algebraic-effects-in-python.md" in readme


def _script_dependencies(source: str) -> list[str]:
    """The dependencies of the script declaration (PEP 723) at the head of tts.py — packages that uv run installs."""
    block = re.search(r"^# /// script\n((?:#.*\n)*?)# ///$", source, re.MULTILINE)
    assert block is not None, "tts.py has no script declaration (# /// script) at its head"
    body = "\n".join(line.removeprefix("#").strip() for line in block.group(1).splitlines())
    match = re.search(r"dependencies\s*=\s*(\[[^\]]*\])", body)
    assert match is not None, "the script declaration has no dependencies"
    return ast.literal_eval(match.group(1))


def test_narration_uses_the_openai_speech_api_and_no_local_model() -> None:
    source = TTS.read_text(encoding="utf-8")
    assert "https://api.openai.com/v1/audio/speech" in source, (
        "tts.py does not call the OpenAI text-to-speech API"
    )
    assert "OPENAI_API_KEY" in source, "tts.py does not read the OpenAI API key from an environment variable"
    dependencies = _script_dependencies(source)
    local = [
        d
        for d in dependencies
        if re.split(r"[\s=<>!~\[]", d, maxsplit=1)[0].lower()
        in {p.lower() for p in LOCAL_TTS_PACKAGES}
    ]
    assert not local, f"tts.py depends on local speech-synthesis packages: {local}"
    nodes = list(ast.walk(ast.parse(source)))
    imported = {
        alias.name.split(".")[0]
        for node in nodes
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module.split(".")[0]
        for node in nodes
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert not imported & set(LOCAL_TTS_PACKAGES), (
        f"tts.py imports local speech-synthesis packages: {imported & set(LOCAL_TTS_PACKAGES)}"
    )


VIDEOS = DOCS / "assets" / "videos"
PAGES = ROOT / "tools" / "docs-video" / "src" / "pages"
# Every video of the docs has its narration burned in as subtitles and written next to it as <name>.vtt and <name>.srt
# (tools/docs-video/render.mjs, from the cues of src/kit/subtitles.tsx).
CAPTIONED = tuple(sorted(video.stem for video in VIDEOS.glob("*.mp4")))
VTT_CUE = re.compile(
    r"^(?P<id>[a-z]+\d+(?:\.\d+)?)\n(?P<start>\d\d:\d\d:\d\d\.\d{3}) --> (?P<end>\d\d:\d\d:\d\d\.\d{3})\n(?P<text>.+)$",
    re.MULTILINE,
)


def _seconds(stamp: str) -> float:
    hours, minutes, seconds = stamp.replace(",", ".").split(":")
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def _mp4_seconds(path: Path) -> float:
    """The length of an mp4 from its movie header (mvhd inside moov), read without ffprobe."""
    data = path.read_bytes()

    def boxes(start: int, end: int):
        while start + 8 <= end:
            size = int.from_bytes(data[start : start + 4], "big")
            kind = data[start + 4 : start + 8].decode("latin-1")
            head = 8
            if size == 1:
                size, head = int.from_bytes(data[start + 8 : start + 16], "big"), 16
            yield kind, start + head, start + size
            start += size

    moov = next((begin, end) for kind, begin, end in boxes(0, len(data)) if kind == "moov")
    begin = next(begin for kind, begin, _ in boxes(*moov) if kind == "mvhd")
    if data[begin] == 1:
        timescale = int.from_bytes(data[begin + 20 : begin + 24], "big")
        duration = int.from_bytes(data[begin + 24 : begin + 32], "big")
    else:
        timescale = int.from_bytes(data[begin + 12 : begin + 16], "big")
        duration = int.from_bytes(data[begin + 16 : begin + 20], "big")
    return duration / timescale


@pytest.mark.parametrize("name", CAPTIONED)
def test_the_subtitles_say_every_narration_line_once_in_order_within_the_video(name: str) -> None:
    vtt = (VIDEOS / f"{name}.vtt").read_text(encoding="utf-8")
    assert vtt.startswith("WEBVTT\n"), f"{name}.vtt is not a WebVTT file"
    cues = [match.groupdict() for match in VTT_CUE.finditer(vtt)]
    lines = json.loads((PAGES / name / "narration.json").read_text(encoding="utf-8"))
    # A line shown in parts has the cues <id>.1, <id>.2, ...: their texts, joined, are the line as written.
    said: dict[str, list[str]] = {}
    for cue in cues:
        said.setdefault(cue["id"].split(".")[0], []).append(cue["text"])
    assert list(said) == [line["id"] for line in lines], f"{name}.vtt does not have every narration line once, in order"
    for line in lines:
        assert " ".join(said[line["id"]]) == line["text"], f"{name}.vtt: line {line['id']} is not the narration's words"
    times = [(_seconds(cue["start"]), _seconds(cue["end"])) for cue in cues]
    assert all(start < end for start, end in times), f"{name}.vtt has a cue that ends before it starts"
    assert all(end <= next_start for (_, end), (next_start, _) in zip(times, times[1:])), f"{name}.vtt has cues that overlap"
    assert times[0][0] >= 0
    assert times[-1][1] <= _mp4_seconds(VIDEOS / f"{name}.mp4"), f"{name}.vtt runs past the end of the video"
    srt = (VIDEOS / f"{name}.srt").read_text(encoding="utf-8")
    assert [cue["text"] for cue in cues] == re.findall(r"^\d+\n[\d:,]+ --> [\d:,]+\n(.+)$", srt, re.MULTILINE), (
        f"{name}.srt does not hold the same cues as {name}.vtt"
    )


def test_the_hook_gives_a_captioned_video_its_track(tmp_path: Path) -> None:
    (tmp_path / "assets" / "videos").mkdir(parents=True)
    (tmp_path / "assets" / "videos" / "home.vtt").write_text("WEBVTT\n", encoding="utf-8")
    content = '<p><a href="../assets/videos/home.mp4"><img alt="A video" src="../assets/videos/home.jpg" /></a></p>'
    out = _hooks().videos_to_players(content, tmp_path)
    assert '<track kind="captions" srclang="en" label="English" src="../assets/videos/home.vtt" default>' in out
    assert "<track" not in _hooks().videos_to_players(content, tmp_path / "elsewhere")
