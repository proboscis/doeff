// The video on docs/09-advanced-effects.md: tasks, shared state, observation, time and control flow of your own are
// all effects, each answered by a handler in one stack. Five scenes, each with headlines a synthetic voice reads
// (narration.json), then the outro:
//   1. The page's effects, and the stack of handlers that answers them.
//   2. Three tasks share one count through the state handler: 300 with no wait between Get and Put, 100 with a
//      Delay between them, 300 again inside CreateSemaphore(1) (measure/shared_state.py).
//   3. WithObserve sees every effect yielded inside its scope, spawned tasks included, and changes nothing
//      (measure/observe.py).
//   4. Control flow of your own: the Audit handler resumes k, the Abort handler does not, so the body ends there
//      (measure/control.py).
//   5. The time handler decides what Delay means: a real sleep, or a virtual clock that moves at once
//      (measure/time.py).
import React from 'react';
import {AbsoluteFill, Easing, interpolate} from 'remotion';
import {clamp, fadeAt, mix, ramp} from '../../kit/motion';
import {narrate} from '../../kit/narrated';
import {Clock, CodeBlock, glow, Headlines, lineMid, Lines, Pill, startsOf} from '../../kit/parts';
import {ACCENT, f, FAIL, FPS, LOOK, MONO, MUTED, PANEL, PROD, SANS, TEST, TEXT} from '../../kit/theme';
import {useSceneFrame} from '../../kit/warp';
import DURATIONS from './durations.json';

type Point = {x: number; y: number};

// Where something moving is between two points, eased over [from, to] seconds.
const between = (frame: number, a: Point, b: Point, from: number, to: number): Point => {
  const t = ramp(frame, from, to);
  return {x: mix(a.x, b.x, t), y: mix(a.y, b.y, t)};
};

// An effect or answer that travels from a to b over [from, to] seconds and fades out on arrival.
const Trip: React.FC<{a: Point; b: Point; from: number; to: number; text: string; color: string; left?: boolean}> = ({a, b, from, to, text, color, left = false}) => {
  const frame = useSceneFrame();
  if (frame < f(from) || frame >= f(to + 0.25)) return null;
  const p = between(frame, a, b, from, to);
  return <Pill x={p.x} y={p.y} text={text} color={color} left={left} opacity={fadeAt(frame, from, 0.12) * (1 - ramp(frame, to, to + 0.25))} />;
};

// A named thing on the screen: a task, a program, a continuation.
const Card: React.FC<{label: string; p: Point; w?: number; opacity?: number; color?: string; on?: boolean; strike?: boolean}> = ({label, p, w = 200, opacity = 1, color = ACCENT, on = false, strike = false}) => (
  <div
    style={{
      position: 'absolute', left: p.x, top: p.y, transform: 'translate(-50%, -50%)', width: w, height: 40, boxSizing: 'border-box',
      borderRadius: 10, background: '#1d1a33', border: `3px solid ${color}`, ...(on ? glow(true, color) : {boxShadow: `0 0 12px ${color}66`}),
      fontFamily: SANS, fontSize: 17, fontWeight: 700, color: TEXT, lineHeight: '34px', textAlign: 'center', whiteSpace: 'nowrap', opacity,
      textDecoration: strike ? 'line-through' : 'none',
    }}
  >
    {label}
  </div>
);

const label = (x: number, y: number, text: React.ReactNode, color: string, opacity: number, size = 20, mono = false): React.ReactNode => (
  <div style={{position: 'absolute', left: x, top: y, fontFamily: mono ? MONO : SANS, fontSize: size, fontWeight: 700, color, whiteSpace: 'nowrap', opacity}}>{text}</div>
);

const centred = (y: number, text: React.ReactNode, color: string, opacity: number, size = 23, bold = true): React.ReactNode => (
  <div style={{position: 'absolute', left: 40, right: 40, top: y, textAlign: 'center', fontFamily: SANS, fontSize: size, fontWeight: bold ? 700 : 400, lineHeight: '32px', color, opacity}}>
    {text}
  </div>
);

// A handler layer drawn as a box with its name at the top left; it glows while it handles an effect.
type Box = {x: number; y: number; w: number; h: number};
const Layer: React.FC<{box: Box; name: string; handles: string; color: string; on: boolean; opacity: number; dashed?: boolean}> = ({box, name, handles, color, on, opacity, dashed = false}) => (
  <div
    style={{
      position: 'absolute', left: box.x, top: box.y, width: box.w, height: box.h, boxSizing: 'border-box', borderRadius: 18,
      border: `3px ${dashed ? 'dashed' : 'solid'} ${color}`, ...glow(on, color), opacity,
    }}
  >
    <div style={{position: 'absolute', left: 16, top: 7, fontFamily: SANS, fontSize: 17, fontWeight: 700, color: on ? LOOK : color, whiteSpace: 'nowrap'}}>
      <span style={{fontFamily: MONO}}>{name}</span>
      <span style={{color: MUTED, fontWeight: 400}}>{`  ${handles}`}</span>
    </div>
  </div>
);

