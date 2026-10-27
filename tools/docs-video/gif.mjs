// Cuts the opening of a page's video into docs/assets/videos/<id>.gif, the moving still shown on GitHub: GitHub
// renders a GIF in the repository view but does not play an mp4 there. The GIF is the first <seconds> (default 15),
// <width> px wide (default 640) at <fps> frames a second (default 10), with one palette made for the clip.
// A GIF over 3 MB is cut again shorter, narrower or at fewer frames a second.
// Usage: node gif.mjs <composition id> [<seconds>] [<width>] [<fps>]
import {execFileSync} from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';

const [id, seconds = '15', width = '640', fps = '10'] = process.argv.slice(2);
if (!id) throw new Error('usage: node gif.mjs <composition id> [<seconds>] [<width>] [<fps>]');
const videos = path.resolve('../../docs/assets/videos');
const mp4 = path.join(videos, `${id}.mp4`);
const gif = path.join(videos, `${id}.gif`);
const filter =
  `scale=${width}:-1:flags=lanczos,split[a][b];` +
  '[a]palettegen=max_colors=128:stats_mode=diff[p];' +
  '[b][p]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle';
// Remotion ships ffmpeg, with the palette filters and the GIF encoder but no fps filter: -r drops the frames.
execFileSync('npx', ['remotion', 'ffmpeg', '-y', '-loglevel', 'error', '-t', seconds, '-i', mp4, '-vf', filter, '-r', fps, '-loop', '0', gif], {stdio: 'inherit'});
console.log('wrote', gif, `${(fs.statSync(gif).size / 1e6).toFixed(2)} MB`, `${seconds} s`, `${width} px`, `${fps} fps`);
