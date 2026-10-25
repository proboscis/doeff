// The video on docs/21-semaphore-effects.md: a semaphore is a fixed number of permits, its three operations are
// effects the scheduler answers, and a task without a permit is a parked k. Five scenes, each with headlines a
// synthetic voice reads (narration.json), then the outro:
//   1. CreateSemaphore, AcquireSemaphore and ReleaseSemaphore are effects; a task that finds no permit is parked.
//   2. A pool of 2 permits shared by 4 calls: at most 2 inside, the others queue (measure/pool.py).
//   3. FIFO and direct transfer: a release hands the permit to the oldest waiter, so a late task cannot take it
//      (measure/fifo.py).
//   4. Cancel while waiting takes the task out of the queue; it took no permit, so the next waiter gets it
//      (measure/cancel.py).
//   5. The permit leak: without try/finally a failure loses the permit and the next task waits forever; with it,
//      the permit comes back (measure/leak.py).
import React from 'react';
import {AbsoluteFill, Easing, interpolate} from 'remotion';
import {clamp, fadeAt, mix, ramp, smooth} from '../../kit/motion';
import {narrate} from '../../kit/narrated';
import {Clock, CodeBlock, glow, Headlines, lineMid, Lines, Pill, startsOf} from '../../kit/parts';
import {ACCENT, BG, f, FAIL, LOOK, MONO, MUTED, PANEL, SANS, TEST, TEXT} from '../../kit/theme';
import {useSceneFrame} from '../../kit/warp';
import DURATIONS from './durations.json';

type Point = {x: number; y: number};

// Where something moving is at a frame: keyframes of [second, point], eased between them.
const along = (frame: number, keys: [number, Point][]): Point => {
  const times = keys.map(([s]) => f(s));
  return {
    x: interpolate(frame, times, keys.map(([, p]) => p.x), smooth),
    y: interpolate(frame, times, keys.map(([, p]) => p.y), smooth),
  };
};

// The virtual clock's time as the runs print it, for a time given in seconds past 09:00:00.
const stamp = (seconds: number) => `09:00:${String(Math.floor(seconds + 1e-6)).padStart(2, '0')}`;

// A task's k: held by the scheduler while the task waits, or a task at work.
const KCard: React.FC<{label: string; p: Point; w?: number; opacity?: number; color?: string}> = ({label, p, w = 200, opacity = 1, color = ACCENT}) => (
  <div
    style={{
      position: 'absolute', left: p.x, top: p.y, transform: 'translate(-50%, -50%)', width: w, height: 40, boxSizing: 'border-box',
      borderRadius: 10, background: '#1d1a33', border: `3px solid ${color}`, boxShadow: `0 0 14px ${color}88`, fontFamily: SANS,
      fontSize: 16, fontWeight: 700, color: TEXT, lineHeight: '34px', textAlign: 'center', whiteSpace: 'nowrap', opacity,
    }}
  >
    {label}
  </div>
);

// A permit: the token a task holds while it is inside.
const Permit: React.FC<{p: Point; opacity?: number; color?: string}> = ({p, opacity = 1, color = TEST}) => (
  <div
    style={{
      position: 'absolute', left: p.x, top: p.y, transform: 'translate(-50%, -50%)', width: 76, height: 30, boxSizing: 'border-box',
      borderRadius: 15, background: color, boxShadow: `0 0 14px ${color}`, fontFamily: SANS, fontSize: 14, fontWeight: 700,
      color: BG, lineHeight: '30px', textAlign: 'center', opacity,
    }}
  >
    permit
  </div>
);

// A box with a title: the scheduler, its permits, its queue.
const Box: React.FC<{x: number; y: number; w: number; h: number; title: string; color?: string; dashed?: boolean; on?: boolean; opacity?: number; size?: number}> = ({
  x, y, w, h, title, color = ACCENT, dashed = false, on = false, opacity = 1, size = 18,
}) => (
  <div
    style={{
      position: 'absolute', left: x, top: y, width: w, height: h, boxSizing: 'border-box', borderRadius: 16, background: dashed ? 'transparent' : PANEL,
      border: `${dashed ? 2 : 3}px ${dashed ? 'dashed' : 'solid'} ${color}`, ...(dashed ? {} : glow(on, color)), opacity,
    }}
  >
    <div style={{position: 'absolute', left: 16, top: 8, fontFamily: SANS, fontSize: size, fontWeight: 700, color: on ? LOOK : color, whiteSpace: 'nowrap'}}>{title}</div>
  </div>
);

// Printed lines that appear at the second each one was printed in the run.
type Printed = {at: number; text: string}[];

const LogBox: React.FC<{lines: Printed; x: number; y: number; w: number; size: number; line: number; opacity?: number}> = ({lines, x, y, w, size, line, opacity = 1}) => {
  const frame = useSceneFrame();
  return (
    <div
      style={{
        position: 'absolute', left: x, top: y, width: w, height: 20 + lines.length * line, boxSizing: 'border-box', padding: '10px 16px',
        background: '#0b0d13', borderRadius: 10, fontFamily: MONO, fontSize: size, lineHeight: `${line}px`, color: TEXT, whiteSpace: 'pre', opacity,
      }}
    >
      {lines.map((printed) => (
        <div key={printed.text} style={{opacity: fadeAt(frame, printed.at, 0.25)}}>
          {printed.text}
        </div>
      ))}
    </div>
  );
};

const label = (x: number, y: number, text: string, color: string, opacity: number, size = 20): React.ReactNode => (
  <div style={{position: 'absolute', left: x, top: y, fontFamily: SANS, fontSize: size, fontWeight: 700, color, whiteSpace: 'nowrap', opacity}}>{text}</div>
);