// Scene 1 — the page's effects, and the stack of handlers that answers them.

const INTRO_LINES: Lines = [
  [0, 'Advanced effects are still effects.'],
  [8.6, 'Each one is answered by a handler. Stack the handlers; they work on one program together.'],
];
const INTRO = f(16.5);
const PAGE_EFFECTS = ['Spawn', 'Gather', 'Get', 'Put', 'WithObserve', 'Delay', 'Abort'];
// The pills' centres, spaced evenly by their widths (19 px bold mono is about 11.4 px a letter, plus padding).
const PILL_W = PAGE_EFFECTS.map((name) => 28 + name.length * 11.4);
const PILL_GAP = 30;
const PILL_X = PILL_W.map((w, i) => (1280 - PILL_W.reduce((sum, v) => sum + v, 0) - PILL_GAP * 6) / 2 + PILL_W.slice(0, i).reduce((sum, v) => sum + v + PILL_GAP, 0) + w / 2);
const STACK_CALL = ['run(state()(scheduled(sim_time_handler()(your_handlers(program())))))'];
const STACK: {box: Box; name: string; handles: string; color: string}[] = [
  {box: {x: 110, y: 176, w: 1060, h: 500}, name: 'state()', handles: 'handles Get · Put', color: TEST},
  {box: {x: 146, y: 220, w: 988, h: 420}, name: 'scheduled', handles: 'handles Spawn · Gather · Semaphores', color: ACCENT},
  {box: {x: 182, y: 264, w: 916, h: 340}, name: 'sim_time_handler', handles: 'handles Delay · GetTime', color: PROD},
  {box: {x: 218, y: 308, w: 844, h: 260}, name: 'your handlers', handles: 'handle Audit · Abort', color: TEXT},
];
const PROGRAM = ['@do', 'def program():', '    yield Get("count")', '    yield Spawn(task())', '    yield Delay(1)', '    yield Abort("out of stock")'];
const PC = {x: 254, y: 352, w: 430, size: 17, line: 26};
// Each effect of the program: its code line, the layer that handles it, its answer, and the second it leaves.
const SENDS = [
  {line: 2, effect: 'Get("count")', layer: 0, answer: '0', leave: 9.8},
  {line: 3, effect: 'Spawn(task())', layer: 1, answer: 'Task', leave: 11.4},
  {line: 4, effect: 'Delay(1)', layer: 2, answer: 'None', leave: 13.0},
  {line: 5, effect: 'Abort(...)', layer: 3, answer: '', leave: 14.6},
];

const IntroScene: React.FC = () => {
  const frame = useSceneFrame();
  const first = 1 - ramp(frame, 8.0, 8.5);
  const sending = SENDS.findIndex((s) => frame >= f(s.leave - 0.2) && frame < f(s.leave + 1.5));
  const band = sending >= 0 ? SENDS[sending].line : null;
  return (
    <AbsoluteFill>
      <Headlines lines={INTRO_LINES} />
      <div style={{position: 'absolute', left: 0, right: 0, top: 130 + mix(16, 0, ramp(frame, 0.2, 0.8)), textAlign: 'center', opacity: fadeAt(frame, 0.2, 0.5) * first}}>
        <div style={{fontFamily: SANS, fontSize: 80, fontWeight: 700, color: TEXT}}>Advanced Effects</div>
        <div style={{fontFamily: SANS, fontSize: 28, color: MUTED, marginTop: 6}}>docs/09-advanced-effects.md</div>
      </div>
      {PAGE_EFFECTS.map((name, i) => (
        <Pill key={name} x={PILL_X[i]} y={400 + mix(14, 0, ramp(frame, 1.4 + i * 0.5, 1.9 + i * 0.5))} text={name} color={ACCENT} opacity={fadeAt(frame, 1.4 + i * 0.5) * first} />
      ))}
      {centred(460, 'tasks · shared state · observation · time · control flow of your own', TEXT, fadeAt(frame, 5.0) * first, 24, false)}
      <CodeBlock lines={STACK_CALL} x={250} y={110} w={780} size={17} line={32} opacity={fadeAt(frame, 8.7)} />
      {STACK.map((layer, i) => (
        <Layer key={layer.name} {...layer} on={sending === i && frame >= f(SENDS[i].leave + 0.8)} opacity={fadeAt(frame, 8.9 + i * 0.25, 0.4)} />
      ))}
      <CodeBlock lines={PROGRAM} {...PC} band={band} opacity={fadeAt(frame, 9.0)} />
      {SENDS.map((s) => {
        const from = {x: PC.x + PC.w + 12, y: lineMid(PC.y, PC.line, s.line)};
        const to = {x: 900, y: STACK[s.layer].box.y + 20};
        return (
          <React.Fragment key={s.effect}>
            <Trip a={from} b={to} from={s.leave} to={s.leave + 0.8} text={s.effect} color={ACCENT} left />
            {s.answer && <Trip a={{x: to.x + 40, y: to.y}} b={from} from={s.leave + 1.0} to={s.leave + 1.6} text={s.answer} color={TEST} left />}
          </React.Fragment>
        );
      })}
    </AbsoluteFill>
  );
};

