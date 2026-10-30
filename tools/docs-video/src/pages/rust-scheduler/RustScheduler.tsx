// The video on the Rust Scheduler page (docs/25-rust-scheduler.md): one scheduler handler, two implementations.
// Five scenes and an outro, each with headlines a synthetic voice reads (narration.json):
//   1. scheduled() is the handler that answers Spawn, Wait and Gather; it has a Rust and a Python implementation.
//   2. Which one runs: the argument, then $DOEFF_SCHEDULER, then DEFAULT_IMPLEMENTATION ("rust").
//   3. The same program under both: the same order of tasks, the same Cancel, the same trace events.
//   4. What moved to Rust (every per-effect decision) and what stays in Python (every class user code sees).
//   5. The speed: the page's benchmark taken again, microseconds per operation under each implementation.
// Every value on screen is printed by a program in measure/ (its output is in the .log next to it).
import React from 'react';
import {AbsoluteFill, Easing, interpolate} from 'remotion';
import {fadeAt, mix, ramp} from '../../kit/motion';
import {narrate} from '../../kit/narrated';
import {CodeBlock, glow, Headlines, lineMid, Lines, Pill, startsOf} from '../../kit/parts';
import {ACCENT, f, FAIL, LOOK, MONO, MUTED, PANEL, PROD, SANS, TEST, TEXT} from '../../kit/theme';
import {useSceneFrame} from '../../kit/warp';
import DURATIONS from './durations.json';

// Rust is drawn in the production colour (it is the default), Python in the handler colour.
const RUST = PROD;
const PY = TEST;

// A label in the page's sans font, placed at (x, y).
const Label: React.FC<{x: number; y: number; color?: string; size?: number; bold?: boolean; opacity?: number; children: React.ReactNode}> = ({
  x, y, color = MUTED, size = 20, bold = true, opacity = 1, children,
}) => (
  <div style={{position: 'absolute', left: x, top: y, fontFamily: SANS, fontSize: size, fontWeight: bold ? 700 : 400, color, whiteSpace: 'nowrap', opacity}}>
    {children}
  </div>
);

// A dark panel of output lines in the mono font.
const Terminal: React.FC<{x: number; y: number; w: number; lines: {text: string; color?: string; opacity?: number}[]; size?: number; line?: number; opacity?: number}> = ({
  x, y, w, lines, size = 17, line = 27, opacity = 1,
}) => (
  <div
    style={{
      position: 'absolute', left: x, top: y, width: w, boxSizing: 'border-box', padding: '10px 16px', background: '#0b0d13', borderRadius: 10,
      border: '2px solid #2a2e40', fontFamily: MONO, fontSize: size, lineHeight: `${line}px`, color: TEXT, opacity,
    }}
  >
    {lines.map((l, i) => (
      <div key={i} style={{whiteSpace: 'pre', color: l.color ?? TEXT, minHeight: line, opacity: l.opacity ?? 1}}>
        {l.text}
      </div>
    ))}
  </div>
);

// A rounded box with a coloured border, a title and a line under it.
const Box: React.FC<{x: number; y: number; w: number; h: number; color: string; title: string; sub?: string; on?: boolean; opacity?: number; children?: React.ReactNode}> = ({
  x, y, w, h, color, title, sub, on = false, opacity = 1, children,
}) => (
  <div
    style={{
      position: 'absolute', left: x, top: y, width: w, height: h, boxSizing: 'border-box', borderRadius: 18, background: PANEL,
      border: `3px solid ${color}`, ...glow(on, color), opacity,
    }}
  >
    <div style={{position: 'absolute', left: 20, top: 12, fontFamily: MONO, fontSize: 24, fontWeight: 700, color: on ? LOOK : color, whiteSpace: 'nowrap'}}>{title}</div>
    {sub && <div style={{position: 'absolute', left: 20, top: 48, fontFamily: SANS, fontSize: 17, color: MUTED, whiteSpace: 'nowrap'}}>{sub}</div>}
    {children}
  </div>
);

// Scene 1 — scheduled() is a handler, with two implementations.