const ClockFace: React.FC<{x: number; y: number; seconds: number; opacity?: number}> = ({x, y, seconds, opacity = 1}) => (
  <div style={{position: 'absolute', left: x, top: y, width: 140, textAlign: 'center', opacity}}>
    <div style={{display: 'inline-block', transform: 'scale(0.8)', transformOrigin: 'top center', height: 96}}>
      <Clock seconds={seconds} c={ACCENT} secondHand />
    </div>
    <div style={{fontFamily: MONO, fontSize: 18, color: TEXT}}>{stamp(seconds)}</div>
    <div style={{fontFamily: SANS, fontSize: 13, color: MUTED, marginTop: 2}}>virtual clock</div>
  </div>
);

// Something on the move between two points over [from, to] seconds, gone shortly after it lands.
const moving = (frame: number, from: number, to: number) => frame >= f(from) && frame < f(to + 0.3);

// Scene 1 — the three operations are effects the scheduler answers; a task with no permit is parked.

const INTRO_LINES: Lines = [
  [0, 'A semaphore: a fixed number of permits that tasks take turns holding.'],
  [5.5, 'CreateSemaphore, AcquireSemaphore and ReleaseSemaphore are effects. The scheduler answers them.'],
  [15, 'A task that finds no permit is parked: the scheduler holds its k, and the other tasks run on.'],
];
const INTRO = f(24);
const EFFECTS = ['CreateSemaphore(2)', 'AcquireSemaphore(sem)', 'ReleaseSemaphore(sem)'];
const CALL = [
  '@do',
  'def call(sem, name):',
  '    yield AcquireSemaphore(sem)',
  '    try:',
  '        return (yield work(name))',
  '    finally:',
  '        yield ReleaseSemaphore(sem)',
];
const ICODE = {x: 40, y: 160, w: 560, size: 18, line: 28};
const ISCHED = {x: 660, y: 160, w: 580, h: 420};
// Where the scheduler keeps its two permits, and its queue of parked k's.
const TRAY = [{x: 800, y: 260}, {x: 900, y: 260}];
const QUEUE_AT: Point = {x: 950, y: 450};
// The three tasks, each at the left; they ask for a permit at the given second.
const ITASKS = [
  {name: 'A', y: 420, ask: 9.5},
  {name: 'B', y: 480, ask: 11.5},
  {name: 'C', y: 540, ask: 15.8},
];
const TASK_X = 320;

const IntroScene: React.FC = () => {
  const frame = useSceneFrame();
  const first = 1 - ramp(frame, 5.0, 5.4);
  const second = fadeAt(frame, 5.6, 0.4);
  // A and B each send AcquireSemaphore and get a permit back; C's Acquire finds none and its k goes to the queue.
  // At 20.5 A sends ReleaseSemaphore and the scheduler hands the permit on to C.
  const permitA = along(frame, [[10.6, TRAY[0]], [11.6, {x: TASK_X + 150, y: 420}], [20.6, {x: TASK_X + 150, y: 420}], [21.6, {x: QUEUE_AT.x + 150, y: QUEUE_AT.y}], [22.0, {x: QUEUE_AT.x + 150, y: QUEUE_AT.y}], [22.8, {x: TASK_X + 150, y: 540}]]);
  const permitB = along(frame, [[12.6, TRAY[1]], [13.6, {x: TASK_X + 150, y: 480}]]);
  const cK = along(frame, [[16.9, {x: TRAY[0].x + 50, y: TRAY[0].y}], [17.7, QUEUE_AT], [22.0, QUEUE_AT], [22.8, {x: TASK_X - 40, y: 540}]]);
  const working = (from: number) => frame >= f(from) && frame < f(from + 0.8);
  const atWork = working(10.4) || working(12.4) || working(16.6) || working(21.4);
  const acquires = [
    {from: {x: TASK_X + 110, y: 420}, leave: 9.6, land: 10.4},
    {from: {x: TASK_X + 110, y: 480}, leave: 11.6, land: 12.4},
    {from: {x: TASK_X + 110, y: 540}, leave: 15.8, land: 16.6},
  ];
  const cState = frame < f(17.7) ? 'C' : frame < f(22.8) ? 'C · parked' : 'C · holds a permit';
  return (
    <AbsoluteFill>
      <Headlines lines={INTRO_LINES} />
      <div style={{position: 'absolute', left: 0, right: 0, top: 150 + mix(16, 0, ramp(frame, 0.2, 0.8)), textAlign: 'center', opacity: fadeAt(frame, 0.2, 0.5) * first}}>
        <div style={{fontFamily: SANS, fontSize: 80, fontWeight: 700, color: TEXT}}>Semaphore Effects</div>
        <div style={{fontFamily: SANS, fontSize: 28, color: MUTED, marginTop: 6}}>docs/21-semaphore-effects.md</div>
      </div>
      {[0, 1].map((i) => (
        <Permit key={i} p={{x: 590 + i * 100, y: 430 + mix(12, 0, ramp(frame, 1.6 + i * 0.5, 2.1 + i * 0.5))}} opacity={fadeAt(frame, 1.6 + i * 0.5) * first} />
      ))}
      <div style={{position: 'absolute', left: 0, right: 0, top: 480, textAlign: 'center', fontFamily: SANS, fontSize: 24, color: TEXT, opacity: fadeAt(frame, 2.8) * first}}>
        2 permits: at most 2 tasks inside at once
      </div>
      {EFFECTS.map((name, i) => (
        <Pill key={name} x={250 + i * 390} y={124 + mix(12, 0, ramp(frame, 6.0 + i * 0.7, 6.5 + i * 0.7))} text={name} color={ACCENT} opacity={fadeAt(frame, 6.0 + i * 0.7)} />
      ))}
      <CodeBlock lines={CALL} {...ICODE} band={frame < f(9.4) ? null : frame < f(20.2) ? 2 : 6} opacity={second * (1 - 0.35 * ramp(frame, 9.0, 9.4))} />
      <Box x={ISCHED.x} y={ISCHED.y} w={ISCHED.w} h={ISCHED.h} title="scheduler" on={atWork} opacity={fadeAt(frame, 6.6, 0.5)} />
      {label(ISCHED.x + 140, ISCHED.y + 11, 'answers Spawn, Wait, … and these three', MUTED, fadeAt(frame, 6.6, 0.5), 15)}
      <Box x={ISCHED.x + 30} y={ISCHED.y + 50} w={520} h={110} title="sem · permits" color={TEST} dashed opacity={fadeAt(frame, 7.4)} size={15} />
      <Box x={ISCHED.x + 30} y={ISCHED.y + 200} w={520} h={190} title="queue · parked k's" color={MUTED} dashed opacity={fadeAt(frame, 7.4)} size={15} />
      {/* The two permits: in the tray until a task takes one. */}
      <Permit p={frame < f(10.6) ? TRAY[0] : permitA} opacity={fadeAt(frame, 7.6)} />
      <Permit p={frame < f(12.6) ? TRAY[1] : permitB} opacity={fadeAt(frame, 7.8)} />
      {ITASKS.map((t) => (
        <React.Fragment key={t.name}>
          {t.name !== 'C' && <KCard label={`task ${t.name}${frame >= f(t.ask + 2) && (t.name !== 'A' || frame < f(20.4)) ? ' · inside' : ''}`} p={{x: TASK_X - 120 + 80, y: t.y}} w={170} color={TEXT} opacity={fadeAt(frame, 8.4)} />}
        </React.Fragment>
      ))}
      {frame < f(16.9) && <KCard label="task C" p={{x: TASK_X - 40, y: 540}} w={170} color={TEXT} opacity={fadeAt(frame, 8.4)} />}
      {frame >= f(16.9) && <KCard label={`k of ${cState}`} p={cK} w={frame < f(22.8) ? 220 : 260} color={frame < f(22.8) ? ACCENT : TEXT} />}
      {acquires.map((a, i) => {
        const t = ramp(frame, a.leave, a.land);
        const to = i < 2 ? TRAY[i] : {x: TRAY[0].x + 50, y: TRAY[0].y};
        return moving(frame, a.leave, a.land) ? (
          <Pill key={i} x={mix(a.from.x, to.x - 120, t)} y={mix(a.from.y, to.y, t)} text="AcquireSemaphore(sem)" color={ACCENT} left opacity={1 - ramp(frame, a.land, a.land + 0.3)} />
        ) : null;
      })}
      {moving(frame, 20.4, 21.2) && (
        <Pill x={mix(TASK_X + 110, ISCHED.x + 40, ramp(frame, 20.4, 21.2))} y={mix(420, 380, ramp(frame, 20.4, 21.2))} text="ReleaseSemaphore(sem)" color={ACCENT} left opacity={1 - ramp(frame, 21.2, 21.5)} />
      )}
      {label(ISCHED.x + 300, ISCHED.y + 86, 'none left', FAIL, fadeAt(frame, 16.6) * (1 - ramp(frame, 21.4, 21.8)), 17)}
      <div style={{position: 'absolute', left: 40, right: 40, top: 610, textAlign: 'center', fontFamily: SANS, fontSize: 22, lineHeight: '32px', color: TEXT, opacity: fadeAt(frame, 18.4)}}>
        C waits as a parked <span style={{color: ACCENT, fontWeight: 700}}>k</span>; A and B run on, and no thread blocks. A&apos;s release resumes C.
      </div>
    </AbsoluteFill>
  );
};