// Scene 2 — three tasks share one count; where a task waits decides whether updates are lost.

const SS_LINES: Lines = [
  [0, 'Three tasks share one count. The state handler sits outside the scheduler: one store for all.'],
  [8.4, 'A task runs until it waits, so a Get and its Put with no wait between are never split.'],
  [18.8, 'Wait between Get and Put, and the others read the same old value: updates are lost.'],
  [28.5, 'Hold a CreateSemaphore(1) across the read and the write: 300 again.'],
];
const SS = f(37.5);
const ADD_ONE = [
  '@do',
  'def add_one(name):',
  '    for _ in range(100):',
  '        yield AcquireSemaphore(lock)',
  '        current = yield Get("count")',
  '        yield Delay(1)',
  '        yield Put("count", current + 1)',
  '        yield ReleaseSemaphore(lock)',
];
const SC = {x: 40, y: 104, w: 560, size: 17, line: 27};
type Task = 'a' | 'b' | 'c';
const TASKS: Task[] = ['a', 'b', 'c'];
const LANE_Y: Record<Task, number> = {a: 318, b: 390, c: 462};
const STORE: Box = {x: 650, y: 104, w: 590, h: 120};
const STORE_AT: Point = {x: 1010, y: 196};
// One step of a phase, from the steps measure/shared_state.log prints for 3 tasks x 2: a Get (and the value it
// read) or a Put (and the value it wrote), at a second of the scene.
type Step = {at: number; task: Task; kind: 'get' | 'put'; value: number};
const STEP = 0.5;
// The log's steps as [task, read, wrote], turned into Gets and Puts in the order the tasks performed them.
const sequential = (from: number, steps: [Task, number, number][], gap: number): Step[] =>
  steps.flatMap(([task, read, wrote], i): Step[] => [
    {at: from + i * (2 * STEP + gap), task, kind: 'get', value: read},
    {at: from + i * (2 * STEP + gap) + STEP + gap, task, kind: 'put', value: wrote},
  ]);
// Without a lock and with a wait, every task reads before any task writes: reads a, b, c, the wait, writes a, b, c.
const interleaved = (from: number, rounds: [number, number][], wait: number): Step[] =>
  rounds.flatMap(([read, wrote], r): Step[] => {
    const at = from + r * (6 * STEP + wait);
    return TASKS.flatMap((task, i): Step[] => [
      {at: at + i * STEP, task, kind: 'get', value: read},
      {at: at + 3 * STEP + wait + i * STEP, task, kind: 'put', value: wrote},
    ]);
  });
const PHASES = [
  // no wait between Get and Put: a read 0, wrote 1 · a read 1, wrote 2 · b 2→3 · b 3→4 · c 4→5 · c 5→6.
  {from: 8.4, to: 18.8, wait: false, locked: false, steps: sequential(9.4, [['a', 0, 1], ['a', 1, 2], ['b', 2, 3], ['b', 3, 4], ['c', 4, 5], ['c', 5, 6]], 0)},
  // Delay(1) between them: a, b and c all read 0 and wrote 1, then all read 1 and wrote 2.
  {from: 18.8, to: 28.5, wait: true, locked: false, steps: interleaved(19.4, [[0, 1], [1, 2]], 0.6)},
  // inside CreateSemaphore(1): a 0→1 · b 1→2 · c 2→3 · … (the first three of the log's six steps).
  {from: 28.5, to: 37.5, wait: true, locked: true, steps: sequential(29.0, [['a', 0, 1], ['b', 1, 2], ['c', 2, 3]], 0.4)},
];
// The counts of measure/shared_state.log, 3 tasks x 100, the same on its 3 runs.
const RESULTS = [
  {at: 17.0, text: 'no wait between Get and Put', count: 300, color: TEST},
  {at: 27.2, text: 'Delay(1) between Get and Put', count: 100, color: FAIL},
  {at: 33.4, text: 'Delay(1) inside CreateSemaphore(1)', count: 300, color: TEST},
];

