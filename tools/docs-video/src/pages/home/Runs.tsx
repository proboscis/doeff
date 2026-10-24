// The last two scenes of the home video (Home.tsx): one program, run line by line under a test stack of handlers
// (left) and a production stack (right), then the outro. The first check plays in slow motion on both sides with a caption per step, each
// held long enough to read. At Delay(30 * 60) the two runs part: the virtual clock skips the 30 minutes in 1.2 s
// of video and the test makes its second check, while the wall clock really waits them, shown fast-forward x180
// (30 minutes in 10 s), and then production makes its second check. A yellow ring shows where to look. Times
// below are in seconds of video. The program and its output are the ones in measure/monitor_straight.py (a few
// ms per test run on doeff main).
import React from 'react';
import {AbsoluteFill, Easing, interpolate} from 'remotion';
import {Clock, highlightYield} from '../../kit/parts';
import {ACCENT, BG, f, LOOK, MONO, MUTED, PANEL, PROD, SANS, TEST, TEXT} from '../../kit/theme';
import {useSceneFrame} from '../../kit/warp';

export const CODE = [
  '@do',
  'def monitor(url):',
  '    page = yield Fetch(url)',
  '    yield Log(f"{len(page)} bytes")',
  '    yield Delay(30 * 60)',
  '    page = yield Fetch(url)',
  '    yield Log(f"{len(page)} bytes")',
];

// Where things sit on the 1280x720 frame: the code box in the middle, the two panels under it.
const CODE_TOP = 72;
const CODE_LEFT = 245;
const CODE_WIDTH = 790;
const CODE_PAD = 12;
const LINE = 34;
const PANEL_TOP = 356;
const PANEL_HEIGHT = 348;
const PANEL_LEFT = {test: 24, prod: 652} as const;
const PANEL_WIDTH = 604;
const CHIP_TOP = 52;
const CHIP_WIDTH = 176;
const CHIP_HEIGHT = 56;
const CHIP_GAP = 14;

// The intro: the code comes in first, then the two stacks of handlers; both runs start at RUN_FROM. Each intro
// line stays on screen for at least 3.5 s.
const PANELS_FROM = f(3.5);
const RUN_FROM = f(7.5);
// The 30-minute wait in seconds, and how long each clock takes to show it: the virtual clock skips it in 1.2 s,
// the wall clock waits it out at x180, which is 10 s of video.
const WAIT = 30 * 60;
const SKIP = 1.2;
const FAST_FORWARD = 10;
const SPEEDUP = WAIT / FAST_FORWARD;
// The fast-forward the viewer sees. A longer video that stretches this scene to fit its narration slows the wall
// clock down on screen, so it passes the speed it really shows, and the badge and headline name that one.
export const SpeedupContext = React.createContext(SPEEDUP);
const SpeedupText: React.FC = () => <>{`Test skips the wait. Production really waits (shown ${React.useContext(SpeedupContext)}× faster).`}</>;

// How long each beat of a step lasts, in seconds: the cursor arrives (dwell), the effect flies out (out), the
// handler acts while its caption shows (act), and the answer flies back. The first check is slow, so the headline
// and the two captions of a step stay for 5 s; the second check repeats it quicker under one caption per side.
type Pace = {dwell: number; out: number; act: number; back: number};
const SLOW: Pace = {dwell: 0.5, out: 0.8, act: 3.0, back: 0.8};
const AGAIN: Pace = {dwell: 0.5, out: 0.5, act: 0.9, back: 0.5};

type Side = 'test' | 'prod';

// Where the ring goes while a handler acts: its chip, the clock, or the printed lines.
type Focus = 'chip' | 'clock' | 'log';

// One yield of the program as a run executes it: the line, its effect, the chip of the handler that answers it
// (0 = web, 1 = clock, 2 = printed), the reply, what the handler does, and where to look meanwhile.
type Effect = {name: string; chip: number; reply: string; say: string; focus: Focus};
type Instr = {line: number; pace: Pace; headline?: string; effect: Effect; act?: number};
type Step = {line: number; start: number; leave: number; act: number; backFrom: number; end: number; headline?: string; effect: Effect};

