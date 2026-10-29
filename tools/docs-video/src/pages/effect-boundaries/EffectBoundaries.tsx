// The video on docs/17-effect-boundaries.md: where doeff answers effects itself, and the one place a program leaves it.
// Five scenes, each with headlines a synthetic voice reads (narration.json), then the outro:
//   1. Two zones: inside doeff the handler stack answers each effect with k; outside, Python's event loop runs
//      coroutines. Await is the effect that crosses.
//   2. The page's pipeline: an observer sees 15 effects, every one answered inside doeff, on one thread, with no
//      event loop running (measure/inside.py).
//   3. Await: await_handler hands the coroutine to the event loop on the doeff-await-bridge thread while the
//      scheduler parks k; other tasks' effects are answered meanwhile; about 200 ms in all (measure/escape.py).
//   4. Scheduler suspension (Wait on a Promise, Delay on the virtual clock) stays inside and takes about a
//      millisecond; Await of a 0.3 s sleep takes 304 ms on the bridge thread (measure/suspend.py).
//   5. Custom handler rules: a handler answers its domain effect inside, or performs Await itself, which needs
//      await_handler outside it (measure/handler_rules.py).
import React from 'react';
import {AbsoluteFill, Easing, interpolate} from 'remotion';
import {clamp, fadeAt, mix, ramp, smooth} from '../../kit/motion';
import {narrate} from '../../kit/narrated';
import {CodeBlock, glow, Headlines, lineMid, Lines, Pill, startsOf} from '../../kit/parts';
import {ACCENT, f, FAIL, LOOK, MONO, MUTED, PANEL, PROD, SANS, TEST, TEXT} from '../../kit/theme';
import {useSceneFrame} from '../../kit/warp';
import DURATIONS from './durations.json';

type Point = {x: number; y: number};
type Box = {x: number; y: number; w: number; h: number};

// Where something moving is at a frame: keyframes of [second, point], eased between them.
const along = (frame: number, keys: [number, Point][]): Point => {
  const times = keys.map(([s]) => f(s));
  return {
    x: interpolate(frame, times, keys.map(([, p]) => p.x), smooth),
    y: interpolate(frame, times, keys.map(([, p]) => p.y), smooth),
  };
};

// Shown between two times: fades in at the first and out at the second.
const between = (frame: number, from: number, to: number, length = 0.3) => fadeAt(frame, from, length) * (1 - ramp(frame, to, to + length));

const label = (x: number, y: number, text: React.ReactNode, color: string, opacity: number, size = 20, bold = true): React.ReactNode => (
  <div style={{position: 'absolute', left: x, top: y, fontFamily: SANS, fontSize: size, fontWeight: bold ? 700 : 400, color, whiteSpace: 'nowrap', opacity}}>{text}</div>
);

const centred = (y: number, text: React.ReactNode, color: string, opacity: number, size = 23, bold = true): React.ReactNode => (
  <div style={{position: 'absolute', left: 40, right: 40, top: y, textAlign: 'center', fontFamily: SANS, fontSize: size, fontWeight: bold ? 700 : 400, lineHeight: `${size + 10}px`, color, opacity}}>
    {text}
  </div>
);

// A zone of the run: inside doeff (its handlers) or outside it (Python's event loop).
const Zone: React.FC<{box: Box; title: string; sub: string; color: string; opacity: number; on?: boolean}> = ({box, title, sub, color, opacity, on = false}) => (
  <div
    style={{
      position: 'absolute', left: box.x, top: box.y, width: box.w, height: box.h, boxSizing: 'border-box', borderRadius: 20, background: PANEL,
      border: `3px solid ${color}`, ...glow(on, color), opacity,
    }}
  >
    <div style={{position: 'absolute', left: 18, top: 10, fontFamily: SANS, fontSize: 20, fontWeight: 700, color, whiteSpace: 'nowrap'}}>
      {title}
      <span style={{color: MUTED, fontWeight: 400, fontSize: 16}}>{`  ${sub}`}</span>
    </div>
  </div>
);

// The line between the zones: dashed, and lit while something crosses it.
const Boundary: React.FC<{x: number; top: number; bottom: number; lit: boolean; opacity: number}> = ({x, top, bottom, lit, opacity}) => (
  <div
    style={{
      position: 'absolute', left: x - 2, top, width: 0, height: bottom - top, borderLeft: `4px dashed ${lit ? LOOK : '#4a4f66'}`,
      boxShadow: lit ? `0 0 18px ${LOOK}` : 'none', opacity,
    }}
  />
);

// One handler of a stack: its name, the effects it answers, and how many it has answered so far.
const Band: React.FC<{box: Box; name: string; answers: string; color: string; on: boolean; opacity: number; count?: string}> = ({box, name, answers, color, on, opacity, count}) => (
  <div
    style={{
      position: 'absolute', left: box.x, top: box.y, width: box.w, height: box.h, boxSizing: 'border-box', borderRadius: 10, background: '#0b0d13',
      border: `2px solid ${color}`, ...glow(on, color), opacity, fontFamily: SANS, whiteSpace: 'nowrap',
    }}
  >
    <div style={{position: 'absolute', left: 14, top: 0, lineHeight: `${box.h - 4}px`, fontSize: 17, fontWeight: 700, color: on ? LOOK : color}}>
      <span style={{fontFamily: MONO}}>{name}</span>
      <span style={{color: MUTED, fontWeight: 400, fontSize: 15}}>{`  ${answers}`}</span>
    </div>
    {count && <div style={{position: 'absolute', right: 14, top: 0, lineHeight: `${box.h - 4}px`, fontFamily: MONO, fontSize: 16, fontWeight: 700, color: TEXT}}>{count}</div>}
  </div>
);

// A parked continuation: the k of a task, held by a handler.
const KCard: React.FC<{text: string; p: Point; w?: number; opacity?: number; color?: string}> = ({text, p, w = 300, opacity = 1, color = ACCENT}) => (
  <div
    style={{
      position: 'absolute', left: p.x, top: p.y, transform: 'translate(-50%, -50%)', width: w, height: 38, boxSizing: 'border-box', borderRadius: 10,
      background: '#1d1a33', border: `3px solid ${color}`, boxShadow: `0 0 14px ${color}88`, fontFamily: SANS, fontSize: 16, fontWeight: 700,
      color: TEXT, lineHeight: '32px', textAlign: 'center', whiteSpace: 'nowrap', opacity,
    }}
  >
    {text}
  </div>
);

// Printed lines that appear at the second each one was printed in the run.
type Printed = {at: number; text: string; color?: string}[];

