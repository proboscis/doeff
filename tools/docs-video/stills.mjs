// Renders single frames of a composition as PNG, to check the layout without rendering the whole video.
// Usage: node stills.mjs <composition id> <out dir> <frame>...
import fs from 'node:fs';
import path from 'node:path';
import {bundle} from '@remotion/bundler';
import {renderStill, selectComposition} from '@remotion/renderer';
import {findChrome} from './browser.mjs';

const [id, outDir, ...frames] = process.argv.slice(2);
if (!id || !outDir) throw new Error('usage: node stills.mjs <composition id> <out dir> <frame>...');
const browserExecutable = findChrome();
fs.mkdirSync(outDir, {recursive: true});
const serveUrl = await bundle({entryPoint: path.resolve('src/index.ts')});
const options = {serveUrl, browserExecutable, chromeMode: 'headless-shell'};
const composition = await selectComposition({...options, id});
console.log('frames in all', composition.durationInFrames, `(${(composition.durationInFrames / composition.fps).toFixed(1)} s)`);
for (const frame of frames) {
  const output = path.join(outDir, `${id}-${frame.padStart(5, '0')}.png`);
  await renderStill({...options, composition, frame: Number(frame), output});
  console.log('wrote', output);
}