const fetchEffect = (say: string): Effect => ({name: 'Fetch', chip: 0, reply: '<p>up</p>', say, focus: 'chip'});
const log = (say: string): Effect => ({name: 'Log', chip: 2, reply: 'ok', say, focus: 'log'});

// The first check is the same on both sides except for what each handler says it does.
const firstCheck = (side: Side): Instr[] => [
  {
    line: 2, pace: SLOW, headline: 'yield Fetch(url): the program asks for the page.',
    effect: fetchEffect(side === 'test' ? 'fake_web answers with a page from a dict.' : 'http_web downloads the page over HTTP.'),
  },
  {line: 3, pace: SLOW, headline: 'yield Log(...): it writes a line.', effect: log("printed prints the line with the clock's time.")},
];

// The second check after the wait, at a quicker pace under one caption, now that each step has been explained once.
const secondCheck = (side: Side): Instr[] => {
  const say = side === 'test' ? 'fake_web and printed answer again, now at 09:30.' : 'http_web and printed answer again, now at 09:30.';
  return [
    {line: 5, pace: AGAIN, effect: fetchEffect(say)},
    {line: 6, pace: AGAIN, effect: log(say)},
  ];
};

const TEST_PROGRAM: Instr[] = [
  ...firstCheck('test'),
  {
    line: 4, pace: SLOW, act: SKIP,
    effect: {name: 'Delay 30 min', chip: 1, reply: 'ok', say: 'The virtual clock skips ahead to 09:30. No real wait.', focus: 'clock'},
  },
  ...secondCheck('test'),
];
const PROD_PROGRAM: Instr[] = [
  ...firstCheck('prod'),
  {
    line: 4, pace: SLOW, act: FAST_FORWARD,
    effect: {name: 'Delay 30 min', chip: 1, reply: 'ok', say: 'The wall clock really waits the 30 minutes.', focus: 'clock'},
  },
  ...secondCheck('prod'),
];

// The absolute frames of each step, laid end to end from RUN_FROM.
const schedule = (program: Instr[]): Step[] => {
  let at = RUN_FROM;
  return program.map(({line, pace, headline, effect, act}) => {
    const leave = at + f(pace.dwell);
    const arrive = leave + f(pace.out);
    const backFrom = arrive + f(act ?? pace.act);
    const end = backFrom + f(pace.back);
    const step = {line, start: at, leave, act: arrive, backFrom, end, headline, effect};
    at = end;
    return step;
  });
};

const STEPS: Record<Side, Step[]> = {test: schedule(TEST_PROGRAM), prod: schedule(PROD_PROGRAM)};
const DONE: Record<Side, number> = {test: STEPS.test[STEPS.test.length - 1].end, prod: STEPS.prod[STEPS.prod.length - 1].end};

// The step of a run that yields Delay(30 * 60); both programs above have one.
const delayStep = (side: Side): Step => {
  const step = STEPS[side].find((s) => s.line === 4);
  if (!step) throw new Error(`the ${side} program has no Delay step`);
  return step;
};
const DELAY: Record<Side, Step> = {test: delayStep('test'), prod: delayStep('prod')};