const SharedStateScene: React.FC = () => {
  const frame = useSceneFrame();
  const now = frame / FPS;
  const phase = PHASES.reduce((found, p, i) => (now >= p.from ? i : found), 0);
  const {steps, wait, locked} = PHASES[phase];
  const arrived = (s: Step) => now >= s.at + STEP * 0.6;
  const count = steps.filter((s) => s.kind === 'put' && arrived(s)).reduce((v, s) => s.value, 0);
  const read = (task: Task) => steps.filter((s) => s.task === task && s.kind === 'get' && now >= s.at + STEP).reduce<number | null>((v, s) => s.value, null);
  // A task waits on Delay(1) between its Get answer and its Put.
  const waiting = (task: Task) =>
    wait && steps.some((s, i) => s.task === task && s.kind === 'get' && now >= s.at + STEP && now < (steps.slice(i).find((p) => p.task === task && p.kind === 'put')?.at ?? 0));
  // Under the lock, the task between its first Get and its Put holds it.
  const holder = locked ? steps.find((s, i) => s.kind === 'get' && now >= s.at - 0.15 && now < steps[i + 1].at + STEP) : undefined;
  // The optional lines of add_one: the semaphore lines in the last phase, the Delay line in the last two.
  const dim = (i: number) => (i === 3 || i === 7 ? (locked ? 1 : 0.18) : i === 5 ? (wait ? 1 : 0.18) : 1);
  const flash = steps.some((s) => s.kind === 'put' && now >= s.at + STEP * 0.6 && now < s.at + STEP * 0.6 + 0.25);
  return (
    <AbsoluteFill>
      <Headlines lines={SS_LINES} />
      <CodeBlock lines={ADD_ONE} {...SC} band={null} dim={dim} opacity={fadeAt(frame, 0)} />
      <div
        style={{
          position: 'absolute', left: STORE.x, top: STORE.y, width: STORE.w, height: STORE.h, boxSizing: 'border-box', borderRadius: 18, background: PANEL,
          border: `3px solid ${TEST}`, ...glow(flash, TEST), opacity: fadeAt(frame, 0.3),
        }}
      >
        <div style={{position: 'absolute', left: 18, top: 10, fontFamily: SANS, fontSize: 18, fontWeight: 700, color: TEST, whiteSpace: 'nowrap'}}>
          <span style={{fontFamily: MONO}}>state()</span>
          <span style={{color: MUTED, fontWeight: 400}}>{'  one store, outside the scheduler'}</span>
        </div>
        <div style={{position: 'absolute', left: 18, top: 48, fontFamily: MONO, fontSize: 40, fontWeight: 700, color: TEXT}}>{`count = ${count}`}</div>
      </div>
      <div
        style={{
          position: 'absolute', left: 650, top: 250, width: 590, height: 250, boxSizing: 'border-box', borderRadius: 18, border: `3px solid ${ACCENT}`,
          opacity: fadeAt(frame, 0.6),
        }}
      >
        <div style={{position: 'absolute', left: 18, top: 8, fontFamily: SANS, fontSize: 18, fontWeight: 700, color: ACCENT, whiteSpace: 'nowrap'}}>
          <span style={{fontFamily: MONO}}>scheduled</span>
          <span style={{color: MUTED, fontWeight: 400}}>{'  runs one task at a time'}</span>
        </div>
      </div>
      {TASKS.map((task, i) => {
        const r = read(task);
        return (
          <React.Fragment key={task}>
            <Card label={`task ${task}`} p={{x: 750, y: LANE_Y[task]}} w={150} on={holder?.task === task} opacity={fadeAt(frame, 1.0 + i * 0.4)} />
            {label(845, LANE_Y[task] - 13, r === null ? '' : `current = ${r}`, TEXT, fadeAt(frame, 1.0 + i * 0.4), 19, true)}
            {waiting(task) && label(1040, LANE_Y[task] - 12, 'Delay(1)', PROD, 1, 18, true)}
            {holder?.task === task && <Pill x={1190} y={LANE_Y[task]} text="lock" color={PROD} />}
          </React.Fragment>
        );
      })}
      {steps.map((s) => {
        const lane = {x: 830, y: LANE_Y[s.task]};
        return s.kind === 'get' ? (
          <React.Fragment key={`${s.at}-g`}>
            <Trip a={lane} b={STORE_AT} from={s.at} to={s.at + STEP * 0.5} text="Get" color={ACCENT} />
            <Trip a={STORE_AT} b={{x: 900, y: lane.y}} from={s.at + STEP * 0.5} to={s.at + STEP} text={String(s.value)} color={TEST} />
          </React.Fragment>
        ) : (
          <Trip key={`${s.at}-p`} a={lane} b={STORE_AT} from={s.at} to={s.at + STEP * 0.6} text={`Put ${s.value}`} color={ACCENT} />
        );
      })}
      {label(40, 372, 'shown: 3 tasks × 2 steps · measured: 3 tasks × 100', MUTED, fadeAt(frame, 2.0), 16)}
      {RESULTS.map((r, i) => (
        <div key={r.text} style={{position: 'absolute', left: 40, top: 412 + i * 56, width: 560, opacity: fadeAt(frame, r.at, 0.4)}}>
          <div style={{fontFamily: SANS, fontSize: 19, color: TEXT, lineHeight: '44px'}}>{r.text}</div>
          <Pill x={410} y={22} text={`count = ${r.count}`} color={r.color} left />
        </div>
      ))}
      {centred(600, 'Where a task waits decides what it can miss.', TEST, fadeAt(frame, 34.6))}
      {centred(642, 'All counts from measure/shared_state.py, the same on every run.', MUTED, fadeAt(frame, 35.2), 16, false)}
    </AbsoluteFill>
  );
};

// Scene 3 — WithObserve sees every effect yielded inside its scope, spawned tasks included, and changes nothing.