const LogBox: React.FC<{lines: Printed; box: Omit<Box, 'h'>; size: number; line: number; opacity?: number}> = ({lines, box, size, line, opacity = 1}) => {
  const frame = useSceneFrame();
  return (
    <div
      style={{
        position: 'absolute', left: box.x, top: box.y, width: box.w, height: 20 + lines.length * line, boxSizing: 'border-box', padding: '10px 16px',
        background: '#0b0d13', borderRadius: 10, fontFamily: MONO, fontSize: size, lineHeight: `${line}px`, color: TEXT, whiteSpace: 'pre', opacity,
      }}
    >
      {lines.map((printed) => (
        <div key={printed.text} style={{color: printed.color ?? TEXT, opacity: fadeAt(frame, printed.at, 0.25)}}>
          {printed.text}
        </div>
      ))}
    </div>
  );
};

// A trip of an effect out and of its answer back: the effect leaves, lands, and the answer is back.
type Trip = {effect: string; from: Point; to: Point; leave: number; land: number; answer?: string; back?: number};

const Travel: React.FC<{trip: Trip}> = ({trip}) => {
  const frame = useSceneFrame();
  const {effect, from, to, leave, land, answer, back} = trip;
  const out = ramp(frame, leave, land);
  return (
    <>
      {frame >= f(leave) && frame < f(land + 0.3) && (
        <Pill x={mix(from.x, to.x, out)} y={mix(from.y, to.y, out)} text={effect} color={ACCENT} opacity={1 - ramp(frame, land, land + 0.3)} />
      )}
      {answer && back !== undefined && frame >= f(land + 0.25) && frame < f(back + 0.3) && (
        <Pill
          x={mix(to.x, from.x, ramp(frame, land + 0.25, back))}
          y={mix(to.y, from.y, ramp(frame, land + 0.25, back))}
          text={answer}
          color={TEST}
          opacity={fadeAt(frame, land + 0.25, 0.15) * (1 - ramp(frame, back, back + 0.3))}
        />
      )}
    </>
  );
};

// Scene 1 — two zones: the handler stack inside doeff, Python's event loop outside it, and Await crossing.

const TITLE_LINES: Lines = [
  [0, 'A doeff program runs in two zones: inside doeff, and outside it.'],
  [6.5, "Inside, handlers answer each effect, with k. Outside, Python's event loop runs coroutines."],
  [15, 'Almost every effect stays inside. Await is the one that crosses the line.'],
];
const TITLE = f(23);
const T_IN: Box = {x: 40, y: 110, w: 700, h: 470};
const T_OUT: Box = {x: 820, y: 110, w: 420, h: 470};
const T_LINE = 780;
const T_CODE = [
  '@do',
  'def program():',
  '    who = yield Ask("who")',
  '    n = yield Get("n")',
  '    yield Tell("hi")',
  '    t = yield Spawn(job())',
  '    page = yield Await(fetch())',
];
const TC = {x: 60, y: 170, w: 340, size: 15, line: 28};
// The stack, innermost first: reader, writer, state, await_handler, scheduled.
const T_BANDS = [
  {name: 'reader', answers: 'Ask', color: TEST},
  {name: 'writer', answers: 'Tell', color: TEST},
  {name: 'state', answers: 'Get · Put', color: TEST},
  {name: 'await_handler', answers: 'Await', color: PROD},
  {name: 'scheduled', answers: 'Spawn · Wait · Gather', color: ACCENT},
];
const tBand = (i: number): Box => ({x: 424, y: 170 + i * 60, w: 300, h: 50});
const tBandIn = (i: number): Point => ({x: tBand(i).x + 240, y: tBand(i).y + 25});
const tCodeOut = (line: number): Point => ({x: TC.x + TC.w - 6, y: lineMid(TC.y, TC.line, line)});
// The effects the scene sends in: code line, band, and the seconds it leaves.
const T_TRIPS: Trip[] = [
  {effect: 'Ask', from: tCodeOut(2), to: tBandIn(0), leave: 8.6, land: 9.1, answer: "'Ada'", back: 9.7},
  {effect: 'Get', from: tCodeOut(3), to: tBandIn(2), leave: 9.9, land: 10.4, answer: '0', back: 11.0},
  {effect: 'Tell', from: tCodeOut(4), to: tBandIn(1), leave: 11.2, land: 11.7, answer: 'None', back: 12.3},
  {effect: 'Spawn', from: tCodeOut(5), to: tBandIn(4), leave: 12.5, land: 13.0, answer: 'Task', back: 13.6},
];
// The band each of T_TRIPS lands on.
const T_TRIP_BANDS = [0, 2, 1, 4];
const T_LOOP: Point = {x: 1030, y: 360};

