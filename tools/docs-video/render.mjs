// Renders a page's video for the docs: docs/assets/videos/<id>.mp4 (1920x1080 at 60 fps: the scenes are laid out at
// 1280x720 and drawn at 1.5 times that size; h264 at CRF 29 with AAC audio, moov atom at the front so a browser can
// start playing before the whole file is in) and <id>.jpg, the still shown on GitHub and as the player's poster. The still is the frame given (default: 2 s in). A video that comes out over 6 MB at
// CRF 29 is rendered again at a higher CRF given as the third argument. The subtitles drawn in the video are also
// written as <id>.vtt and <id>.srt (captions.mjs), from the same cues.
// The narration (public/<id>/*.wav, made by tts.py) is not in the repository. With --audio-from <mp4>, the video is
// rendered without sound and takes its sound from that mp4 instead, e.g. the committed one, so a page whose narration
// and durations.json have not changed can be rendered again without saying it again.
// Usage: node render.mjs <composition id> [<poster frame>] [<crf>] [--audio-from <mp4>]
import {execFileSync} from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import {bundle} from '@remotion/bundler';
import {renderMedia, renderStill, selectComposition} from '@remotion/renderer';
import {findChrome} from './browser.mjs';
import {writeCaptions} from './captions.mjs';

const args = process.argv.slice(2);
const audioAt = args.indexOf('--audio-from');
const audioFrom = audioAt < 0 ? null : path.resolve(args.splice(audioAt, 2)[1]);
const [id, posterFrame = '120', crf = '29'] = args;
if (!id) throw new Error('usage: node render.mjs <composition id> [<poster frame>] [<crf>] [--audio-from <mp4>]');
// The scenes are laid out at 1280x720 (src/kit/theme.ts); the video is drawn at 1920x1080.
const scale = 1.5;
const browserExecutable = findChrome();
const videos = path.resolve('../../docs/assets/videos');
fs.mkdirSync(videos, {recursive: true});
fs.mkdirSync(path.resolve('out'), {recursive: true});
const work = fs.mkdtempSync(path.join(path.resolve('out'), `${id}-`));
// Copy the sound's source first: it may be the mp4 this render writes over.
const sound = audioFrom && path.join(work, 'sound.mp4');
if (sound) fs.copyFileSync(audioFrom, sound);
// Remotion fetches every <Audio> even when the render is muted, so a line whose wav is missing gets a silent
// stand-in (the slots come from durations.json, not from the wavs); the stand-ins are removed after the render.
const standIns = [];
if (sound) {
  const silence = Buffer.alloc(46);
  silence.write('RIFF', 0); silence.writeUInt32LE(38, 4); silence.write('WAVEfmt ', 8); silence.writeUInt32LE(16, 16);
  silence.writeUInt16LE(1, 20); silence.writeUInt16LE(1, 22); silence.writeUInt32LE(24000, 24); silence.writeUInt32LE(48000, 28);
  silence.writeUInt16LE(2, 32); silence.writeUInt16LE(16, 34); silence.write('data', 36); silence.writeUInt32LE(2, 40);
  const lines = JSON.parse(fs.readFileSync(path.resolve('src/pages', id, 'narration.json'), 'utf8'));
  fs.mkdirSync(path.resolve('public', id), {recursive: true});
  for (const {id: line} of lines) {
    const wav = path.resolve('public', id, `${line}.wav`);
    if (!fs.existsSync(wav)) (fs.writeFileSync(wav, silence), standIns.push(wav));
  }
}

const serveUrl = await bundle({entryPoint: path.resolve('src/index.ts')});
const options = {serveUrl, browserExecutable, chromeMode: 'headless-shell'};
const composition = await selectComposition({...options, id});
const captions = writeCaptions(videos, id, composition.props.cues, composition.fps);
const raw = path.join(work, `${id}.mp4`);
let shown = -1;
await renderMedia({
  ...options, composition, scale, muted: Boolean(sound), codec: 'h264', crf: Number(crf), audioBitrate: '96k', pixelFormat: 'yuv420p', outputLocation: raw, concurrency: 12,
  onProgress: ({progress}) => {
    const percent = Math.floor(progress * 4) * 25;
    if (percent > shown) console.log(`${(shown = percent)}%`);
  },
});
// Move the moov atom to the front, copying the streams as they are (Remotion ships ffmpeg); with --audio-from, take
// the picture from the render and the sound from that mp4.
const mp4 = path.join(videos, `${id}.mp4`);
const streams = sound ? ['-i', raw, '-i', sound, '-map', '0:v:0', '-map', '1:a:0'] : ['-i', raw];
execFileSync('npx', ['remotion', 'ffmpeg', '-y', '-loglevel', 'error', ...streams, '-c', 'copy', '-movflags', '+faststart', mp4], {stdio: 'inherit'});
const jpg = path.join(videos, `${id}.jpg`);
await renderStill({...options, composition, scale, frame: Number(posterFrame), output: jpg, imageFormat: 'jpeg', jpegQuality: 85});
fs.rmSync(work, {recursive: true});
for (const wav of standIns) fs.rmSync(wav);
const seconds = (composition.durationInFrames / composition.fps).toFixed(1);
console.log('wrote', mp4, `${(fs.statSync(mp4).size / 1e6).toFixed(1)} MB`, `CRF ${crf}`, `${seconds} s`, sound ? `sound from ${audioFrom}` : 'sound from public/', 'and', jpg, 'and', ...captions, `(${composition.props.cues.length} cues)`);
