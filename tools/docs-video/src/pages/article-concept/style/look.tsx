// The look of the motion version of article-concept, as parts: a backdrop with depth, glass panels, display type,
// syntax-coloured code with a scanner line, capsules that travel on arcs with a comet tail, and handlers as rings.
// StyleFrames.tsx draws the storyboard's style frames with these; the scenes of the video are to be built from them.
import React from 'react';
import {AbsoluteFill} from 'remotion';
import {loadFont as loadInter} from '@remotion/google-fonts/Inter';
import {loadFont as loadMono} from '@remotion/google-fonts/JetBrainsMono';
import {noise2D} from '@remotion/noise';
import {getLength, getPointAtLength} from '@remotion/paths';
import {ACCENT, LOOK, MUTED, TEST, TEXT} from '../../../kit/theme';

export const DISPLAY = loadInter('normal', {weights: ['400', '600', '800'], subsets: ['latin']}).fontFamily;
export const CODE = loadMono('normal', {weights: ['400', '700'], subsets: ['latin']}).fontFamily;

export const INK = '#06070c';
export const LINE = 'rgba(255, 255, 255, 0.13)';
export const STRING = '#8fe3c8';
export const KEYWORD = '#c9bdff';

// The backdrop: three soft lights that drift (noise, by time t in seconds), a dot grid, and a vignette.
export const Backdrop: React.FC<{t: number}> = ({t}) => {
  const lights: [number, number, number, string][] = [
    [240, 120, 620, ACCENT],
    [1080, 600, 680, TEST],
    [760, 260, 420, '#4d6bff'],
  ];
  return (
    <AbsoluteFill style={{background: INK}}>
      {lights.map(([x, y, size, color], i) => {
        const dx = noise2D(`x${i}`, t * 0.05, 0) * 70;
        const dy = noise2D(`y${i}`, 0, t * 0.05) * 50;
        return (
          <div
            key={i}
            style={{
              position: 'absolute', left: x + dx - size / 2, top: y + dy - size / 2, width: size, height: size, borderRadius: '50%',
              background: `radial-gradient(closest-side, ${color}38, ${color}00)`,
            }}
          />
        );
      })}
      <svg width="1280" height="720" style={{position: 'absolute', opacity: 0.5}}>
        <defs>
          <pattern id="dots" width="32" height="32" patternUnits="userSpaceOnUse">
            <circle cx="1.2" cy="1.2" r="1.2" fill="rgba(255,255,255,0.16)" />
          </pattern>
          <radialGradient id="dotsFade" cx="50%" cy="45%" r="62%">
            <stop offset="0%" stopColor="#fff" stopOpacity="1" />
            <stop offset="100%" stopColor="#fff" stopOpacity="0" />
          </radialGradient>
          <mask id="dotsMask">
            <rect width="1280" height="720" fill="url(#dotsFade)" />
          </mask>
        </defs>
        <rect width="1280" height="720" fill="url(#dots)" mask="url(#dotsMask)" />
      </svg>
      <AbsoluteFill style={{background: 'radial-gradient(ellipse at 50% 42%, rgba(6,7,12,0) 45%, rgba(6,7,12,0.85) 100%)'}} />
    </AbsoluteFill>
  );
};

// A glass panel: a faint gradient, a hairline border, and a soft shadow that lifts it off the backdrop.
export const glass = (edge: string = LINE, lit = false): React.CSSProperties => ({
  boxSizing: 'border-box', borderRadius: 18, border: `1.5px solid ${lit ? LOOK : edge}`,
  background: 'linear-gradient(160deg, rgba(255,255,255,0.085), rgba(255,255,255,0.02) 60%)',
  boxShadow: lit ? `0 0 34px ${LOOK}66, 0 24px 60px rgba(0,0,0,0.5)` : '0 24px 60px rgba(0,0,0,0.5)',
});

// The chapter of the video at the top left, and the video's scenes as a rail with the current one lit.
export const Chapter: React.FC<{n: number; of: number; name: string}> = ({n, of, name}) => (
  <>
    <div style={{position: 'absolute', left: 60, top: 26, display: 'flex', alignItems: 'center', gap: 12, fontFamily: DISPLAY, fontSize: 15, letterSpacing: '0.16em', color: MUTED}}>
      <span style={{color: LOOK, fontWeight: 800}}>{String(n).padStart(2, '0')}</span>
      <span style={{fontWeight: 600}}>{name.toUpperCase()}</span>
    </div>
    <div style={{position: 'absolute', right: 60, top: 32, display: 'flex', gap: 6}}>
      {Array.from({length: of}, (_, i) => (
        <div key={i} style={{width: i === n - 1 ? 34 : 14, height: 4, borderRadius: 2, background: i === n - 1 ? LOOK : i < n - 1 ? 'rgba(255,255,255,0.45)' : 'rgba(255,255,255,0.16)'}} />
      ))}
    </div>
  </>
);