// Scene 2 — a pool of 2 permits shared by 4 calls.

const PL_LINES: Lines = [
  [0, 'CreateSemaphore(2): four calls share two permits, so at most two are inside at once.'],
  [8.5, "C and D find no permit. Their k's wait in the semaphore's queue, in the order they asked."],
  [16.5, 'Each release lets the next one in: C at 09:00:01, D at 09:00:02.'],
];
const PL = f(26);
const POOL_CODE = [
  '@do',
  'def call(sem, name, seconds):',
  '    yield AcquireSemaphore(sem)',
  '    try:',
  '        yield Delay(seconds)',
  '    finally:',
  '        yield ReleaseSemaphore(sem)',
  ' ',
  'sem = yield CreateSemaphore(2)',
  'for name, s in [("A",3), ("B",1), ("C",1), ("D",2)]:',
  '    tasks.append((yield Spawn(call(sem, name, s))))',
];
const PLC = {x: 40, y: 92, w: 600, size: 15, line: 23};
const INSIDE = {x: 680, y: 92, w: 560, h: 120};
const PQUEUE = {x: 680, y: 228, w: 560, h: 100};
const SLOT = [{x: 780, y: 162}, {x: 1050, y: 162}];
const QSLOT = (i: number): Point => ({x: 810 + i * 230, y: 288});
const GATE: Point = {x: 960, y: 360};
// The lines of measure/pool.log, with the second each one is printed.
const PL_PRINTS: Printed = [
  {at: 1.2, text: '09:00:00 A asks for a permit'},
  {at: 1.9, text: '09:00:00 A in    (inside: A)'},
  {at: 2.6, text: '09:00:00 B asks for a permit'},
  {at: 3.3, text: '09:00:00 B in    (inside: A B)'},
  {at: 9.0, text: '09:00:00 C asks for a permit'},
  {at: 10.4, text: '09:00:00 D asks for a permit'},
  {at: 18.1, text: '09:00:01 B out'},
  {at: 19.2, text: '09:00:01 C in    (inside: A C)'},
  {at: 20.7, text: '09:00:02 C out'},
  {at: 21.7, text: '09:00:02 D in    (inside: A D)'},
  {at: 22.7, text: '09:00:03 A out'},
  {at: 24.1, text: '09:00:04 D out'},
  {at: 24.5, text: "09:00:04 Gather -> ['A', 'B', 'C', 'D']"},
];
// Each call: when it shows up at the gate, when it gets in (and to which slot), and when it leaves.
const CALLS = [
  {name: 'A', ask: 1.2, enter: 1.9, slot: 0, out: 22.7, from: 0, to: 3},
  {name: 'B', ask: 2.6, enter: 3.3, slot: 1, out: 18.1, from: 0, to: 1},
  {name: 'C', ask: 9.0, queue: 0, enter: 19.2, slot: 1, out: 20.7, from: 1, to: 2},
  {name: 'D', ask: 10.4, queue: 1, enter: 21.7, slot: 1, out: 24.1, from: 2, to: 4},
];
const plX = (s: number) => 110 + 120 * s;
const PL_LANE = (i: number) => 410 + i * 46;