const TitleScene: React.FC = () => {
  const frame = useSceneFrame();
  const first = 1 - ramp(frame, 6.0, 6.4);
  const zones = fadeAt(frame, 6.5, 0.5);
  const lit = (i: number) => T_TRIPS.some((t, j) => T_TRIP_BANDS[j] === i && frame >= f(t.land) && frame < f(t.land + 0.4));
  const crossing = frame >= f(17.3) && frame < f(18.3);
  const toBand = ramp(frame, 15.8, 16.4);
  const across = ramp(frame, 16.9, 18.1);
  const backAcross = ramp(frame, 19.6, 20.6);
  const backIn = ramp(frame, 20.7, 21.2);
  const awaitAt = tBandIn(3);
  const awaitOut = {x: tBand(3).x + tBand(3).w, y: awaitAt.y};
  // The code line of the latest effect sent, then the Await's line.
  const band = frame < f(8.4) ? null : frame < f(15.6) ? T_TRIPS.reduce((found, t, i) => (frame >= f(t.leave - 0.2) ? 2 + i : found), 2) : 6;
  const spin = interpolate(frame, [f(18.0), f(19.6)], [0, 540], clamp);
  return (
    <AbsoluteFill>
      <Headlines lines={TITLE_LINES} />
      <div style={{position: 'absolute', left: 0, right: 0, top: 170 + mix(16, 0, ramp(frame, 0.2, 0.8)), textAlign: 'center', opacity: fadeAt(frame, 0.2, 0.5) * first}}>
        <div style={{fontFamily: SANS, fontSize: 80, fontWeight: 700, color: TEXT}}>Effect Boundaries</div>
        <div style={{fontFamily: SANS, fontSize: 28, color: MUTED, marginTop: 6}}>docs/17-effect-boundaries.md</div>
      </div>
      <div style={{opacity: first}}>
        {label(250, 430, 'inside doeff', TEST, fadeAt(frame, 2.2), 34)}
        {label(250, 478, 'handlers answer effects', MUTED, fadeAt(frame, 2.6), 20, false)}
        <Boundary x={640} top={410} bottom={530} lit={false} opacity={fadeAt(frame, 3.0)} />
        {label(760, 430, 'outside doeff', PROD, fadeAt(frame, 3.4), 34)}
        {label(760, 478, "Python's event loop", MUTED, fadeAt(frame, 3.8), 20, false)}
      </div>
      <Zone box={T_IN} title="inside doeff" sub="the handler stack, innermost first" color={TEST} opacity={zones} />
      <Zone box={T_OUT} title="outside doeff" sub="Python's event loop" color={PROD} opacity={zones} />
      <Boundary x={T_LINE} top={100} bottom={590} lit={crossing} opacity={zones} />
      <CodeBlock lines={T_CODE} {...TC} band={band} opacity={fadeAt(frame, 7.0)} />
      {T_BANDS.map((b, i) => (
        <Band key={b.name} box={tBand(i)} name={b.name} answers={b.answers} color={b.color} on={lit(i) || (i === 3 && frame >= f(16.4) && frame < f(17.0))} opacity={fadeAt(frame, 7.2 + i * 0.25)} />
      ))}
      {label(T_IN.x + 20, T_IN.y + 400, <>each handler receives the effect and <span style={{color: ACCENT}}>k</span>, the rest of the program</>, TEXT, fadeAt(frame, 8.2), 19, false)}
      {T_TRIPS.map((trip) => (
        <Travel key={trip.effect} trip={trip} />
      ))}
      <div style={{position: 'absolute', left: T_LOOP.x - 90, top: T_LOOP.y - 90, width: 180, height: 180, opacity: fadeAt(frame, 7.4)}}>
        <svg width={180} height={180} viewBox="0 0 180 180">
          <circle cx={90} cy={90} r={78} fill="none" stroke={PROD} strokeWidth={4} strokeDasharray="22 12" transform={`rotate(${spin} 90 90)`} />
        </svg>
        <div style={{position: 'absolute', left: 0, right: 0, top: 70, textAlign: 'center', fontFamily: MONO, fontSize: 17, color: MUTED}}>event loop</div>
      </div>
      {label(T_OUT.x + 24, T_OUT.y + 400, 'thread: doeff-await-bridge', MUTED, fadeAt(frame, 7.6), 18, false)}
      {frame >= f(15.8) && frame < f(18.4) && (
        <Pill
          x={frame < f(16.9) ? mix(tCodeOut(6).x, awaitAt.x, toBand) : mix(awaitOut.x, T_LOOP.x, across)}
          y={frame < f(16.9) ? mix(tCodeOut(6).y, awaitAt.y, toBand) : mix(awaitOut.y, T_LOOP.y, across)}
          text={frame < f(16.9) ? 'Await(fetch())' : 'fetch() coroutine'}
          color={frame < f(16.9) ? ACCENT : PROD}
          opacity={1 - ramp(frame, 18.1, 18.4)}
        />
      )}
      {frame >= f(18.1) && frame < f(19.8) && <Pill x={T_LOOP.x} y={T_LOOP.y + 30} text="await …" color={PROD} opacity={between(frame, 18.1, 19.5, 0.2)} />}
      {frame >= f(19.6) && frame < f(21.5) && (
        <Pill
          x={frame < f(20.7) ? mix(T_LOOP.x, awaitOut.x, backAcross) : mix(awaitAt.x, tCodeOut(6).x, backIn)}
          y={frame < f(20.7) ? mix(T_LOOP.y, awaitOut.y, backAcross) : mix(awaitAt.y, tCodeOut(6).y, backIn)}
          text="'<page>'"
          color={TEST}
          opacity={1 - ramp(frame, 21.2, 21.5)}
        />
      )}
      {centred(612, 'Await: the one effect that leaves doeff, and comes back with its answer.', LOOK, fadeAt(frame, 19.0))}
    </AbsoluteFill>
  );
};

// Scene 2 — the page's pipeline: 15 effects, each answered by a handler inside doeff, on one thread.

const IN_LINES: Lines = [
  [0, "The page's pipeline: Ask, Spawn, Gather, Wait, Try, and each worker's Get, Put and Tell."],
  [7.5, 'An observer sees all 15 effects. Each one is answered by a handler inside doeff.'],
  [17.5, "One thread, no event loop at all. Even the tasks are the scheduler's work."],
];
const INSIDE = f(26);
const PIPELINE = [
  '@do',
  'def worker(name):',
  '    count = yield Get("count")',
  '    yield Put("count", count + 1)',
  '    yield Tell(f"{name} done")',
  '    return name.upper()',
  ' ',
  '@do',
  'def pipeline():',
  '    who = yield Ask("who")',
  '    t1 = yield Spawn(worker("a"))',
  '    t2 = yield Spawn(worker("b"))',
  '    values = yield Gather(t1, t2)',
  '    first = yield Wait(t1)',
  '    safe = yield Try(worker("c"))',
  '    return who, values, first, safe',
];
const PC = {x: 40, y: 100, w: 520, size: 16, line: 23};
// The stack of measure/inside.py, innermost first.
const IN_BANDS = [
  {name: 'observed', answers: 'sees each effect, passes it on', color: MUTED, kinds: [] as string[]},
  {name: 'reader', answers: 'Ask', color: TEST, kinds: ['Ask']},
  {name: 'writer', answers: 'Tell', color: TEST, kinds: ['WriterTellEffect']},
  {name: 'state', answers: 'Get · Put', color: TEST, kinds: ['Get', 'Put']},
  {name: 'try_handler', answers: 'Try', color: TEST, kinds: ['Try']},
  {name: 'scheduled', answers: 'Spawn · Gather · Wait', color: ACCENT, kinds: ['Spawn', 'Gather', 'Wait']},
];
const IN_STACK: Box = {x: 600, y: 100, w: 640, h: 392};
const inBand = (i: number): Box => ({x: 620, y: 142 + i * 56, w: 600, h: 46});
// measure/inside.log: the 15 effects observed sees, in order, and the code line each comes from.
const SEEN = [
  ['Ask', 9], ['Spawn', 10], ['Get', 2], ['Put', 3], ['WriterTellEffect', 4], ['Spawn', 11], ['Get', 2], ['Put', 3],
  ['WriterTellEffect', 4], ['Gather', 12], ['Wait', 13], ['Try', 14], ['Get', 2], ['Put', 3], ['WriterTellEffect', 4],
].map(([name, line], i) => ({name: name as string, line: line as number, at: 8.2 + i * 0.6}));
const pillText = (name: string) => (name === 'WriterTellEffect' ? 'Tell' : name);
const bandOf = (name: string) => IN_BANDS.findIndex((b) => b.kinds.includes(name));