const INTRO_LINES: Lines = [
  [0, 'The Rust scheduler: one scheduler, two implementations.'],
  [5.5, 'Concurrency is effects too. scheduled() is the handler that answers them.'],
  [13.5, 'It comes in Rust and in Python, with the same meaning. Rust is the default.'],
];
const INTRO = f(22);
const MAIN = ['@do', 'def main():', '    a = yield Spawn(work("a"))', '    b = yield Spawn(work("b"))', '    return (yield Gather(a, b))'];
// measure/intro.py: the program's value under the default and under each implementation.
const INTRO_RUNS = [
  {impl: 'rust', label: 'Rust', where: 'packages/doeff-vm/src/scheduler.rs', color: RUST, note: 'the default'},
  {impl: 'python', label: 'Python', where: '_scheduled_python in scheduler.py', color: PY, note: 'opt-in'},
];

const IntroScene: React.FC = () => {
  const frame = useSceneFrame();
  const code = {x: 60, y: 210, w: 540, size: 20, line: 34};
  const box = {x: 700, y: 180, w: 530, h: 170};
  // The three effects go out to the handler one after another, and each answer comes back to its yield.
  const trips = [
    {line: 2, at: 6.6, effect: 'Spawn(work("a"))', answer: 'Task'},
    {line: 3, at: 8.6, effect: 'Spawn(work("b"))', answer: 'Task'},
    {line: 4, at: 10.6, effect: 'Gather(a, b)', answer: "['A', 'B']"},
  ];
  const trip = trips.reduce((found, t, i) => (frame >= f(t.at) ? i : found), -1);
  const band = trip < 0 ? null : trips[trip].line;
  const split = ramp(frame, 14.2, 15.4);
  const handling = trips.some((t) => frame >= f(t.at + 0.7) && frame < f(t.at + 1.3));
  const startX = code.x + code.w - 30;
  return (
    <AbsoluteFill>
      <Headlines lines={INTRO_LINES} />
      <CodeBlock lines={MAIN} {...code} band={band} opacity={fadeAt(frame, 0.3)} />
      <Label x={code.x} y={code.y - 40} color={ACCENT} size={19} opacity={fadeAt(frame, 0.6)}>run(scheduled(main()))</Label>
      <Label x={code.x} y={440} size={22} color={TEXT} opacity={fadeAt(frame, 1.6) * (1 - ramp(frame, 13.0, 13.5))}>
        Spawn, Wait, Gather, Race, Cancel, Semaphore, Promise:
      </Label>
      <Label x={code.x} y={476} size={22} color={MUTED} bold={false} opacity={fadeAt(frame, 2.6) * (1 - ramp(frame, 13.0, 13.5))}>
        effects the program yields, like any other effect.
      </Label>
      {/* One handler box, which splits into the two implementations. */}
      <Box
        x={box.x} y={box.y} w={box.w} h={box.h} color={TEST} title="scheduled()" sub="the handler that answers the scheduler's effects" on={handling}
        opacity={fadeAt(frame, 1.0) * (1 - split)}
      />
      {trips.map((t, i) => {
        const out = ramp(frame, t.at, t.at + 0.7);
        const back = ramp(frame, t.at + 1.0, t.at + 1.7);
        const y = lineMid(code.y, code.line, t.line);
        return (
          <React.Fragment key={i}>
            {frame >= f(t.at) && frame < f(t.at + 0.8) && (
              <Pill x={mix(startX, box.x + box.w / 2, out)} y={mix(y, box.y + box.h / 2 + 20, out)} text={t.effect} color={ACCENT} opacity={1 - ramp(frame, t.at + 0.6, t.at + 0.8)} />
            )}
            {frame >= f(t.at + 1.0) && frame < f(t.at + 1.9) && (
              <Pill x={mix(box.x + box.w / 2, startX, back)} y={mix(box.y + box.h / 2 + 20, y, back)} text={t.answer} color={TEST} opacity={1 - ramp(frame, t.at + 1.7, t.at + 1.9)} />
            )}
          </React.Fragment>
        );
      })}
      <Label x={box.x + 20} y={box.y + box.h + 20} color={TEXT} size={20} opacity={fadeAt(frame, 12.4) * (1 - split)}>
        k resumes with the answer: <span style={{fontFamily: MONO, color: TEST}}>['A', 'B']</span>
      </Label>
      {INTRO_RUNS.map((r, i) => {
        const y = mix(box.y, 150 + i * 210, split);
        return (
          <Box
            key={r.impl} x={box.x} y={y} w={box.w} h={170} color={r.color} title={r.label} sub={r.where} opacity={split}
            on={frame >= f(15.6 + i * 1.6) && frame < f(16.8 + i * 1.6)}
          >
            <div style={{position: 'absolute', right: 20, top: 16, fontFamily: SANS, fontSize: 17, fontWeight: 700, color: r.color}}>{r.note}</div>
            <div style={{position: 'absolute', left: 20, top: 90, fontFamily: MONO, fontSize: 18, color: MUTED, opacity: fadeAt(frame, 15.8 + i * 1.6)}}>
              main() returns <span style={{color: TEXT, fontWeight: 700}}>['A', 'B']</span>
            </div>
          </Box>
        );
      })}
      <Label x={code.x} y={440} size={24} color={TEXT} opacity={fadeAt(frame, 17.6)}>
        The same effects, the same answers.
      </Label>
      <Label x={code.x} y={480} size={20} color={MUTED} bold={false} opacity={fadeAt(frame, 18.4)}>
        Only the code inside the handler differs.
      </Label>
    </AbsoluteFill>
  );
};