const OB_LINES: Lines = [
  [0, 'WithObserve shows you every effect a part of the program yields, and handles none of them.'],
  [7.0, 'It sees the effects of tasks spawned inside its scope, and nothing yielded outside it.'],
  [14.0, 'Each effect still goes on to its handler: the same result, the same log.'],
];
const OB = f(22.5);
const OB_CODE = [
  '@do',
  'def child(n):',
  '    user = yield Ask("user")',
  '    yield Tell(f"child {n} served {user}")',
  '    return f"{user}-{n}"',
  ' ',
  '@do',
  'def body():',
  '    t1 = yield Spawn(child(1))',
  '    t2 = yield Spawn(child(2))',
  '    return (yield Gather(t1, t2))',
  ' ',
  '@do',
  'def program():',
  '    yield Tell("before the scope")',
  '    result = yield WithObserve(observer, body())',
  '    yield Tell("after the scope")',
];
const OC = {x: 30, y: 100, w: 590, size: 15, line: 22};
const SCOPE: Box = {x: 650, y: 176, w: 320, h: 250};
const OBSERVER: Box = {x: 990, y: 176, w: 270, h: 250};
const HANDLERS: Box = {x: 650, y: 456, w: 610, h: 56};
const NODES = {program: {x: 810, y: 130}, body: {x: 810, y: 236}, child1: {x: 730, y: 350}, child2: {x: 890, y: 350}};
// The effects in the order measure/observe.log prints them: who yields each, whether the observer sees it, and the
// second it leaves. The two Tells outside WithObserve are not seen.
const OB_EFFECTS: {from: keyof typeof NODES; text: string; seen: boolean; at: number}[] = [
  {from: 'program', text: 'Tell("before the scope")', seen: false, at: 1.2},
  {from: 'body', text: 'Spawn', seen: true, at: 3.4},
  {from: 'child1', text: 'Ask("user")', seen: true, at: 7.4},
  {from: 'child1', text: 'Tell("child 1 served ada")', seen: true, at: 8.5},
  {from: 'body', text: 'Spawn', seen: true, at: 9.6},
  {from: 'child2', text: 'Ask("user")', seen: true, at: 10.7},
  {from: 'child2', text: 'Tell("child 2 served ada")', seen: true, at: 11.8},
  {from: 'body', text: 'Gather', seen: true, at: 12.9},
  {from: 'program', text: 'Tell("after the scope")', seen: false, at: 14.4},
];
// The program's line for each yielding node, for the code band.
const OB_LINE = {program: 14, body: 8, child1: 2, child2: 2};

const ObserveScene: React.FC = () => {
  const frame = useSceneFrame();
  const seen = OB_EFFECTS.filter((e) => e.seen);
  const active = OB_EFFECTS.reduce((found, e) => (frame >= f(e.at - 0.1) ? e : found), undefined as (typeof OB_EFFECTS)[number] | undefined);
  const band = !active ? null : active.from === 'program' ? (active.at > 14 ? 16 : 14) : active.text === 'Gather' ? 10 : active.from === 'body' ? (active.at > 5 ? 9 : 8) : active.text.startsWith('Ask') ? 2 : 3;
  const handling = OB_EFFECTS.some((e) => frame >= f(e.at + 0.8) && frame < f(e.at + 1.1));
  const watching = OB_EFFECTS.some((e) => e.seen && frame >= f(e.at + 0.8) && frame < f(e.at + 1.1));
  return (
    <AbsoluteFill>
      <Headlines lines={OB_LINES} />
      <CodeBlock lines={OB_CODE} {...OC} band={band} opacity={fadeAt(frame, 0)} />
      <Card label="program" p={NODES.program} w={160} color={TEXT} opacity={fadeAt(frame, 0.3)} />
      <Layer box={SCOPE} name="WithObserve" handles="scope" color={LOOK} on={false} opacity={fadeAt(frame, 0.5)} dashed />
      <Card label="body" p={NODES.body} w={140} opacity={fadeAt(frame, 0.7)} />
      <Card label="child 1" p={NODES.child1} w={130} color={PROD} opacity={fadeAt(frame, 3.9)} />
      <Card label="child 2" p={NODES.child2} w={130} color={PROD} opacity={fadeAt(frame, 10.1)} />
      <div
        style={{
          position: 'absolute', left: OBSERVER.x, top: OBSERVER.y, width: OBSERVER.w, height: OBSERVER.h, boxSizing: 'border-box', borderRadius: 18,
          background: PANEL, border: `3px solid ${LOOK}`, ...glow(watching, LOOK), opacity: fadeAt(frame, 0.5),
        }}
      >
        <div style={{position: 'absolute', left: 16, top: 8, fontFamily: SANS, fontSize: 17, fontWeight: 700, color: LOOK}}>observer saw</div>
        {seen.map((e, i) => (
          <div key={i} style={{position: 'absolute', left: 16, top: 42 + i * 28, fontFamily: MONO, fontSize: 14, color: TEXT, whiteSpace: 'nowrap', opacity: fadeAt(frame, e.at + 0.8, 0.2)}}>
            {e.text}
          </div>
        ))}
      </div>
      <div
        style={{
          position: 'absolute', left: HANDLERS.x, top: HANDLERS.y, width: HANDLERS.w, height: HANDLERS.h, boxSizing: 'border-box', borderRadius: 14,
          border: `3px solid ${TEST}`, ...glow(handling, TEST), opacity: fadeAt(frame, 0.5), fontFamily: SANS, fontSize: 18, fontWeight: 700,
          color: TEST, lineHeight: '50px', paddingLeft: 18, whiteSpace: 'nowrap',
        }}
      >
        handlers <span style={{fontFamily: MONO, color: MUTED, fontWeight: 400}}>reader · writer · state · scheduled</span>
      </div>
      {OB_EFFECTS.map((e, i) => {
        const from = NODES[e.from];
        const to = {x: e.from === 'program' ? 1150 : from.x, y: HANDLERS.y + 28};
        return (
          <React.Fragment key={i}>
            <Trip a={from} b={to} from={e.at} to={e.at + 0.9} text={e.text} color={ACCENT} />
            {e.seen && <Trip a={from} b={{x: OBSERVER.x + 130, y: OBSERVER.y + 50 + seen.indexOf(e) * 28}} from={e.at} to={e.at + 0.8} text={e.text} color={LOOK} />}
          </React.Fragment>
        );
      })}
      {label(650, 540, "result ['ada-1', 'ada-2']", TEST, fadeAt(frame, 15.6), 17, true)}
      {label(650, 572, 'log [before the scope, child 1 served ada,', TEST, fadeAt(frame, 16.4), 17, true)}
      {label(650, 600, '     child 2 served ada, after the scope]', TEST, fadeAt(frame, 16.4), 17, true)}
      {label(30, 504, 'the observer returns "ignored": not used', MUTED, fadeAt(frame, 17.6), 17)}
      {centred(646, 'Seven effects seen, two not: on every run of measure/observe.py.', MUTED, fadeAt(frame, 18.6), 16, false)}
    </AbsoluteFill>
  );
};