const InsideScene: React.FC = () => {
  const frame = useSceneFrame();
  const current = SEEN.reduce((found, s, i) => (frame >= f(s.at) ? i : found), -1);
  const reading = frame < f(1.2) ? null : frame < f(7.6) ? Math.min(15, 9 + Math.floor((frame - f(1.2)) / f(0.9))) : null;
  const band = current >= 0 && frame < f(SEEN[SEEN.length - 1].at + 0.8) ? SEEN[current].line : reading;
  // How many of a kind have been answered so far, as the band's counter shows them.
  const answered = (kind: string) => SEEN.filter((s, i) => i <= current && s.name === kind).length;
  const counter = (i: number) =>
    i === 0 ? `${current + 1} seen` : IN_BANDS[i].kinds.filter((k) => answered(k) > 0).map((k) => `${pillText(k)} ×${answered(k)}`).join('  ');
  return (
    <AbsoluteFill>
      <Headlines lines={IN_LINES} />
      <CodeBlock lines={PIPELINE} {...PC} band={band} opacity={fadeAt(frame, 0.2)} />
      <div style={{position: 'absolute', left: IN_STACK.x, top: IN_STACK.y, width: IN_STACK.w, height: IN_STACK.h, boxSizing: 'border-box', borderRadius: 18, background: PANEL, border: `3px solid ${TEST}`, opacity: fadeAt(frame, 0.4)}}>
        <div style={{position: 'absolute', left: 18, top: 8, fontFamily: SANS, fontSize: 19, fontWeight: 700, color: TEST}}>
          inside doeff
          <span style={{color: MUTED, fontWeight: 400, fontSize: 16}}>{'  the stack of measure/inside.py, innermost first'}</span>
        </div>
      </div>
      {IN_BANDS.map((b, i) => {
        const on = current >= 0 && frame < f(SEEN[current].at + 0.55) && (i === 0 ? frame < f(SEEN[current].at + 0.3) && frame >= f(SEEN[current].at + 0.1) : bandOf(SEEN[current].name) === i && frame >= f(SEEN[current].at + 0.35));
        return <Band key={b.name} box={inBand(i)} name={b.name} answers={b.answers} color={b.color} on={on} opacity={fadeAt(frame, 0.6 + i * 0.2)} count={current >= 0 ? counter(i) || undefined : undefined} />;
      })}
      {SEEN.map((s, i) => {
        if (frame < f(s.at) || frame >= f(s.at + 0.75)) return null;
        const from = {x: PC.x + PC.w - 10, y: lineMid(PC.y, PC.line, s.line)};
        const obs = {x: inBand(0).x + 440, y: inBand(0).y + 23};
        const target = inBand(bandOf(s.name));
        const to = {x: target.x + 330, y: target.y + 23};
        const p = along(frame, [[s.at, from], [s.at + 0.2, obs], [s.at + 0.45, to]]);
        return <Pill key={i} x={p.x} y={p.y} text={pillText(s.name)} color={ACCENT} opacity={1 - ramp(frame, s.at + 0.5, s.at + 0.75)} />;
      })}
      {label(40, 512, 'measure/inside.py · the effects observed sees, in order', MUTED, fadeAt(frame, 8.0), 16, false)}
      <div style={{position: 'absolute', left: 40, top: 540, width: 1200, display: 'flex', gap: 6, flexWrap: 'nowrap'}}>
        {SEEN.map((s, i) => (
          <div
            key={i}
            style={{
              fontFamily: MONO, fontSize: 13, fontWeight: 700, padding: '4px 8px', borderRadius: 8, background: bandOf(s.name) === 5 ? ACCENT : TEST, color: '#0f1117',
              opacity: fadeAt(frame, s.at + 0.45, 0.2), whiteSpace: 'nowrap',
            }}
          >
            {s.name}
          </div>
        ))}
      </div>
      {centred(596, "threads: ['MainThread']   ·   event loop running: [False]", TEST, fadeAt(frame, 18.2), 24)}
      {centred(640, "result ('pipeline', ['A', 'B'], 'A', Ok('C')) · 3 runs, the same 15 effects in the same order", MUTED, fadeAt(frame, 19.2), 17, false)}
    </AbsoluteFill>
  );
};

// Scene 3 — Await leaves doeff: the coroutine runs on the bridge thread's event loop while the scheduler parks k.

const ES_LINES: Lines = [
  [0, "Await is different. Python's await is syntax, and it only runs under an event loop."],
  [6.5, 'await_handler hands each coroutine to an event loop on its own thread. The scheduler parks the k.'],
  [15, "While a and b are outside, c's Steps are still answered inside doeff."],
  [21, 'b returns at 106.5 ms and its k resumes; a at 206.6 ms. About 200 ms in all, not 300.'],
];
const ESCAPE = f(32);
const E_IN: Box = {x: 40, y: 100, w: 700, h: 340};
const E_OUT: Box = {x: 800, y: 100, w: 440, h: 340};
const E_LINE = 770;
const FETCH = ['@do', 'def fetch(name, seconds):', '    page = yield Await(download(name, seconds))', '    return page'];
const FC = {x: 60, y: 146, w: 470, size: 15, line: 24};
const DOWNLOAD = ['async def download(name, seconds):', '    await asyncio.sleep(seconds)', '    return f"<{name}>"'];
const DC = {x: 820, y: 146, w: 400, size: 14, line: 22};
const SCHED: Box = {x: 60, y: 282, w: 660, h: 140};
const STEPPED: Box = {x: 550, y: 146, w: 170, h: 120};
// The coroutines' cards on the event loop.
const coroCard = (i: number): Point => ({x: 1020, y: 290 + i * 56});
const kSlot = (i: number): Point => ({x: 240, y: 336 + i * 46});
// Task c, which only performs Steps, while it runs.
const TASK_C: Point = {x: 600, y: 336};
// The lines of measure/escape.log (run 1), in the order printed, with the second each one is shown.
const E_PRINTS: Printed = [
  {at: 7.6, text: "   3.81 ms  MainThread          a: yield Await(download('a', 0.2))"},
  {at: 10.2, text: "   4.89 ms  MainThread          b: yield Await(download('b', 0.1))"},
  {at: 16.1, text: '   5.06 ms  MainThread          c: Step(1) answered by stepped'},
  {at: 17.3, text: '   5.10 ms  MainThread          c: Step(3) answered by stepped'},
  {at: 18.4, text: '   5.63 ms  doeff-await-bridge  a: coroutine starts, loop running: True', color: PROD},
  {at: 18.8, text: '   5.71 ms  doeff-await-bridge  b: coroutine starts, loop running: True', color: PROD},
  {at: 23.8, text: ' 106.52 ms  doeff-await-bridge  b: coroutine returns', color: PROD},
  {at: 25.0, text: " 106.78 ms  MainThread          b: resumed with '<b>'"},
  {at: 27.8, text: ' 206.55 ms  doeff-await-bridge  a: coroutine returns', color: PROD},
  {at: 29.0, text: " 206.76 ms  MainThread          a: resumed with '<a>'"},
];
// The run's milliseconds at each shown line, and the clock between them.
const E_MS: [number, number][] = [[7.6, 3.81], [10.2, 4.89], [16.1, 5.06], [17.3, 5.1], [18.4, 5.63], [18.8, 5.71], [21.4, 5.71], [23.8, 106.52], [25.0, 106.78], [25.4, 106.78], [27.8, 206.55], [29.0, 206.76]];
const E_TASKS = [
  {name: 'a', seconds: 0.2, leave: 7.0, park: 7.6, cross: 8.2, over: 9.2, start: 18.4, done: 27.8, resumed: 29.0},
  {name: 'b', seconds: 0.1, leave: 9.6, park: 10.2, cross: 10.8, over: 11.8, start: 18.8, done: 23.8, resumed: 25.0},
];