// Scene 2 — which implementation runs.

const CHOOSE_LINES: Lines = [
  [0, 'Which one runs: the argument first, then DOEFF_SCHEDULER, then the default.'],
  [9.5, 'The default is rust. Any other name is refused with a ValueError.'],
];
const CHOOSE = f(18);
const LADDER = [
  {n: '1', text: 'scheduled(main(), implementation="python")', note: 'for one call'},
  {n: '2', text: 'DOEFF_SCHEDULER=python', note: 'for the whole process, read on every call'},
  {n: '3', text: 'DEFAULT_IMPLEMENTATION = "rust"', note: 'in scheduler.py'},
];
// measure/choose.log, one case per row: what is set, the rung that decides, and the implementation chosen.
const CASES = [
  {at: 3.0, set: 'scheduled(main())', rung: 2, impl: 'rust'},
  {at: 4.6, set: 'scheduled(main(), implementation="python")', rung: 0, impl: 'python'},
  {at: 6.2, set: 'DOEFF_SCHEDULER=python  scheduled(main())', rung: 1, impl: 'python'},
  {at: 7.8, set: 'DOEFF_SCHEDULER=python  implementation="rust"', rung: 0, impl: 'rust'},
];

const ChooseScene: React.FC = () => {
  const frame = useSceneFrame();
  const current = CASES.reduce((found, c, i) => (frame >= f(c.at) ? i : found), -1);
  const lit = current >= 0 && frame < f(9.4) ? CASES[current].rung : frame >= f(10.0) && frame < f(12.4) ? 2 : -1;
  return (
    <AbsoluteFill>
      <Headlines lines={CHOOSE_LINES} />
      {LADDER.map((r, i) => (
        <div
          key={r.n}
          style={{
            position: 'absolute', left: 60, top: 100 + i * 78, width: 1160, height: 64, boxSizing: 'border-box', borderRadius: 14, background: PANEL,
            border: `2px solid ${lit === i ? LOOK : '#2a2e40'}`, boxShadow: lit === i ? `0 0 18px ${LOOK}66` : 'none', opacity: fadeAt(frame, 0.6 + i * 0.6),
            transform: `translateX(${mix(-20, 0, ramp(frame, 0.6 + i * 0.6, 1.1 + i * 0.6))}px)`,
          }}
        >
          <div style={{position: 'absolute', left: 20, top: 14, fontFamily: SANS, fontSize: 26, fontWeight: 700, color: lit === i ? LOOK : MUTED}}>{r.n}</div>
          <div style={{position: 'absolute', left: 64, top: 16, fontFamily: MONO, fontSize: 22, fontWeight: 700, color: TEXT}}>{r.text}</div>
          <div style={{position: 'absolute', right: 22, top: 20, fontFamily: SANS, fontSize: 17, color: MUTED}}>{r.note}</div>
        </div>
      ))}
      <Terminal
        x={60} y={354} w={1160} size={19} line={34}
        lines={[
          ...CASES.map((c) => ({
            text: `${c.set.padEnd(48)}->  ${c.impl}`, color: c.impl === 'rust' ? RUST : PY, opacity: fadeAt(frame, c.at, 0.25),
          })),
          {text: "DOEFF_SCHEDULER=go  ->  ValueError: scheduler implementation must be", color: FAIL, opacity: fadeAt(frame, 12.6, 0.25)},
          {text: "                        'python' or 'rust', got 'go'", color: FAIL, opacity: fadeAt(frame, 12.6, 0.25)},
        ]}
        opacity={fadeAt(frame, 2.4)}
      />
    </AbsoluteFill>
  );
};

