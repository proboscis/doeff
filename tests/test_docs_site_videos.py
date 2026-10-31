"""docs の章の動画の検(取り組み doeff-publish の区切り 23)。

章の page は動画を `[![<説明>](assets/videos/<名>.jpg)](assets/videos/<名>.mp4)` と書く。GitHub の repo の画面では 1 こまの画像が
mp4 への link になり、docs の site では hook(tools/docs_site/hooks.py)が video の要素に替える。動画は tools/docs-video で作る。

1. page が指す mp4 と 1 こまの画像が在り、mp4 が上限の大きさの内である — repo に入れる動画が clone を重くしすぎないため。
   画像を動画の頭の GIF(`assets/videos/<名>.gif`)にした page では、同じ名の 1 こまの画像(site の poster)も在り、GIF が
   上限の大きさの内である。GitHub は repo の mp4 を再生しないが GIF は画像として動かす。
2. hook が link を、その画像を poster にした video の要素に替える。
3. ナレーションの声は OpenAI の text-to-speech の API で作り、ローカルの音声合成の model を使わない。戻す時はこの検と tools/docs-video/tts.py を
   同じ変更で直す。
"""

from __future__ import annotations

import ast
import importlib.util
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
TTS = ROOT / "tools" / "docs-video" / "tts.py"
VIDEO_LINK = re.compile(r"\[!\[[^\]]*\]\(([^)\s]+\.(?:jpg|gif))\)\]\(([^)\s]+\.mp4)\)")
MAX_VIDEO_BYTES = 6_000_000
MAX_GIF_BYTES = 3_000_000
# ローカルで声を作る package(model を手元で走らせる物)。tts.py はこれらを import も依存もしない。
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
    """docs の page が書く動画の link の (page, 画像, mp4) の一覧 — 在るかと大きさを確かめるため。"""
    return [
        (page, (page.parent / poster).resolve(), (page.parent / video).resolve())
        for page in sorted(DOCS.rglob("*.md"))
        for poster, video in VIDEO_LINK.findall(page.read_text(encoding="utf-8"))
    ]


def test_the_home_page_has_its_video() -> None:
    pages = {page.relative_to(DOCS).as_posix() for page, _, _ in _video_links()}
    assert "index.md" in pages, "docs/index.md の頭に動画の link が無い"


def test_every_video_link_points_at_files_within_the_size_limit() -> None:
    for page, poster, video in _video_links():
        where = page.relative_to(ROOT)
        if poster.suffix == ".gif":
            assert poster.is_file(), f"{where}: GIF {poster.relative_to(ROOT)} が無い"
            gif_size = poster.stat().st_size
            assert gif_size <= MAX_GIF_BYTES, (
                f"{where}: {poster.name} が {gif_size} byte — 上限 {MAX_GIF_BYTES}。tools/docs-video/gif.mjs で短く・狭く作り直す"
            )
            poster = poster.with_suffix(".jpg")
        assert poster.is_file(), f"{where}: 1 こまの画像 {poster.relative_to(ROOT)} が無い"
        assert video.is_file(), f"{where}: 動画 {video.relative_to(ROOT)} が無い"
        size = video.stat().st_size
        assert size <= MAX_VIDEO_BYTES, (
            f"{where}: {video.name} が {size} byte — 上限 {MAX_VIDEO_BYTES}。tools/docs-video/render.mjs の crf を上げて作り直す"
        )


def _hooks():
    """mkdocs を入れずに hook の module を読むため(mkdocs の型は型の確かめの時だけ import する)。"""
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


def _script_dependencies(source: str) -> list[str]:
    """tts.py の頭の script の宣言(PEP 723)の dependencies — uv run が入れる package。"""
    block = re.search(r"^# /// script\n((?:#.*\n)*?)# ///$", source, re.MULTILINE)
    assert block is not None, "tts.py の頭に script の宣言(# /// script)が無い"
    body = "\n".join(line.removeprefix("#").strip() for line in block.group(1).splitlines())
    match = re.search(r"dependencies\s*=\s*(\[[^\]]*\])", body)
    assert match is not None, "script の宣言に dependencies が無い"
    return ast.literal_eval(match.group(1))


def test_narration_uses_the_openai_speech_api_and_no_local_model() -> None:
    source = TTS.read_text(encoding="utf-8")
    assert "https://api.openai.com/v1/audio/speech" in source, (
        "tts.py が OpenAI の text-to-speech の API を呼んでいない"
    )
    assert "OPENAI_API_KEY" in source, "tts.py が OpenAI の API key を環境変数から読んでいない"
    dependencies = _script_dependencies(source)
    local = [
        d
        for d in dependencies
        if re.split(r"[\s=<>!~\[]", d, maxsplit=1)[0].lower()
        in {p.lower() for p in LOCAL_TTS_PACKAGES}
    ]
    assert not local, f"tts.py がローカルの音声合成の package に依存している: {local}"
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
        f"tts.py がローカルの音声合成の package を import している: {imported & set(LOCAL_TTS_PACKAGES)}"
    )
