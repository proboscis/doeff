"""mkdocs hook -- fixes 5 things so docs pages written to be read on GitHub also read the same on the site.

1. Rewrite relative links to repo files outside the site into links to the file on GitHub main. docs pages link relatively to repo files
   (packages/*/README.md, examples/, the specs in docs/specs/), but only the pages reachable from docs/index.md enter the site
   (exclude_docs in mkdocs.yml), so they would break on the site as they are. Only links to existing files are rewritten; links to missing files are
   not rewritten -- mkdocs emits a warning and a --strict build fails. Code in fences and inside `` ` `` is not rewritten.
2. Drop the word after the language at the head of a fence. The example checks in docs read the markers ```python excerpt and ```hy file=greet.hy, but pymdownx.superfences
   cannot read a bare word after the language and does not see the fence as a fence, so even the headings after it collapse. file=<name> becomes the fence's title.
3. Make chapter videos playable. A page writes a video as `[![<description>](assets/videos/<name>.jpg)](assets/videos/<name>.mp4)` --
   GitHub strips markdown video elements, so on the repo screen the one-frame image becomes a link to the mp4. On the site, that link is
   replaced with a video element whose poster is the one-frame image (videos are made with tools/docs-video). For pages whose image is the head GIF of the video
   (`assets/videos/<name>.gif` -- animates on the GitHub repo screen), the poster is the one-frame image of the same name (<name>.jpg).
4. Make click-through figures animate. A page writes a figure as a single line `[![<description>](assets/figures/<name>.gif)](<link target>)` -- on GitHub and Zenn
   the GIF of the same content animates and the link target can be followed. On the site, that line is replaced with the figure body (`docs/assets/figures/<name>.html` -- the figure's style and motion are held by
   step-figure.css and step-figure.js in the same dir). The GIF is left for screens where script does not run. The GIF is made by tools/docs-video/figure-gif.mjs from
   the same figure, so the contents of the two versions do not drift. A name without a body fails the build.
5. Make videos from a video service playable. A page writes a video as a single line `[![<description>](https://img.youtube.com/vi/<id>/maxresdefault.jpg)](https://youtu.be/<id>)`
   -- GitHub strips markdown iframes, so on the repo screen the thumbnail image becomes a link to the video. On the site, that line is replaced with
   the video service's player (iframe).
"""

from __future__ import annotations

import html
import re
from itertools import accumulate
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import unquote

if TYPE_CHECKING:
    from mkdocs.config.defaults import MkDocsConfig
    from mkdocs.structure.files import Files
    from mkdocs.structure.pages import Page

GITHUB_BRANCH = "main"
FENCE = re.compile(r"^(\s*)(`{3,}|~{3,})(.*)$")
CODE_SPAN = re.compile(r"(`+[^`]*`+)")
INLINE_LINK = re.compile(r"(\]\()([^)\s]+)(\s+\"[^\"]*\")?(\))")
SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
# A form where a one-frame image is a link to an mp4 (HTML after building from markdown). The order of img attributes does not matter.
VIDEO_LINK = re.compile(r'<a href="([^"]+\.mp4)">(<img\b[^>]*>)</a>')
IMG_ATTR = re.compile(r'\b(src|alt)="([^"]*)"')
# A one-line click-through figure (HTML after building from markdown). A form where a paragraph holds only the figure's GIF; the link may or may not be present.
FIGURE_LINE = re.compile(
    r'<p>(?:<a href="[^"]*">)?(<img\b[^>]*\bsrc="[^"]*assets/figures/([a-z0-9-]+)\.gif"[^>]*>)(?:</a>)?</p>'
)
# A one-line form where a video service thumbnail is a link to the video (HTML after building from markdown).
YOUTUBE_LINE = re.compile(r'<p><a href="https://youtu\.be/([A-Za-z0-9_-]+)">(<img\b[^>]*>)</a></p>')


def _github_url(
    target: str, page_dir: Path, repo_root: Path, docs_dir: Path, files: Files, repo_url: str
) -> str:
    """Return the GitHub URL if target is a repo file outside the site, otherwise return target unchanged."""
    path, hash_mark, anchor = target.partition("#")
    if not path or path.startswith("/") or SCHEME.match(path):
        return target
    resolved = (page_dir / unquote(path)).resolve()
    if resolved.is_relative_to(docs_dir):
        site_file = files.get_file_from_path(resolved.relative_to(docs_dir).as_posix())
        if site_file is not None and not site_file.inclusion.is_excluded():
            return target
    if not resolved.exists() or not resolved.is_relative_to(repo_root):
        return target
    kind = "tree" if resolved.is_dir() else "blob"
    return f"{repo_url}/{kind}/{GITHUB_BRANCH}/{resolved.relative_to(repo_root).as_posix()}{hash_mark}{anchor}"