const EscapeScene: React.FC = () => {
  const frame = useSceneFrame();
  const ms = interpolate(frame, E_MS.map(([s]) => f(s)), E_MS.map(([, v]) => v), clamp);
  const crossing = E_TASKS.some((t) => (frame >= f(t.cross) && frame < f(t.over)) || (frame >= f(t.done) && frame < f(t.done + 0.8)));
  const fBand = frame < f(6.8) ? null : frame < f(12.2) ? 2 : null;
  const steps = [16.1, 16.7, 17.3];
  return (
    <AbsoluteFill>
      <Headlines lines={ES_LINES} />
      <Zone box={E_IN} title="inside doeff" sub="thread MainThread · no event loop" color={TEST} opacity={fadeAt(frame, 0)} />
      <Zone box={E_OUT} title="outside doeff" sub="thread doeff-await-bridge" color={PROD} opacity={fadeAt(frame, 0.4)} />
      <Boundary x={E_LINE} top={92} bottom={448} lit={crossing} opacity={fadeAt(frame, 0.4)} />
      <CodeBlock lines={FETCH} {...FC} band={fBand} opacity={fadeAt(frame, 0.3)} />
      <CodeBlock lines={DOWNLOAD} {...DC} opacity={fadeAt(frame, 1.2)} />
      {label(DC.x + 4, 236, 'await needs a running loop: this thread runs one', PROD, fadeAt(frame, 2.6), 14, false)}
      <div style={{position: 'absolute', left: SCHED.x, top: SCHED.y, width: SCHED.w, height: SCHED.h, boxSizing: 'border-box', borderRadius: 12, background: '#0b0d13', border: `2px solid ${ACCENT}`, opacity: fadeAt(frame, 0.6)}}>
        <div style={{position: 'absolute', left: 14, top: 8, fontFamily: SANS, fontSize: 17, fontWeight: 700, color: ACCENT, whiteSpace: 'nowrap'}}>
          <span style={{fontFamily: MONO}}>scheduler</span>
          <span style={{color: MUTED, fontWeight: 400, fontSize: 15}}>{'  holds each task’s k until its ExternalPromise completes'}</span>
        </div>
      </div>
      <div style={{position: 'absolute', left: STEPPED.x, top: STEPPED.y, width: STEPPED.w, height: STEPPED.h, boxSizing: 'border-box', borderRadius: 12, background: '#0b0d13', border: `2px solid ${TEST}`, ...glow(steps.some((s) => frame >= f(s - 0.1) && frame < f(s + 0.3)), TEST), opacity: fadeAt(frame, 13.0) }}>
        <div style={{position: 'absolute', left: 12, top: 8, fontFamily: MONO, fontSize: 16, fontWeight: 700, color: TEST}}>stepped</div>
        <div style={{position: 'absolute', left: 12, top: 34, fontFamily: SANS, fontSize: 14, color: MUTED, lineHeight: '19px', width: 150, whiteSpace: 'normal'}}>answers task c&apos;s Step(n) inside doeff</div>
        {steps.map((s, i) => (
          <div key={i} style={{position: 'absolute', left: 12 + i * 50, top: 82, fontFamily: MONO, fontSize: 14, fontWeight: 700, padding: '2px 8px', borderRadius: 8, background: TEST, color: '#0f1117', opacity: fadeAt(frame, s, 0.2)}}>
            {i + 1}
          </div>
        ))}
      </div>
      <KCard text="task c · running" p={TASK_C} w={200} color={TEXT} opacity={between(frame, 15.3, 17.9)} />
      {steps.map((s, i) => (
        <React.Fragment key={i}>
          {frame >= f(s - 0.6) && frame < f(s + 0.1) && (
            <Pill x={mix(TASK_C.x, STEPPED.x + 24 + i * 50, ramp(frame, s - 0.6, s - 0.1))} y={mix(TASK_C.y, STEPPED.y + 94, ramp(frame, s - 0.6, s - 0.1))} text={`Step(${i + 1})`} color={ACCENT} opacity={1 - ramp(frame, s - 0.15, s + 0.1)} />
          )}
        </React.Fragment>
      ))}
      {E_TASKS.map((t, i) => {
        const fromCode = {x: FC.x + FC.w - 20, y: lineMid(FC.y, FC.line, 2)};
        const coro = coroCard(i);
        const toPark = ramp(frame, t.leave, t.park);
        const over = ramp(frame, t.cross, t.over);
        const back = ramp(frame, t.done, t.done + 0.8);
        const parked = kSlot(i);
        const running = frame >= f(t.start);
        const fill = interpolate(ms, [t.name === 'a' ? 5.63 : 5.71, t.name === 'a' ? 206.55 : 106.52], [0, 1], clamp);
        return (
          <React.Fragment key={t.name}>
            {frame >= f(t.leave) && frame < f(t.park + 0.3) && (
              <Pill x={mix(fromCode.x, parked.x, toPark)} y={mix(fromCode.y, parked.y, toPark)} text={`Await(download('${t.name}', ${t.seconds}))`} color={ACCENT} opacity={1 - ramp(frame, t.park, t.park + 0.3)} />
            )}
            <KCard text={frame < f(t.resumed) ? `k of ${t.name} · parked` : `k of ${t.name} · resumed with '<${t.name}>'`} p={parked} w={330} color={frame < f(t.resumed) ? ACCENT : TEST} opacity={fadeAt(frame, t.park, 0.25) * (1 - ramp(frame, t.resumed + 1.6, t.resumed + 2.0))} />
            {frame >= f(t.cross) && frame < f(t.over + 0.2) && (
              <Pill x={mix(parked.x + 200, coro.x, over)} y={mix(parked.y, coro.y, over)} text={`download('${t.name}', ${t.seconds})`} color={PROD} opacity={1 - ramp(frame, t.over, t.over + 0.2)} />
            )}
            <div
              style={{
                position: 'absolute', left: coro.x - 190, top: coro.y - 22, width: 380, height: 44, boxSizing: 'border-box', borderRadius: 10, background: '#2a2016',
                border: `2px solid ${PROD}`, opacity: fadeAt(frame, t.over, 0.2) * (1 - ramp(frame, t.done + 0.4, t.done + 0.8)), overflow: 'hidden',
              }}
            >
              <div style={{position: 'absolute', left: 0, top: 0, bottom: 0, width: `${fill * 100}%`, background: PROD, opacity: 0.35}} />
              <div style={{position: 'absolute', left: 12, top: 0, lineHeight: '40px', fontFamily: MONO, fontSize: 15, fontWeight: 700, color: TEXT, whiteSpace: 'nowrap'}}>
                {`download('${t.name}', ${t.seconds}) · ${running ? 'running' : 'handed over'}`}
              </div>
            </div>
            {frame >= f(t.done) && frame < f(t.done + 1.1) && (
              <Pill x={mix(coro.x - 100, parked.x + 120, back)} y={mix(coro.y, parked.y, back)} text={`'<${t.name}>'`} color={TEST} opacity={1 - ramp(frame, t.done + 0.8, t.done + 1.1)} />
            )}
          </React.Fragment>
        );
      })}
      <div style={{position: 'absolute', left: 1060, top: 468, width: 160, textAlign: 'right', fontFamily: MONO, fontSize: 18, fontWeight: 700, color: LOOK, opacity: fadeAt(frame, 7.4)}}>
        {`${ms.toFixed(2)} ms`}
      </div>
      <LogBox lines={E_PRINTS} box={{x: 40, y: 458, w: 1200}} size={14} line={21} opacity={fadeAt(frame, 0.6)} />
      {label(SCHED.x + 16, SCHED.y + 76, "main: Gather -> ['<a>', '<b>', 'c'] · 206.9 ms in all", TEST, fadeAt(frame, 29.6), 17)}
      {label(SCHED.x + 16, SCHED.y + 104, '10 runs of measure/escape.py: 201.8–209.2 ms in all', MUTED, fadeAt(frame, 30.2), 15, false)}
    </AbsoluteFill>
  );
};