const PoolScene: React.FC = () => {
  const frame = useSceneFrame();
  // The virtual clock reaches 1, 2, 3 and 4 s past 09:00:00 as each release is printed.
  const seconds = interpolate(frame, [f(17.0), f(18.0), f(19.6), f(20.6), f(21.8), f(22.6), f(23.2), f(24.0)], [0, 1, 1, 2, 2, 3, 3, 4], smooth);
  const queued = frame >= f(8.5) && frame < f(16.5);
  return (
    <AbsoluteFill>
      <Headlines lines={PL_LINES} />
      <CodeBlock lines={POOL_CODE} {...PLC} band={frame < f(0.6) ? null : frame < f(4.0) ? 10 : frame < f(17) ? 2 : 6} opacity={fadeAt(frame, 0)} />
      <Box {...INSIDE} title="inside · 2 permits" color={TEST} opacity={fadeAt(frame, 0.3)} size={17} />
      <Box {...PQUEUE} title="queue · parked k's, oldest first" color={MUTED} dashed opacity={fadeAt(frame, 0.3)} size={16} />
      {queued && <div style={{position: 'absolute', left: PQUEUE.x, top: PQUEUE.y, width: PQUEUE.w, height: PQUEUE.h, borderRadius: 16, boxShadow: `0 0 22px ${LOOK}66`, border: `2px solid ${LOOK}`, opacity: 0.8}} />}
      {SLOT.map((p, i) => (
        <div key={i} style={{position: 'absolute', left: p.x - 95, top: p.y - 28, width: 270, height: 56, borderRadius: 12, border: '2px dashed #3a3f55', opacity: fadeAt(frame, 0.5)}} />
      ))}
      {/* The two permits stay in their slots: bright while a call holds one, dim while it is free. */}
      {SLOT.map((p, i) => {
        const held = CALLS.some((c) => c.slot === i && frame >= f(c.enter) && frame < f(c.out));
        return <Permit key={i} p={{x: p.x + 130, y: p.y}} opacity={fadeAt(frame, 0.5) * (held ? 1 : 0.35)} />;
      })}
      {CALLS.map((c) => {
        const keys: [number, Point][] = [[c.ask, GATE]];
        if (c.queue !== undefined) keys.push([c.ask + 0.7, QSLOT(c.queue)]);
        if (c.name === 'D') keys.push([19.2, QSLOT(1)], [19.8, QSLOT(0)]);
        keys.push([c.enter - 0.6, keys[keys.length - 1][1]], [c.enter, SLOT[c.slot]]);
        const p = along(frame, keys);
        const inside = frame >= f(c.enter);
        const gone = ramp(frame, c.out, c.out + 0.4);
        if (frame < f(c.ask)) return null;
        return (
          <React.Fragment key={c.name}>
            <KCard
              label={inside ? `${c.name} · inside` : frame >= f(c.ask + 0.6) && c.queue !== undefined ? `k of ${c.name} · waits` : `${c.name} asks`}
              p={p} w={170} color={inside ? TEST : ACCENT} opacity={fadeAt(frame, c.ask, 0.2) * (1 - gone)}
            />
          </React.Fragment>
        );
      })}
      {/* The lanes: dashed while a call waits, solid while it is inside, on the virtual clock. */}
      {CALLS.map((c, i) => (
        <React.Fragment key={c.name}>
          <div style={{position: 'absolute', left: 50, top: PL_LANE(i) - 12, fontFamily: MONO, fontSize: 18, fontWeight: 700, color: TEXT, opacity: fadeAt(frame, c.ask)}}>{c.name}</div>
          <div style={{position: 'absolute', left: plX(0), top: PL_LANE(i), width: plX(4) - plX(0), height: 2, background: '#3a3f55', opacity: fadeAt(frame, c.ask)}} />
          {c.from > 0 && (
            <div style={{position: 'absolute', left: plX(0), top: PL_LANE(i) - 2, width: plX(Math.min(seconds, c.from)) - plX(0), height: 6, borderTop: `2px dashed ${MUTED}`, opacity: fadeAt(frame, c.ask)}} />
          )}
          <div
            style={{
              position: 'absolute', left: plX(c.from), top: PL_LANE(i) - 5, width: Math.max(0, plX(Math.min(Math.max(seconds, c.from), c.to)) - plX(c.from)), height: 12,
              borderRadius: 6, background: TEST, opacity: 0.8 * fadeAt(frame, c.enter),
            }}
          />
        </React.Fragment>
      ))}
      {[0, 1, 2, 3, 4].map((s) => (
        <div key={s} style={{position: 'absolute', left: plX(s), top: PL_LANE(3) + 22, transform: 'translateX(-50%)', fontFamily: MONO, fontSize: 13, color: MUTED, opacity: fadeAt(frame, 1.0)}}>
          {stamp(s)}
        </div>
      ))}
      <div style={{position: 'absolute', left: plX(seconds) - 1, top: PL_LANE(0) - 20, width: 3, height: 3 * 46 + 34, background: LOOK, boxShadow: `0 0 10px ${LOOK}`, opacity: fadeAt(frame, 16.8)}} />
      <LogBox lines={PL_PRINTS} x={680} y={350} w={560} size={14} line={20} opacity={fadeAt(frame, 0.3)} />
      {label(40, 640, 'never more than 2 inside', TEST, fadeAt(frame, 24.6), 21)}
      {label(40, 674, '3 runs of measure/pool.py: the same 13 lines', MUTED, fadeAt(frame, 25.0), 15)}
    </AbsoluteFill>
  );
};

// Scene 3 — FIFO: a release hands the permit straight to the oldest waiter.

