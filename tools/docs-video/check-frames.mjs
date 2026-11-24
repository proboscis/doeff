// Prints the frames of a page's video to look at: 15 frames after each motion of the named scenes has stopped.
// Usage: node check-frames.mjs <name> <scene of PACED>...
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import {build} from 'esbuild';

const [name, ...scenes] = process.argv.slice(2);
const dir = path.resolve('src/pages', name);
const entry = fs.readdirSync(dir).find((file) => file.endsWith('.tsx'));
const out = path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'frames-')), `${name}.mjs`);
await build({entryPoints: [path.join(dir, entry)], bundle: true, format: 'esm', platform: 'node', outfile: out, jsx: 'automatic', logLevel: 'error'});
const page = await import(pathToFileURL(out).href);
const video = Object.values(page).find((value) => value && value.ends && value.cues);
const order = Object.keys(page.PACED);
const frames = [];
for (const scene of scenes) {
  const i = order.indexOf(scene);
  const start = i === 0 ? 0 : video.ends[i - 1];
  for (const {to} of page.PACED[scene].motions) frames.push(start + Math.round(to * 60) + 15);
  console.error(scene, 'starts on frame', start, 'and ends on', video.ends[i]);
}
console.log([...new Set(frames)].sort((a, b) => a - b).join(' '));