// The headline, as words: [the word(s), its colour when it is a word the line turns on].
export type Word = [string, string?];
export const Kinetic: React.FC<{words: Word[]; top?: number; size?: number}> = ({words, top = 62, size = 40}) => (
  <div style={{position: 'absolute', left: 60, right: 60, top, textAlign: 'center', fontFamily: DISPLAY, fontSize: size, fontWeight: 800, letterSpacing: '-0.02em', lineHeight: 1.15, color: TEXT}}>
    {words.map(([text, color], i) => (
      <span key={i} style={color ? {color, textShadow: `0 0 26px ${color}88`} : undefined}>
        {text}{' '}
      </span>
    ))}
  </div>
);

// Something on the move: an effect, an answer, k. A capsule in its role's colour, with a glow.
export const Capsule: React.FC<{x: number; y: number; text: string; color: string; size?: number; mono?: boolean; ghost?: boolean}> = ({x, y, text, color, size = 20, mono = true, ghost = false}) => (
  <div
    style={{
      position: 'absolute', left: x, top: y, transform: 'translate(-50%, -50%)', whiteSpace: 'nowrap', fontFamily: mono ? CODE : DISPLAY, fontSize: size,
      fontWeight: 700, padding: `${size * 0.32}px ${size * 0.8}px`, borderRadius: size * 1.2,
      background: ghost ? 'transparent' : `linear-gradient(180deg, ${color}, ${color}cc)`, color: ghost ? color : INK,
      border: ghost ? `1.5px dashed ${color}` : '1px solid rgba(255,255,255,0.55)',
      boxShadow: ghost ? 'none' : `0 0 30px ${color}aa, 0 6px 18px rgba(0,0,0,0.45)`,
    }}
  >
    {text}
  </div>
);

// A trip from (x0, y0) to (x1, y1) on an arc that bows by `bow` px (up when negative). The whole way is a faint line;
// the part just travelled is a comet tail behind the capsule at progress p. done draws the whole way bright.
export const arcOf = (x0: number, y0: number, x1: number, y1: number, bow: number) =>
  `M ${x0} ${y0} C ${x0 + (x1 - x0) * 0.3} ${y0 + bow}, ${x0 + (x1 - x0) * 0.7} ${y1 + bow}, ${x1} ${y1}`;

export const Arc: React.FC<{d: string; color: string; p?: number; tail?: number; done?: boolean; dashed?: boolean; id: string}> = ({d, color, p = 0, tail = 260, done = false, dashed = false, id}) => {
  const length = getLength(d);
  const head = getPointAtLength(d, length * p);
  const from = getPointAtLength(d, Math.max(0, length * p - tail));
  const cut = Math.min(tail, length * p);
  return (
    <svg width="1280" height="720" style={{position: 'absolute', overflow: 'visible'}}>
      <defs>
        <linearGradient id={id} gradientUnits="userSpaceOnUse" x1={from.x} y1={from.y} x2={head.x} y2={head.y}>
          <stop offset="0%" stopColor={color} stopOpacity="0" />
          <stop offset="100%" stopColor={color} stopOpacity="1" />
        </linearGradient>
      </defs>
      <path d={d} fill="none" stroke={color} strokeOpacity={done ? 0.9 : 0.28} strokeWidth={done ? 3 : 2} strokeDasharray={dashed ? '3 9' : undefined} strokeLinecap="round" />
      {!done && p > 0 && (
        <path d={d} fill="none" stroke={`url(#${id})`} strokeWidth={7} strokeLinecap="round" strokeDasharray={`${cut} ${length}`} strokeDashoffset={-(length * p - cut)} style={{filter: `drop-shadow(0 0 10px ${color})`}} />
      )}
    </svg>
  );
};

// The point of an arc at progress p: where its capsule is drawn.
export const pointOn = (d: string, p: number) => getPointAtLength(d, getLength(d) * p);