// Scene 4 — control flow of your own: the Audit handler resumes k; the Abort handler does not.

const CT_LINES: Lines = [
  [0, 'Control flow of your own is a handler for an effect of your own.'],
  [7.6, 'The Audit handler resumes k: checkout goes on after the Audit.'],
  [13.0, 'The Abort handler never resumes k. The body ends at Abort; the handler’s value is the result.'],
  [21.0, 'checkout(3) returns "ordered". checkout(0) gives "aborted: out of stock".'],
];
const CT = f(29.5);
const EFFECTS_CODE = ['@dataclass(frozen=True)', 'class Audit(EffectBase):', '    message: str', ' ', '@dataclass(frozen=True)', 'class Abort(EffectBase):', '    reason: str'];
const CHECKOUT = ['@do', 'def checkout(stock):', '    yield Audit(f"stock={stock}")', '    if stock == 0:', '        yield Abort("out of stock")', '    return "ordered"'];
const HANDLERS_CODE = ['@handler', 'def audit_handler(effect: Audit, k):', '    return (yield Resume(k, None))', ' ', '@handler', 'def abort_handler(effect: Abort, k):', '    return f"aborted: {effect.reason}"'];
const KC = {x: 40, y: 330, w: 560, size: 16, line: 25};
const HC = {x: 40, y: 104, w: 560, size: 16, line: 25};
const OUTER: Box = {x: 650, y: 104, w: 590, h: 420};
const INNER: Box = {x: 680, y: 150, w: 530, h: 340};
const BODY: Point = {x: 830, y: 330};
const OUT_AT: Point = {x: 950, y: OUTER.y + 20};
const IN_AT: Point = {x: 990, y: INNER.y + 20};

