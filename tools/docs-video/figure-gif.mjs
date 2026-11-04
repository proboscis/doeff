// Makes docs/assets/figures/<name>.gif from the click-through figure of the same name in the built site: the version of the
// figure for places that run no script (the GitHub repository view, Zenn). It shows the same stage and captions as the site, so
// there is nothing to keep in sync. Each step plays its transitions, frame by frame on the animations' own clock, then holds for
// long enough to read its caption. Run it under agora-browser, or with DOCS_VIDEO_CHROME set, after make docs-site.
// Usage: node figure-gif.mjs <page in the built site, relative to site/> <name> [env FIGURE_THEME=dark|light, FIGURE_WIDTH=<px>]
import {execFileSync} from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {createRequire} from 'node:module';
import {findChrome} from './browser.mjs';

// playwright is not in this package's dependencies; PLAYWRIGHT_MODULES names the directory it is installed in.
const require = createRequire(path.join(process.env.PLAYWRIGHT_MODULES ?? process.cwd(), 'x.js'));
const {chromium} = require('playwright');

const [pagePath, name] = process.argv.slice(2);
if (!name) throw new Error('usage: node figure-gif.mjs <page in the built site> <name>');
const FPS = 10;
// A step's frames run until its last move ends (a token that waits its turn, or travels through other anchors, moves for longer).
const moveFrames = (endMs) => Math.min(60, Math.ceil(endMs / (1000 / FPS)) + 1);
const HOLD_SECONDS = (chars) => Math.max(2.5, chars / 25); // long enough to read the caption at about 25 characters a second
const MAX_BYTES = 3_000_000;
const width = Number(process.env.FIGURE_WIDTH ?? 760);
const site = path.resolve(process.env.DOCS_SITE_DIR ?? '../../site');
const out = path.resolve('../../docs/assets/figures', `${name}.gif`);
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'figure-gif-'));

const browser = await chromium.launch({executablePath: findChrome()});
const page = await browser.newPage({viewport: {width: width + 40, height: 900}, colorScheme: process.env.FIGURE_THEME ?? 'dark'});
await page.route(/^https?:/, (route) => route.abort());
await page.goto(`file://${path.join(site, pagePath)}`);
await page.addStyleTag({content: '.md-header,.md-sidebar,.md-footer,.md-tabs{display:none!important}.md-grid{max-width:none!important;margin:0!important}'});
const figure = page.locator(`[data-step-figure="${name}"]`);
await figure.evaluate((node) => {
  node.classList.add('is-recording');
  node.style.margin = '0';
});
const steps = await figure.evaluate((node) => node.stepFigure.length);
// A step can make the figure taller (a traceback that opens, a ring that comes in), and every frame of a GIF has one size: the
// figure is held at the height of its tallest step, so no frame is squeezed into the size of the first.
await figure.evaluate((node) => {
  let tallest = 0;
  for (let n = 0; n < node.stepFigure.length; n++) {
    node.stepFigure.show(n);
    tallest = Math.max(tallest, node.getBoundingClientRect().height);
  }
  node.style.boxSizing = 'border-box';
  node.style.height = `${Math.ceil(tallest)}px`;
});

let frame = 0;
const shoot = async () => {
  const file = path.join(tmp, `${String(frame++).padStart(5, '0')}.png`);
  await figure.screenshot({path: file});
  return file;
};
for (let step = 0; step < steps; step++) {
  const end = await figure.evaluate((node, n) => {
    node.stepFigure.show(n);
    const animations = document.getAnimations();
    animations.forEach((animation) => animation.pause());
    return Math.max(0, ...animations.map((animation) => animation.effect.getComputedTiming().endTime));
  }, step);
  for (let i = 0; i < moveFrames(end); i++) {
    await figure.evaluate((_, ms) => document.getAnimations().forEach((animation) => (animation.currentTime = ms)), (i * 1000) / FPS);
    await shoot();
  }
  await figure.evaluate(() => document.getAnimations().forEach((animation) => animation.finish()));
  const held = await shoot();
  const chars = await figure.evaluate((node) => node.querySelector('.sf-steps li.is-current').textContent.length);
  const hold = Math.round(HOLD_SECONDS(chars) * FPS);
  for (let i = 0; i < hold; i++) fs.copyFileSync(held, path.join(tmp, `${String(frame++).padStart(5, '0')}.png`));
}
await browser.close();

const ffmpeg = (...args) => execFileSync('npx', ['remotion', 'ffmpeg', '-y', '-loglevel', 'error', ...args], {stdio: 'inherit'});
const encode = (scale) => {
  const filter = `scale=${scale}:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=128:stats_mode=diff[p];[b][p]paletteuse=dither=none:diff_mode=rectangle`;
  ffmpeg('-framerate', String(FPS), '-i', path.join(tmp, '%05d.png'), '-vf', filter, '-loop', '0', out);
};
let scale = width;
encode(scale);
while (fs.statSync(out).size > MAX_BYTES && scale > 480) {
  scale -= 80;
  encode(scale);
}
console.log(`${out}: ${frame} frames, ${scale} px wide, ${fs.statSync(out).size} bytes`);
fs.rmSync(tmp, {recursive: true});