// Scene 4 — scheduler suspension stays inside doeff and costs about a millisecond; Await waits on the world outside.

const SU_LINES: Lines = [
  [0, 'Waiting inside doeff is scheduler suspension: a handler holds the k, and resumes it later.'],
  [7, 'Wait on a Promise another task completes: 1.91 ms. Delay(3) on the virtual clock: 0.52 ms.'],
  [14, 'Await of a 0.3 s sleep: 304 ms of real time, answered on the bridge thread. Only it leaves doeff.'],
];
const SUSPEND = f(23);
// measure/suspend.log run 1: the three waits, their wall times and the thread that answered each.
const WAITS = [
  {code: 'yield Wait(promise.future)', note: 'another task completes the Promise', ms: 1.91, by: 'MainThread', who: 'scheduler', color: TEST, park: 2.0, grow: 7.6},
  {code: 'yield Delay(3)', note: 'sim_time_handler: the virtual clock moves 3 s', ms: 0.52, by: 'MainThread', who: 'scheduler + virtual clock', color: TEST, park: 3.4, grow: 10.0},
  {code: 'yield Await(sleep_outside())', note: 'asyncio.sleep(0.3) on the event loop', ms: 304.03, by: 'doeff-await-bridge', who: 'scheduler, until the loop answers', color: PROD, park: 14.8, grow: 15.4},
];
const rowY = (i: number) => 200 + i * 130;
const BAR_X = 450;
const PX_PER_MS = 1.3;

const SuspendScene: React.FC = () => {
  const frame = useSceneFrame();
  return (
    <AbsoluteFill>
      <Headlines lines={SU_LINES} />
      {label(40, 118, 'the wait', MUTED, fadeAt(frame, 0.2), 17, false)}
      {label(BAR_X, 118, 'wall time, measured', MUTED, fadeAt(frame, 0.2), 17, false)}
      {label(1010, 118, 'answered on', MUTED, fadeAt(frame, 0.2), 17, false)}
      <div style={{position: 'absolute', left: BAR_X - 6, top: rowY(2) - 56, width: 560, height: 112, borderRadius: 14, background: PROD, opacity: 0.08 * fadeAt(frame, 14.2)}} />
      {label(BAR_X + 8, rowY(2) - 52, 'outside doeff', PROD, fadeAt(frame, 14.2), 15)}
      {WAITS.map((w, i) => {
        const y = rowY(i);
        const show = i < 2 ? fadeAt(frame, 0.4 + i * 0.6) : fadeAt(frame, 14.2);
        const grown = i < 2 ? ramp(frame, w.grow, w.grow + 0.5) : ramp(frame, w.grow, w.grow + 2.4);
        const width = Math.max(4, w.ms * PX_PER_MS * grown);
        const done = frame >= f(w.grow + (i < 2 ? 0.5 : 2.4));
        return (
          <React.Fragment key={w.code}>
            <CodeBlock lines={[w.code]} x={40} y={y - 46} w={380} size={16} line={28} opacity={show} />
            {label(44, y + 12, w.note, MUTED, show, 14, false)}
            <KCard text={done ? 'k resumed' : `k parked · ${w.who}`} p={{x: BAR_X + 170 + (i === 2 ? 60 : 0), y: y - 28 + 56}} w={i === 2 ? 360 : 300} color={done ? TEST : ACCENT} opacity={fadeAt(frame, w.park, 0.25) * (1 - ramp(frame, w.grow + (i < 2 ? 1.6 : 3.4), w.grow + (i < 2 ? 2.0 : 3.8)))} />
            <div style={{position: 'absolute', left: BAR_X, top: y - 14, width, height: 22, borderRadius: 6, background: w.color, boxShadow: `0 0 12px ${w.color}`, opacity: fadeAt(frame, w.grow, 0.15)}} />
            {label(BAR_X + width + 12, y - 16, `${w.ms.toFixed(2)} ms`, TEXT, fadeAt(frame, w.grow + (i < 2 ? 0.3 : 2.2)), 22)}
            <div
              style={{
                position: 'absolute', left: 1010, top: y - 18, fontFamily: MONO, fontSize: 16, fontWeight: 700, padding: '4px 12px', borderRadius: 10, background: w.color,
                color: '#0f1117', opacity: fadeAt(frame, w.grow + (i < 2 ? 0.6 : 2.6)), whiteSpace: 'nowrap',
              }}
            >
              {w.by}
            </div>
          </React.Fragment>
        );
      })}
      {centred(598, 'Suspension parks k inside doeff. Await waits on the world outside.', TEST, fadeAt(frame, 19.2), 24)}
      {centred(642, '5 runs of measure/suspend.py: Wait 0.27–1.91 ms · Delay(3) 0.36–0.52 ms · Await 302.9–312.7 ms', MUTED, fadeAt(frame, 19.8), 16, false)}
    </AbsoluteFill>
  );
};