const ControlScene: React.FC = () => {
  const frame = useSceneFrame();
  const t = frame / FPS;
  // checkout(3) runs in the second headline, checkout(0) in the third.
  const run0 = t >= 13.0;
  const stock = run0 ? 0 : 3;
  const auditAt = run0 ? 13.8 : 8.2;
  const resumed = t >= auditAt + 1.6;
  const abortAt = 16.6;
  const aborted = run0 && t >= abortAt + 1.0;
  const band = t < 7.6 ? null : !resumed ? 2 : run0 ? (t < abortAt ? 3 : 4) : t < 10.8 ? 3 : 5;
  const dim = (i: number) => (run0 && aborted && i === 5 ? 0.25 : !run0 && t >= 10.8 && i === 4 ? 0.25 : 1);
  const auditOn = t >= auditAt + 0.8 && t < auditAt + 1.6;
  const abortOn = run0 && t >= abortAt + 0.8;
  const effectsOut = 1 - ramp(frame, 7.0, 7.5);
  return (
    <AbsoluteFill>
      <Headlines lines={CT_LINES} />
      <CodeBlock lines={EFFECTS_CODE} x={HC.x} y={HC.y} w={HC.w} size={HC.size} line={HC.line} opacity={fadeAt(frame, 0.3) * effectsOut} />
      <CodeBlock lines={HANDLERS_CODE} {...HC} opacity={fadeAt(frame, 7.2)} />
      <CodeBlock lines={CHECKOUT} {...KC} band={band} dim={dim} opacity={fadeAt(frame, 2.0)} />
      <Layer box={OUTER} name="abort_handler" handles="handles Abort" color={FAIL} on={abortOn && t < abortAt + 2.2} opacity={fadeAt(frame, 2.8)} />
      <Layer box={INNER} name="audit_handler" handles="handles Audit" color={TEST} on={auditOn} opacity={fadeAt(frame, 3.2)} />
      {label(BODY.x - 100, BODY.y - 70, t < 7.6 ? 'checkout(stock)' : `checkout(${stock})`, TEXT, fadeAt(frame, 3.6), 19, true)}
      <Card label="body" p={BODY} w={200} color={ACCENT} opacity={fadeAt(frame, 3.6) * (aborted ? 0.35 : 1)} strike={aborted} />
      {aborted && label(BODY.x - 100, BODY.y + 32, 'k never resumed', FAIL, fadeAt(frame, abortAt + 1.0), 17)}
      {/* Audit: out to audit_handler, and Resume(k) back. */}
      <Trip a={{x: BODY.x + 100, y: BODY.y}} b={IN_AT} from={auditAt} to={auditAt + 0.8} text="Audit(stock=…)" color={ACCENT} left />
      <Trip a={IN_AT} b={{x: BODY.x + 100, y: BODY.y}} from={auditAt + 1.0} to={auditAt + 1.6} text="Resume(k, None)" color={TEST} left />
      {/* checkout(3) returns its own value. */}
      {!run0 && <Trip a={{x: BODY.x + 100, y: BODY.y}} b={{x: 820, y: 584}} from={10.9} to={11.8} text={'"ordered"'} color={TEST} left />}
      {/* checkout(0): Abort passes audit_handler by, to abort_handler, which answers for the whole body. */}
      {run0 && <Trip a={{x: BODY.x + 100, y: BODY.y}} b={OUT_AT} from={abortAt} to={abortAt + 0.8} text={'Abort("out of stock")'} color={FAIL} left />}
      {run0 && <Trip a={OUT_AT} b={{x: 820, y: 620}} from={abortAt + 1.6} to={abortAt + 2.5} text={'"aborted: out of stock"'} color={FAIL} left />}
      {label(650, 568, <><span style={{color: MUTED}}>{'checkout(3) -> '}</span>{"'ordered'"}</>, TEST, fadeAt(frame, 11.8), 19, true)}
      {label(650, 604, <><span style={{color: MUTED}}>{'checkout(0) -> '}</span>{"'aborted: out of stock'"}</>, FAIL, fadeAt(frame, abortAt + 2.5), 19, true)}
      {centred(652, 'Whether the rest of the program runs is the handler’s call.', TEST, fadeAt(frame, 22.4))}
      {label(650, 538, 'measure/control.py, every run:', MUTED, fadeAt(frame, 11.8), 15)}
    </AbsoluteFill>
  );
};

// Scene 5 — the time handler decides what Delay means.

const TM_LINES: Lines = [
  [0, 'Time is effects too: Delay and GetTime mean what the time handler says.'],
  [6.7, 'sync_time_handler sleeps for real. sim_time_handler moves a virtual clock at once.'],
];
const TM = f(19);
const NAP = ['@do', 'def nap():', '    before = yield GetTime()', '    yield Delay(1.5)', '    after = yield GetTime()', '    return after - before'];
const NC = {x: 40, y: 140, w: 470, size: 18, line: 30};
// measure/time.log: the clock moved 1.50 s on both; the wall time of 3 runs each.
const RUNS = [
  {name: 'sync_time_handler', wall: '1501–1503 ms', real: 1.5, color: PROD, y: 120},
  {name: 'sim_time_handler', wall: '0.38–0.54 ms', real: 0, color: ACCENT, y: 330},
];
const SYNC_FROM = 8.4;

