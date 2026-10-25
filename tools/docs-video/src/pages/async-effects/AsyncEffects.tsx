// The video on docs/04-async-effects.md: concurrency in doeff is effects plus one more handler, the scheduler.
// Five scenes, each with headlines a synthetic voice reads (narration.json), then the outro:
//   1. Spawn, Wait, Gather, Race and Cancel are effects; scheduled() adds the scheduler as a handler layer.
//   2. Spawn starts tasks; they park their k's on the virtual clock and finish in time order; Gather returns
//      their values in input order (measure/spawn_gather.py: 21 runs, the same 8 lines, 0.35–2.66 ms each).
//   3. One task runs at a time and keeps running until it waits (measure/interleave.py).
//   4. Gather is fail-fast and cancels nothing; Race returns the first value and leaves the loser running
//      (measure/fail_race.py).
//   5. Cancel is delivered into the task as TaskCancelledError, its finally runs, and the waiter hears after
//      (measure/cancel.py).
import React from 'react';
import {AbsoluteFill, Easing, interpolate} from 'remotion';
import {clamp, fadeAt, mix, ramp, smooth} from '../../kit/motion';
import {narrate} from '../../kit/narrated';
import {Clock, CodeBlock, glow, Headlines, lineMid, Lines, Pill, startsOf} from '../../kit/parts';
import {ACCENT, f, FAIL, LOOK, MONO, MUTED, PANEL, PROD, SANS, TEST, TEXT} from '../../kit/theme';
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