const KEYWORDS = new Set(['def', 'return', 'if', 'else', 'while', 'and', 'for', 'in', 'not']);
const EFFECTS = new Set(['Price', 'Confirm', 'Roll', 'Choose', 'Show', 'Played', 'Resume']);

// One line of Python, coloured by what each word is. yield is the word the videos turn on, so it glows.
export const Coloured: React.FC<{line: string}> = ({line}) => (
  <>
    {(line.match(/(\s+|"[^"]*"|'[^']*'|@\w+|\w+|[^\s\w])/g) ?? []).map((token, i) => {
      const style: React.CSSProperties =
        token === 'yield' ? {color: ACCENT, fontWeight: 700, textShadow: `0 0 18px ${ACCENT}`}
        : token.startsWith('@') ? {color: ACCENT, fontWeight: 700}
        : KEYWORDS.has(token) ? {color: KEYWORD}
        : EFFECTS.has(token) ? {color: '#ffffff', fontWeight: 700}
        : /^["']/.test(token) ? {color: STRING}
        : {};
      return <span key={i} style={style}>{token}</span>;
    })}
  </>
);

// A code window: its file name, its lines, the line the program is on (a scanner bar), and lines lifted out as k.
export const CodeWindow: React.FC<{
  file: string; lines: string[]; x: number; y: number; w: number; size: number; line: number; scan?: number; k?: [number, number]; kColor?: string; dim?: number[];
}> = ({file, lines, x, y, w, size, line, scan, k, kColor = '#7cc4ff', dim = []}) => (
  <div style={{position: 'absolute', left: x, top: y, width: w, ...glass(), overflow: 'hidden'}}>
    <div style={{height: 38, display: 'flex', alignItems: 'center', gap: 8, padding: '0 16px', borderBottom: `1px solid ${LINE}`, background: 'rgba(0,0,0,0.25)'}}>
      {['#ff6b81', '#ffb86b', '#3ecfb2'].map((color) => <div key={color} style={{width: 11, height: 11, borderRadius: '50%', background: color, opacity: 0.85}} />)}
      <div style={{marginLeft: 10, fontFamily: CODE, fontSize: 15, color: MUTED}}>{file}</div>
    </div>
    <div style={{position: 'relative', padding: '14px 0'}}>
      {scan !== undefined && (
        <div style={{position: 'absolute', left: 0, right: 0, top: 14 + scan * line, height: line, background: `linear-gradient(90deg, ${LOOK}55, ${LOOK}10 70%, transparent)`, borderLeft: `4px solid ${LOOK}`, boxShadow: `0 0 26px ${LOOK}33`}} />
      )}
      {k && (
        <div style={{position: 'absolute', left: 10, right: 10, top: 14 + k[0] * line, height: (k[1] - k[0] + 1) * line, borderRadius: 10, background: `${kColor}26`, border: `1.5px solid ${kColor}`, boxShadow: `0 0 26px ${kColor}55`}} />
      )}
      {lines.map((text, i) => (
        <div key={i} style={{position: 'relative', height: line, display: 'flex', alignItems: 'center', paddingLeft: 22, fontFamily: CODE, fontSize: size, color: TEXT, whiteSpace: 'pre', opacity: dim.includes(i) ? 0.35 : 1}}>
          <span style={{width: 34, color: 'rgba(255,255,255,0.25)', fontSize: size * 0.72}}>{i + 1}</span>
          <Coloured line={text} />
        </div>
      ))}
    </div>
  </div>
);

// The vertical middle of line i of a CodeWindow at y.
export const codeMid = (y: number, line: number, i: number) => y + 38 + 14 + i * line + line / 2;

// Handlers as rings around a function, innermost first; `lit` is the ring at work.
export type RingOf = {label: string; color?: string};
export const Rings: React.FC<{cx: number; cy: number; core: string; rings: RingOf[]; w?: number; h?: number; gap?: number; font?: number; lit?: number; note?: string}> = ({
  cx, cy, core, rings, w = 200, h = 84, gap = 44, font = 17, lit, note,
}) => (
  <>
    {[...rings.keys()].reverse().map((i) => {
      const rw = w + 2 * gap * (i + 1);
      const rh = h + 2 * gap * (i + 1);
      const color = rings[i].color ?? TEST;
      const on = lit === i;
      return (
        <div
          key={i}
          style={{
            position: 'absolute', left: cx - rw / 2, top: cy - rh / 2, width: rw, height: rh, boxSizing: 'border-box', borderRadius: 22 + 10 * i,
            border: `2px solid ${on ? LOOK : color}`, background: `linear-gradient(160deg, ${color}1f, ${color}08)`,
            boxShadow: on ? `0 0 40px ${LOOK}88, inset 0 0 30px ${LOOK}22` : `0 18px 40px rgba(0,0,0,0.4)`,
          }}
        >
          <div style={{position: 'absolute', left: 16, top: gap * 0.2, fontFamily: CODE, fontSize: font, fontWeight: 700, color: on ? LOOK : color, whiteSpace: 'nowrap'}}>{rings[i].label}</div>
        </div>
      );
    })}
    <div
      style={{
        position: 'absolute', left: cx - w / 2, top: cy - h / 2, width: w, height: h, boxSizing: 'border-box', borderRadius: 16, border: `2px solid ${ACCENT}`,
        background: `linear-gradient(160deg, #1b1840, #0c0c18)`, boxShadow: `0 0 30px ${ACCENT}55`, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
      }}
    >
      <div style={{fontFamily: CODE, fontSize: font * 1.45, fontWeight: 700, color: TEXT}}>{core}</div>
      {note && <div style={{fontFamily: DISPLAY, fontSize: font * 0.82, color: LOOK, marginTop: 2}}>{note}</div>}
    </div>
  </>
);

// The edge of the program: inside it on the left, the handlers outside it on the right.
export const Seam: React.FC<{x: number; top?: number; bottom?: number}> = ({x, top = 140, bottom = 680}) => (
  <>
    <div style={{position: 'absolute', left: x, top, width: 2, height: bottom - top, background: `linear-gradient(180deg, transparent, ${ACCENT}aa 20%, ${TEST}aa 80%, transparent)`, boxShadow: `0 0 24px ${ACCENT}88`}} />
    <div style={{position: 'absolute', left: x - 210, top: top + 4, width: 196, textAlign: 'right', fontFamily: DISPLAY, fontSize: 13, fontWeight: 600, letterSpacing: '0.18em', color: `${ACCENT}`}}>INSIDE THE PROGRAM</div>
    <div style={{position: 'absolute', left: x + 16, top: top + 4, fontFamily: DISPLAY, fontSize: 13, fontWeight: 600, letterSpacing: '0.18em', color: TEST}}>OUTSIDE</div>
  </>
);

// A program as its steps in plain words: one row each, in the places the code lines take when the scene cuts to code.
export type RowOf = {text: string; lit?: string; out?: boolean};
export const ROW = 52;
export const Program: React.FC<{title: string; rows: RowOf[]; x: number; y: number; w: number}> = ({title, rows, x, y, w}) => (
  <div style={{position: 'absolute', left: x, top: y, width: w, ...glass(`${ACCENT}88`), overflow: 'hidden'}}>
    <div style={{height: 38, display: 'flex', alignItems: 'center', padding: '0 20px', borderBottom: `1px solid ${LINE}`, fontFamily: DISPLAY, fontSize: 15, fontWeight: 600, letterSpacing: '0.12em', color: ACCENT}}>{title.toUpperCase()}</div>
    {rows.map((row, i) => (
      <div key={i} style={{position: 'relative', height: ROW, display: 'flex', alignItems: 'center', paddingLeft: 22}}>
        {row.lit && <div style={{position: 'absolute', inset: 0, background: `linear-gradient(90deg, ${row.lit}55, ${row.lit}0d 75%, transparent)`, borderLeft: `4px solid ${row.lit}`}} />}
        <div style={{position: 'relative', fontFamily: DISPLAY, fontSize: 22, fontWeight: 600, color: TEXT, opacity: row.out ? 0.3 : 1, textDecoration: row.out ? 'line-through' : 'none'}}>{row.text}</div>
      </div>
    ))}
  </div>
);
export const rowMid = (y: number, i: number) => y + 38 + i * ROW + ROW / 2;

// A handler outside the program: its name, and what it does.
export const Handler: React.FC<{x: number; y: number; w: number; name: string; note: string; lit?: boolean; color?: string}> = ({x, y, w, name, note, lit = false, color = TEST}) => (
  <div style={{position: 'absolute', left: x, top: y, width: w, padding: '16px 22px', ...glass(`${color}aa`, lit)}}>
    <div style={{fontFamily: DISPLAY, fontSize: 24, fontWeight: 800, color: lit ? LOOK : color}}>{name}</div>
    <div style={{fontFamily: DISPLAY, fontSize: 17, color: MUTED, marginTop: 4}}>{note}</div>
  </div>
);