def _fence_line(line: str) -> str:
    """So superfences can read the fence line -- keep only the language and title at the head; the closing line stays as is."""
    match = FENCE.match(line)
    if match is None:
        return line
    indent, marks, info = match.groups()
    words = info.split()
    if not words:
        return line
    titles = [word.removeprefix("file=") for word in words[1:] if word.startswith("file=")]
    return f"{indent}{marks}{words[0]}" + "".join(f' title="{title}"' for title in titles[:1])


def on_page_markdown(markdown: str, page: Page, config: MkDocsConfig, files: Files) -> str:
    """So that a page written to be read on GitHub reads the same on the site (a mkdocs hook; the 2 fixes are at the head of the module).

    Whether a line is inside a fence is read from the parity of the fence line count -- assumes fences in docs pages are not nested (no 4-backtick fences).
    """
    docs_dir = Path(config.docs_dir).resolve()
    repo_root = Path(config.config_file_path).resolve().parent
    page_dir = Path(page.file.abs_src_path).resolve().parent
    repo_url = config.repo_url.rstrip("/")

    def rewrite(match: re.Match[str]) -> str:
        """Replace only the target of one `](target)`, keeping the title and parentheses."""
        target = _github_url(match.group(2), page_dir, repo_root, docs_dir, files, repo_url)
        return f"{match.group(1)}{target}{match.group(3) or ''}{match.group(4)}"

    def rewrite_line(line: str) -> str:
        """Within one line, do not rewrite code examples inside `` ` `` (odd-numbered split parts are code)."""
        return "".join(
            part if index % 2 else INLINE_LINK.sub(rewrite, part)
            for index, part in enumerate(CODE_SPAN.split(line))
        )

    # A line is inside a fence = the number of fence lines up to that line is odd (the fence lines themselves do not get their links rewritten).
    lines = markdown.split("\n")
    fences = [FENCE.match(line) is not None for line in lines]
    return "\n".join(
        _fence_line(line) if is_fence else line if fences_so_far % 2 else rewrite_line(line)
        for line, is_fence, fences_so_far in zip(lines, fences, accumulate(fences), strict=True)
    )


def videos_to_players(content: str) -> str:
    """Replace a one-frame image or GIF made into a link to an mp4 with a video element that plays with the one-frame image as poster (3 at the head of the module)."""

    def player(match: re.Match[str]) -> str:
        """Replace one link with a video. The description (alt) becomes the name for screen readers."""
        attrs = dict(IMG_ATTR.findall(match.group(2)))
        poster = attrs.get("src", "")
        if poster.endswith(".gif"):
            poster = poster.removesuffix(".gif") + ".jpg"
        return (
            f'<video class="docs-video" controls preload="metadata" playsinline src="{match.group(1)}" '
            f'poster="{poster}" aria-label="{attrs.get("alt", "")}" '
            f'style="width: 100%; height: auto;">{html.escape(html.unescape(attrs.get("alt", "")))}</video>'
        )

    return VIDEO_LINK.sub(player, content)


def figures_to_steps(content: str, docs_dir: Path) -> str:
    """Replace a paragraph that holds only a figure GIF with the click-through figure body (4 at the head of the module)."""

    def figure(match: re.Match[str]) -> str:
        """Replace one paragraph with a figure. The description (alt) becomes the name for screen readers, and the GIF stands in for screens where script does not run."""
        image, name = match.groups()
        body = docs_dir / "assets" / "figures" / f"{name}.html"
        if not body.is_file():
            raise ValueError(f"the page that places {name}.gif has no figure body: {body}")
        label = dict(IMG_ATTR.findall(image)).get("alt", "")
        return (
            f'<figure class="step-figure" data-step-figure="{name}" aria-label="{label}">'
            f"{body.read_text(encoding='utf-8')}<noscript>{image}</noscript></figure>"
        )

    return FIGURE_LINE.sub(figure, content)


def youtube_to_iframes(content: str) -> str:
    """Replace a paragraph that holds only a video service thumbnail link with that video's player (5 at the head of the module)."""

    def player(match: re.Match[str]) -> str:
        """Replace one paragraph with an iframe. The description (alt) becomes the iframe's title. The referrer is sent because the player reads the embedding site."""
        video_id, image = match.groups()
        title = dict(IMG_ATTR.findall(image)).get("alt", "")
        return (
            f'<p><iframe class="docs-video" src="https://www.youtube.com/embed/{video_id}" title="{title}" '
            'allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share" '
            'referrerpolicy="strict-origin-when-cross-origin" allowfullscreen loading="lazy" '
            'style="width: 100%; aspect-ratio: 16 / 9; border: 0;"></iframe></p>'
        )

    return YOUTUBE_LINE.sub(player, content)


def on_page_content(content: str, page: Page, config: MkDocsConfig, files: Files) -> str:
    """In the built page's HTML, replace figure GIFs with click-through figures, chapter video links with video elements, and video service links with players (a mkdocs hook).

    Called after mkdocs has rewritten relative paths of links and images to site paths, so paths can be used as they are.
    """
    return youtube_to_iframes(videos_to_players(figures_to_steps(content, Path(config.docs_dir).resolve())))