// Scene 3 — the same program under both implementations.

const SAME_LINES: Lines = [
  [0, 'Same meaning: run one program under both implementations.'],
  [5.5, 'a and b wait on one promise; b has high priority. slow waits on a promise never kept.'],
  [13.5, 'The opener keeps the promise. b wakes first, then a, in both.'],
  [20.5, 'Cancel unwinds slow: its finally runs, then Wait raises TaskCancelledError.'],
  [27.5, 'The same result, the same order, the same 64 trace events.'],
];
const SAME = f(35);
const SAME_CODE = [
  'gate = yield CreatePromise()',
  'a = yield Spawn(step("a", gate))',
  'b = yield Spawn(step("b", gate), priority=TaskPriority.HIGH)',
  'slow = yield Spawn(stuck(never))',
  'yield Spawn(opener(gate))',
  'results = yield Gather(a, b)',
  'yield Cancel(slow)',
  'yield Wait(slow)',
];
// measure/same_semantics.log: what the tasks recorded, in order (the same list under both), and when it shows.
const ORDER = [
  {text: 'a starts', at: 6.6, line: 5},
  {text: 'b starts', at: 7.6, line: 5},
  {text: 'slow starts', at: 8.6, line: 5},
  {text: 'opener completes the promise', at: 14.2, line: 5},
  {text: 'b wakes with 42', at: 15.6, line: 5},
  {text: 'a wakes with 42', at: 17.0, line: 5},
  {text: 'slow finally', at: 21.4, line: 6},
  {text: 'slow is cancelled', at: 23.4, line: 7},
];

const SameScene: React.FC = () => {
  const frame = useSceneFrame();
  const code = {x: 40, y: 100, w: 560, size: 15, line: 30};
  const shown = ORDER.reduce((found, o, i) => (frame >= f(o.at) ? i : found), -1);
  const band = frame < f(1.2) ? null : frame < f(6.4) ? mix(0, 4, ramp(frame, 1.2, 5.2)) : shown < 0 ? 5 : ORDER[shown].line;
  const cols = [
    {name: 'python', color: PY, x: 630},
    {name: 'rust', color: RUST, x: 940},
  ];
  return (
    <AbsoluteFill>
      <Headlines lines={SAME_LINES} />
      <CodeBlock lines={SAME_CODE} {...code} band={band} opacity={fadeAt(frame, 0.2)} />
      <Label x={code.x} y={code.y + 24 + SAME_CODE.length * code.line + 10} size={17} bold={false} opacity={fadeAt(frame, 1.0)}>
        main() of measure/same_semantics.py
      </Label>
      {cols.map((c) => (
        <div
          key={c.name}
          style={{
            position: 'absolute', left: c.x, top: 100, width: 290, height: 330, boxSizing: 'border-box', borderRadius: 16, background: PANEL,
            border: `3px solid ${c.color}`, opacity: fadeAt(frame, 0.6),
          }}
        >
          <div style={{position: 'absolute', left: 16, top: 10, fontFamily: MONO, fontSize: 20, fontWeight: 700, color: c.color}}>{c.name}</div>
          {ORDER.map((o, i) => {
            const wake = i === 4 || i === 5;
            const on = frame >= f(o.at) && frame < f(o.at + 1.0);
            return (
              <div
                key={o.text}
                style={{
                  position: 'absolute', left: 16, top: 46 + i * 34, fontFamily: SANS, fontSize: 16, whiteSpace: 'nowrap',
                  color: on ? LOOK : wake ? TEXT : i >= 6 ? FAIL : MUTED, fontWeight: wake ? 700 : 400, opacity: fadeAt(frame, o.at, 0.25),
                  transform: `translateX(${mix(-12, 0, ramp(frame, o.at, o.at + 0.3))}px)`,
                }}
              >
                {i + 1}. {o.text}
              </div>
            );
          })}
        </div>
      ))}
      <Label x={630} y={444} size={17} color={MUTED} bold={false} opacity={fadeAt(frame, 16.0) * (1 - ramp(frame, 27.0, 27.4))}>
        b (TaskPriority.HIGH) is ahead of a in the ready queue
      </Label>
      <Terminal
        x={40} y={486} w={1200} size={18} line={30}
        lines={[
          {text: "python: result ['A', 'B']   trace: 64 events      rust: result ['A', 'B']   trace: 64 events", opacity: fadeAt(frame, 27.8, 0.25)},
          {text: 'same result: True    same order: True    same trace events: True', color: TEST, opacity: fadeAt(frame, 29.2, 0.25)},
        ]}
        opacity={fadeAt(frame, 27.6)}
      />
    </AbsoluteFill>
  );
};