const clamp = {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'} as const;
const smooth = {...clamp, easing: Easing.inOut(Easing.cubic)};

// Opacity from 0 to 1 over a few frames, so each element can appear on its own beat.
const fade = (frame: number, start: number, length = f(0.2)) => interpolate(frame, [start, start + length], [0, 1], clamp);

// The step running at a frame (the last one once the run is over).
const stepAt = (side: Side, frame: number) => {
  const steps = STEPS[side];
  return steps.find((s) => frame < s.end) ?? steps[steps.length - 1];
};

// The cursor's line position at a frame, moving smoothly from the previous line while it arrives.
const cursorLine = (side: Side, frame: number) => {
  const steps = STEPS[side];
  const i = steps.indexOf(stepAt(side, frame));
  const from = i > 0 ? steps[i - 1].line : 1;
  return interpolate(frame, [steps[i].start, steps[i].leave], [from, steps[i].line], {...clamp, easing: Easing.out(Easing.cubic)});
};

type Rect = {x: number; y: number; w: number; h: number};

// The rectangle of a code line at a (possibly fractional) line position.
const codeRect = (line: number): Rect => ({x: CODE_LEFT - 4, y: CODE_TOP + CODE_PAD + line * LINE - 3, w: CODE_WIDTH + 8, h: LINE + 6});

// The rectangle on the frame of what a handler changes: its chip, the clock with its time, or the printed lines.
const targetRect = (side: Side, effect: Effect): Rect => {
  const left = PANEL_LEFT[side];
  if (effect.focus === 'chip') return {x: left + 24 + effect.chip * (CHIP_WIDTH + CHIP_GAP) - 4, y: PANEL_TOP + CHIP_TOP - 4, w: CHIP_WIDTH + 8, h: CHIP_HEIGHT + 8};
  if (effect.focus === 'clock') return {x: left + 14, y: PANEL_TOP + 118, w: 140, h: 172};
  return {x: left + 164, y: PANEL_TOP + 120, w: 422, h: 90};
};

const lerpRect = (a: Rect, b: Rect, t: number): Rect => ({
  x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t, w: a.w + (b.w - a.w) * t, h: a.h + (b.h - a.h) * t,
});

// Where a side's "look here" ring is at a frame: on the code line while the cursor is there, sliding to what the
// handler changes while the effect flies out, staying there while the handler acts, and sliding back with the reply.
const ringRect = (side: Side, frame: number): Rect | null => {
  if (frame < RUN_FROM || frame >= DONE[side]) return null;
  const step = stepAt(side, frame);
  if (frame < step.leave) return codeRect(cursorLine(side, frame));
  const target = targetRect(side, step.effect);
  if (frame < step.act) return lerpRect(codeRect(step.line), target, interpolate(frame, [step.leave, step.act], [0, 1], smooth));
  if (frame < step.backFrom) return target;
  return lerpRect(target, codeRect(step.line), interpolate(frame, [step.backFrom, step.end], [0, 1], smooth));
};

// Where a chip sits on the frame: the top centre, where effects land.
const chipPoint = (side: Side, chip: number) => ({
  x: PANEL_LEFT[side] + 24 + chip * (CHIP_WIDTH + CHIP_GAP) + CHIP_WIDTH / 2,
  y: PANEL_TOP + CHIP_TOP,
});

// A point on the curve from the code line to a chip: it leaves the code box sideways, then drops onto the chip.
const along = (side: Side, line: number, chip: number, t: number) => {
  const a = {x: side === 'test' ? CODE_LEFT : CODE_LEFT + CODE_WIDTH, y: CODE_TOP + CODE_PAD + line * LINE + LINE / 2};
  const b = chipPoint(side, chip);
  const c = {x: b.x, y: a.y};
  const u = 1 - t;
  return {x: u * u * a.x + 2 * u * t * c.x + t * t * b.x, y: u * u * a.y + 2 * u * t * c.y + t * t * b.y};
};

const color = (side: Side) => (side === 'test' ? TEST : PROD);

// Seconds since 09:00 as HH:MM, or HH:MM:SS for the wall clock.
const clockText = (seconds: number, withSeconds: boolean) => {
  const total = 9 * 3600 + Math.floor(seconds);
  const hh = String(Math.floor(total / 3600)).padStart(2, '0');
  const mm = String(Math.floor((total % 3600) / 60)).padStart(2, '0');
  const ss = String(total % 60).padStart(2, '0');
  return withSeconds ? `${hh}:${mm}:${ss}` : `${hh}:${mm}`;
};

// Seconds past 09:00 on each side's clock at a frame: the virtual clock eases over the skip, the wall clock
// moves at a constant x180 through the wait.
const clockSeconds = (side: Side, frame: number) => {
  const d = DELAY[side];
  return WAIT * interpolate(frame, [d.act, d.backFrom], [0, 1], side === 'test' ? smooth : clamp);
};

// The code box, with one highlighted band and gutter arrow per run, so the viewer sees which line each run is on.
const CodeBox: React.FC = () => {
  const frame = useSceneFrame();
  const shown = (side: Side) => frame >= RUN_FROM && frame < DONE[side];
  const band = (side: Side) => (
    <div
      style={{
        position: 'absolute', left: 0, right: 0, height: LINE, top: CODE_PAD + cursorLine(side, frame) * LINE,
        background: color(side), opacity: shown(side) ? 0.22 : 0,
      }}
    />
  );
  const arrow = (side: Side) => (
    <div
      style={{
        position: 'absolute', top: CODE_PAD + cursorLine(side, frame) * LINE, height: LINE, lineHeight: `${LINE}px`,
        [side === 'test' ? 'left' : 'right']: 12, color: color(side), fontSize: 24, opacity: shown(side) ? 1 : 0,
      }}
    >
      {side === 'test' ? '▶' : '◀'}
    </div>
  );
  return (
    <div
      style={{
        position: 'absolute', left: CODE_LEFT, top: CODE_TOP, width: CODE_WIDTH, height: CODE_PAD * 2 + CODE.length * LINE,
        background: '#0b0d13', borderRadius: 14, border: `2px solid #2a2e40`, overflow: 'hidden', opacity: fade(frame, 0, f(0.15)),
      }}
    >
      {band('test')}
      {band('prod')}
      {arrow('test')}
      {arrow('prod')}
      <div style={{position: 'absolute', top: CODE_PAD, left: 46, fontFamily: MONO, fontSize: 23, lineHeight: `${LINE}px`}}>
        {CODE.map((line, i) => (
          <div key={i} style={{whiteSpace: 'pre', color: i === 0 ? ACCENT : TEXT, opacity: fade(frame, f(0.2 + i * 0.17))}}>
            {line.includes('yield') ? highlightYield(line) : line}
          </div>
        ))}
      </div>
    </div>
  );
};

// The yellow "look here" ring of one side.
const Ring: React.FC<{side: Side}> = ({side}) => {
  const frame = useSceneFrame();
  const r = ringRect(side, frame);
  if (!r) return null;
  return (
    <div
      style={{
        position: 'absolute', left: r.x, top: r.y, width: r.w, height: r.h, boxSizing: 'border-box', borderRadius: 12,
        border: `3px solid ${LOOK}`, boxShadow: `0 0 18px ${LOOK}99`,
      }}
    />
  );
};

// The effect on its way out to a handler, and the handler's answer on its way back.
const Packets: React.FC<{side: Side}> = ({side}) => {
  const frame = useSceneFrame();
  const step = stepAt(side, frame);
  if (frame < step.leave || frame >= step.end) return null;
  const {name, chip, reply} = step.effect;
  const outgoing = frame < step.act;
  const returning = frame >= step.backFrom;
  if (!outgoing && !returning) return null;
  const t = outgoing
    ? interpolate(frame, [step.leave, step.act], [0, 1], smooth)
    : interpolate(frame, [step.backFrom, step.end], [1, 0], smooth);
  const p = along(side, step.line, chip, t);
  const c = color(side);
  return (
    <div
      style={{
        position: 'absolute', left: p.x, top: p.y, transform: 'translate(-50%, -50%)', whiteSpace: 'nowrap',
        fontFamily: MONO, fontSize: 20, padding: '5px 14px', borderRadius: 14, boxShadow: `0 0 16px ${c}`,
        background: outgoing ? c : PANEL, color: outgoing ? BG : c, border: `2px solid ${c}`, fontWeight: 700,
      }}
    >
      {outgoing ? name : reply}
    </div>
  );
};

type Chip = {name: string; handles: string};
const CHIPS: Record<Side, Chip[]> = {
  test: [
    {name: 'fake_web', handles: 'Fetch'},
    {name: 'virtual clock', handles: 'Delay · the time'},
    {name: 'printed', handles: 'Log'},
  ],
  prod: [
    {name: 'http_web', handles: 'Fetch'},
    {name: 'wall clock', handles: 'Delay · the time'},
    {name: 'printed', handles: 'Log'},
  ],
};

// The handlers of one run as a row of chips; the chip whose effect has arrived lights up while it acts, and the
// others dim, so the eye goes to the one at work.
const Chips: React.FC<{side: Side}> = ({side}) => {
  const frame = useSceneFrame();
  const step = stepAt(side, frame);
  const lit = frame >= step.act && frame < step.backFrom ? step.effect.chip : -1;
  const c = color(side);
  return (
    <>
      {CHIPS[side].map((chip, i) => (
        <div
          key={chip.name}
          style={{
            position: 'absolute', left: 24 + i * (CHIP_WIDTH + CHIP_GAP), top: CHIP_TOP, width: CHIP_WIDTH, height: CHIP_HEIGHT,
            boxSizing: 'border-box', border: `2px solid ${c}`, borderRadius: 10, textAlign: 'center', paddingTop: 5,
            background: i === lit ? `${c}66` : 'transparent', transform: `scale(${i === lit ? 1.06 : 1})`,
            opacity: fade(frame, PANELS_FROM + f(0.3 + i * 0.33)) * (lit >= 0 && i !== lit ? 0.45 : 1),
          }}
        >
          <div style={{fontFamily: MONO, fontSize: 17, color: c, fontWeight: 700}}>{chip.name}</div>
          <div style={{fontFamily: SANS, fontSize: 14, color: MUTED, marginTop: 2}}>{chip.handles}</div>
        </div>
      ))}
    </>
  );
};

const LOG_LINES = ['09:00 9 bytes', '09:30 9 bytes'];

// What a side's handlers are doing now, in one sentence at the foot of its panel: the handlers in the intro,
// then the caption of the last effect that reached a handler, then the end state. A caption that the next step
// repeats stays on without fading in again.
const Caption: React.FC<{side: Side}> = ({side}) => {
  const frame = useSceneFrame();
  const c = color(side);
  const reached = STEPS[side].filter((s) => frame >= s.act);
  let text: React.ReactNode =
    side === 'test' ? 'Test handlers: a fake web, a virtual clock, printed lines.' : 'Production handlers: real HTTP, the wall clock, printed lines.';
  let from = PANELS_FROM + f(1.2);
  if (reached.length > 0) {
    let first = reached.length - 1;
    while (first > 0 && reached[first - 1].effect.say === reached[first].effect.say) first--;
    text = reached[first].effect.say;
    from = reached[first].act;
  }
  if (frame >= DONE[side]) {
    text = side === 'test' ? '✓ Done. The real run took a few ms.' : '✓ Done, after 30 real minutes.';
    from = DONE[side];
  }
  return (
    <div
      style={{
        position: 'absolute', left: 24, right: 24, top: 302, height: 32, lineHeight: '32px', fontFamily: SANS, fontSize: 19,
        fontWeight: 700, color: c, whiteSpace: 'nowrap', opacity: fade(frame, from),
      }}
    >
      {text}
    </div>
  );
};

// One run: its name and playback speed, its handlers, its clock, what it has printed, how far its wait is, and
// the caption of what its handlers are doing. A finished run dims while the other one still runs.
const Panel: React.FC<{side: Side}> = ({side}) => {
  const frame = useSceneFrame();
  const c = color(side);
  const seconds = clockSeconds(side, frame);
  const logged = STEPS[side].filter((s) => s.effect.name === 'Log' && frame >= s.act).length;
  const delay = DELAY[side];
  const atWait = frame >= delay.act;
  const fastForward = side === 'prod' && frame >= delay.act && frame < delay.backFrom;
  const done = frame >= DONE[side];
  const other: Side = side === 'test' ? 'prod' : 'test';
  const resting = done && frame < DONE[other];
  const dim = resting ? interpolate(frame, [DONE[side] + f(0.8), DONE[side] + f(1.3)], [1, 0.45], clamp) : 1;
  const part = seconds / WAIT;
  const realSoFar = side === 'prod' ? seconds : 0;
  const dots = '.'.repeat(1 + (Math.floor(frame / f(0.33)) % 3));
  const speedup = React.useContext(SpeedupContext);
  const badge = done ? "✓ done" : fastForward ? `▶▶ fast-forward ×${speedup}` : "▶ slow motion";
  const filled = done || fastForward;
  return (
    <div
      style={{
        position: 'absolute', left: PANEL_LEFT[side], top: PANEL_TOP, width: PANEL_WIDTH, height: PANEL_HEIGHT,
        borderRadius: 18, background: PANEL, border: `3px solid ${c}`, opacity: fade(frame, PANELS_FROM) * dim,
      }}
    >
      <div style={{position: 'absolute', left: 24, top: 10, fontFamily: SANS, fontSize: 26, fontWeight: 700, color: c, letterSpacing: 2}}>
        {side === 'test' ? 'TEST' : 'PRODUCTION'}
      </div>
      <div
        style={{
          position: 'absolute', right: 20, top: 12, fontFamily: SANS, fontSize: 17, fontWeight: 700, padding: '4px 12px', borderRadius: 14,
          color: filled ? BG : c, background: filled ? c : 'transparent', border: `2px solid ${c}`, opacity: fade(frame, RUN_FROM),
        }}
      >
        {badge}
      </div>
      <Chips side={side} />
      <div style={{position: 'absolute', left: 24, top: 126}}>
        <Clock seconds={seconds} c={c} secondHand={side === 'prod'} />
        <div style={{fontFamily: MONO, fontSize: 24, color: TEXT, textAlign: 'center', marginTop: 4}}>
          {clockText(seconds, side === 'prod')}
        </div>
      </div>
      <div
        style={{
          position: 'absolute', left: 170, top: 126, width: 410, height: 78, boxSizing: 'border-box', padding: '8px 16px',
          background: '#0b0d13', borderRadius: 10, fontFamily: MONO, fontSize: 20, lineHeight: '31px',
        }}
      >
        {LOG_LINES.slice(0, logged).map((line) => (
          <div key={line} style={{color: TEXT}}>{line}</div>
        ))}
        {fastForward && <div style={{color: MUTED}}>waiting for 09:30{dots}</div>}
      </div>
      <div style={{position: 'absolute', left: 170, top: 222, width: 410, opacity: atWait ? 1 : 0}}>
        <div style={{display: 'flex', justifyContent: 'space-between', fontFamily: SANS, fontSize: 17, color: MUTED}}>
          <span>the 30-minute wait</span>
          <span style={{color: c, fontWeight: 700}}>
            {side === 'test'
              ? part >= 1 ? 'skipped · real time ~0 s' : 'skipping ahead'
              : `real time ${Math.floor(realSoFar / 60)} min ${String(Math.floor(realSoFar % 60)).padStart(2, '0')} s`}
          </span>
        </div>
        <div style={{marginTop: 6, height: 14, borderRadius: 7, background: '#262a3a', overflow: 'hidden'}}>
          <div style={{width: `${part * 100}%`, height: '100%', background: c}} />
        </div>
      </div>
      <Caption side={side} />
    </div>
  );
};

// The story line at the top, phase by phase: the intro, the first check step by step (both runs are on the same
// step until Delay), how the two clocks part at Delay, and the end. Each line stays for at least 3 s.
const PHASES: [number, React.ReactNode][] = [
  [0, 'monitor() checks a page, waits 30 minutes, and checks again.'],
  [PANELS_FROM, 'Run it twice: test handlers on the left, production on the right.'],
  ...STEPS.test.filter((s) => s.headline).map((s): [number, React.ReactNode] => [s.start, s.headline]),
  [DELAY.test.start, 'yield Delay(30 * 60): each clock decides what waiting means.'],
  [DELAY.test.end, <SpeedupText />],
  [DONE.test, 'Test is done. Production is still waiting out the 30 minutes.'],
  [DELAY.prod.backFrom, 'The wait is over. Production checks again.'],
  [
    DONE.prod,
    <>
      <span>Same code, same output. </span>
      <span style={{color: TEST}}>Test: a few ms.</span>
      <span style={{color: PROD, marginLeft: 14}}>Production: 30 real minutes.</span>
    </>,
  ],
];

// The frames where the headlines change, and the stretch where the wall clock waits: a longer video times its
// narration to the first and works out the fast-forward it shows from the second.
export const RUN_HEADLINE_STARTS = PHASES.map(([from]) => from);
export const PROD_WAIT = {from: DELAY.prod.act, to: DELAY.prod.backFrom, seconds: WAIT};

// The story line of the phase the frame is in, faded in at the start of that phase. A longer video that has
// already introduced monitor() passes its own two intro lines.
const Headline: React.FC<{intro?: [string, string]}> = ({intro}) => {
  const frame = useSceneFrame();
  const i = PHASES.reduce((found, [from], j) => (frame >= from ? j : found), 0);
  const [from, phaseNode] = PHASES[i];
  const node = intro && i < 2 ? intro[i] : phaseNode;
  return (
    <div
      key={i}
      style={{
        position: 'absolute', left: 0, right: 0, top: 18, textAlign: 'center', fontFamily: SANS, fontSize: 26, fontWeight: 700,
        color: TEXT, whiteSpace: 'nowrap', opacity: fade(frame, from),
      }}
    >
      {node}
    </div>
  );
};

// Scene 1: the program, then both runs side by side, sharing one code box.
export const Runs: React.FC<{intro?: [string, string]}> = ({intro}) => (
  <AbsoluteFill>
    <Headline intro={intro} />
    <CodeBox />
    <Panel side="test" />
    <Panel side="prod" />
    <Ring side="test" />
    <Ring side="prod" />
    <Packets side="test" />
    <Packets side="prod" />
  </AbsoluteFill>
);

// Scene 2: what doeff is, in the core sentence, and how to install it.
export const Outro: React.FC = () => {
  const frame = useSceneFrame();
  const rise = interpolate(frame, [0, f(0.67)], [20, 0], {extrapolateRight: 'clamp', easing: Easing.out(Easing.cubic)});
  return (
    <AbsoluteFill style={{padding: 90, justifyContent: 'center', transform: `translateY(${rise}px)`}}>
      <div style={{fontFamily: SANS, fontSize: 56, fontWeight: 700, color: TEXT, opacity: fade(frame, 0, f(0.4))}}>doeff</div>
      <div style={{fontFamily: SANS, fontSize: 30, color: ACCENT, marginTop: 6, opacity: fade(frame, f(0.2), f(0.4))}}>
        Algebraic effects for Python
      </div>
      <div style={{fontFamily: SANS, fontSize: 28, lineHeight: '42px', color: TEXT, marginTop: 34, width: 1050, opacity: fade(frame, f(0.6), f(0.4))}}>
        Handlers get the rest of your program, so time, concurrency, retries, budgets and I/O become layers you stack around the same code.
      </div>
      <div style={{fontFamily: MONO, fontSize: 28, color: TEST, marginTop: 40, opacity: fade(frame, f(1.1), f(0.4))}}>pip install doeff</div>
    </AbsoluteFill>
  );
};

// The design lengths of the two scenes in frames. The runs scene holds 4 s after
// production is done and the outro 6 s, so the last line and the core sentence can be read.
export const RUNS = DONE.prod + f(4);
export const OUTRO = f(6);