// A parked continuation: the k of a task, held by the scheduler.
const KCard: React.FC<{label: string; p: Point; w?: number; opacity?: number; scale?: number; color?: string}> = ({label, p, w = 340, opacity = 1, scale = 1, color = ACCENT}) => (
  <div
    style={{
      position: 'absolute', left: p.x, top: p.y, transform: `translate(-50%, -50%) scale(${scale})`, width: w, height: 42, boxSizing: 'border-box',
      borderRadius: 10, background: '#1d1a33', border: `3px solid ${color}`, boxShadow: `0 0 16px ${color}88`, fontFamily: SANS,
      fontSize: 17, fontWeight: 700, color: TEXT, lineHeight: '36px', textAlign: 'center', whiteSpace: 'nowrap', opacity,
    }}
  >
    {label}
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

// Scene 1 — async is effects too, and scheduled() adds the scheduler as one more handler layer.

const INTRO_LINES: Lines = [
  [0, 'Async in doeff is made of effects, too.'],
  [7.5, 'scheduled() wraps your program in one more handler layer: the scheduler.'],
  [16.5, 'Spawn travels out to the scheduler, which answers with a Task handle.'],
];
const INTRO = f(23.5);
const EFFECTS = ['Spawn', 'Wait', 'Gather', 'Race', 'Cancel'];
const PROGRAM = [
  '@do',
  'def program():',
  '    a = yield Spawn(fetch("a", 3))',
  '    b = yield Spawn(fetch("b", 1))',
  '    c = yield Spawn(fetch("c", 2))',
  '    return (yield Gather(a, b, c))',
];
const ICODE = {x: 200, y: 330, w: 480, size: 19, line: 30};
const ILAYER = {x: 150, y: 270, w: 980, h: 330};
// The two Spawns the scene sends out: the code line, and the seconds it leaves, lands and its answer is back.
const ISPAWNS = [
  {line: 2, effect: 'Spawn(fetch("a", 3))', task: 'Task a', leave: 17.2, land: 18.1, back: 19.9},
  {line: 3, effect: 'Spawn(fetch("b", 1))', task: 'Task b', leave: 20.3, land: 21.2, back: 23.0},
];

const IntroScene: React.FC = () => {
  const frame = useSceneFrame();
  const first = 1 - ramp(frame, 6.9, 7.4);
  const band = frame < f(16.7) ? null : frame < f(20.0) ? 2 : mix(2, 3, ramp(frame, 20.0, 20.3));
  const working = ISPAWNS.some((s) => frame >= f(s.land) && frame < f(s.land + 0.9));
  return (
    <AbsoluteFill>
      <Headlines lines={INTRO_LINES} />
      <div style={{position: 'absolute', left: 0, right: 0, top: 130 + mix(16, 0, ramp(frame, 0.2, 0.8)), textAlign: 'center', opacity: fadeAt(frame, 0.2, 0.5) * first}}>
        <div style={{fontFamily: SANS, fontSize: 80, fontWeight: 700, color: TEXT}}>Async Effects</div>
        <div style={{fontFamily: SANS, fontSize: 28, color: MUTED, marginTop: 6}}>docs/04-async-effects.md</div>
      </div>
      {EFFECTS.map((name, i) => (
        <Pill key={name} x={260 + i * 190} y={400 + mix(14, 0, ramp(frame, 1.5 + i * 0.6, 2.0 + i * 0.6))} text={`${name}(...)`} color={ACCENT} opacity={fadeAt(frame, 1.5 + i * 0.6) * first} />
      ))}
      <div style={{position: 'absolute', left: 0, right: 0, top: 470, textAlign: 'center', fontFamily: SANS, fontSize: 24, color: TEXT, opacity: fadeAt(frame, 4.6) * first}}>
        effects your program yields, like any other
      </div>
      <CodeBlock lines={['run(scheduled(program()))']} x={410} y={110} w={460} size={22} line={36} opacity={fadeAt(frame, 7.6)} />
      <div
        style={{
          position: 'absolute', left: ILAYER.x, top: ILAYER.y, width: ILAYER.w, height: ILAYER.h, boxSizing: 'border-box', borderRadius: 22,
          border: `3px solid ${ACCENT}`, ...glow(working, ACCENT), opacity: fadeAt(frame, 8.2, 0.5),
          transform: `scale(${mix(1.04, 1, ramp(frame, 8.2, 8.7))})`,
        }}
      >
        <div style={{position: 'absolute', left: 18, top: 10, fontFamily: SANS, fontSize: 18, fontWeight: 700, color: working ? LOOK : ACCENT, whiteSpace: 'nowrap'}}>
          <span style={{fontFamily: MONO}}>scheduler</span>
          <span style={{color: MUTED, fontWeight: 400}}>{'  handles Spawn · Wait · Gather · Race · Cancel'}</span>
        </div>
        <div style={{position: 'absolute', left: 600, top: 70, fontFamily: SANS, fontSize: 17, color: MUTED, opacity: fadeAt(frame, 16.8)}}>tasks it holds</div>
        {ISPAWNS.map((s, i) => (
          <div key={s.task} style={{position: 'absolute', left: 600, top: 130 + i * 54, opacity: fadeAt(frame, s.land + 0.5)}}>
            <KCard label={`${s.task} · holds its k`} p={{x: 150, y: 0}} w={300} />
          </div>
        ))}
      </div>
      <CodeBlock lines={PROGRAM} {...ICODE} band={band} opacity={fadeAt(frame, 7.8)} />
      <div style={{position: 'absolute', left: 0, right: 0, top: 625, textAlign: 'center', fontFamily: SANS, fontSize: 23, lineHeight: '32px', color: TEXT, opacity: fadeAt(frame, 11.0)}}>
        A handler like any other: it gets each effect, and <span style={{color: ACCENT, fontWeight: 700}}>k</span>, the rest of the program.
      </div>
      {ISPAWNS.map((s) => {
        const from = {x: ICODE.x + ICODE.w + 10, y: lineMid(ICODE.y, ICODE.line, s.line)};
        const to = {x: 760, y: ILAYER.y + 130 + ISPAWNS.indexOf(s) * 54};
        const out = ramp(frame, s.leave, s.land);
        const back = ramp(frame, s.land + 0.9, s.back);
        return (
          <React.Fragment key={s.effect}>
            {frame >= f(s.leave) && frame < f(s.land + 0.3) && (
              <Pill x={mix(from.x, to.x, out)} y={mix(from.y, to.y, out)} text={s.effect} color={ACCENT} left opacity={1 - ramp(frame, s.land, s.land + 0.3)} />
            )}
            {frame >= f(s.land + 0.9) && frame < f(s.back + 0.3) && (
              <Pill x={mix(to.x + 120, from.x, back)} y={mix(to.y, from.y, back)} text={s.task} color={TEST} left opacity={1 - ramp(frame, s.back, s.back + 0.3)} />
            )}
          </React.Fragment>
        );
      })}
    </AbsoluteFill>
  );
};

// Scene 2 — Spawn starts tasks, their k's park on the virtual clock, and Gather returns the values in input order.

const SG_LINES: Lines = [
  [0, 'Spawn starts a task and hands back its handle. main goes on once the task waits.'],
  [9.2, 'Each task waits on the virtual clock. They finish in time order: b, c, then a.'],
  [20.2, 'Gather waits for all three and returns their values in input order.'],
];
const SG = f(28);
const SG_CODE = [
  '@do',
  'def fetch(name, seconds):',
  '    yield Delay(seconds)',
  '    return name.upper()',
  ' ',
  '@do',
  'def program():',
  '    a = yield Spawn(fetch("a", 3))',
  '    b = yield Spawn(fetch("b", 1))',
  '    c = yield Spawn(fetch("c", 2))',
  '    return (yield Gather(a, b, c))',
];
const SGC = {x: 40, y: 100, w: 560, size: 17, line: 25};
// The tasks: the second main spawns each, how long it waits, and its code line.
const SG_TASKS = [
  {name: 'a', spawn: 1.2, wait: 3, line: 7},
  {name: 'b', spawn: 3.4, wait: 1, line: 8},
  {name: 'c', spawn: 5.6, wait: 2, line: 9},
];
const SG_LANE = {a: 470, b: 535, c: 600} as const;
const sgX = (seconds: number) => 120 + 150 * seconds;
const SG_SLOT = (s: number): Point => ({x: 1030, y: 200 + s * 50});
// The seconds the virtual clock reaches 1, 2 and 3 s past 09:00:00.
const SG_WAKE = [11.2, 14.2, 17.2];
// The lines of measure/spawn_gather.log, with the second each one is printed.
const SG_PRINTS: Printed = [
  {at: 1.7, text: '09:00:00 a starts'},
  {at: 3.9, text: '09:00:00 b starts'},
  {at: 6.1, text: '09:00:00 c starts'},
  {at: 7.6, text: '09:00:00 main spawned a, b, c'},
  {at: 11.9, text: '09:00:01 b done'},
  {at: 14.9, text: '09:00:02 c done'},
  {at: 17.9, text: '09:00:03 a done'},
  {at: 21.8, text: "09:00:03 main Gather -> ['A', 'B', 'C']"},
];

const SpawnGatherScene: React.FC = () => {
  const frame = useSceneFrame();
  const spawned = SG_TASKS.reduce((found, task, i) => (frame >= f(task.spawn) ? i : found), -1);
  const band = spawned < 0 ? null : frame < f(7.9) ? SG_TASKS[spawned].line : mix(9, 10, ramp(frame, 7.9, 8.2));
  const seconds = interpolate(frame, [f(10.2), f(SG_WAKE[0]), f(13.2), f(SG_WAKE[1]), f(16.2), f(SG_WAKE[2])], [0, 1, 1, 2, 2, 3], smooth);
  const lane = (name: 'a' | 'b' | 'c', s: number): Point => ({x: sgX(s), y: SG_LANE[name]});
  const start = (name: 'a' | 'b' | 'c'): Point => ({x: 290, y: SG_LANE[name]});
  const codePoint = {x: SGC.x + SGC.w - 40, y: lineMid(SGC.y, SGC.line, 10)};
  const cards = {
    a: along(frame, [[1.8, start('a')], [2.5, SG_SLOT(2)], [12.0, SG_SLOT(2)], [12.5, SG_SLOT(1)], [15.0, SG_SLOT(1)], [15.5, SG_SLOT(0)], [17.2, SG_SLOT(0)], [17.9, lane('a', 3)]]),
    b: along(frame, [[4.0, start('b')], [4.7, SG_SLOT(0)], [11.2, SG_SLOT(0)], [11.9, lane('b', 1)]]),
    c: along(frame, [[6.2, start('c')], [6.9, SG_SLOT(1)], [12.0, SG_SLOT(1)], [12.5, SG_SLOT(0)], [14.2, SG_SLOT(0)], [14.9, lane('c', 2)]]),
  };
  const mainCard = along(frame, [[8.0, codePoint], [8.7, SG_SLOT(3)], [12.0, SG_SLOT(3)], [12.5, SG_SLOT(2)], [15.0, SG_SLOT(2)], [15.5, SG_SLOT(1)], [18.0, SG_SLOT(1)], [18.5, SG_SLOT(0)], [20.6, SG_SLOT(0)], [21.3, codePoint]]);
  const result = ramp(frame, 20.8, 21.8);
  return (
    <AbsoluteFill>
      <Headlines lines={SG_LINES} />
      <CodeBlock lines={SG_CODE} {...SGC} band={band} opacity={fadeAt(frame, 0)} />
      <div
        style={{
          position: 'absolute', left: 640, top: 100, width: 600, height: 310, boxSizing: 'border-box', borderRadius: 18, background: PANEL,
          border: `3px solid ${ACCENT}`, opacity: fadeAt(frame, 0.3),
        }}
      >
        <div style={{position: 'absolute', left: 20, top: 12, fontFamily: SANS, fontSize: 21, fontWeight: 700, color: ACCENT}}>scheduler + virtual clock</div>
        <div style={{position: 'absolute', left: 22, top: 62, transform: 'scale(0.8)', transformOrigin: 'top left'}}>
          <Clock seconds={seconds} c={ACCENT} secondHand />
        </div>
        <div style={{position: 'absolute', left: 10, top: 166, width: 120, textAlign: 'center', fontFamily: MONO, fontSize: 20, color: TEXT}}>{stamp(seconds)}</div>
        <div style={{position: 'absolute', left: 230, top: 54, fontFamily: SANS, fontSize: 16, color: MUTED}}>parked k&apos;s, by wake-up time</div>
      </div>
      {SG_TASKS.map((task) => (
        <React.Fragment key={task.name}>
          {label(sgX(0), SG_LANE[task.name as 'a'] - 30, `Task ${task.name} · fetch("${task.name}", ${task.wait})`, MUTED, fadeAt(frame, task.spawn + 0.3), 16)}
          <div style={{position: 'absolute', left: sgX(0), top: SG_LANE[task.name as 'a'], width: sgX(3) - sgX(0), height: 2, background: '#3a3f55', opacity: fadeAt(frame, task.spawn + 0.3)}} />
          <div
            style={{
              position: 'absolute', left: sgX(task.wait) - 9, top: SG_LANE[task.name as 'a'] - 8, width: 18, height: 18, borderRadius: 9, background: TEST,
              boxShadow: `0 0 12px ${TEST}`, opacity: fadeAt(frame, SG_WAKE[task.wait - 1] + 0.7),
            }}
          />
        </React.Fragment>
      ))}
      {[0, 1, 2, 3].map((s) => (
        <div key={s} style={{position: 'absolute', left: sgX(s), top: 616, transform: 'translateX(-50%)', fontFamily: MONO, fontSize: 14, color: MUTED, opacity: fadeAt(frame, 1.5)}}>
          {stamp(s)}
        </div>
      ))}
      <div style={{position: 'absolute', left: sgX(seconds) - 1, top: 438, width: 3, height: 176, background: LOOK, boxShadow: `0 0 10px ${LOOK}`, opacity: fadeAt(frame, 9.6)}} />
      {SG_TASKS.map((task) => {
        const wake = SG_WAKE[task.wait - 1];
        if (frame < f(task.spawn + 0.6)) return null;
        return (
          <KCard
            key={task.name}
            label={`k of ${task.name} · wakes ${stamp(task.wait)}`}
            p={cards[task.name as 'a']}
            opacity={fadeAt(frame, task.spawn + 0.6, 0.2) * (1 - ramp(frame, wake + 0.4, wake + 0.7))}
            scale={1 - 0.5 * ramp(frame, wake + 0.3, wake + 0.7)}
          />
        );
      })}
      {frame >= f(8.0) && <KCard label="k of main · in Gather(a, b, c)" p={mainCard} color={TEXT} opacity={fadeAt(frame, 8.0, 0.2) * (1 - ramp(frame, 21.0, 21.3))} />}
      {frame >= f(20.8) && frame < f(22.1) && (
        <Pill x={mix(SG_SLOT(0).x, codePoint.x, result)} y={mix(SG_SLOT(0).y, codePoint.y, result)} text="['A', 'B', 'C']" color={TEST} opacity={1 - ramp(frame, 21.8, 22.1)} />
      )}
      <LogBox lines={SG_PRINTS} x={640} y={430} w={600} size={17} line={28} opacity={fadeAt(frame, 0.3)} />
      {label(40, 646, "finished b, c, a · Gather gave ['A', 'B', 'C']", TEST, fadeAt(frame, 23.0), 21)}
      {label(40, 680, '21 runs of measure/spawn_gather.py: the same 8 lines, 0.35–2.66 ms each', MUTED, fadeAt(frame, 24.0), 16)}
    </AbsoluteFill>
  );
};

// Scene 3 — one task runs at a time, and it keeps running until it waits.

const TL_LINES: Lines = [
  [0, 'The scheduler runs one task at a time, on one thread.'],
  [4.5, 'A task runs until it waits. These tasks never wait, so a runs to its end first.'],
  [11.6, 'Wait after each step: the scheduler parks that k and resumes the next one.'],
];
const TL = f(23);
const BUSY = ['def busy(name):', '    for n in (1, 2, 3):', '        yield Step(f"{name}{n}")'];
const POLITE = ['def polite(name):', '    for n in (1, 2, 3):', '        yield Step(f"{name}{n}")', '        yield Delay(0)'];
// The orders of measure/interleave.log, with the second each step is shown.
const BUSY_ORDER = ['a1', 'a2', 'a3', 'b1', 'b2', 'b3'].map((step, i) => ({step, at: 5.8 + i * 0.6}));
const POLITE_ORDER = ['a1', 'b1', 'a2', 'b2', 'a3', 'b3'].map((step, i) => ({step, at: 12.8 + i * 0.8}));
const TASK_COLOR = {a: ACCENT, b: PROD} as const;
const PHASES = [
  {steps: BUSY_ORDER, from: 0.5, to: 10.6},
  {steps: POLITE_ORDER, from: 12.4, to: 22.4},
];
const RUN_AT: Point = {x: 240, y: 244};
const PARK_AT: Point = {x: 240, y: 410};

const TimelineScene: React.FC = () => {
  const frame = useSceneFrame();
  // Each phase's cards follow measure/interleave.log: a task's card comes in when it first runs, goes to the thread
  // when its step is shown, parks while the other task's steps run, and leaves after its last step.
  const cards = PHASES.flatMap(({steps, from, to}, phase) =>
    (['a', 'b'] as const).map((name) => {
      const mine = steps.filter((s) => s.step[0] === name);
      const last = mine[mine.length - 1].at;
      const next = steps.find((s) => s.at > last);
      // Out before the next task's card comes in, so the two never cross on the thread.
      const leave = next ? next.at - 0.65 : to;
      const enter = phase === 0 && name === 'a' ? from : mine[0].at - 0.35;
      const keys: [number, Point][] = [[enter, RUN_AT]];
      steps.forEach((s, i) => {
        if (i === 0 || s.step[0] === steps[i - 1].step[0] || s.at <= mine[0].at || s.at > last) return;
        const runs = s.step[0] === name;
        keys.push([s.at - 0.35, runs ? PARK_AT : RUN_AT], [s.at - 0.05, runs ? RUN_AT : PARK_AT]);
      });
      if (keys.length === 1) keys.push([enter + 1, RUN_AT]);
      const running = steps.reduce((found, s) => (frame >= f(s.at - 0.05) ? s.step[0] : found), steps[0].step[0]) === name;
      const shown = frame >= f(enter) && frame < f(leave + 0.3);
      const opacity = fadeAt(frame, enter, 0.3) * (1 - ramp(frame, leave, leave + 0.3));
      return {key: `${phase}-${name}`, name, shown, opacity, p: along(frame, keys), state: running ? 'running' : phase === 0 ? 'ready' : 'parked on Delay(0)'};
    }),
  );
  const chip = (s: {step: string; at: number}, i: number, y: number) => (
    <Pill key={`${y}-${i}`} x={500 + i * 118} y={y + mix(12, 0, ramp(frame, s.at, s.at + 0.3))} text={s.step} color={TASK_COLOR[s.step[0] as 'a']} left opacity={fadeAt(frame, s.at, 0.25)} />
  );
  return (
    <AbsoluteFill>
      <Headlines lines={TL_LINES} />
      <div
        style={{
          position: 'absolute', left: 40, top: 110, width: 400, height: 470, boxSizing: 'border-box', borderRadius: 18, background: PANEL,
          border: `3px solid ${ACCENT}`, opacity: fadeAt(frame, 0.2),
        }}
      >
        <div style={{position: 'absolute', left: 20, top: 12, fontFamily: SANS, fontSize: 21, fontWeight: 700, color: ACCENT}}>scheduler</div>
        <div style={{position: 'absolute', left: 20, top: 58, fontFamily: SANS, fontSize: 16, color: MUTED}}>running: one thread, one task</div>
        <div style={{position: 'absolute', left: 18, top: 88, width: 364, height: 92, boxSizing: 'border-box', borderRadius: 12, border: `2px dashed ${LOOK}`, boxShadow: `0 0 14px ${LOOK}55`}} />
        <div style={{position: 'absolute', left: 20, top: 208, fontFamily: SANS, fontSize: 16, color: MUTED}}>parked or ready, waiting for the thread</div>
        <div style={{position: 'absolute', left: 18, top: 238, width: 364, height: 210, boxSizing: 'border-box', borderRadius: 12, border: '2px dashed #3a3f55'}} />
      </div>
      {cards.map((card) => card.shown && <KCard key={card.key} label={`k of ${card.name} · ${card.state}`} p={card.p} w={320} color={TASK_COLOR[card.name]} opacity={card.opacity} />)}
      <KCard label="k of main" p={{x: 240, y: 500}} w={320} color={TEXT} opacity={fadeAt(frame, 0.8)} />
      {label(480, 106, 'tasks that never wait', TEXT, fadeAt(frame, 4.6))}
      <CodeBlock lines={BUSY} x={480} y={138} w={430} size={17} line={26} opacity={fadeAt(frame, 4.6)} />
      {BUSY_ORDER.map((s, i) => chip(s, i, 290))}
      {label(480, 350, 'tasks that wait after each step', TEXT, fadeAt(frame, 11.7))}
      <CodeBlock lines={POLITE} x={480} y={382} w={430} size={17} line={26} opacity={fadeAt(frame, 11.7)} />
      {POLITE_ORDER.map((s, i) => chip(s, i, 560))}
      <div style={{position: 'absolute', left: 40, right: 40, top: 628, textAlign: 'center', fontFamily: SANS, fontSize: 23, fontWeight: 700, color: TEST, opacity: fadeAt(frame, 18.0)}}>
        Same tasks, same steps: where a task waits decides the order.
      </div>
      <div style={{position: 'absolute', left: 40, right: 40, top: 668, textAlign: 'center', fontFamily: SANS, fontSize: 16, color: MUTED, opacity: fadeAt(frame, 18.6)}}>
        Both orders from measure/interleave.py, the same on every run.
      </div>
    </AbsoluteFill>
  );
};

// Scene 4 — Gather is fail-fast and cancels nothing; Race returns the first value and leaves the loser running.

const FR_LINES: Lines = [
  [0, 'Gather is fail-fast: b raises at 09:00:01, and Gather raises right then.'],
  [6, 'It cancels nothing: c still finishes at 09:00:02, and main can still Wait for it.'],
  [12.4, 'Race returns the first value to finish. The loser runs on until you Cancel it.'],
];
const FR = f(20.5);
const GATHER_CODE = ['a = yield Spawn(job("a", 3))', 'b = yield Spawn(broken("b", 1))', 'c = yield Spawn(job("c", 2))', 'yield Gather(a, b, c)'];
const RACE_CODE = ['a = yield Spawn(job("a", 3))', 'b = yield Spawn(job("b", 1))', 'winner = yield Race(a, b)'];
// The lines of measure/fail_race.log (gather_fails and race), with the second each one is printed.
const GATHER_PRINTS: Printed = [
  {at: 2.5, text: '09:00:01 b raises ValueError'},
  {at: 3.5, text: "09:00:01 main Gather raised ValueError('boom')"},
  {at: 7.9, text: '09:00:02 c done'},
  {at: 8.8, text: "09:00:02 main Wait(c) -> 'c'"},
  {at: 10.0, text: '09:00:02 main Cancel(a)'},
];
const RACE_PRINTS: Printed = [
  {at: 14.0, text: '09:00:01 b done'},
  {at: 14.8, text: "09:00:01 main Race -> 'b'"},
  {at: 16.6, text: '09:00:03 a done'},
];
const LANE_TOP = 310;
const LANE_GAP = 52;

// One task's lane: a bar that grows with the clock until the task ends, and the mark of how it ended.
const Lane: React.FC<{x0: number; y: number; name: string; now: number; end: number; mark: 'done' | 'fail' | 'cut'; markAt: number; opacity: number}> = ({x0, y, name, now, end, mark, markAt, opacity}) => {
  const frame = useSceneFrame();
  const x = (s: number) => x0 + 50 + 160 * s;
  const color = mark === 'fail' ? FAIL : mark === 'done' ? TEST : MUTED;
  return (
    <div style={{opacity}}>
      <div style={{position: 'absolute', left: x0, top: y - 13, fontFamily: MONO, fontSize: 20, fontWeight: 700, color: TEXT}}>{name}</div>
      <div style={{position: 'absolute', left: x(0), top: y, width: x(3) - x(0), height: 2, background: '#3a3f55'}} />
      <div style={{position: 'absolute', left: x(0), top: y - 3, width: x(Math.min(now, end)) - x(0), height: 8, borderRadius: 4, background: ACCENT, opacity: 0.7}} />
      {mark === 'cut' ? (
        <div style={{position: 'absolute', left: x(end) + 8, top: y - 12, fontFamily: SANS, fontSize: 15, color: MUTED, whiteSpace: 'nowrap', opacity: fadeAt(frame, markAt)}}>
          cancelled
        </div>
      ) : (
        <div
          style={{
            position: 'absolute', left: x(end) - 10, top: y - 9, width: 20, height: 20, borderRadius: 10, background: color, boxShadow: `0 0 12px ${color}`,
            opacity: fadeAt(frame, markAt, 0.2),
          }}
        />
      )}
    </div>
  );
};

const Ticks: React.FC<{x0: number; y: number; opacity: number}> = ({x0, y, opacity}) => (
  <>
    {[0, 1, 2, 3].map((s) => (
      <div key={s} style={{position: 'absolute', left: x0 + 50 + 160 * s, top: y, transform: 'translateX(-50%)', fontFamily: MONO, fontSize: 14, color: MUTED, opacity}}>
        {stamp(s)}
      </div>
    ))}
  </>
);

const FailRaceScene: React.FC = () => {
  const frame = useSceneFrame();
  const left = interpolate(frame, [f(0.8), f(2.4), f(6.6), f(7.8)], [0, 1, 1, 2], smooth);
  const right = interpolate(frame, [f(12.9), f(13.9), f(15.3), f(16.5)], [0, 1, 1, 3], smooth);
  const leftOpacity = mix(1, 0.4, ramp(frame, 12.2, 12.8));
  const rightOpacity = fadeAt(frame, 0.3) * mix(0.35, 1, ramp(frame, 12.2, 12.8));
  const L = {x: 40, code: {x: 40, y: 140, w: 580, size: 16, line: 24}};
  const R = {x: 660, code: {x: 660, y: 140, w: 580, size: 16, line: 24}};
  const laneY = (i: number) => LANE_TOP + i * LANE_GAP;
  const nowLine = (x0: number, now: number, lanes: number, opacity: number) => (
    <div style={{position: 'absolute', left: x0 + 50 + 160 * now - 1, top: LANE_TOP - 24, width: 3, height: lanes * LANE_GAP + 8, background: LOOK, boxShadow: `0 0 10px ${LOOK}`, opacity}} />
  );
  const raised = ramp(frame, 2.7, 3.5);
  const won = ramp(frame, 14.1, 14.8);
  return (
    <AbsoluteFill>
      <Headlines lines={FR_LINES} />
      <div style={{opacity: leftOpacity}}>
        {label(L.x, 104, 'Gather(a, b, c)', ACCENT, fadeAt(frame, 0), 22)}
        <CodeBlock lines={GATHER_CODE} {...L.code} band={frame < f(0.5) ? null : 3} opacity={fadeAt(frame, 0)} />
        <Lane x0={L.x} y={laneY(0)} name="a" now={left} end={2} mark="cut" markAt={10.0} opacity={fadeAt(frame, 0.3)} />
        <Lane x0={L.x} y={laneY(1)} name="b" now={left} end={1} mark="fail" markAt={2.4} opacity={fadeAt(frame, 0.3)} />
        <Lane x0={L.x} y={laneY(2)} name="c" now={left} end={2} mark="done" markAt={7.8} opacity={fadeAt(frame, 0.3)} />
        <Ticks x0={L.x} y={LANE_TOP + 2 * LANE_GAP + 22} opacity={fadeAt(frame, 0.3)} />
        {nowLine(L.x, left, 3, fadeAt(frame, 0.6) * (1 - ramp(frame, 11.0, 11.5)))}
        <LogBox lines={GATHER_PRINTS} x={L.x} y={490} w={580} size={16} line={27} opacity={fadeAt(frame, 0.3)} />
      </div>
      {frame >= f(2.7) && frame < f(3.8) && (
        <Pill x={mix(L.x + 50 + 160 + 20, L.code.x + 330, raised)} y={mix(laneY(1), lineMid(L.code.y, L.code.line, 3), raised)} text="ValueError('boom')" color={FAIL} left opacity={1 - ramp(frame, 3.5, 3.8)} />
      )}
      <div style={{opacity: rightOpacity}}>
        {label(R.x, 104, 'Race(a, b)', ACCENT, 1, 22)}
        <CodeBlock lines={RACE_CODE} {...R.code} band={frame < f(12.6) ? null : 2} />
        <Lane x0={R.x} y={laneY(0)} name="a" now={right} end={3} mark="done" markAt={16.5} opacity={1} />
        <Lane x0={R.x} y={laneY(1)} name="b" now={right} end={1} mark="done" markAt={13.9} opacity={1} />
        <Ticks x0={R.x} y={LANE_TOP + 1 * LANE_GAP + 22} opacity={1} />
        {nowLine(R.x, right, 2, ramp(frame, 12.6, 12.9))}
        <LogBox lines={RACE_PRINTS} x={R.x} y={490} w={580} size={16} line={27} />
        {label(R.x + 50 + 160 * 3 - 230, laneY(0) - 40, 'a, the loser, kept running', PROD, fadeAt(frame, 16.8), 16)}
      </div>
      {frame >= f(14.1) && frame < f(15.1) && (
        <Pill x={mix(R.x + 50 + 160 + 20, R.code.x + 300, won)} y={mix(laneY(1), lineMid(R.code.y, R.code.line, 2), won)} text="'b'" color={TEST} left opacity={1 - ramp(frame, 14.8, 15.1)} />
      )}
      <div style={{position: 'absolute', left: R.x, top: 620, width: 580, fontFamily: SANS, fontSize: 19, fontWeight: 700, lineHeight: '28px', color: TEST, opacity: fadeAt(frame, 17.8)}}>
        With yield Cancel(a) after the Race, a never finishes.
      </div>
    </AbsoluteFill>
  );
};

// Scene 5 — Cancel is delivered into the task: TaskCancelledError at its wait, its finally runs, the waiter hears after.

const CN_LINES: Lines = [
  [0, 'Cancel is an effect too. yield Cancel(task) asks, and returns at once.'],
  [7.2, 'The task receives TaskCancelledError where it waits, so its finally block runs.'],
  [13.7, 'Cleanup may perform effects. Wait(task) answers only after it is done.'],
];
const CN = f(20);
const WORK = [
  '@do',
  'def work():',
  '    try:',
  '        reply = yield CreatePromise()',
  '        return (yield Wait(reply.future))',
  '    finally:',
  '        yield Delay(1)',
  '        yield note("released lease-1")',
];
const MAIN = ['@do', 'def main():', '    task = yield Spawn(work())', '    yield Delay(2)', '    yield Cancel(task)', '    result = yield Try(Wait(task))'];
const WC = {x: 40, y: 110, w: 600, size: 16, line: 27};
const MC = {x: 710, y: 110, w: 530, size: 16, line: 27};
const BAR = {x: 655, y: 110, w: 40, h: 240};
// The lines of measure/cancel.log, with the second each one is printed.
const CN_PRINTS: Printed = [
  {at: 1.6, text: '09:00:00 work got lease-1, waits for a reply'},
  {at: 5.9, text: '09:00:02 main Cancel(task) returned'},
  {at: 8.6, text: '09:00:02 work receives TaskCancelledError at its Wait'},
  {at: 11.6, text: '09:00:03 work finally: released lease-1'},
  {at: 15.0, text: '09:00:03 main Wait(task) -> TaskCancelledError'},
];

const CancelScene: React.FC = () => {
  const frame = useSceneFrame();
  const seconds = interpolate(frame, [f(3.2), f(4.4), f(10.2), f(11.0)], [0, 2, 2, 3], smooth);
  const workBand = frame < f(0.9) ? null : frame < f(1.3) ? 3 : frame < f(9.0) ? mix(3, 4, ramp(frame, 1.3, 1.6)) : frame < f(11.3) ? mix(4, 6, ramp(frame, 9.0, 9.4)) : mix(6, 7, ramp(frame, 11.3, 11.6));
  const mainBand = frame < f(0.5) ? null : frame < f(2.4) ? 2 : frame < f(4.8) ? mix(2, 3, ramp(frame, 2.4, 2.7)) : frame < f(6.3) ? mix(3, 4, ramp(frame, 4.8, 5.1)) : mix(4, 5, ramp(frame, 6.3, 6.6));
  const atBar = frame >= f(5.6) && frame < f(8.2);
  const cancel = ramp(frame, 5.0, 5.6);
  const intoWork = ramp(frame, 7.6, 8.4);
  const intoMain = ramp(frame, 14.0, 14.8);
  const parked = (x: number, y: number, on: number) => (
    <div style={{position: 'absolute', left: x, top: y, transform: 'translateY(-50%)', fontFamily: SANS, fontSize: 15, fontWeight: 700, color: MUTED, opacity: on}}>◀ parked here</div>
  );
  return (
    <AbsoluteFill>
      <Headlines lines={CN_LINES} />
      <CodeBlock lines={WORK} {...WC} band={workBand} opacity={fadeAt(frame, 0)} />
      <CodeBlock lines={MAIN} {...MC} band={mainBand} opacity={fadeAt(frame, 0)} />
      {parked(WC.x + 470, lineMid(WC.y, WC.line, 4), fadeAt(frame, 1.8) * (1 - ramp(frame, 7.9, 8.2)))}
      {parked(MC.x + 382, lineMid(MC.y, MC.line, 5), fadeAt(frame, 6.6) * (1 - ramp(frame, 14.6, 14.9)))}
      <div
        style={{
          position: 'absolute', left: BAR.x, top: BAR.y, width: BAR.w, height: BAR.h, boxSizing: 'border-box', borderRadius: 12, background: PANEL,
          border: `3px solid ${ACCENT}`, ...glow(atBar, ACCENT), opacity: fadeAt(frame, 0.2),
        }}
      >
        <div style={{position: 'absolute', left: 0, right: 0, top: 0, bottom: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', writingMode: 'vertical-rl', fontFamily: MONO, fontSize: 17, fontWeight: 700, color: atBar ? LOOK : ACCENT}}>
          scheduler
        </div>
      </div>
      {frame >= f(5.0) && frame < f(5.9) && (
        <Pill x={mix(MC.x + 200, BAR.x + BAR.w / 2, cancel)} y={lineMid(MC.y, MC.line, 4)} text="Cancel(task)" color={ACCENT} opacity={1 - ramp(frame, 5.6, 5.9)} />
      )}
      {frame >= f(7.6) && frame < f(8.8) && (
        <Pill x={mix(BAR.x + BAR.w / 2, WC.x + 535, intoWork)} y={mix(lineMid(MC.y, MC.line, 4), lineMid(WC.y, WC.line, 4), intoWork)} text="TaskCancelledError" color={FAIL} opacity={1 - ramp(frame, 8.5, 8.8)} />
      )}
      {frame >= f(14.0) && frame < f(15.2) && (
        <Pill x={mix(BAR.x + BAR.w / 2, MC.x + 300, intoMain)} y={lineMid(MC.y, MC.line, 5)} text="TaskCancelledError" color={FAIL} opacity={1 - ramp(frame, 14.9, 15.2)} />
      )}
      {label(MC.x, MC.y + 200, 'result: Err(TaskCancelledError)', FAIL, fadeAt(frame, 15.0), 18)}
      <LogBox lines={CN_PRINTS} x={40} y={390} w={1000} size={18} line={30} opacity={fadeAt(frame, 0.3)} />
      <div style={{position: 'absolute', left: 1080, top: 380, width: 160, textAlign: 'center', opacity: fadeAt(frame, 0.3)}}>
        <div style={{display: 'inline-block'}}>
          <Clock seconds={seconds} c={ACCENT} secondHand />
        </div>
        <div style={{fontFamily: MONO, fontSize: 20, color: TEXT, marginTop: 4}}>{stamp(seconds)}</div>
        <div style={{fontFamily: SANS, fontSize: 14, color: MUTED, marginTop: 2}}>virtual clock</div>
      </div>
      <div style={{position: 'absolute', left: 40, right: 40, top: 610, textAlign: 'center', fontFamily: SANS, fontSize: 23, fontWeight: 700, lineHeight: '34px', color: TEST, opacity: fadeAt(frame, 16.2)}}>
        Cancel asks. The task unwinds itself, effects and all.
        <br />
        Whoever waits on it hears TaskCancelledError after the cleanup.
      </div>
    </AbsoluteFill>
  );
};

// Outro — what to take away.

const OUTRO = f(10);

const Outro: React.FC = () => {
  const frame = useSceneFrame();
  const rise = interpolate(frame, [0, f(0.67)], [20, 0], {...clamp, easing: Easing.out(Easing.cubic)});
  return (
    <AbsoluteFill style={{padding: 90, justifyContent: 'center', transform: `translateY(${rise}px)`}}>
      <div style={{fontFamily: SANS, fontSize: 56, fontWeight: 700, color: TEXT, opacity: fadeAt(frame, 0, 0.4)}}>doeff</div>
      <div style={{fontFamily: SANS, fontSize: 30, color: ACCENT, marginTop: 6, opacity: fadeAt(frame, 0.2, 0.4)}}>Async Effects</div>
      <div style={{fontFamily: SANS, fontSize: 28, lineHeight: '42px', color: TEXT, marginTop: 34, width: 1050, opacity: fadeAt(frame, 0.6, 0.4)}}>
        A task is a paused program. The scheduler is a handler that holds each task&apos;s k and decides when it resumes.
      </div>
      <div style={{fontFamily: MONO, fontSize: 28, color: TEST, marginTop: 40, opacity: fadeAt(frame, 1.1, 0.4)}}>pip install doeff</div>
    </AbsoluteFill>
  );
};

// The scenes in order; Root.tsx registers ASYNC_EFFECTS.length frames of ASYNC_EFFECTS.Video.
export const ASYNC_EFFECTS = narrate('async-effects', DURATIONS, [
  {length: INTRO, starts: startsOf(INTRO_LINES), ids: ['a1', 'a2', 'a3'], node: <IntroScene />},
  {length: SG, starts: startsOf(SG_LINES), ids: ['g1', 'g2', 'g3'], node: <SpawnGatherScene />},
  {length: TL, starts: startsOf(TL_LINES), ids: ['t1', 't2', 't3'], node: <TimelineScene />},
  {length: FR, starts: startsOf(FR_LINES), ids: ['f1', 'f2', 'f3'], node: <FailRaceScene />},
  {length: CN, starts: startsOf(CN_LINES), ids: ['c1', 'c2', 'c3'], node: <CancelScene />},
  {length: OUTRO, starts: [0], ids: ['o1'], node: <Outro />},
]);