// Scene 4 — what moved to Rust, and what stays in Python.

const SPLIT_LINES: Lines = [
  [0, 'What moved to Rust: every decision the scheduler makes for an effect.'],
  [8.5, 'What stays in Python: every class your code sees, raises or subclasses.'],
  [17, 'Under both: a subclass resolves as its parent, Spawn gives a Python Task.'],
];
const SPLIT = f(28);
const MOVED = [
  'the state of tasks, promises, semaphores',
  'the ready queue (a port of heapq)',
  'Cancel and Discard',
  'external completions',
  'deadlock detection',
  'the sweep of finished tasks',
];
const STAYS = [
  'effect classes: Spawn, Wait, ...',
  'handles: Task, Promise, Semaphore',
  'exception classes',
  'end-of-run warnings, traceback text',
  'trace events and the clock',
  'the step limit check',
];
// measure/stays_python.log: the same three answers under python and under rust.
const STAYED = [
  'class CreateNamedSemaphore(CreateSemaphore)  ->  Semaphore',
  'yield Spawn(...)                             ->  doeff_core_effects.scheduler.Task',
  'yield Cancel(t); yield Wait(t)               ->  TaskCancelledError, the same class',
];

const SplitScene: React.FC = () => {
  const frame = useSceneFrame();
  const panelY = 92;
  const panelH = 330;
  const itemY = (i: number) => panelY + 62 + i * 42;
  const left = {x: 40, w: 590};
  const right = {x: 650, w: 590};
  // Every item starts in one pile in the middle (scheduler.py before the port) and goes to its side.
  const item = (text: string, i: number, side: 'rust' | 'py') => {
    const at = side === 'rust' ? 1.6 + i * 0.7 : 9.4 + i * 0.7;
    const go = ramp(frame, at, at + 0.7);
    const target = side === 'rust' ? left : right;
    const x = mix(640 - 190, target.x + 22, go);
    const y = mix(panelY + 70 + i * 42 + (side === 'rust' ? 0 : 10), itemY(i), go);
    const on = frame >= f(at + 0.4) && frame < f(at + 1.2);
    return (
      <div
        key={text}
        style={{
          position: 'absolute', left: x, top: y, width: 380, fontFamily: SANS, fontSize: 19, whiteSpace: 'nowrap',
          color: on ? LOOK : go >= 1 ? TEXT : MUTED, opacity: fadeAt(frame, 0.6) * (side === 'py' && go === 0 ? 0.45 : 1) * (side === 'rust' && go === 0 ? 0.45 : 1),
        }}
      >
        {text}
      </div>
    );
  };
  return (
    <AbsoluteFill>
      <Headlines lines={SPLIT_LINES} />
      <Box x={left.x} y={panelY} w={left.w} h={panelH} color={RUST} title="moved to Rust" sub="" on={frame >= f(1.6) && frame < f(6.6)} opacity={fadeAt(frame, 0.4)} />
      <Box x={right.x} y={panelY} w={right.w} h={panelH} color={PY} title="stays in Python" sub="" on={frame >= f(9.4) && frame < f(14.4)} opacity={fadeAt(frame, 8.6)} />
      {MOVED.map((t, i) => item(t, i, 'rust'))}
      {frame >= f(8.6) && STAYS.map((t, i) => item(t, i, 'py'))}
      <Terminal
        x={40} y={450} w={1200} size={17} line={30}
        lines={[
          {text: 'the same under python and rust (measure/stays_python.log):', color: MUTED},
          ...STAYED.map((text, i) => ({text, color: TEXT, opacity: fadeAt(frame, 17.8 + i * 1.6, 0.25)})),
        ]}
        opacity={fadeAt(frame, 17.4)}
      />
    </AbsoluteFill>
  );
};

