"""記事・機能対応表・パッケージ一覧・図の組が揃っていることを検査する。"""

from __future__ import annotations

import ast
import hashlib
import json
import re
import struct
from pathlib import Path

SOURCE = Path(__file__).resolve().parent
ROOT = SOURCE.parents[1]


def verify_image_manifest(expected_images: set[str]) -> None:
    manifest = json.loads((SOURCE / "imagegen/manifest.json").read_text())
    assert {entry["id"] for entry in manifest} == expected_images
    assert len(manifest) == len(expected_images)
    for entry in manifest:
        image_path = ROOT / entry["image"]
        assert image_path.stem == entry["id"]
        assert image_path.parent == ROOT / "images/zenn-use-cases-v0/generated"
        png = image_path.read_bytes()
        assert hashlib.sha256(png).hexdigest() == entry["sha256"]
        assert png[:8] == b"\x89PNG\r\n\x1a\n"
        width, height = struct.unpack(">II", png[16:24])
        assert (width, height) == (entry["width"], entry["height"])
        assert width >= 1024
        assert height > 0
        assert len(png) == entry["bytes"]
        assert len(png) < 3_000_000
        assert entry["tool"] == "image_gen (built-in)"
        assert entry["review"]
        for prompt in [entry["generation_prompt"], *entry["edit_prompts"]]:
            assert (SOURCE / "imagegen" / prompt).read_text().strip()


def verify() -> None:
    articles = {path.stem.removeprefix("doeff-"): path for path in SOURCE.glob("doeff-*.md")}
    captions = json.loads((SOURCE / "visuals/captions.json").read_text())
    assert len(articles) == 23
    assert set(articles) == set(captions)
    expected_images = {f"{slug}-{kind}" for slug in articles for kind in ("concept", "flow")}
    expected_images.add("main-hero")
    image_directory = ROOT / "images/zenn-use-cases-v0/generated"
    assert {path.stem for path in image_directory.glob("*.png")} == expected_images
    verify_image_manifest(expected_images)
    blocks = 0
    for slug, path in articles.items():
        text = path.read_text()
        assert "published: false" in text
        for kind in ("concept", "flow"):
            stem = f"{slug}-{kind}"
            assert f"/images/zenn-use-cases-v0/generated/{stem}.png" in text
            assert captions[slug][f"{kind}_title"] in text
            assert captions[slug][f"{kind}_caption"] in text
            png = (ROOT / f"images/zenn-use-cases-v0/generated/{stem}.png").read_bytes()
            assert png[:8] == b"\x89PNG\r\n\x1a\n"
            width, height = struct.unpack(">II", png[16:24])
            assert width >= 1024
            assert height > 0
            assert len(png) < 3_000_000
        for code in re.findall(r"```python\n(.*?)```", text, re.S):
            ast.parse(code, filename=path.name)
            blocks += 1
        assert "```" in text
        if slug != "main":
            assert "doeff-main.md" in text
            assert path.name in articles["main"].read_text()
    main = articles["main"].read_text()
    hero = f'![{captions["main"]["hero_title"]}](/images/zenn-use-cases-v0/generated/main-hero.png)'
    assert main.split("---", 2)[2].lstrip().startswith(hero)
    assert main.index(hero) < main.index("## doeffとは") < main.index("```python")
    assert main.index("```python") < main.index("/images/zenn-use-cases-v0/generated/main-concept.png")

    for path in SOURCE.glob("*.md"):
        for target in re.findall(r"\]\(([^)]+)\)", path.read_text()):
            if target.startswith(("https://", "http://", "#")):
                continue
            local_target = target.split("#")[0]
            destination = (
                ROOT / local_target.lstrip("/")
                if local_target.startswith("/")
                else path.parent / local_target
            )
            assert destination.is_file(), (path.name, target)

    coverage = (SOURCE / "package-coverage.md").read_text()
    declared = set(re.findall(r"^\| `(doeff-[^`]+)`", coverage, re.M))
    packages = {path.name for path in (ROOT / "packages").iterdir() if path.is_dir()}
    assert declared == packages
    features = (SOURCE / "feature-examples.md").read_text()
    feature_rows = [line for line in features.splitlines() if line.startswith("| ")][2:]
    assert len(feature_rows) == 43
    assert all(
        "doeff-" in row and ("本文にコード" in row or "examples/" in row) for row in feature_rows
    )
    print(
        f"OK: {len(articles)}記事・{len(packages)}パッケージ・{len(feature_rows)}機能・{len(expected_images)}枚の図・Python {blocks}ブロック"
    )


if __name__ == "__main__":
    verify()