// Scene 5 — custom handler rules: answer domain effects inside, perform Await only for real async work, and put
// await_handler outside the handler that performs it.

const HR_LINES: Lines = [
  [0, 'Your own handlers keep the boundary strict. One program, Fetch(url), under two handlers.'],
  [7, 'from_table answers inside doeff. over_async needs Python async, so it performs Await itself.'],
  [15, 'That Await travels outward: await_handler goes outside over_async, and scheduled outside both.'],
  [24, 'Leave await_handler out, or put it on the inside, and the Await finds no handler.'],
  [31, 'Answer domain effects in handlers. Cross only for real async work. Leave tasks to the scheduler.'],
];
const RULES = f(40);
const HCODE = [
  '@do',
  'def page(url):',
  '    return (yield Fetch(url))',
  ' ',
  '@handler',
  'def from_table(effect: Fetch, k):',
  '    return (yield Resume(k, f"<html>{effect.url}</html>"))',
  ' ',
  '@handler',
  'def over_async(effect: Fetch, k):',
  '    body = yield Await(http_get(effect.url))',
  '    return (yield Resume(k, body))',
];
const HC = {x: 40, y: 100, w: 600, size: 15, line: 22};
const DIAGRAM: Box = {x: 680, y: 100, w: 560, h: 300};
const layer = (d: number): Box => ({x: DIAGRAM.x + 24 * d, y: DIAGRAM.y + 40 * d, w: DIAGRAM.w - 48 * d, h: DIAGRAM.h - 80 * d});
const LAYER_COLOR: Record<string, string> = {scheduled: ACCENT, await_handler: PROD, over_async: TEST};
// The three ways measure/handler_rules.py nests the handlers around page("a.dev"), outermost first.
const NESTINGS = [
  {layers: ['scheduled', 'await_handler', 'over_async'], from: 15.0, to: 24.2},
  {layers: ['scheduled', 'over_async'], from: 24.4, to: 27.6},
  {layers: ['scheduled', 'over_async', 'await_handler'], from: 27.8, to: 31.2},
];
const LOOP_BOX: Box = {x: 950, y: 412, w: 290, h: 40};
// measure/handler_rules.log run 1, one line per nesting, with the second each one is shown.
const OUTCOMES = [
  {at: 10.0, code: 'scheduled(from_table(prog))', result: "-> '<html>a.dev</html>'", color: TEST},
  {at: 22.2, code: 'scheduled(await_handler(over_async(prog)))', result: "-> '<html>a.dev</html> from doeff-await-bridge'", color: TEST},
  {at: 26.2, code: 'scheduled(over_async(prog))', result: 'raises UnhandledEffect: no handler found for effect: Await', color: FAIL},
  {at: 29.6, code: 'scheduled(over_async(await_handler(prog)))', result: 'raises UnhandledEffect: no handler found for effect: Await', color: FAIL},
];
const RULE_TEXTS = [
  'Answer domain effects in handlers, inside doeff.',
  'Perform Await only for work that must run in Python async.',
  'Leave task switching to the scheduler handler.',
];