// Scene 5 — the speed, measured again.

const SPEED_LINES: Lines = [
  [0, "And it is faster. The page's benchmark, run again: CPU microseconds per operation."],
  [9, 'Scheduler work takes 6 to 12 times less CPU time in Rust.'],
  [17.5, 'Passing through gains least: that time is spent outside the scheduler.'],
];
const SPEED = f(27);
// measure/speed.log: best of 3 alternating runs of benchmarks/scheduler_bench.py under each implementation.
const MACHINE = 'Linux x86_64, 36 logical CPUs, load average 38, Python 3.14.3 (free-threaded), 5,000 operations';
const FLOWS = [
  {name: 'spawn_wait', what: 'Spawn + Wait', py: 112.7, rs: 12.0},
  {name: 'gather', what: 'one Gather child', py: 112.0, rs: 17.5},
  {name: 'race', what: 'one Race', py: 278.7, rs: 23.6},
  {name: 'semaphore_uncontended', what: 'Acquire + Release', py: 31.2, rs: 3.7},
  {name: 'semaphore_contended', what: '4 tasks contend', py: 194.7, rs: 22.8},
  {name: 'promise', what: 'Create + Complete + Wait', py: 212.7, rs: 20.7},
  {name: 'cancel', what: 'Spawn + Cancel + finally', py: 447.9, rs: 71.5},
  {name: 'pass_through', what: 'one effect passing through', py: 8.2, rs: 3.2},
];

const SpeedScene: React.FC = () => {
  const frame = useSceneFrame();
  const top = Math.max(...FLOWS.map((fl) => fl.py));
  const barX = 360;
  const barW = 620;
  const rowY = (i: number) => 96 + i * 64;
  return (
    <AbsoluteFill>
      <Headlines lines={SPEED_LINES} />
      <div style={{position: 'absolute', left: barX, top: 72, fontFamily: SANS, fontSize: 16, color: MUTED, opacity: fadeAt(frame, 0.6)}}>
        <span style={{color: PY, fontWeight: 700, marginRight: 16}}>python</span><span style={{color: RUST, fontWeight: 700, marginRight: 16}}>rust</span>CPU µs per operation
      </div>
      {FLOWS.map((fl, i) => {
        const at = 1.2 + i * 0.5;
        const grow = ramp(frame, at, at + 1.0);
        const growRs = ramp(frame, at + 0.6, at + 1.4);
        const last = i === FLOWS.length - 1;
        const on = last ? frame >= f(18.0) : frame >= f(9.4) && frame < f(17.4);
        return (
          <React.Fragment key={fl.name}>
            <div style={{position: 'absolute', left: 40, top: rowY(i) + 2, fontFamily: MONO, fontSize: 17, fontWeight: 700, color: last && on ? LOOK : TEXT, opacity: fadeAt(frame, at)}}>{fl.name}</div>
            <div style={{position: 'absolute', left: 40, top: rowY(i) + 26, fontFamily: SANS, fontSize: 14, color: MUTED, opacity: fadeAt(frame, at)}}>{fl.what}</div>
            <div style={{position: 'absolute', left: barX, top: rowY(i) + 4, height: 20, width: (barW * fl.py * grow) / top, background: PY, borderRadius: 5, opacity: fadeAt(frame, at)}} />
            <div style={{position: 'absolute', left: barX + (barW * fl.py * grow) / top + 10, top: rowY(i) + 3, fontFamily: MONO, fontSize: 15, color: PY, opacity: fadeAt(frame, at + 0.8)}}>{fl.py.toFixed(1)}</div>
            <div style={{position: 'absolute', left: barX, top: rowY(i) + 30, height: 20, width: Math.max(3, (barW * fl.rs * growRs) / top), background: RUST, borderRadius: 5, opacity: fadeAt(frame, at + 0.6)}} />
            <div style={{position: 'absolute', left: barX + Math.max(3, (barW * fl.rs * growRs) / top) + 10, top: rowY(i) + 29, fontFamily: MONO, fontSize: 15, color: RUST, opacity: fadeAt(frame, at + 1.2)}}>{fl.rs.toFixed(1)}</div>
            <div
              style={{
                position: 'absolute', right: 40, top: rowY(i) + 8, width: 110, textAlign: 'right', fontFamily: MONO, fontSize: 24, fontWeight: 700,
                color: on ? LOOK : TEXT, opacity: fadeAt(frame, 9.4 + i * 0.25),
              }}
            >
              {(fl.py / fl.rs).toFixed(1)}×
            </div>
          </React.Fragment>
        );
      })}
      <div style={{position: 'absolute', left: 40, right: 40, top: 616, fontFamily: SANS, fontSize: 15, color: MUTED, opacity: fadeAt(frame, 2.0)}}>
        measure/speed.py: {MACHINE}, best of 3 alternating runs
      </div>
      <Label x={40} y={650} size={17} color={MUTED} bold={false} opacity={fadeAt(frame, 19.6)}>
        pass_through: the outer handler and the VM boundary do most of the work; the scheduler only declines the effect.
      </Label>
    </AbsoluteFill>
  );
};

