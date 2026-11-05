// The subtitle files of a page's video, from the cues its composition carries (src/kit/subtitles.tsx): the same
// cues the video draws, at the same frames. <id>.vtt is the docs site player's captions track; <id>.srt is for
// a video service that takes a subtitle file. Each cue's start and end are its first frame and the frame after its last.
import fs from 'node:fs';
import path from 'node:path';

// Frame to "HH:MM:SS.mmm" (WebVTT) or "HH:MM:SS,mmm" (SRT).
const stamp = (frame, fps, mark) => {
  const ms = Math.round((frame * 1000) / fps);
  const pad = (n, width = 2) => String(n).padStart(width, '0');
  return `${pad(Math.floor(ms / 3600000))}:${pad(Math.floor(ms / 60000) % 60)}:${pad(Math.floor(ms / 1000) % 60)}${mark}${pad(ms % 1000, 3)}`;
};

export const writeCaptions = (dir, id, cues, fps) => {
  const vtt = ['WEBVTT', '', ...cues.flatMap((cue) => [cue.id, `${stamp(cue.from, fps, '.')} --> ${stamp(cue.to, fps, '.')}`, cue.text, ''])];
  const srt = cues.flatMap((cue, i) => [String(i + 1), `${stamp(cue.from, fps, ',')} --> ${stamp(cue.to, fps, ',')}`, cue.text, '']);
  const files = [path.join(dir, `${id}.vtt`), path.join(dir, `${id}.srt`)];
  fs.writeFileSync(files[0], vtt.join('\n'));
  fs.writeFileSync(files[1], srt.join('\n'));
  return files;
};
