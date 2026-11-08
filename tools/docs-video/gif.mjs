// Makes docs/assets/videos/<id>.gif, the moving preview shown on GitHub: GitHub renders a GIF in the repository view
// but does not play an mp4 there. The GIF is a trailer, not a cut-off opening: it plays the video's first scenes and
// stops at the last scene boundary (a scene's own fade-out) within <seconds> (default 15; the whole first scene when it is longer), then holds an end card
// "Watch the full video (m:ss)" for <hold> seconds (default 1.5), so a reader sees it ended on purpose and how long the
// video is. It is <width> px wide (default 640) at <fps> frames a second (default 10), with one palette made for it.
// A GIF over 3 MB is made again narrower, then at fewer frames a second.
// Usage: node gif.mjs <composition id>... | --all   (env GIF_SECONDS, GIF_WIDTH, GIF_FPS, GIF_HOLD override the defaults)
import {execFileSync} from 'node:child_process';
import {createRequire} from 'node:module';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {build} from 'esbuild';
import {bundle} from '@remotion/bundler';
import {renderStill, selectComposition} from '@remotion/renderer';
import {findChrome} from './browser.mjs';

const seconds = Number(process.env.GIF_SECONDS ?? 15);
const hold = Number(process.env.GIF_HOLD ?? 1.5);
let width = Number(process.env.GIF_WIDTH ?? 640);
let fps = Number(process.env.GIF_FPS ?? 10);
const MAX_BYTES = 3_000_000;
const FPS = 60; // kit/theme.ts
const videos = path.resolve('../../docs/assets/videos');
const pagesDir = path.resolve('src/pages');
const ids = process.argv.slice(2);
if (ids[0] === '--all') ids.splice(0, 1, ...fs.readdirSync(pagesDir).sort());
if (ids.length === 0) throw new Error('usage: node gif.mjs <composition id>... | --all');

const ffmpeg = (...args) => execFileSync('npx', ['remotion', 'ffmpeg', '-y', '-loglevel', 'error', ...args], {stdio: 'inherit'});
const lengthOf = (file) =>
  Number(execFileSync('npx', ['remotion', 'ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', file]).toString());
// Rounded to the second first, so that 179.6 s reads 3:00, not 2:60.
const clock = (s) => {
  const whole = Math.round(s);
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, '0')}`;
};

// The frames the page's scenes end on, read from its narrate() export.
const sceneEnds = async (id) => {
  const dir = path.join(pagesDir, id);
  for (const name of fs.readdirSync(dir).filter((n) => n.endsWith('.tsx'))) {
    const out = await build({entryPoints: [path.join(dir, name)], bundle: true, platform: 'node', format: 'cjs', write: false, external: ['react', 'remotion', 'react/jsx-runtime'], loader: {'.json': 'json'}, logLevel: 'error'});
    const module = {exports: {}};
    new Function('module', 'exports', 'require', out.outputFiles[0].text)(module, module.exports, createRequire(path.resolve('x.js')));
    const video = Object.values(module.exports).find((v) => v && v.id === id && v.ends);
    if (video) return video.ends.map((frame) => frame / FPS);
  }
  throw new Error(`no narrate() export with id ${id} under ${dir}`);
};

const serveUrl = await bundle({entryPoint: path.resolve('src/index.ts')});
const options = {serveUrl, browserExecutable: findChrome(), chromeMode: 'headless-shell'};
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'docs-gif-'));

for (const id of ids) {
  const mp4 = path.join(videos, `${id}.mp4`);
  const gif = path.join(videos, `${id}.gif`);
  const total = lengthOf(mp4);
  const ends = await sceneEnds(id);
  // The last scene boundary within the limit; when even the first scene is longer, the whole first scene (the GIF
  // never stops inside a scene).
  const cut = Math.max(0, ...ends.filter((end) => end <= seconds + 0.05 && end < total - 1)) || ends[0];
  const card = path.join(tmp, `${id}.png`);
  const composition = await selectComposition({...options, id: 'gif-end-card', inputProps: {label: `Watch the full video (${clock(total)})`}});
  await renderStill({...options, composition, frame: 0, output: card});
  for (;;) {
    const filter =
      `[0:v]scale=${width}:-1:flags=lanczos[a];[1:v]scale=${width}:-1:flags=lanczos[b];` +
      '[a][b]concat=n=2:v=1:a=0,split[c][d];[c]palettegen=max_colors=128:stats_mode=diff[p];' +
      '[d][p]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle';
    ffmpeg('-t', String(cut), '-i', mp4, '-loop', '1', '-framerate', String(FPS), '-t', String(hold), '-i', card, '-filter_complex', filter, '-r', String(fps), '-loop', '0', gif);
    if (fs.statSync(gif).size <= MAX_BYTES || (width <= 320 && fps <= 5)) break;
    if (width > 320) width = Math.round(width * 0.8 / 2) * 2;
    else fps = 5;
  }
  console.log('wrote', gif, `${(fs.statSync(gif).size / 1e6).toFixed(2)} MB`, `${cut.toFixed(1)} s + ${hold} s card of ${clock(total)}`, `${width} px`, `${fps} fps`);
}
fs.rmSync(tmp, {recursive: true, force: true});
