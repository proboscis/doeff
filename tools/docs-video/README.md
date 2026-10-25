# docs-video — narrated motion videos for the doeff docs

Each docs page that teaches an idea opens with a short video: the idea shown in motion, with a narrator
reading one line per headline. This directory makes those videos with [Remotion](https://www.remotion.dev/)
(React rendered frame by frame) and writes them to `docs/assets/videos/<name>.mp4`, next to a still
`<name>.jpg`.

## What this README is for

After reading it you can make the video of one docs page in the same look and at the same level as the
home page's video, check it, and attach it to the page.

## How a page shows its video

A page links its still to its video, right below the page's opening paragraph:

```markdown
[![<what the video shows, in one sentence>](assets/videos/<name>.jpg)](assets/videos/<name>.mp4)
```

| Where the page is read | What the reader sees |
|---|---|
| GitHub (repository view) | The still, linking to the mp4. GitHub removes `<video>` from Markdown, so a link is all a repository page can carry. |
| The docs site (`make docs-site`) | A player with the still as its poster. The site hook (`tools/docs_site/hooks.py`) turns the link into a `<video>`. |

`tests/test_docs_site_videos.py` checks that every such link points at both files and that each mp4 is at
most 6 MB.

## Setup

| Need | How |
|---|---|
| Node.js 20 or later and npm | `npm ci` in this directory installs Remotion at the locked version. |
| chrome-headless-shell | `npx playwright install chromium-headless-shell`, then set `PLAYWRIGHT_BROWSERS_PATH` to where it went, or set `DOCS_VIDEO_CHROME` to the binary. `browser.mjs` finds it. |
| An OpenAI API key | `OPENAI_API_KEY` for `tts.py`. The narration always comes from OpenAI's text-to-speech API. |
| uv | Runs `tts.py` (standard library only). |

The narration never uses a local text-to-speech model: the maintainer decided so on 2026-10-25, and
`tests/test_docs_site_videos.py` fails if `tts.py` depends on or imports one.

## Layout

| Path | What it holds |
|---|---|
| `src/kit/theme.ts` | Size (1280×720), 60 fps, `f(seconds)`, the colours (one per role) and the fonts. |
| `src/kit/motion.ts` | `fadeAt`, `ramp`, `mix`: timing in seconds of a scene's design time. |
| `src/kit/parts.tsx` | `Headlines`, `CodeBlock`, `Pill` (an effect or answer on the move), `glow`, `Clock`, `highlightYield`. |
| `src/kit/warp.ts` | Narration time: a headline's slot stretches to fit its spoken line; scenes read `useSceneFrame()`. |
| `src/kit/narrated.tsx` | `narrate(page, durations, scenes)`: plays the scenes in order with their narration. |
| `src/pages/<name>/` | One page's video: its scenes, `narration.json`, `durations.json`, and `measure/` (the runs behind its numbers). |
| `src/Root.tsx` | One `<Composition>` per page, with the id `<name>`. |
| `tts.py` | Says `narration.json` through OpenAI's speech endpoint into `public/<name>/`, writes `durations.json`. |
| `render.mjs` | Renders `docs/assets/videos/<name>.mp4` and `<name>.jpg`. |
| `stills.mjs` | Renders single frames as PNG, to check a layout without rendering the whole video. |

`src/pages/home/` is the reference: the home page's video, scene by scene.

## Making the video of a page

1. Read the page. Pick the 3 to 5 ideas a reader should leave with, in the page's order. Start from the
   idea, not from an API list: the first scene says what the page is about and why it matters.
2. For every behaviour the video shows (an output, an order of events, a timing), write a small program
   under `src/pages/<name>/measure/`, run it against doeff, and keep its output next to it as a `.log`.
   The video shows only values that a run produced.
3. Write `src/pages/<name>/narration.json`: a list of `{"id": "<scene letter><n>", "text": "…"}`, one short
   English sentence or two per headline. Write `doeff` as it is spelled; `tts.py` respells it for the voice.
4. Write the scenes in `src/pages/<name>/<Name>.tsx` with the kit's parts. Each scene is designed in seconds
   of its own time with `Lines` (headline times and texts); export the page's video as
   `narrate('<name>', DURATIONS, [...scenes])` and add its `<Composition>` to `src/Root.tsx`.
5. Make the narration: `OPENAI_API_KEY=… uv run tts.py <name>` (add line ids to redo only those lines).
6. Check the layout: `node stills.mjs <name> out/check <frame>…` at the frames where headlines change and
   where things move, and look at every PNG — nothing overlaps, nothing runs off the frame, text is readable.
7. Render: `node render.mjs <name> <poster frame>`. Choose a poster frame that shows the page's idea at a glance.
8. Attach it to the page with the link above, below the page's opening paragraph.
9. Check: `uv run pytest tests/test_docs_site_videos.py` and `make docs-site`.

## The look and the rules

| Rule | Why |
|---|---|
| 1280×720 at 60 fps, the kit's colours and fonts. | Every page's video looks like one series; 60 fps keeps motion smooth. |
| Show the mechanism moving: effects travel out to handlers, answers come back, `k` waits and resumes. | The point is to see what the text can only describe. |
| doeff is algebraic effects for Python. Do not present it as only a way to swap I/O. | Swapping I/O is one use of handlers, not what doeff is (`docs/positioning/what-doeff-is.md`). |
| Each headline stays long enough to read, and its narration fits its slot (`warp.ts` stretches the slot). | The voice never runs into the next headline. |
| Numbers on screen come from a run in `measure/`. | The video must hold true for anyone who runs the same code. |
| English on screen and in the narration; no people drawn. | The docs are in English; the videos show code and its runs. |
| Up to about 3 minutes, and at most 6 MB (`render.mjs` encodes at CRF 29; a video over 6 MB is rendered again with a higher CRF as the third argument). | The mp4 lives in the repository. |
