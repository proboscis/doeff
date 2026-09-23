"""リポジトリのMarkdown原稿から、記事間を移動できる閲覧用HTMLを生成する。"""

from __future__ import annotations

import argparse
import base64
import html
import re
from pathlib import Path

from markdown_it import MarkdownIt


def render_article(source: str, slug: str, filename: str, repo_root: Path) -> tuple[str, str]:
    title_match = re.search(r'^title: "(.+)"$', source, re.MULTILINE)
    if title_match is None:
        raise ValueError(f"記事のtitleがありません: {filename}")
    title: str = title_match.group(1)
    body: str = source.split("---", 2)[2]
    body = re.sub(r"^:::details (.*)$", r"<details><summary>\1</summary>", body, flags=re.MULTILINE)
    body = re.sub(r"^:::message$", "<aside>", body, flags=re.MULTILINE)
    # このシリーズの囲みは、同じ記事内でdetailsとmessageを混在させない。
    close_tag: str = "</details>" if "<details>" in body else "</aside>"
    body = re.sub(r"^:::$", close_tag, body, flags=re.MULTILINE)
    renderer: MarkdownIt = MarkdownIt("commonmark", {"html": True}).enable("table")
    rendered: str = renderer.render(body)
    rendered = re.sub(r'href="doeff-([a-z]+)\.md"', r'href="#\1"', rendered)

    def embed_image(match: re.Match[str]) -> str:
        image_path = (repo_root / match.group(1).lstrip("/")).resolve()
        if not image_path.is_relative_to(repo_root / "images"):
            raise ValueError(f"画像はリポジトリのimages配下に置いてください: {image_path}")
        encoded_image = base64.b64encode(image_path.read_bytes()).decode()
        return f'src="data:image/png;base64,{encoded_image}"'

    rendered = re.sub(r'src="(/images/[^\"]+\.png)"', embed_image, rendered)

    def embed_example(match: re.Match[str]) -> str:
        relative = match.group(1)
        example_path = (Path(__file__).resolve().parent / relative).resolve()
        if not example_path.is_relative_to(Path(__file__).resolve().parent / "examples"):
            raise ValueError(f"コード例のパスが不正です: {relative}")
        encoded_example = base64.b64encode(example_path.read_bytes()).decode()
        return (
            f'download="{html.escape(example_path.name)}" '
            f'href="data:text/plain;base64,{encoded_example}"'
        )

    rendered = re.sub(r'href="(examples/[^\"]+)"', embed_example, rendered)
    encoded: str = base64.b64encode(source.encode()).decode()
    section: str = (
        f'<section id="{slug}" class="article" hidden>'
        '<p class="meta">未公開の草稿・リポジトリ原稿から生成</p>'
        f"<h1>{html.escape(title)}</h1>{rendered}"
        "<details><summary>この原稿のMarkdown</summary>"
        f'<a download="{filename}" href="data:text/markdown;base64,{encoded}">'
        "ダウンロード</a>"
        f"<textarea readonly>{html.escape(source)}</textarea></details></section>"
    )
    return title, section


def render_series(source_dir: Path) -> str:
    article_paths: list[Path] = sorted(source_dir.glob("doeff-*.md"))
    article_paths.sort(key=lambda path: (path.stem != "doeff-main", path.name))
    if not article_paths:
        raise ValueError("記事原稿がありません")
    sections: list[str] = []
    options: list[str] = []
    for path in article_paths:
        slug: str = path.stem.removeprefix("doeff-")
        title, section = render_article(path.read_text(), slug, path.name, source_dir.parents[1])
        sections.append(section)
        options.append(f'<option value="{slug}">{html.escape(title)}</option>')
    return (
        '<!doctype html><html lang="ja"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>doeffとは? — 処理とハンドラを学ぶ</title>"
        + STYLE
        + '</head><body><nav><a href="#main">全体像へ戻る</a>'
        '<label for="article-picker">記事を選ぶ</label><select id="article-picker">'
        + "".join(options)
        + "</select></nav><main>"
        + "".join(sections)
        + "</main><footer>原稿の正本: publications/zenn-use-cases-v0/。"
        "このHTMLは閲覧用の生成物です。Zennには未公開です。</footer>" + SCRIPT + "</body></html>"
    )


STYLE: str = """<style>
:root{color-scheme:light dark;--bg:#f6f8fb;--fg:#1c293a;--card:#fff;
--line:#d6dfe9;--accent:#1269aa;--code:#eef2f7}
@media(prefers-color-scheme:dark){:root{--bg:#121920;--fg:#e7edf4;
--card:#1a2530;--line:#455263;--accent:#85ccff;--code:#101820}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:17px/1.9 system-ui,sans-serif}nav{background:var(--card);padding:12px 20px;
border-bottom:1px solid var(--line);display:flex;align-items:center;gap:16px;
flex-wrap:wrap;font-size:14px}select{max-width:100%;padding:7px;font:inherit}
a{color:var(--accent)}main{max-width:960px;margin:24px auto}
.article{background:var(--card);padding:38px;border-radius:14px}
.article img{display:block;max-width:100%;height:auto;max-height:820px;
object-fit:contain;margin:28px auto 12px;border-radius:10px}
h1{font-size:29px;line-height:1.5}h2{font-size:24px;margin-top:2em;
border-bottom:1px solid var(--line)}h3{font-size:20px;margin-top:2em}
.meta{font-size:13px}pre{overflow:auto;background:var(--code);padding:18px;
font:14px/1.7 monospace}code{background:var(--code)}table{border-collapse:collapse;
font-size:15px;width:100%}td,th{border:1px solid var(--line);padding:10px;text-align:left}
blockquote,aside{border-left:3px solid var(--accent);padding-left:15px}
details{border:1px solid var(--line);border-radius:8px;padding:14px;margin:22px 0}
summary{cursor:pointer}textarea{width:100%;height:300px;background:var(--code);
color:var(--fg);font:13px/1.6 monospace}footer{max-width:960px;margin:25px auto;
font-size:13px;padding:20px}@media(max-width:640px){main{margin:0}.article{padding:20px}
h1{font-size:25px}table{display:block;overflow:auto}}
</style>"""

SCRIPT: str = """<script>
const picker = document.getElementById('article-picker');
function showArticle() {
  const slug = location.hash.slice(1) || 'main';
  const target = document.getElementById(slug);
  if (!target || !target.classList.contains('article')) {
    location.hash = 'main'; return;
  }
  document.querySelectorAll('.article').forEach(el => {el.hidden = el !== target;});
  picker.value = slug;
  window.scrollTo(0, 0);
}
picker.addEventListener('change', () => {location.hash = picker.value;});
window.addEventListener('hashchange', showArticle);
showArticle();
</script>"""


def main() -> None:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args: argparse.Namespace = parser.parse_args()
    source_dir: Path = Path(__file__).resolve().parent
    output: Path = args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_series(source_dir))
    print(f"プレビューを生成しました: {output}")


if __name__ == "__main__":
    main()