const FF_LINES: Lines = [
  [0, 'With one permit, a semaphore is a mutex. Waiters are served first come, first served.'],
  [6.5, 'A release hands the permit straight to the oldest waiter. It never sits free to grab.'],
  [14.5, 'So late, which asks right after the release, queues behind second and third.'],
];
const FF = f(23);
const FIFO_CODE = [
  'sem = yield CreateSemaphore(1)',
  'yield AcquireSemaphore(sem)  # main holds it',
  'first  = yield Spawn(worker(sem, "first"))',
  'second = yield Spawn(worker(sem, "second"))',
  'third  = yield Spawn(worker(sem, "third"))',
  'yield Delay(1)',
  'late   = yield Spawn(worker(sem, "late"))',
  'yield ReleaseSemaphore(sem)',
];
const FFC = {x: 40, y: 92, w: 600, size: 16, line: 25};
const HOLDER = {x: 680, y: 92, w: 560, h: 110};
const HOLD_AT: Point = {x: 960, y: 158};
// Where the holder's card sits in the holder box.
const HOLD_CARD: Point = {x: HOLD_AT.x - 120, y: HOLD_AT.y};
const FQUEUE = {x: 680, y: 222, w: 560, h: 100};
const FQ = (i: number): Point => ({x: 762 + i * 140, y: 280});
const FF_PRINTS: Printed = [
  {at: 0.6, text: '09:00:00 main holds the permit'},
  {at: 1.6, text: '09:00:00 first asks'},
  {at: 2.6, text: '09:00:00 second asks'},
  {at: 3.6, text: '09:00:00 third asks'},
  {at: 8.4, text: '09:00:01 main releases'},
  {at: 9.6, text: '09:00:01 first holds the permit'},
  {at: 15.2, text: '09:00:01 late asks'},
  {at: 18.4, text: '09:00:02 second holds the permit'},
  {at: 20.0, text: '09:00:03 third holds the permit'},
  {at: 21.6, text: '09:00:04 late holds the permit'},
];
// Who holds the permit from which second: main, then each waiter in turn.
const HOLDS = [
  {name: 'main', at: 0.6},
  {name: 'first', at: 9.6},
  {name: 'second', at: 18.4},
  {name: 'third', at: 20.0},
  {name: 'late', at: 21.6},
];
const WAITERS = [
  {name: 'first', ask: 1.6},
  {name: 'second', ask: 2.6},
  {name: 'third', ask: 3.6},
  {name: 'late', ask: 15.2},
];
// The second a waiter takes the permit.
const took = (name: string) => HOLDS.find((h) => h.name === name)!.at;

const FifoScene: React.FC = () => {
  const frame = useSceneFrame();
  const seconds = interpolate(frame, [f(6.8), f(7.8), f(17.4), f(18.4), f(19.0), f(20.0), f(20.6), f(21.6)], [0, 1, 1, 2, 2, 3, 3, 4], smooth);
  // Each new holder: the permit flies from the holder box to its k at the head of the queue, then the two ride
  // up into the box together; the old holder fades out meanwhile.
  const holder = HOLDS.reduce((found, h, i) => (frame >= f(h.at - 1.1) ? i : found), 0);
  const h = HOLDS[holder];
  const permitSpot: Point = {x: HOLD_CARD.x + 160, y: HOLD_CARD.y};
  const atHead: Point = {x: FQ(0).x, y: FQ(0).y - 34};
  const ride = holder === 0 ? 1 : ramp(frame, h.at - 0.6, h.at);
  const card = holder === 0 ? HOLD_CARD : along(frame, [[h.at - 0.6, FQ(0)], [h.at, HOLD_CARD]]);
  const permit =
    holder === 0 || frame >= f(h.at)
      ? permitSpot
      : frame < f(h.at - 0.6)
        ? along(frame, [[h.at - 1.1, permitSpot], [h.at - 0.6, atHead]])
        : {x: mix(card.x, card.x + 160, ride), y: mix(card.y - 34, card.y, ride)};
  const flying = holder > 0 && frame < f(h.at);
  const band = frame < f(0.4) ? null : frame < f(1.4) ? 1 : frame < f(2.4) ? 2 : frame < f(3.4) ? 3 : frame < f(4.4) ? 4 : frame < f(7.9) ? 5 : frame < f(8.4) ? 6 : 7;
  // A waiter's place in the queue at a second: the earlier waiters that have asked and not yet left the queue.
  const place = (wi: number, at: number) => WAITERS.slice(0, wi).filter((o) => o.ask <= at && took(o.name) - 0.6 > at).length;
  return (
    <AbsoluteFill>
      <Headlines lines={FF_LINES} />
      <CodeBlock lines={FIFO_CODE} {...FFC} band={band} opacity={fadeAt(frame, 0)} />
      <Box {...HOLDER} title="holds the permit" color={TEST} on={flying} opacity={fadeAt(frame, 0.2)} size={17} />
      <Box {...FQUEUE} title="queue · oldest first" color={MUTED} dashed opacity={fadeAt(frame, 0.2)} size={16} />
      {holder > 0 && frame < f(h.at - 0.4) && (
        <KCard label={`${HOLDS[holder - 1].name} · holds it`} p={HOLD_CARD} w={200} color={TEST} opacity={1 - ramp(frame, h.at - 1.1, h.at - 0.6)} />
      )}
      <KCard label={holder > 0 && frame < f(h.at) ? `k of ${h.name}` : `${h.name} · holds it`} p={card} w={holder > 0 && frame < f(h.at) ? 128 : 200} color={TEST} opacity={holder === 0 ? fadeAt(frame, h.at) : 1} />
      <Permit p={permit} color={flying ? LOOK : TEST} opacity={fadeAt(frame, 0.6)} />
      {label(HOLDER.x + 262, HOLDER.y + 12, 'straight to the oldest waiter', LOOK, fadeAt(frame, 8.6) * (1 - ramp(frame, 11.0, 11.4)), 16)}
      {WAITERS.map((w, wi) => {
        if (frame < f(w.ask) || frame >= f(took(w.name) - 0.6)) return null;
        const keys: [number, Point][] = [[w.ask, {x: FQ(place(wi, w.ask)).x, y: FQ(0).y + 40}], [w.ask + 0.5, FQ(place(wi, w.ask))]];
        WAITERS.slice(0, wi)
          .map((o) => took(o.name) - 0.6)
          .filter((left) => left > w.ask + 0.5)
          .forEach((left) => keys.push([left, FQ(place(wi, left - 0.01))], [left + 0.5, FQ(place(wi, left + 0.01))]));
        return <KCard key={w.name} label={`k of ${w.name}`} p={along(frame, keys)} w={128} color={w.name === 'late' ? LOOK : ACCENT} opacity={fadeAt(frame, w.ask, 0.3)} />;
      })}
      <LogBox lines={FF_PRINTS} x={40} y={320} w={600} size={15} line={24} opacity={fadeAt(frame, 0.2)} />
      <ClockFace x={1100} y={350} seconds={seconds} opacity={fadeAt(frame, 0.4)} />
      <div style={{position: 'absolute', left: 680, top: 380, width: 400, fontFamily: SANS, fontSize: 21, lineHeight: '31px', fontWeight: 700, color: TEST, opacity: fadeAt(frame, 11.2)}}>
        The permit goes from hand to hand. No task that comes later can take it first.
      </div>
      {label(680, 640, 'served: first, second, third, late', TEST, fadeAt(frame, 21.9), 21)}
      {label(680, 674, '3 runs of measure/fifo.py: the same order', MUTED, fadeAt(frame, 22.2), 15)}
    </AbsoluteFill>
  );
};