// Outro — the chapter in one sentence.

const OUTRO = f(10);

const Outro: React.FC = () => {
  const frame = useSceneFrame();
  const rise = interpolate(frame, [0, f(0.67)], [20, 0], {extrapolateRight: 'clamp', easing: Easing.out(Easing.cubic)});
  const steps = [
    {text: 'yield Spawn(...)', color: ACCENT},
    {text: 'scheduled()', color: TEST},
    {text: 'Rust or Python', color: RUST},
    {text: 'same answers', color: LOOK},
  ];
  return (
    <AbsoluteFill style={{padding: 90, justifyContent: 'center', transform: `translateY(${rise}px)`}}>
      <div style={{fontFamily: SANS, fontSize: 56, fontWeight: 700, color: TEXT, opacity: fadeAt(frame, 0, 0.4)}}>The Rust scheduler</div>
      <div style={{fontFamily: SANS, fontSize: 30, color: ACCENT, marginTop: 6, opacity: fadeAt(frame, 0.2, 0.4)}}>One handler, two implementations, one meaning</div>
      <div style={{display: 'flex', alignItems: 'center', marginTop: 50, gap: 18}}>
        {steps.map((s, i) => (
          <React.Fragment key={s.text}>
            {i > 0 && <div style={{fontFamily: SANS, fontSize: 30, color: MUTED, opacity: fadeAt(frame, 0.8 + i * 0.9)}}>→</div>}
            <div
              style={{
                fontFamily: MONO, fontSize: 24, fontWeight: 700, color: s.color, padding: '10px 18px', borderRadius: 12, border: `2px solid ${s.color}`,
                background: PANEL, whiteSpace: 'nowrap', opacity: fadeAt(frame, 0.8 + i * 0.9),
              }}
            >
              {s.text}
            </div>
          </React.Fragment>
        ))}
      </div>
      <div style={{fontFamily: MONO, fontSize: 26, color: TEST, marginTop: 50, opacity: fadeAt(frame, 4.6, 0.4)}}>pip install doeff</div>
    </AbsoluteFill>
  );
};

// The scenes in order; Root.tsx registers RUST_SCHEDULER.length frames of RUST_SCHEDULER.Video.
export const RUST_SCHEDULER = narrate('rust-scheduler', DURATIONS, [
  {length: INTRO, starts: startsOf(INTRO_LINES), ids: ['a1', 'a2', 'a3'], node: <IntroScene />},
  {length: CHOOSE, starts: startsOf(CHOOSE_LINES), ids: ['b1', 'b2'], node: <ChooseScene />},
  {length: SAME, starts: startsOf(SAME_LINES), ids: ['c1', 'c2', 'c3', 'c4', 'c5'], node: <SameScene />},
  {length: SPLIT, starts: startsOf(SPLIT_LINES), ids: ['d1', 'd2', 'd3'], node: <SplitScene />},
  {length: SPEED, starts: startsOf(SPEED_LINES), ids: ['e1', 'e2', 'e3'], node: <SpeedScene />},
  {length: OUTRO, starts: [0], ids: ['o1'], node: <Outro />},
]);
