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

A page can show the opening of its video moving on GitHub instead of the still: GitHub plays no mp4 in a
repository view, but it animates a GIF. `node gif.mjs <name>` (or `--all`; run it under `agora-browser` or with
`DOCS_VIDEO_CHROME` set) makes a trailer, not a cut-off opening: the first scenes up to the last scene boundary within
15 seconds (the whole first scene when it is longer), then a 1.5 second end card "Watch the full video (m:ss)" that
shows the video goes on and how long it is. It writes `docs/assets/videos/<name>.gif` (640 px wide, 10 frames a
second, under 1.5 MB), and the page links the GIF to the video in the same way:

```markdown
[![<what the video shows, in one sentence>](assets/videos/<name>.gif)](assets/videos/<name>.mp4)
```

On the docs site the hook still makes a player of it, with `<name>.jpg` as the poster. Every page with a
video does this; `mkdocs.yml` leaves the GIFs out of the site, which never shows them.

`tests/test_docs_site_videos.py` checks that every such link points at its files (for a GIF, the still as
well), that each mp4 is at most 6 MB and that each GIF is at most 3 MB.

## Setup

| Need | How |
|---|---|
| Node.js 20 or later and npm | `npm ci` in this directory installs Remotion at the locked version. |
| chrome-headless-shell | `npx playwright install chromium-headless-shell`, then set `PLAYWRIGHT_BROWSERS_PATH` to where it went, or set `DOCS_VIDEO_CHROME` to the binary. `browser.mjs` finds it. |
| An OpenAI API key | `OPENAI_API_KEY` for `tts.py`. The narration always comes from OpenAI's text-to-speech API. |
| uv | Runs `tts.py` (standard library only). |

The narration never uses a local text-to-speech model, and
`tests/test_docs_site_videos.py` fails if `tts.py` depends on or imports one.

## Layout

| Path | What it holds |
|---|---|
| `src/kit/theme.ts` | The layout size (1280×720; the video is drawn at 1920×1080), 60 fps, `f(seconds)`, the colours (one per role) and the fonts. |
| `src/kit/motion.ts` | `fadeAt`, `ramp`, `mix`: timing in seconds of a scene's design time. |
| `src/kit/parts.tsx` | `Headlines`, `CodeBlock`, `Pill` (an effect or answer on the move), `glow`, `Clock`, `highlightYield`. |
| `src/kit/warp.ts` | Narration time: a headline's slot stretches to fit its spoken line; scenes read `useSceneFrame()`. |
| `src/kit/narrated.tsx` | `narrate(page, durations, narration, scenes)`: plays the scenes in order with their narration, and shows each line's words as subtitles while it is heard. |
| `src/kit/subtitles.tsx` | The subtitles: each narration line as `narration.json` writes it, cut at its sentences into parts of at most two rows, at the frames its sound plays. |
| `src/pages/<name>/` | One page's video: its scenes, `narration.json`, `durations.json`, and `measure/` (the runs behind its numbers). |
| `src/Root.tsx` | One `<Composition>` per page, with the id `<name>`: it finds every `narrate(...)` export under `src/pages/` itself, so a new page does not edit it. |
| `tts.py` | Says `narration.json` through OpenAI's speech endpoint into `public/<name>/`, writes `durations.json`. |
| `render.mjs` | Renders `docs/assets/videos/<name>.mp4` and `<name>.jpg` at 1920×1080 (1.5 times the layout), and writes the same subtitles as `<name>.vtt` and `<name>.srt` (`captions.mjs`). |
| `gif.mjs` | Makes `<name>.gif` from the first scenes of `docs/assets/videos/<name>.mp4` plus an end card, the moving trailer for GitHub. |
| `src/kit/EndCard.tsx` | The end card (the `gif-end-card` composition `gif.mjs` renders as a still). |
| `stills.mjs` | Renders single frames as PNG, to check a layout without rendering the whole video. |

`src/pages/home/` is the reference: the home page's video, scene by scene.

## Making the video of a page

1. Read the page. Pick the 3 to 5 ideas a reader should leave with, in the page's order. Start from the
   idea, not from an API list: the first scene says what the page is about and why it matters.
2. For every behaviour the video shows (an output, an order of events, a timing), write a small program
   under `src/pages/<name>/measure/`, run it against doeff (`uv run src/pages/<name>/measure/<program>.py > src/pages/<name>/measure/<program>.log`).
   The `.log` files are generated and not committed (`.gitignore`); rerun a program to make its log again.
   The video shows only values that a run produced.
3. Write `src/pages/<name>/narration.json`: a list of `{"id": "<scene letter><n>", "text": "…"}`, one short
   English sentence or two per headline. Write `doeff` as it is spelled; `tts.py` respells it for the voice.
4. Write the scenes in `src/pages/<name>/<Name>.tsx` with the kit's parts. Each scene is designed in seconds
   of its own time with `Lines` (headline times and texts); export the page's video as
   `narrate('<name>', DURATIONS, [...scenes])`. `src/Root.tsx` picks it up as the composition `<name>`; do not edit it.