// Scene 4 — Cancel while waiting: out of the queue, no permit taken.

const CN_LINES: Lines = [
  [0, 'Cancel a task while it waits for a permit, and the scheduler takes it out of the queue.'],
  [6.5, 'w1 resumes with TaskCancelledError at its Acquire. It never took a permit.'],
  [13, 'So when holder releases at 09:00:02, the permit goes to w2.'],
];
const CN = f(20.5);
const CANCEL_CODE = [
  'sem = yield CreateSemaphore(1)',
  'yield Spawn(holder(sem))        # holds it 2 s',
  'w1 = yield Spawn(waiter(sem, "w1"))',
  'w2 = yield Spawn(waiter(sem, "w2"))',
  'yield Delay(1)',
  'yield Cancel(w1)',
];
const CNC = {x: 40, y: 92, w: 600, size: 16, line: 25};
const CN_PRINTS: Printed = [
  {at: 0.6, text: '09:00:00 holder holds the permit'},
  {at: 1.2, text: '09:00:00 w1 waits'},
  {at: 1.9, text: '09:00:00 w2 waits'},
  {at: 5.6, text: '09:00:01 main Cancel(w1) returned'},
  {at: 8.6, text: '09:00:01 w1 receives TaskCancelledError at its Acquire'},
  {at: 10.6, text: '09:00:01 main Wait(w1) -> TaskCancelledError'},
  {at: 15.0, text: '09:00:02 holder releases'},
  {at: 16.4, text: '09:00:02 w2 holds the permit'},
];

const CancelScene: React.FC = () => {
  const frame = useSceneFrame();
  const seconds = interpolate(frame, [f(3.0), f(4.0), f(13.6), f(14.6)], [0, 1, 1, 2], smooth);
  const band = frame < f(0.4) ? null : frame < f(1.2) ? 1 : frame < f(1.9) ? 2 : frame < f(2.8) ? 3 : frame < f(4.4) ? 4 : 5;
  const cancel = ramp(frame, 4.6, 5.5);
  const lift = ramp(frame, 7.0, 7.8);
  const error = ramp(frame, 7.9, 8.7);
  const w1 = {x: mix(FQ(0).x, FQ(0).x, lift), y: mix(FQ(0).y, 410, lift)};
  const w2 = along(frame, [[9.6, FQ(1)], [10.3, FQ(0)], [15.6, FQ(0)], [16.4, {x: HOLD_AT.x - 120, y: HOLD_AT.y}]]);
  const handOver = ramp(frame, 15.0, 16.0);
  return (
    <AbsoluteFill>
      <Headlines lines={CN_LINES} />
      <CodeBlock lines={CANCEL_CODE} {...CNC} band={band} opacity={fadeAt(frame, 0)} />
      <Box {...HOLDER} title="holds the permit" color={TEST} on={frame >= f(15.0) && frame < f(16.2)} opacity={fadeAt(frame, 0.2)} size={17} />
      <Box {...FQUEUE} title="queue · oldest first" color={MUTED} dashed on={frame >= f(5.4) && frame < f(8.0)} opacity={fadeAt(frame, 0.2)} size={16} />
      <KCard label="holder · holds it" p={{x: HOLD_AT.x - 120, y: HOLD_AT.y}} w={200} color={TEST} opacity={fadeAt(frame, 0.6) * (1 - ramp(frame, 15.0, 15.5))} />
      {frame < f(15.0) && <Permit p={{x: HOLD_AT.x + 40, y: HOLD_AT.y}} opacity={fadeAt(frame, 0.6)} />}
      {frame >= f(15.0) && frame < f(16.0) && <Permit p={{x: mix(HOLD_AT.x + 40, FQ(0).x, handOver), y: mix(HOLD_AT.y, FQ(0).y - 30, handOver)}} color={LOOK} />}
      {frame >= f(16.4) && <Permit p={{x: HOLD_AT.x + 40, y: HOLD_AT.y}} />}
      {frame >= f(1.2) && (
        <KCard
          label={frame < f(8.7) ? 'k of w1' : 'w1 · cancelled'}
          p={w1} w={frame < f(8.7) ? 128 : 170} color={frame < f(8.7) ? ACCENT : FAIL}
          opacity={fadeAt(frame, 1.2, 0.2) * (1 - ramp(frame, 12.0, 12.5))}
        />
      )}
      {frame >= f(1.9) && <KCard label={frame < f(16.4) ? 'k of w2' : 'w2 · holds it'} p={w2} w={frame < f(16.4) ? 128 : 200} color={frame < f(16.4) ? ACCENT : TEST} opacity={fadeAt(frame, 1.9, 0.2)} />}
      {moving(frame, 4.6, 5.5) && (
        <Pill x={mix(CNC.x + 300, FQ(0).x, cancel)} y={mix(lineMid(CNC.y, CNC.line, 5), FQ(0).y + 40, cancel)} text="Cancel(w1)" color={ACCENT} opacity={1 - ramp(frame, 5.5, 5.8)} />
      )}
      {moving(frame, 7.9, 8.7) && <Pill x={mix(FQ(1).x + 120, w1.x + 210, error)} y={mix(FQUEUE.y + FQUEUE.h + 10, 410, error)} text="TaskCancelledError" color={FAIL} opacity={1 - ramp(frame, 8.7, 9.0)} />}
      {label(FQ(0).x + 110, 398, 'no permit taken', FAIL, fadeAt(frame, 9.2) * (1 - ramp(frame, 12.0, 12.5)), 18)}
      <LogBox lines={CN_PRINTS} x={40} y={290} w={620} size={15} line={24} opacity={fadeAt(frame, 0.2)} />
      <ClockFace x={1100} y={420} seconds={seconds} opacity={fadeAt(frame, 0.4)} />
      <div style={{position: 'absolute', left: 40, right: 40, top: 590, textAlign: 'center', fontFamily: SANS, fontSize: 22, lineHeight: '32px', fontWeight: 700, color: TEST, opacity: fadeAt(frame, 17.0)}}>
        A cancelled waiter leaves the queue with nothing. The count of permits stays right.
      </div>
      {label(40, 670, '3 runs of measure/cancel.py: the same 10 lines', MUTED, fadeAt(frame, 17.6), 15)}
    </AbsoluteFill>
  );
};

