// The parts the scenes of the docs videos are drawn with: the headline at the top, a code box, an effect or
// answer on the move, the glow of whatever is at work, and a clock face.
import React from 'react';
import {fadeAt} from './motion';
import {ACCENT, BG, f, LOOK, MONO, MUTED, SANS, TEXT} from './theme';
import {useSceneFrame} from './warp';

// Headlines of a scene: [the second it appears, the text]. The narration reads one line per headline.
export type Lines = [number, string][];

// The design frames where a scene's headlines appear, for its narrated scene (narrated.tsx).
export const startsOf = (lines: Lines) => lines.map(([seconds]) => f(seconds));

// The story line at the top: the latest line whose time has come, faded in at that time.
export const Headlines: React.FC<{lines: Lines}> = ({lines}) => {
  const frame = useSceneFrame();
  const i = lines.reduce((found, [from], j) => (frame >= f(from) ? j : found), -1);
  if (i < 0) return null;
  const [from, text] = lines[i];
  return (
    <div
      key={i}
      style={{
        position: 'absolute', left: 60, right: 60, top: 18, textAlign: 'center', fontFamily: SANS, fontSize: 26,
        fontWeight: 700, lineHeight: '34px', color: TEXT, opacity: fadeAt(frame, from),
      }}
    >
      {text}
    </div>
  );
};

// Marks the yield in a line, the place where the program hands an effect to the handlers.
export const highlightYield = (line: string) => {
  const [before, after] = line.split('yield');
  return (
    <>
      {before}
      <span style={{color: ACCENT, fontWeight: 700}}>yield</span>
      {after}
    </>
  );
};

export const PAD = 12;

type CodeProps = {
  lines: string[]; x: number; y: number; w: number; size: number; line: number;
  band?: number | null; dim?: (i: number) => number; opacity?: number;
};

// A code box. The band marks the line (possibly between two lines while it moves) the program is on.
export const CodeBlock: React.FC<CodeProps> = ({lines, x, y, w, size, line, band = null, dim, opacity = 1}) => (
  <div
    style={{
      position: 'absolute', left: x, top: y, width: w, height: PAD * 2 + lines.length * line, background: '#0b0d13',
      borderRadius: 14, border: '2px solid #2a2e40', overflow: 'hidden', opacity,
    }}
  >
    {band !== null && <div style={{position: 'absolute', left: 0, right: 0, top: PAD + band * line, height: line, background: LOOK, opacity: 0.2}} />}
    <div style={{position: 'absolute', top: PAD, left: 24, fontFamily: MONO, fontSize: size, lineHeight: `${line}px`}}>
      {lines.map((text, i) => (
        <div key={i} style={{whiteSpace: 'pre', color: text.startsWith('@') ? ACCENT : TEXT, opacity: dim ? dim(i) : 1}}>
          {text.includes('yield') ? highlightYield(text) : text}
        </div>
      ))}
    </div>
  </div>
);

// The vertical middle of line i of a code box at y.
export const lineMid = (y: number, line: number, i: number) => y + PAD + i * line + line / 2;

// An effect or an answer on the move: centred on (x, y), or with its left edge at x.
export const Pill: React.FC<{x: number; y: number; text: string; color: string; left?: boolean; opacity?: number}> = ({x, y, text, color, left = false, opacity = 1}) => (
  <div
    style={{
      position: 'absolute', left: x, top: y, transform: left ? 'translateY(-50%)' : 'translate(-50%, -50%)', whiteSpace: 'nowrap',
      fontFamily: MONO, fontSize: 19, fontWeight: 700, padding: '5px 14px', borderRadius: 14, background: color, color: BG,
      boxShadow: `0 0 16px ${color}`, opacity,
    }}
  >
    {text}
  </div>
);

// A glow for whatever is at work: a yellow border and halo.
export const glow = (on: boolean, color: string) => ({
  borderColor: on ? LOOK : color, boxShadow: on ? `0 0 22px ${LOOK}aa` : 'none',
});

// An analog clock face for a time given in seconds past 09:00.
export const Clock: React.FC<{seconds: number; c: string; secondHand: boolean}> = ({seconds, c, secondHand}) => {
  const hour = ((9 * 3600 + seconds) / 43200) * 360;
  const minute = ((seconds % 3600) / 3600) * 360;
  const second = ((seconds % 60) / 60) * 360;
  const hand = (angle: number, length: number, width: number, stroke: string) => (
    <line x1={60} y1={60} x2={60} y2={60 - length} stroke={stroke} strokeWidth={width} strokeLinecap="round" transform={`rotate(${angle} 60 60)`} />
  );
  return (
    <svg width={120} height={120} viewBox="0 0 120 120">
      <circle cx={60} cy={60} r={55} fill="#0b0d13" stroke={c} strokeWidth={3} />
      {Array.from({length: 12}, (_, i) => (
        <line key={i} x1={60} y1={9} x2={60} y2={16} stroke={MUTED} strokeWidth={2} transform={`rotate(${i * 30} 60 60)`} />
      ))}
      {hand(hour, 28, 5, TEXT)}
      {hand(minute, 42, 3.5, TEXT)}
      {secondHand && hand(second, 46, 1.5, c)}
      <circle cx={60} cy={60} r={4} fill={c} />
    </svg>
  );
};