5. Make the narration: `OPENAI_API_KEY=… uv run tts.py <name>` (add line ids to redo only those lines).
6. Check the layout: `node stills.mjs <name> out/check <frame>…` at the frames where headlines change and
   where things move, and look at every PNG — nothing overlaps, nothing runs off the frame, text is readable.
7. Render: `node render.mjs <name> <poster frame>`. Choose a poster frame that shows the page's idea at a glance.
   Where memory is short, set `DOCS_VIDEO_CONCURRENCY` (e.g. 4) to render with fewer browser tabs at once.
   The narration wavs are not committed: to render a page again whose `narration.json` and `durations.json` have not
   changed, without saying it again, add `--audio-from ../../docs/assets/videos/<name>.mp4` to take the sound of the committed mp4.
8. Attach it to the page with the link above, below the page's opening paragraph.
9. Check: `uv run pytest tests/test_docs_site_videos.py` and `make docs-site`.

## The look and the rules

| Rule | Why |
|---|---|
| Laid out at 1280×720, rendered at 1920×1080 (full HD) and 60 fps, the kit's colours and fonts. | Every page's video looks like one series; full HD keeps code and subtitles sharp, and 60 fps keeps motion smooth. |
| Show the mechanism moving: effects travel out to handlers, answers come back, `k` waits and resumes. | The point is to see what the text can only describe. |
| doeff is algebraic effects for Python. Do not present it as only a way to swap I/O. | Swapping I/O is one use of handlers, not what doeff is (`docs/positioning/what-doeff-is.md`). |
| Each headline stays long enough to read, and its narration fits its slot (`warp.ts` stretches the slot). | The voice never runs into the next headline. |
| The narration is burned in as subtitles at the bottom, on a dark band, and the scenes are drawn smaller against the top so the band covers nothing (`narrated.tsx`). The subtitles are the words of `narration.json`, never a summary; `tts.py`'s respellings are for the voice only. | A video played without sound still explains itself, and a GitHub attachment cannot carry a subtitle track. The `.vtt` is the site player's captions track (`tools/docs_site/hooks.py`); the `.srt` is for a video service. |
| Numbers on screen come from a run in `measure/`. | The video must hold true for anyone who runs the same code. |
| English on screen and in the narration; no people drawn. | The docs are in English; the videos show code and its runs. |
| Up to about 3 minutes, and at most 6 MB (`render.mjs` encodes at CRF 29; a video over 6 MB is rendered again with a higher CRF as the third argument). | The mp4 lives in the repository. |

## Click-through figures

A figure that the reader steps through by clicking (`docs/assets/figures/<name>.html`) is the other moving thing a page can
show. The page writes one line, the figure's GIF as a link: `[![<what it shows>](../assets/figures/<name>.gif)](../assets/figures/<name>.gif)`.
GitHub and Zenn show the GIF; on the site, `tools/docs_site/hooks.py` puts the figure in place of that line, and
`docs/assets/figures/step-figure.js` and `step-figure.css` move it.

1. Write the stage (`.sf-stage`) and one `<li>` per step in `.sf-steps`. Each `<li>` is the caption, and its `data-state` says what
   the stage looks like at that step:

   | Key | Meaning |
   |---|---|
   | `active` | the panel (`data-panel`) drawn with a strong border |
   | `now` | anchors (`data-anchor`) to light up |
   | `marks` | a class for an anchor: `is-k`, `is-dead`, `is-on`, `is-off` (a ring that takes no room), `is-new`, `is-skip`, `is-fail`, `is-pass` |
   | `values` | text for each `data-slot`; a step lists only what changes |
   | `tokens` | the pills shown and where: `at` and `place` (`"end"` = just past the anchor's text), `from` and `via` (the trip it makes in this step), `delay` (seconds before it shows and moves), `drop`, `merge`. A pill a step leaves out is hidden |

2. `make docs-site`, then in this directory `node figure-gif.mjs articles/<page>/index.html <name>` (under `agora-browser`, with
   `PLAYWRIGHT_MODULES` naming the `node_modules` that has playwright). The GIF is recorded from the built figure, so the two never differ.
   It holds every step, at 20 frames a second and 960 px wide, and is at most 10 MB (narrower only when the steps do not fit).
   A figure with many repeated parts can be written by a script: `tools/docs_site/figures_measure/article_figures.py` writes the
   three figures of the article's videos (`article-opening`, `monte-carlo`, `hundred-fights`).
3. Check: `uv run pytest tests/test_docs_site_step_figures.py` (every step names only what the figure has, and every figure has its
   GIF and the other way round) and `make docs-site`.

## Recordings of real programs

`record/terminal_window.py` makes `docs/assets/videos/article-terminal-window.gif` without Remotion: it runs `python spire.py play 39` in a real xterm and `python spire.py window 39` in its real tkinter window on an Xvfb screen, presses keys and clicks with xdotool, and adds only the code lines, the captions and the mouse pointer around the captures. It stops without writing the GIF if either program does not play the fight it worked out beforehand. Run it again when the window in `examples/effects_tour/spire.py` changes; its header says what to install.
