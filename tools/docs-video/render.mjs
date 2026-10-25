// Renders a page's video for the docs: docs/assets/videos/<id>.mp4 (h264 at CRF 29 with AAC audio, moov atom at
// the front so a browser can start playing before the whole file is in) and <id>.jpg, the still shown on GitHub
// and as the player's poster. The still is the frame given (default: 2 s in). A video that comes out over 6 MB at
// CRF 29 is rendered again at a higher CRF given as the third argument.
// Usage: node render.mjs <composition id> [<poster frame>] [<crf>]
import {execFileSync} from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import {bundle} from '@remotion/bundler';
import {renderMedia, renderStill, selectComposition} from '@remotion/renderer';
import {findChrome} from './browser.mjs';

const [id, posterFrame = '120', crf = '29'] = process.argv.slice(2);
if (!id) throw new Error('usage: node render.mjs <composition id> [<poster frame>] [<crf>]');
const browserExecutable = findChrome();
const videos = path.resolve('../../docs/assets/videos');
fs.mkdirSync(videos, {recursive: true});
const work = fs.mkdtempSync(path.join(path.resolve('out'), `${id}-`));

const serveUrl = await bundle({entryPoint: path.resolve('src/index.ts')});
const options = {serveUrl, browserExecutable, chromeMode: 'headless-shell'};
const composition = await selectComposition({...options, id});
const raw = path.join(work, `${id}.mp4`);
let shown = -1;
await renderMedia({
  ...options, composition, codec: 'h264', crf: Number(crf), audioBitrate: '96k', pixelFormat: 'yuv420p', outputLocation: raw, concurrency: 12,
  onProgress: ({progress}) => {
    const percent = Math.floor(progress * 4) * 25;
    if (percent > shown) console.log(`${(shown = percent)}%`);
  },
});
// Move the moov atom to the front, copying the streams as they are (Remotion ships ffmpeg).
const mp4 = path.join(videos, `${id}.mp4`);
execFileSync('npx', ['remotion', 'ffmpeg', '-y', '-loglevel', 'error', '-i', raw, '-c', 'copy', '-movflags', '+faststart', mp4], {stdio: 'inherit'});
const jpg = path.join(videos, `${id}.jpg`);
await renderStill({...options, composition, frame: Number(posterFrame), output: jpg, imageFormat: 'jpeg', jpegQuality: 85});
fs.rmSync(work, {recursive: true});
const seconds = (composition.durationInFrames / composition.fps).toFixed(1);
console.log('wrote', mp4, `${(fs.statSync(mp4).size / 1e6).toFixed(1)} MB`, `CRF ${crf}`, `${seconds} s`, 'and', jpg);