const TimeScene: React.FC = () => {
  const frame = useSceneFrame();
  const t = frame / FPS;
  const delayOut = t >= 2.0 && t < 4.6;
  return (
    <AbsoluteFill>
      <Headlines lines={TM_LINES} />
      <CodeBlock lines={NAP} {...NC} band={t < 1.2 ? null : t < 2.0 ? 2 : t < 5.0 ? 3 : 4} opacity={fadeAt(frame, 0)} />
      <Trip a={{x: NC.x + NC.w + 10, y: lineMid(NC.y, NC.line, 3)}} b={{x: 712, y: 400}} from={2.2} to={3.0} text="Delay(1.5)" color={ACCENT} left />
      {t < 6.6 && (
        <div style={{opacity: fadeAt(frame, 1.0) * (1 - ramp(frame, 6.2, 6.6))}}>
          {centred(560, 'Which handler answers decides what waiting is.', TEXT, 1, 22, false)}
          <div
            style={{
              position: 'absolute', left: 700, top: 360, width: 480, height: 120, boxSizing: 'border-box', borderRadius: 18, border: `3px dashed ${PROD}`,
              ...glow(delayOut && t >= 3.0, PROD), fontFamily: MONO, fontSize: 22, color: PROD, textAlign: 'center', lineHeight: '114px',
            }}
          >
            a time handler
          </div>
        </div>
      )}
      {RUNS.map((r, i) => {
        const start = SYNC_FROM + i * 0.2;
        const done = r.real > 0 ? ramp(frame, start, start + r.real) : t >= start + 0.02 ? 1 : 0;
        const seconds = 1.5 * done;
        const show = fadeAt(frame, 6.9 + i * 0.4);
        return (
          <div key={r.name} style={{position: 'absolute', left: 560, top: r.y, width: 680, height: 190, boxSizing: 'border-box', borderRadius: 18, background: PANEL, border: `3px solid ${r.color}`, opacity: show}}>
            <div style={{position: 'absolute', left: 18, top: 10, fontFamily: MONO, fontSize: 20, fontWeight: 700, color: r.color}}>{r.name}</div>
            <div style={{position: 'absolute', left: 18, top: 52, transform: 'scale(0.9)', transformOrigin: 'top left'}}>
              <Clock seconds={seconds} c={r.color} secondHand />
            </div>
            <div style={{position: 'absolute', left: 160, top: 64, fontFamily: SANS, fontSize: 16, color: MUTED}}>wall time</div>
            <div style={{position: 'absolute', left: 160, top: 92, width: 320, height: 14, borderRadius: 7, background: '#2a2e40'}} />
            <div style={{position: 'absolute', left: 160, top: 92, width: 320 * Math.max(done, r.real > 0 ? 0 : done * 0.004), height: 14, borderRadius: 7, background: r.color}} />
            <div style={{position: 'absolute', left: 500, top: 84, fontFamily: MONO, fontSize: 19, color: TEXT, opacity: t >= start + Math.max(r.real, 0.1) ? 1 : 0}}>{r.wall}</div>
            <div style={{position: 'absolute', left: 160, top: 130, fontFamily: MONO, fontSize: 18, color: TEXT, opacity: t >= start + Math.max(r.real, 0.1) ? 1 : 0}}>
              clock moved 1.50 s
            </div>
          </div>
        );
      })}
      {label(560, 542, <>sim_time_handler, <span style={{color: ACCENT}}>Delay(3600)</span>: 09:00:00 → 10:00:00 in 0.25–0.82 ms</>, TEXT, fadeAt(frame, 13.6), 17)}
      {label(40, 380, 'The program is the same in both.', TEST, fadeAt(frame, 11.0), 21)}
      {centred(612, 'Same program, same answers: the handler decides what time is.', TEST, fadeAt(frame, 15.0))}
      {centred(654, 'Wall times of 3 runs each, from measure/time.py.', MUTED, fadeAt(frame, 15.6), 16, false)}
    </AbsoluteFill>
  );
};

// Outro — what to take away.

const OUTRO = f(13);

const Outro: React.FC = () => {
  const frame = useSceneFrame();
  const rise = interpolate(frame, [0, f(0.67)], [20, 0], {...clamp, easing: Easing.out(Easing.cubic)});
  return (
    <AbsoluteFill style={{padding: 90, justifyContent: 'center', transform: `translateY(${rise}px)`}}>
      <div style={{fontFamily: SANS, fontSize: 56, fontWeight: 700, color: TEXT, opacity: fadeAt(frame, 0, 0.4)}}>doeff</div>
      <div style={{fontFamily: SANS, fontSize: 30, color: ACCENT, marginTop: 6, opacity: fadeAt(frame, 0.2, 0.4)}}>Advanced Effects</div>
      <div style={{fontFamily: SANS, fontSize: 28, lineHeight: '42px', color: TEXT, marginTop: 34, width: 1050, opacity: fadeAt(frame, 0.6, 0.4)}}>
        Tasks, state, observation, time and your own control flow are all effects. The handlers you stack decide what each one does.
      </div>
      <div style={{fontFamily: MONO, fontSize: 28, color: TEST, marginTop: 40, opacity: fadeAt(frame, 1.1, 0.4)}}>pip install doeff</div>
    </AbsoluteFill>
  );
};

// The scenes in order; Root.tsx registers ADVANCED_EFFECTS.length frames of ADVANCED_EFFECTS.Video.
export const ADVANCED_EFFECTS = narrate('advanced-effects', DURATIONS, [
  {length: INTRO, starts: startsOf(INTRO_LINES), ids: ['i1', 'i2'], node: <IntroScene />},
  {length: SS, starts: startsOf(SS_LINES), ids: ['s1', 's2', 's3', 's4'], node: <SharedStateScene />},
  {length: OB, starts: startsOf(OB_LINES), ids: ['w1', 'w2', 'w3'], node: <ObserveScene />},
  {length: CT, starts: startsOf(CT_LINES), ids: ['c1', 'c2', 'c3', 'c4'], node: <ControlScene />},
  {length: TM, starts: startsOf(TM_LINES), ids: ['t1', 't2'], node: <TimeScene />},
  {length: OUTRO, starts: [0], ids: ['o1'], node: <Outro />},
]);