// Scene 5 — the permit leak, without and with try/finally.

const LK_LINES: Lines = [
  [0, 'A semaphore does not track who holds a permit. Fail before the release, and it is lost.'],
  [8.5, 'Without try/finally, the next task is still waiting 5 s later, and would wait forever.'],
  [17, 'With try/finally, the release runs on failure too, and the next task enters at once.'],
];
const LK = f(25.5);
const LEAKY = ['@do', 'def leaky(sem):', '    yield AcquireSemaphore(sem)', '    yield Delay(1)', '    raise ValueError("boom")', '    yield ReleaseSemaphore(sem)'];
const GUARDED = ['@do', 'def guarded(sem):', '    yield AcquireSemaphore(sem)', '    try:', '        yield Delay(1)', '        raise ValueError("boom")', '    finally:', '        yield ReleaseSemaphore(sem)'];
const LEAKY_PRINTS: Printed = [
  {at: 0.8, text: '09:00:00 leaky holds the permit'},
  {at: 5.2, text: "09:00:01 leaky raised ValueError('boom')"},
  {at: 14.0, text: '09:00:06 next task: still waiting'},
];
const GUARDED_PRINTS: Printed = [
  {at: 0.8, text: '09:00:00 guarded holds the permit'},
  {at: 20.6, text: '09:00:01 next task got the permit'},
  {at: 21.0, text: "09:00:01 guarded raised ValueError('boom')"},
  {at: 21.6, text: '09:00:01 next task: entered'},
];

// One side of the leak scene: the failing task's code, who holds the permit, the next task, and the log.
const LeakPanel: React.FC<{
  x: number; title: string; code: string[]; prints: Printed; seconds: number; band: number | null; raiseAt: number;
  guarded: boolean; opacity: number;
}> = ({x, title, code, prints, seconds, band, raiseAt, guarded, opacity}) => {
  const frame = useSceneFrame();
  const name = guarded ? 'guarded' : 'leaky';
  const w = 580;
  const codeY = 130;
  const codeLine = 22;
  const holdY = codeY + 24 + code.length * codeLine + 70;
  const cardAt: Point = {x: x + 120, y: holdY};
  const permitSpot: Point = {x: x + 250, y: holdY};
  const queueAt: Point = {x: x + 445, y: holdY};
  const raised = ramp(frame, raiseAt, raiseAt + 0.5);
  // Guarded: the release in finally sends the permit to the next task's k, and the two ride into the holder box.
  // Leaky: nothing releases; the permit greys out and drops, lost.
  const nextP = guarded ? along(frame, [[raiseAt + 1.8, queueAt], [raiseAt + 2.4, cardAt]]) : queueAt;
  const nextIn = guarded && frame >= f(raiseAt + 2.4);
  const ride = ramp(frame, raiseAt + 1.8, raiseAt + 2.4);
  const permit: Point = !guarded
    ? {x: permitSpot.x, y: permitSpot.y + 12 * ramp(frame, raiseAt + 1.2, raiseAt + 2.2)}
    : frame < f(raiseAt + 1.8)
      ? along(frame, [[raiseAt + 1.0, permitSpot], [raiseAt + 1.6, {x: queueAt.x, y: queueAt.y - 34}]])
      : {x: mix(nextP.x, nextP.x + 130, ride), y: mix(nextP.y - 34, nextP.y, ride)};
  const lost = guarded ? 0 : ramp(frame, raiseAt + 1.2, raiseAt + 2.2);
  const handing = guarded && frame >= f(raiseAt + 1.0) && frame < f(raiseAt + 2.4);
  return (
    <div style={{opacity}}>
      {label(x, 92, title, guarded ? TEST : FAIL, 1, 20)}
      <div style={{position: 'absolute', left: x + w - 100, top: 94, fontFamily: MONO, fontSize: 18, color: TEXT}}>{stamp(seconds)}</div>
      <CodeBlock lines={code} x={x} y={codeY} w={w} size={15} line={codeLine} band={band} />
      <div style={{position: 'absolute', left: x, top: holdY - 38, width: 300, height: 76, boxSizing: 'border-box', borderRadius: 14, border: `2px dashed ${TEST}`}} />
      {label(x + 10, holdY - 62, 'holds the permit', TEST, 1, 14)}
      <div style={{position: 'absolute', left: x + 316, top: holdY - 38, width: 264, height: 76, boxSizing: 'border-box', borderRadius: 14, border: '2px dashed #3a3f55'}} />
      {label(x + 326, holdY - 62, 'queue', MUTED, 1, 14)}
      <KCard
        label={frame < f(raiseAt + 0.5) ? `${name} · holds it` : `${name} · raised`} p={cardAt} w={180}
        color={frame < f(raiseAt + 0.5) ? TEST : FAIL} opacity={fadeAt(frame, 0.8) * (1 - ramp(frame, raiseAt + 1.0, raiseAt + 1.5))}
      />
      {moving(frame, raiseAt, raiseAt + 0.5) && (
        <Pill x={mix(x + 360, cardAt.x, raised)} y={mix(lineMid(codeY, codeLine, guarded ? 5 : 4), cardAt.y - 34, raised)} text="ValueError('boom')" color={FAIL} opacity={1 - ramp(frame, raiseAt + 0.5, raiseAt + 0.8)} />
      )}
      <Permit p={permit} color={lost > 0 ? MUTED : handing ? LOOK : TEST} opacity={fadeAt(frame, 0.8) * (1 - 0.7 * lost)} />
      {!guarded && label(x + 170, holdY - 64, 'permit lost', FAIL, fadeAt(frame, raiseAt + 2.2), 15)}
      <KCard
        label={nextIn ? 'next · holds it' : guarded || frame < f(14.0) ? 'k of next · waits' : 'k of next · still waits'}
        p={nextP} w={nextIn ? 180 : 230} color={nextIn ? TEST : ACCENT} opacity={fadeAt(frame, 1.6)}
      />
      <LogBox lines={prints} x={x} y={holdY + 60} w={w} size={14} line={21} />
    </div>
  );
};

