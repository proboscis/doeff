// The motions of the motion version of article-concept, in a scene's design time (seconds): things that rise into
// place, a headline that arrives word by word, a capsule that travels an arc and lands with a ring, a line that draws
// itself, and a camera that eases between framings.
import React from 'react';
import {Easing, interpolate, spring} from 'remotion';
import {getLength} from '@remotion/paths';
import {clamp, fadeAt} from '../../../kit/motion';
import {Step} from '../../../kit/pace';
import {f, FPS, TEXT} from '../../../kit/theme';
import {useSceneFrame} from '../../../kit/warp';
import {Arc, Capsule, DISPLAY, pointOn, Word} from './look';

// Progress from 0 to 1 that starts at a time and settles like a spring, without overshooting. A spring only comes
// near 1, so it stops at 1 once it is within a hundredth of a pixel's worth: what has settled then draws the same in
// every frame, and the video spends no bytes on it.
export const pop = (frame: number, at: number, stiffness = 120): number => {
  if (frame < f(at)) return 0;
  const p = spring({frame: frame - f(at), fps: FPS, config: {damping: 22, mass: 0.8, stiffness}});
  return p > 0.9995 ? 1 : p;
};

// How far something has gone at `at` (it fades in 0.3 s); never, for a time that never comes.
const gone = (frame: number, at: number): number => (Number.isFinite(at) ? fadeAt(frame, at, 0.3) : 0);

// Eased progress from 0 to 1 across a step.
export const along = (frame: number, {from, to}: Step): number =>
  interpolate(frame, [f(from), f(to)], [0, 1], {...clamp, easing: Easing.inOut(Easing.cubic)});

// What is inside rises into place at a time (and leaves at `out`): from `y` px below and a little smaller, around
// the point `origin` of the frame.
export const Rise: React.FC<{at: number; out?: number; y?: number; scale?: number; origin?: [number, number]; children: React.ReactNode}> = ({
  at, out = Infinity, y = 26, scale = 1, origin = [640, 360], children,
}) => {
  const frame = useSceneFrame();
  if (frame < f(at) || frame >= f(out + 0.3)) return null;
  const p = pop(frame, at);
  const opacity = fadeAt(frame, at, 0.25) * (1 - gone(frame, out));
  return (
    <div style={{position: 'absolute', inset: 0, opacity, transform: `translateY(${(1 - p) * y}px) scale(${scale + (1 - scale) * p})`, transformOrigin: `${origin[0]}px ${origin[1]}px`}}>
      {children}
    </div>
  );
};

// The headlines of a scene, as words: [the second it appears, its words]. Each arrives word by word, rising.
export type Said = [number, Word[]][];
export const saidStarts = (lines: Said) => lines.map(([seconds]) => f(seconds));
export const Headline: React.FC<{lines: Said; size?: number; top?: number}> = ({lines, size = 38, top = 62}) => {
  const frame = useSceneFrame();
  const i = lines.reduce((found, [from], j) => (frame >= f(from) ? j : found), -1);
  if (i < 0 || lines[i][1].length === 0) return null;
  const [from, words] = lines[i];
  return (
    <div key={i} style={{position: 'absolute', left: 50, right: 50, top, textAlign: 'center', fontFamily: DISPLAY, fontSize: size, fontWeight: 800, letterSpacing: '-0.02em', lineHeight: 1.15, color: TEXT}}>
      {words.map(([text, color], j) => {
        const at = from + j * 0.05;
        return (
          <span
            key={j}
            style={{
              display: 'inline-block', whiteSpace: 'pre', opacity: fadeAt(frame, at, 0.2), transform: `translateY(${(1 - pop(frame, at, 160)) * 20}px)`,
              ...(color ? {color, textShadow: `0 0 26px ${color}88`} : {}),
            }}
          >
            {text}{' '}
          </span>
        );
      })}
    </div>
  );
};

// A ring that opens where something has landed, and fades.
export const Ping: React.FC<{x: number; y: number; color: string; at: number}> = ({x, y, color, at}) => {
  const frame = useSceneFrame();
  if (frame < f(at) || frame > f(at + 0.7)) return null;
  const p = interpolate(frame, [f(at), f(at + 0.7)], [0, 1], {...clamp, easing: Easing.out(Easing.cubic)});
  return <div style={{position: 'absolute', left: x - 12 - p * 36, top: y - 12 - p * 36, width: 24 + p * 72, height: 24 + p * 72, borderRadius: '50%', border: `2px solid ${color}`, opacity: 1 - p, boxShadow: `0 0 20px ${color}`}} />;
};

