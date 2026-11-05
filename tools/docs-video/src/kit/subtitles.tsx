// The subtitles: the narration's own words (narration.json, as written, not as tts.py respells them for the voice)
// at the bottom of the frame while that line is heard. A line too long for two rows of the band is cut at its
// sentences (or, inside one long sentence, at a comma) and its sound's time is shared out by the length of each
// part. render.mjs writes the same cues as <page>.vtt and <page>.srt next to the video.
import React from 'react';
import {useCurrentFrame} from 'remotion';
import {HEIGHT, SANS, TEXT} from './theme';

// One subtitle: the narration line it comes from, its video frames [from, to), and its text.
export type Cue = {id: string; from: number; to: number; text: string};

// The most characters one cue shows (two rows of the band at 28 px), and one row shows.
export const CUE_CHARS = 110;
const ROW_CHARS = 76;
const FONT = 28;
const ROW = 36;
const PAD_Y = 4;
const BOTTOM = 12;
// The top of the band when it shows two rows; the scenes are drawn above it (narrated.tsx).
export const BAND_TOP = HEIGHT - BOTTOM - 2 * ROW - 2 * PAD_Y;

// A sentence too long for one cue, cut at the comma nearest its middle (and again, while a part is too long).
const halves = (sentence: string): string[] => {
  if (sentence.length <= CUE_CHARS) return [sentence];
  const commas = [...sentence.matchAll(/, /g)].map((match) => (match.index ?? 0) + 1);
  if (commas.length === 0) return [sentence];
  const cut = commas.reduce((best, at) => (Math.abs(at - sentence.length / 2) < Math.abs(best - sentence.length / 2) ? at : best));
  return [...halves(sentence.slice(0, cut)), ...halves(sentence.slice(cut + 1))];
};

const SENTENCES = /(?<=[.!?])\s+/;
const joined = (pieces: string[]) => pieces.join(' ');

// The parts a line is shown in: whole sentences (a long one cut at a comma), in as few parts as fit, as even as they can be.
export const partsOf = (text: string): string[] => {
  const pieces = text.split(SENTENCES).flatMap(halves);
  // A run of pieces fits when it is within the limit or is one piece (a sentence with no comma to cut it at).
  const fits = (run: string[]) => run.length === 1 || joined(run).length <= CUE_CHARS;
  const longest = (parts: string[]) => Math.max(...parts.map((part) => part.length));
  // Of the ways to cut pieces[from..] into `count` runs that fit, the one whose longest part is shortest.
  const best = (from: number, count: number): string[] | null => {
    if (count === 1) return fits(pieces.slice(from)) ? [joined(pieces.slice(from))] : null;
    let found: string[] | null = null;
    for (let to = from + 1; to <= pieces.length - (count - 1) && fits(pieces.slice(from, to)); to++) {
      const rest = best(to, count - 1);
      const parts = rest && [joined(pieces.slice(from, to)), ...rest];
      if (parts && (!found || longest(parts) < longest(found))) found = parts;
    }
    return found;
  };
  // Every piece on its own always fits, so some count up to the number of pieces does.
  for (let count = 1; count < pieces.length; count++) {
    const parts = best(0, count);
    if (parts) return parts;
  }
  return pieces;
};

// The cues of one line heard over the video frames [from, to).
export const cuesOf = (id: string, text: string, from: number, to: number): Cue[] => {
  const parts = partsOf(text);
  const total = parts.reduce((sum, part) => sum + part.length, 0);
  let done = 0;
  return parts.map((part, i) => {
    const start = from + Math.round(((to - from) * done) / total);
    done += part.length;
    return {id: parts.length > 1 ? `${id}.${i + 1}` : id, from: start, to: from + Math.round(((to - from) * done) / total), text: part};
  });
};

// A cue of two sentences that each fit a row is shown a sentence a row; any other cue wraps where the band ends.
const rows = (text: string): string[] => {
  const sentences = text.split(SENTENCES);
  return sentences.length === 2 && sentences.every((sentence) => sentence.length <= ROW_CHARS) ? sentences : [text];
};

// The cue being heard at this frame, on a half-transparent dark band.
export const Subtitles: React.FC<{cues: Cue[]}> = ({cues}) => {
  const frame = useCurrentFrame();
  const cue = cues.find(({from, to}) => frame >= from && frame < to);
  if (!cue) return null;
  return (
    <div style={{position: 'absolute', left: 40, right: 40, bottom: BOTTOM, display: 'flex', justifyContent: 'center'}}>
      <div
        style={{
          maxWidth: '100%', padding: `${PAD_Y}px 18px`, borderRadius: 8, background: 'rgba(0, 0, 0, 0.62)', color: TEXT, fontFamily: SANS,
          fontSize: FONT, lineHeight: `${ROW}px`, textAlign: 'center', textWrap: 'balance',
        }}
      >
        {rows(cue.text).map((row) => (
          <div key={row}>{row}</div>
        ))}
      </div>
    </div>
  );
};