const LeakScene: React.FC = () => {
  const frame = useSceneFrame();
  const left = interpolate(frame, [f(3.6), f(4.6), f(9.5), f(13.5)], [0, 1, 1, 6], smooth);
  const right = interpolate(frame, [f(17.8), f(18.8)], [0, 1], smooth);
  const leftBand = frame < f(0.6) ? null : frame < f(3.4) ? 2 : frame < f(4.8) ? 3 : 4;
  const rightBand = frame < f(0.6) ? null : frame < f(17.6) ? 2 : frame < f(19.0) ? 4 : frame < f(19.6) ? 5 : 7;
  return (
    <AbsoluteFill>
      <Headlines lines={LK_LINES} />
      <LeakPanel x={40} title="without try/finally" code={LEAKY} prints={LEAKY_PRINTS} seconds={left} band={leftBand} raiseAt={5.0} guarded={false} opacity={fadeAt(frame, 0) * mix(1, 0.45, ramp(frame, 16.6, 17.2))} />
      <LeakPanel x={660} title="with try/finally" code={GUARDED} prints={GUARDED_PRINTS} seconds={right} band={rightBand} raiseAt={19.0} guarded opacity={fadeAt(frame, 0.2) * mix(0.35, 1, ramp(frame, 16.6, 17.2))} />
      <div style={{position: 'absolute', left: 40, right: 40, top: 600, textAlign: 'center', fontFamily: SANS, fontSize: 22, fontWeight: 700, color: TEST, opacity: fadeAt(frame, 22.2)}}>
        Release in finally: even when the task fails, the permit comes back.
      </div>
      {label(40, 676, '3 runs of measure/leak.py: the same lines on both sides', MUTED, fadeAt(frame, 22.6), 15)}
    </AbsoluteFill>
  );
};

// Outro — what to take away.

const OUTRO = f(15);

const Outro: React.FC = () => {
  const frame = useSceneFrame();
  const rise = interpolate(frame, [0, f(0.67)], [20, 0], {...clamp, easing: Easing.out(Easing.cubic)});
  return (
    <AbsoluteFill style={{padding: 90, justifyContent: 'center', transform: `translateY(${rise}px)`}}>
      <div style={{fontFamily: SANS, fontSize: 56, fontWeight: 700, color: TEXT, opacity: fadeAt(frame, 0, 0.4)}}>doeff</div>
      <div style={{fontFamily: SANS, fontSize: 30, color: ACCENT, marginTop: 6, opacity: fadeAt(frame, 0.2, 0.4)}}>Semaphore Effects</div>
      <div style={{fontFamily: SANS, fontSize: 28, lineHeight: '42px', color: TEXT, marginTop: 34, width: 1080, opacity: fadeAt(frame, 0.6, 0.4)}}>
        Permits are effects. A waiting task is a parked k that the scheduler resumes in order, and another handler can answer the same effects,
        for example across a cluster.
      </div>
      <div style={{fontFamily: MONO, fontSize: 28, color: TEST, marginTop: 40, opacity: fadeAt(frame, 1.1, 0.4)}}>pip install doeff</div>
    </AbsoluteFill>
  );
};

// The scenes in order; Root.tsx registers SEMAPHORE_EFFECTS.length frames of SEMAPHORE_EFFECTS.Video.
export const SEMAPHORE_EFFECTS = narrate('semaphore-effects', DURATIONS, [
  {length: INTRO, starts: startsOf(INTRO_LINES), ids: ['i1', 'i2', 'i3'], node: <IntroScene />},
  {length: PL, starts: startsOf(PL_LINES), ids: ['p1', 'p2', 'p3'], node: <PoolScene />},
  {length: FF, starts: startsOf(FF_LINES), ids: ['f1', 'f2', 'f3'], node: <FifoScene />},
  {length: CN, starts: startsOf(CN_LINES), ids: ['c1', 'c2', 'c3'], node: <CancelScene />},
  {length: LK, starts: startsOf(LK_LINES), ids: ['l1', 'l2', 'l3'], node: <LeakScene />},
  {length: OUTRO, starts: [0], ids: ['o1'], node: <Outro />},
]);