const RulesScene: React.FC = () => {
  const frame = useSceneFrame();
  const codeBand = frame < f(7.4) ? null : frame < f(11.4) ? mix(2, 6, ramp(frame, 8.6, 9.0)) : frame < f(14.6) ? 10 : null;
  const fetchFrom = {x: HC.x + 300, y: lineMid(HC.y, HC.line, 2)};
  const fetchTo = {x: HC.x + 420, y: lineMid(HC.y, HC.line, 6)};
  const results = 1 - ramp(frame, 30.8, 31.2);
  return (
    <AbsoluteFill>
      <Headlines lines={HR_LINES} />
      <CodeBlock lines={HCODE} {...HC} band={codeBand} opacity={fadeAt(frame, 0.3)} />
      {label(HC.x + 430, lineMid(HC.y, HC.line, 10) - 11, '◀ performs Await itself', PROD, between(frame, 11.6, 14.8), 15)}
      {frame >= f(8.6) && frame < f(9.6) && (
        <Pill x={mix(fetchFrom.x, fetchTo.x, ramp(frame, 8.6, 9.1))} y={mix(fetchFrom.y, fetchTo.y, ramp(frame, 8.6, 9.1))} text='Fetch("a.dev")' color={ACCENT} opacity={1 - ramp(frame, 9.1, 9.4)} />
      )}
      {NESTINGS.map((n, ni) => {
        const opacity = between(frame, n.from, n.to, 0.3);
        if (opacity <= 0) return null;
        const depth = n.layers.length;
        const prog = layer(depth);
        const progMid = {x: prog.x + prog.w / 2, y: prog.y + prog.h / 2};
        const at = (name: string): Point => {
          const d = n.layers.indexOf(name);
          return {x: layer(d).x + 110, y: layer(d).y + 18};
        };
        const t0 = n.from + 1.2;
        const reach = ni === 0 ? at('await_handler') : {x: 1110, y: DIAGRAM.y + 18};
        const fetchTrip = ramp(frame, t0, t0 + 0.6);
        const awaitTrip = ramp(frame, t0 + 1.0, t0 + 1.7);
        const outTrip = ramp(frame, t0 + 2.0, t0 + 3.0);
        const backTrip = ramp(frame, t0 + 3.6, t0 + 4.8);
        const loopMid = {x: LOOP_BOX.x + LOOP_BOX.w / 2, y: LOOP_BOX.y + LOOP_BOX.h / 2};
        const failAt = {x: 1110, y: DIAGRAM.y + 18};
        return (
          <div key={ni} style={{opacity}}>
            {n.layers.map((name, d) => {
              const box = layer(d);
              const lit = name === 'await_handler' && ni === 0 && frame >= f(t0 + 1.6) && frame < f(t0 + 2.2);
              return (
                <div
                  key={name}
                  style={{
                    position: 'absolute', left: box.x, top: box.y, width: box.w, height: box.h, boxSizing: 'border-box', borderRadius: 16, background: d === 0 ? PANEL : '#0b0d13',
                    border: `3px solid ${LAYER_COLOR[name]}`, ...glow(lit, LAYER_COLOR[name]),
                  }}
                >
                  <div style={{position: 'absolute', left: 14, top: 6, fontFamily: MONO, fontSize: 17, fontWeight: 700, color: lit ? LOOK : LAYER_COLOR[name]}}>{name}</div>
                </div>
              );
            })}
            <div style={{position: 'absolute', left: prog.x, top: prog.y, width: prog.w, height: prog.h, boxSizing: 'border-box', borderRadius: 12, border: `2px solid ${TEXT}`, background: '#0b0d13'}}>
              <div style={{position: 'absolute', left: 0, right: 0, top: 0, bottom: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', fontFamily: MONO, fontSize: 17, fontWeight: 700, color: TEXT}}>page(&quot;a.dev&quot;)</div>
            </div>
            {frame >= f(t0) && frame < f(t0 + 0.9) && (
              <Pill x={mix(progMid.x, at('over_async').x, fetchTrip)} y={mix(progMid.y, at('over_async').y, fetchTrip)} text="Fetch" color={ACCENT} opacity={1 - ramp(frame, t0 + 0.6, t0 + 0.9)} />
            )}
            {frame >= f(t0 + 1.0) && frame < f(t0 + 2.0) && (
              <Pill x={mix(at('over_async').x, reach.x, awaitTrip)} y={mix(at('over_async').y, reach.y, awaitTrip)} text="Await" color={ACCENT} opacity={1 - ramp(frame, t0 + 1.7, t0 + 2.0)} />
            )}
            {ni === 0 && frame >= f(t0 + 2.0) && frame < f(t0 + 3.2) && (
              <Pill x={mix(reach.x, loopMid.x, outTrip)} y={mix(reach.y, loopMid.y, outTrip)} text="http_get coroutine" color={PROD} opacity={1 - ramp(frame, t0 + 3.0, t0 + 3.2)} />
            )}
            {ni === 0 && frame >= f(t0 + 3.6) && frame < f(t0 + 5.1) && (
              <Pill x={mix(loopMid.x, progMid.x, backTrip)} y={mix(loopMid.y, progMid.y, backTrip)} text="body" color={TEST} opacity={1 - ramp(frame, t0 + 4.8, t0 + 5.1)} />
            )}
            {ni > 0 && frame >= f(t0 + 2.0) && <Pill x={failAt.x} y={failAt.y} text="no handler for Await" color={FAIL} opacity={fadeAt(frame, t0 + 2.0, 0.2)} />}
          </div>
        );
      })}
      <div
        style={{
          position: 'absolute', left: LOOP_BOX.x, top: LOOP_BOX.y, width: LOOP_BOX.w, height: LOOP_BOX.h, boxSizing: 'border-box', borderRadius: 10, border: `2px solid ${PROD}`,
          background: '#2a2016', fontFamily: MONO, fontSize: 13, fontWeight: 700, color: PROD, lineHeight: '36px', textAlign: 'center', whiteSpace: 'nowrap',
          opacity: between(frame, 15.2, 31.0) * (frame >= f(24.4) ? 0.35 : 1),
        }}
      >
        event loop · doeff-await-bridge
      </div>
      {label(DIAGRAM.x + 4, LOOP_BOX.y + 8, 'outside doeff', PROD, between(frame, 15.2, 31.0) * (frame >= f(24.4) ? 0.35 : 1), 16)}
      <div style={{opacity: results}}>
        {label(40, 462, 'measure/handler_rules.py', MUTED, fadeAt(frame, 9.6), 15, false)}
        {OUTCOMES.map((o, i) => (
          <div key={o.code} style={{position: 'absolute', left: 40, top: 490 + i * 50, width: 1200, height: 40, boxSizing: 'border-box', padding: '0 14px', borderRadius: 10, background: '#0b0d13', fontFamily: MONO, fontSize: 15, lineHeight: '40px', whiteSpace: 'nowrap', opacity: fadeAt(frame, o.at)}}>
            <span style={{color: TEXT}}>{o.code}</span>
            <span style={{color: o.color, fontWeight: 700}}>{`  ${o.result}`}</span>
          </div>
        ))}
      </div>
      {RULE_TEXTS.map((text, i) => (
        <div
          key={text}
          style={{
            position: 'absolute', left: 120, top: 470 + i * 72, width: 1040, height: 58, boxSizing: 'border-box', borderRadius: 14, background: PANEL, border: `2px solid ${TEST}`,
            fontFamily: SANS, fontSize: 24, fontWeight: 700, color: TEXT, lineHeight: '54px', paddingLeft: 24, whiteSpace: 'nowrap',
            opacity: fadeAt(frame, 31.6 + i * 1.6), transform: `translateY(${mix(12, 0, ramp(frame, 31.6 + i * 1.6, 32.1 + i * 1.6))}px)`,
          }}
        >
          <span style={{color: TEST, marginRight: 16}}>{i + 1}</span>
          {text}
        </div>
      ))}
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
      <div style={{fontFamily: SANS, fontSize: 30, color: ACCENT, marginTop: 6, opacity: fadeAt(frame, 0.2, 0.4)}}>Effect Boundaries</div>
      <div style={{fontFamily: SANS, fontSize: 28, lineHeight: '42px', color: TEXT, marginTop: 34, width: 1050, opacity: fadeAt(frame, 0.6, 0.4)}}>
        Effects are answered by handlers, inside doeff. Await is the one door out, to Python&apos;s event loop, and its answer comes back in.
      </div>
      <div style={{fontFamily: MONO, fontSize: 28, color: TEST, marginTop: 40, opacity: fadeAt(frame, 1.1, 0.4)}}>pip install doeff</div>
    </AbsoluteFill>
  );
};

// The scenes in order; Root.tsx registers EFFECT_BOUNDARIES.length frames of EFFECT_BOUNDARIES.Video.
export const EFFECT_BOUNDARIES = narrate('effect-boundaries', DURATIONS, [
  {length: TITLE, starts: startsOf(TITLE_LINES), ids: ['t1', 't2', 't3'], node: <TitleScene />},
  {length: INSIDE, starts: startsOf(IN_LINES), ids: ['i1', 'i2', 'i3'], node: <InsideScene />},
  {length: ESCAPE, starts: startsOf(ES_LINES), ids: ['e1', 'e2', 'e3', 'e4'], node: <EscapeScene />},
  {length: SUSPEND, starts: startsOf(SU_LINES), ids: ['s1', 's2', 's3'], node: <SuspendScene />},
  {length: RULES, starts: startsOf(HR_LINES), ids: ['h1', 'h2', 'h3', 'h4', 'h5'], node: <RulesScene />},
  {length: OUTRO, starts: [0], ids: ['o1'], node: <Outro />},
]);
