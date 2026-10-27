"""記事・機能対応表・パッケージ一覧・図の組が揃っていることを検査する。

図の台帳は記事ごとのファイルに置き、ディレクトリの中を全部読む。記事を足す仕事は、自分の記事のファイルだけを足す。

- visuals/captions/<slug>.json: 記事に載せる図の題と説明。
- imagegen/manifest/<slug>.json: 採用画像の生成・編集プロンプト・寸法・容量・SHA-256・目視確認の記録。
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
import struct
from itertools import pairwise
from pathlib import Path

from export_zenn_book import Book
from export_zenn_book import prepare_all as prepare_books
from render_release import read_rows

SOURCE = Path(__file__).resolve().parent
ROOT = SOURCE.parents[1]
CAPTIONS = SOURCE / "visuals/captions"
MANIFESTS = SOURCE / "imagegen/manifest"
IMAGE_DIRECTORY = ROOT / "images/zenn-use-cases-v0/generated"
# 採用画像を作った道具。SVGの図は正本をvisuals/に置き、作り方をimagegen/の台帳の文書に書く。
IMAGE_TOOLS = {
    "image_gen (built-in)",
    "OpenAI Images API (gpt-image-2)",  # 組み込みのimagegenを使えない環境で、同じプロンプトから描き直した図。
    "SVG (hand-written) + chromium screenshot",
    "example's HTML page + chromium screenshot",  # 完全な例が組んだページの写し(記事が見せる結果そのもの)。
}
# コードの枠とインラインのコード。この中の`](`はリンクではない。
_CODE = re.compile(r"^```.*?^```[ \t]*$|`[^`\n]*`", re.S | re.M)


def link_targets(text: str) -> list[str]:
    """Markdownのリンクと画像の参照先。コードの中の`](`を参照先と読み違えないよう、コードを除いてから探す。"""
    return re.findall(r"\]\(([^)]+)\)", _CODE.sub("", text))


def read_ledger(directory: Path) -> dict[str, object]:
    """記事ごとの台帳(<slug>.json)を、ディレクトリの中から全部読む。"""
    return {path.stem: json.loads(path.read_text()) for path in sorted(directory.glob("*.json"))}


def missing_images() -> set[Path]:
    """まだリポジトリに無い掲載画像。台帳の項目のmissingに訳を書いた物だけを、画像の照合から外す。"""
    return {
        ROOT / entry["image"]
        for entries in read_ledger(MANIFESTS).values()
        for entry in entries
        if "missing" in entry
    }


def verify_image_manifest(expected: dict[str, set[str]]) -> None:
    manifests = read_ledger(MANIFESTS)
    assert set(manifests) == set(expected), ("原稿とimagegen/manifest/の組が違う", sorted(set(manifests) ^ set(expected)))
    for slug, manifest in manifests.items():
        ids = sorted(entry["id"] for entry in manifest)
        assert ids == sorted(expected[slug]), (slug, ids, sorted(expected[slug]))
        for entry in manifest:
            image_path = ROOT / entry["image"]
            assert image_path.stem == entry["id"]
            assert image_path.parent == IMAGE_DIRECTORY
            assert entry["tool"] in IMAGE_TOOLS
            assert entry["review"]
            for prompt in [entry["generation_prompt"], *entry["edit_prompts"]]:
                assert (SOURCE / "imagegen" / prompt).read_text().strip()
            if "missing" in entry:
                # 描き直した画像を置いたら、missingを外して下の照合へ戻す。
                assert entry["missing"].strip(), entry["id"]
                assert not image_path.exists(), (entry["id"], "画像が在るので、台帳のmissingを外す")
                continue
            png = image_path.read_bytes()
            assert hashlib.sha256(png).hexdigest() == entry["sha256"]
            assert png[:8] == b"\x89PNG\r\n\x1a\n"
            width, height = struct.unpack(">II", png[16:24])
            assert (width, height) == (entry["width"], entry["height"])
            assert width >= 1024
            assert height > 0
            assert len(png) == entry["bytes"]
            assert len(png) < 3_000_000


def kinds_of(caption: dict) -> tuple[str, ...]:
    """記事が載せる図の種類。概念図・処理図の2枚と、台帳のextra_kindsに書いた図(開発の記録の数の図など)。"""
    return ("concept", "flow", *caption.get("extra_kinds", ()))


def verify_article_figures(slug: str, text: str, caption: dict, missing: set[Path]) -> None:
    """記事の本文が台帳の図の参照・題・説明を載せている事と、リポジトリに在る図のPNGの形を照らす。"""
    for kind in kinds_of(caption):
        stem = f"{slug}-{kind}"
        assert f"/images/zenn-use-cases-v0/generated/{stem}.png" in text
        assert caption[f"{kind}_title"] in text
        assert caption[f"{kind}_caption"] in text
        if IMAGE_DIRECTORY / f"{stem}.png" in missing:
            continue
        png = (IMAGE_DIRECTORY / f"{stem}.png").read_bytes()
        assert png[:8] == b"\x89PNG\r\n\x1a\n"
        width, height = struct.unpack(">II", png[16:24])
        assert width >= 1024
        assert height > 0
        assert len(png) < 3_000_000


def series_names() -> dict[str, list[str]]:
    """公開順の表の回(公開予定・公開済み)の原稿の名を、連載ごとに表の順で返す。"""
    series: dict[str, list[str]] = {}
    for row in read_rows((SOURCE / "publication-plan.md").read_text()):
        series.setdefault(row.series, []).append(row.name)
    return series


def verify_series_links() -> int:
    """読者がどの回からも前後の回へ辿れるよう、公開順の表の回(公開予定・公開済み)の参考の節を連載ごとに照らす。

    連載の1本目には前の回を、最後の回には次の回を置かない。別の連載の回へは、前後の回のリンクを張らない。
    """
    series = series_names()
    names = [name for members in series.values() for name in members]
    references = {}
    for name in names:
        _, heading, reference = (SOURCE / name).read_text().partition("\n## 参考")
        assert heading, (name, "参考の節が無い")
        references[name] = reference
    for members in series.values():
        for previous, following in pairwise(members):
            for name, label, neighbor in ((following, "前の回", previous), (previous, "次の回", following)):
                link = rf"^- {label}:\[[^\]]+\]\({re.escape(neighbor)}\)$"
                assert re.search(link, references[name], re.M), (name, f"参考に{label}へのリンクが無い", neighbor)
        for name, label in ((members[0], "前の回"), (members[-1], "次の回")):
            assert not re.search(rf"^- {label}:", references[name], re.M), (name, f"連載の端の回に{label}がある")
    return len(names)


def verify_books() -> tuple[Book, ...]:
    """本の正本(books/<本のslug>/)が、今の原稿から崩れずに書き出せることを照らし、書き出す形の本を返す。

    原稿の文が変わり、rewrites.jsonの直す前の文が見つからない時や、連載の言葉が章に残る時に落ちる。
    """
    books = prepare_books()
    problems = tuple(problem for book in books for problem in book.problems)
    assert not problems, ("本の正本を書き出せない(export_zenn_book.pyを走らせて直し方を見る)", problems)
    return books


def verify() -> None:
    articles = {path.stem.removeprefix("doeff-"): path for path in SOURCE.glob("doeff-*.md")}
    captions = read_ledger(CAPTIONS)
    # 記事の数は書かない。原稿(doeff-<slug>.md)ごとに、図の台帳のファイルが1つずつある事を照らす。
    assert set(articles) == set(captions), ("原稿とvisuals/captions/の組が違う", sorted(set(articles) ^ set(captions)))
    expected = {slug: {f"{slug}-{kind}" for kind in kinds_of(captions[slug])} for slug in articles}
    expected["main"].add("main-hero")
    expected_images = set().union(*expected.values())
    missing = missing_images()
    stored = {name for name in expected_images if IMAGE_DIRECTORY / f"{name}.png" not in missing}
    present = {path.stem for path in IMAGE_DIRECTORY.glob("*.png")}
    assert present == stored, ("generated/のPNGと台帳の組が違う(図を置いたらmissingを外す)", sorted(present ^ stored))
    verify_image_manifest(expected)
    blocks = 0
    for slug, path in articles.items():
        text = path.read_text()
        assert "published: false" in text
        verify_article_figures(slug, text, captions[slug], missing)
        for code in re.findall(r"```python\n(.*?)```", text, re.S):
            ast.parse(code, filename=path.name)
            blocks += 1
        assert "```" in text
        if slug != "main":
            assert "doeff-main.md" in text
            # 別の連載の回は、メイン記事の代わりに、その連載の1本目(入口)から案内する。
            entrance = next((members[0] for members in series_names().values() if path.name in members[1:]), None)
            guides = [articles["main"].read_text()] + ([(SOURCE / entrance).read_text()] if entrance else [])
            assert any(path.name in guide for guide in guides), (path.name, "メイン記事と連載の1本目から案内していない")
    main = articles["main"].read_text()
    hero = f'![{captions["main"]["hero_title"]}](/images/zenn-use-cases-v0/generated/main-hero.png)'
    assert main.split("---", 2)[2].lstrip().startswith(hero)
    assert main.index(hero) < main.index("## doeffとは") < main.index("```python")
    assert main.index("```python") < main.index("/images/zenn-use-cases-v0/generated/main-concept.png")

    for path in SOURCE.glob("*.md"):
        for target in link_targets(path.read_text()):
            if target.startswith(("https://", "http://", "#")):
                continue
            local_target = target.split("#")[0]
            destination = (
                ROOT / local_target.lstrip("/")
                if local_target.startswith("/")
                else path.parent / local_target
            )
            assert destination.is_file() or destination in missing, (path.name, target)

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
    series = verify_series_links()
    books = verify_books()
    print(
        f"OK: {len(articles)}記事・{len(packages)}パッケージ・{len(feature_rows)}機能・{len(expected_images)}枚の図"
        f"(うち{len(missing)}枚は台帳のmissingの通りまだ無い)・Python {blocks}ブロック・公開順の{series}回の前後のリンク"
        f"・本{len(books)}冊({sum(len(book.chapters) for book in books)}章)"
    )


if __name__ == "__main__":
    verify()