// Something travels an arc during a step: a comet tail behind the capsule, a ring where it lands. Until `until` the
// capsule stays where it landed; the way it took stays as a thin line unless `leave` is set.
export const Travel: React.FC<{
  id: string; d: string; color: string; step: Step; text: string; until?: number; size?: number; mono?: boolean; leave?: boolean; tail?: number;
}> = ({id, d, color, step, text, until = Infinity, size = 19, mono = true, leave = false, tail = 240}) => {
  const frame = useSceneFrame();
  if (frame < f(step.from)) return null;
  const p = along(frame, step);
  const left = gone(frame, until);
  const at = pointOn(d, p);
  const end = pointOn(d, 1);
  const landed = frame >= f(step.to);
  return (
    <>
      <div style={{position: 'absolute', inset: 0, opacity: fadeAt(frame, step.from, 0.15) * (leave ? 1 - left : 1)}}>
        <Arc id={id} d={d} color={color} p={p} tail={tail} done={landed} />
      </div>
      <Ping x={end.x} y={end.y} color={color} at={step.to} />
      {left < 1 && (
        <div style={{position: 'absolute', inset: 0, opacity: fadeAt(frame, step.from, 0.15) * (1 - left)}}>
          <Capsule x={at.x} y={at.y} text={text} color={color} size={size} mono={mono} />
        </div>
      )}
    </>
  );
};

// A line that draws itself during a step, and stays (a dashed one for a way not taken yet, or one that stops short).
export const Draw: React.FC<{d: string; color: string; step: Step; dashed?: boolean; upTo?: number; width?: number; out?: number}> = ({
  d, color, step, dashed = false, upTo = 1, width = 2.5, out = Infinity,
}) => {
  const frame = useSceneFrame();
  if (frame < f(step.from)) return null;
  const length = getLength(d);
  const shown = length * upTo * along(frame, step);
  return (
    <svg width="1280" height="720" style={{position: 'absolute', overflow: 'visible', opacity: 1 - gone(frame, out)}}>
      <defs>
        <mask id={`draw-${d.length}-${Math.round(length)}-${Math.round(step.from * 100)}`} maskUnits="userSpaceOnUse" x="-100" y="-100" width="1480" height="920">
          <path d={d} fill="none" stroke="#fff" strokeWidth={width + 8} strokeDasharray={`${shown} ${length}`} />
        </mask>
      </defs>
      <path
        d={d} fill="none" stroke={color} strokeWidth={width} strokeLinecap="round" strokeDasharray={dashed ? '3 9' : undefined}
        mask={`url(#draw-${d.length}-${Math.round(length)}-${Math.round(step.from * 100)})`} style={{filter: dashed ? undefined : `drop-shadow(0 0 8px ${color})`}}
      />
    </svg>
  );
};

// The camera: the framing eases from one key to the next. A key is [the second it is reached, zoom, x, y], where
// (x, y) is the point of the frame that sits at the centre.
export type Framing = [number, number, number, number];
export const Camera: React.FC<{keys: Framing[]; children: React.ReactNode}> = ({keys, children}) => {
  const frame = useSceneFrame();
  const times = keys.map(([seconds]) => f(seconds));
  const read = (k: 1 | 2 | 3) => (keys.length === 1 ? keys[0][k] : interpolate(frame, times, keys.map((key) => key[k]), {...clamp, easing: Easing.inOut(Easing.cubic)}));
  const zoom = read(1);
  return <div style={{position: 'absolute', inset: 0, transformOrigin: '0 0', transform: `translate(${640 - read(2) * zoom}px, ${360 - read(3) * zoom}px) scale(${zoom})`}}>{children}</div>;
};

// Text typed a character at a time during a step.
export const typed = (frame: number, text: string, step: Step): string => text.slice(0, Math.round(text.length * interpolate(frame, [f(step.from), f(step.to)], [0, 1], clamp)));
