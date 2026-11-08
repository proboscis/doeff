// Prints, for each scene a page paces one motion at a time (the PACED export of src/pages/<name>/<Name>.tsx), how many
// motions it has, how long it is, the shortest gap between a motion and the one before it has stopped, and the mean
// time a motion has (the scene's seconds over its motions).
// Usage: node pace.mjs <name>…
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import {build} from 'esbuild';

const names = process.argv.slice(2);
if (names.length === 0) throw new Error('usage: node pace.mjs <name>…');
const work = fs.mkdtempSync(path.join(os.tmpdir(), 'pace-'));
console.log('| video | scene | motions | seconds | shortest gap (s) | seconds a motion |');
console.log('|---|---|---|---|---|---|');
for (const name of names) {
  const dir = path.resolve('src/pages', name);
  const entry = fs.readdirSync(dir).find((file) => file.endsWith('.tsx'));
  const out = path.join(work, `${name}.mjs`);
  await build({entryPoints: [path.join(dir, entry)], bundle: true, format: 'esm', platform: 'node', outfile: out, jsx: 'automatic', logLevel: 'error'});
  const {PACED} = await import(pathToFileURL(out).href);
  for (const [scene, {motions, seconds}] of Object.entries(PACED)) {
    let end = -Infinity;
    let gap = Infinity;
    for (const {from, to} of [...motions].sort((a, b) => a.from - b.from)) {
      // In frames, as the video shows them (theme.ts: 60 fps, f() rounds each time to a frame).
      if (end > -Infinity) gap = Math.min(gap, (Math.round(from * 60) - Math.round(end * 60)) / 60);
      end = Math.max(end, to);
    }
    console.log(`| ${name} | ${scene} | ${motions.length} | ${seconds.toFixed(1)} | ${gap.toFixed(2)} | ${(seconds / motions.length).toFixed(2)} |`);
  }
}
fs.rmSync(work, {recursive: true});
