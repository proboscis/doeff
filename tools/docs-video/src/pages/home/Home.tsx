// The video on the docs home page: what doeff is, told before the side-by-side runs. Two opening scenes say what doeff is
// and what yield does; four scenes follow, each with headlines a synthetic voice reads (narration.json):
//   1. monitor() only yields effects: what it wants, never how.
//   2. Handlers wrap it in layers; an effect travels out until a layer handles it.
//   3. The handler also gets k, the rest of the program, so it decides when the rest runs.
//   4. A scheduler is a layer too: two monitors park their k's and the virtual clock wakes them in time order.
// Then the side-by-side runs of Runs.tsx (swap the stack, keep the code) and the outro. The scheduling scene
// is backed by measure/two_monitors.py: 21 runs, the same four lines in the same order, 0.39–2.45 ms each.
import React from 'react';
import {AbsoluteFill, interpolate} from 'remotion';
import {clamp, fadeAt, mix, ramp, smooth} from '../../kit/motion';
import {narrate} from '../../kit/narrated';
import {Clock, CodeBlock, glow, Headlines, lineMid, Lines, PAD, Pill, startsOf} from '../../kit/parts';
import {ACCENT, f, FPS, LOOK, MONO, MUTED, PANEL, SANS, TEST, TEXT} from '../../kit/theme';
import {realOf, Slot, useSceneFrame} from '../../kit/warp';
import DURATIONS from './durations.json';
import {CODE, Outro, OUTRO, PROD_WAIT, RUN_HEADLINE_STARTS, Runs, RUNS, SpeedupContext} from './Runs';

// Opening — what doeff is: code that says what it wants, and stacked handlers that decide how.

const INTRO_LINES: Lines = [
  [0, 'This is doeff: algebraic effects for Python.'],
  [3.5, 'Your code says what it wants. Stacked handlers decide how it happens.'],
];
const INTRO = f(9);
const INTRO_WANTS = ['yield Fetch(url)', 'yield Log(...)', 'yield Delay(30 * 60)'];

const IntroScene: React.FC = () => {
  const frame = useSceneFrame();
  const code = {x: 170, y: 400, w: 400, size: 22, line: 40};
  const barY = (i: number) => 390 + i * 54;
  return (
    <AbsoluteFill>
      <Headlines lines={INTRO_LINES} />
      <div style={{position: 'absolute', left: 0, right: 0, top: 110 + mix(16, 0, ramp(frame, 0.2, 0.8)), textAlign: 'center', opacity: fadeAt(frame, 0.2, 0.5)}}>
        <div style={{fontFamily: SANS, fontSize: 84, fontWeight: 700, color: TEXT}}>doeff</div>
        <div style={{fontFamily: SANS, fontSize: 32, color: ACCENT, marginTop: 4}}>Algebraic effects for Python</div>
      </div>
      <div style={{position: 'absolute', left: code.x, top: code.y - 34, fontFamily: SANS, fontSize: 20, fontWeight: 700, color: MUTED, opacity: fadeAt(frame, 3.6)}}>your code</div>
      <CodeBlock lines={INTRO_WANTS} {...code} opacity={fadeAt(frame, 3.6)} />
      <div style={{position: 'absolute', left: code.x, top: code.y + 160, fontFamily: SANS, fontSize: 22, fontWeight: 700, color: ACCENT, opacity: fadeAt(frame, 4.0)}}>says what it wants</div>
      <div style={{position: 'absolute', left: 760, top: code.y - 34, fontFamily: SANS, fontSize: 20, fontWeight: 700, color: MUTED, opacity: fadeAt(frame, 4.4)}}>handlers, stacked</div>
      {INTRO_WANTS.map((_, i) => {
        const lands = 5.2 + i * 1.0;
        const on = frame >= f(lands) && frame < f(lands + 0.8);
        return (
          <div
            key={i}
            style={{
              position: 'absolute', left: 760 + i * 12, top: barY(i), width: 340, height: 44, boxSizing: 'border-box', borderRadius: 10,
              border: `3px solid ${TEST}`, ...glow(on, TEST), background: PANEL, fontFamily: MONO, fontSize: 19, fontWeight: 700,
              color: on ? LOOK : TEST, lineHeight: '38px', paddingLeft: 16, opacity: fadeAt(frame, 4.4 + i * 0.2),
            }}
          >
            handler
          </div>
        );
      })}
      <div style={{position: 'absolute', left: 760, top: code.y + 160, fontFamily: SANS, fontSize: 22, fontWeight: 700, color: TEST, opacity: fadeAt(frame, 4.8)}}>decide how it happens</div>
      {INTRO_WANTS.map((want, i) => {
        const leave = 4.5 + i * 1.0;
        if (frame < f(leave) || frame >= f(leave + 1.0)) return null;
        const t = ramp(frame, leave, leave + 0.7);
        return (
          <Pill
            key={want}
            x={mix(code.x + code.w + 70, 750, t)}
            y={mix(lineMid(code.y, code.line, i), barY(i) + 22, t)}
            text={want.replace('yield ', '')}
            color={ACCENT}
            opacity={1 - ramp(frame, leave + 0.75, leave + 1.0)}
          />
        );
      })}
    </AbsoluteFill>
  );
};

// Opening — what yield does in a doeff program: it hands an effect outward and gets the answer back.

const YIELD_LINES: Lines = [
  [0, 'A doeff program is a Python generator, marked with @do.'],
  [5, 'yield Fetch(url) fetches nothing by itself. It hands an effect outward.'],
  [10, 'A handler answers it. The answer comes back as page, and the code goes on.'],
];
const YIELD = f(15);
const SIZE_OF = ['@do', 'def size_of(url):', '    page = yield Fetch(url)', '    return len(page)'];

const YieldScene: React.FC = () => {
  const frame = useSceneFrame();
  const code = {x: 90, y: 190, w: 600, size: 26, line: 46};
  const box = {x: 830, y: 230, w: 360, h: 150};
  const band = frame < f(5.0) ? null : frame < f(12.0) ? 2 : mix(2, 3, ramp(frame, 12.0, 12.3));
  const out = ramp(frame, 5.6, 6.6);
  const back = ramp(frame, 10.6, 11.6);
  const working = frame >= f(6.6) && frame < f(10.6);
  return (
    <AbsoluteFill>
      <Headlines lines={YIELD_LINES} />
      <CodeBlock lines={SIZE_OF} {...code} band={band} opacity={fadeAt(frame, 0)} />
      <div
        style={{
          position: 'absolute', left: code.x + 14, top: code.y + PAD - 2, width: 74, height: code.line + 4, boxSizing: 'border-box',
          borderRadius: 8, border: `3px solid ${LOOK}`, boxShadow: `0 0 16px ${LOOK}88`, opacity: fadeAt(frame, 0.8) * (1 - ramp(frame, 4.6, 5.0)),
        }}
      />
      <div style={{position: 'absolute', left: code.x, top: code.y + 230, width: 640, fontFamily: SANS, fontSize: 22, lineHeight: '32px', color: MUTED, opacity: fadeAt(frame, 1.2) * (1 - ramp(frame, 4.6, 5.0))}}>
        @do makes this generator a doeff program. It pauses at each yield.
      </div>
      <div style={{position: 'absolute', left: code.x, top: code.y + 230, width: 640, fontFamily: SANS, fontSize: 22, lineHeight: '32px', color: MUTED, opacity: fadeAt(frame, 6.8) * (1 - ramp(frame, 11.6, 12.0))}}>
        size_of() is paused here, waiting for the answer.
      </div>
      <div
        style={{
          position: 'absolute', left: box.x, top: box.y, width: box.w, height: box.h, boxSizing: 'border-box', borderRadius: 18, background: PANEL,
          border: `3px solid ${TEST}`, ...glow(working, TEST), opacity: fadeAt(frame, 5.2),
        }}
      >
        <div style={{position: 'absolute', left: 20, top: 16, fontFamily: SANS, fontSize: 26, fontWeight: 700, color: working ? LOOK : TEST}}>a handler</div>
        <div style={{position: 'absolute', left: 20, top: 58, fontFamily: SANS, fontSize: 19, color: MUTED}}>answers Fetch, for example</div>
        <div style={{position: 'absolute', left: 20, top: 86, fontFamily: SANS, fontSize: 19, color: MUTED}}>with a page from the web</div>
      </div>
      {frame >= f(5.6) && frame < f(6.9) && (
        <Pill x={mix(code.x + code.w + 70, box.x, out)} y={mix(lineMid(code.y, code.line, 2), box.y + 60, out)} text="Fetch(url)" color={ACCENT} opacity={1 - ramp(frame, 6.6, 6.9)} />
      )}
      {frame >= f(10.6) && frame < f(12.0) && (
        <Pill x={mix(box.x, code.x + code.w + 70, back)} y={mix(box.y + 60, lineMid(code.y, code.line, 2), back)} text={'"<p>up</p>"'} color={TEST} opacity={1 - ramp(frame, 11.7, 12.0)} />
      )}
      <div
        style={{
          position: 'absolute', left: code.x + 390, top: lineMid(code.y, code.line, 3), transform: 'translateY(-50%)', fontFamily: MONO, fontSize: 24,
          fontWeight: 700, color: ACCENT, opacity: fadeAt(frame, 12.6),
        }}
      >
        → 9
      </div>
    </AbsoluteFill>
  );
};

// Scene 1 — the program only says what it wants.

const WANT_LINES: Lines = [
  [0, 'Here is monitor(). It only says what it wants: a page, a line, a 30-minute wait.'],
  [6.5, 'Each yield hands an effect outward. The code never says how it is done.'],
];
const WANTS = [
  {line: 2, effect: 'Fetch(url)', means: 'get the page'},
  {line: 3, effect: 'Log(...)', means: 'write a line'},
  {line: 4, effect: 'Delay(30 * 60)', means: 'wait 30 minutes'},
  {line: 5, effect: 'Fetch(url)', means: 'get the page again'},
  {line: 6, effect: 'Log(...)', means: 'write another line'},
];
const WANT = f(12);

// The second the program reaches the i-th yield.
const wantAt = (i: number) => 1.5 + i * 1.6;

const WantScene: React.FC = () => {
  const frame = useSceneFrame();
  const code = {x: 80, y: 150, w: 600, size: 23, line: 34};
  const reached = WANTS.reduce((found, _, i) => (frame >= f(wantAt(i)) ? i : found), -1);
  const band = reached < 0 ? null : mix(WANTS[Math.max(reached - 1, 0)].line, WANTS[reached].line, ramp(frame, wantAt(reached), wantAt(reached) + 0.3));
  return (
    <AbsoluteFill>
      <Headlines lines={WANT_LINES} />
      <CodeBlock lines={CODE} {...code} band={band} opacity={fadeAt(frame, 0)} />
      {WANTS.map((want, i) => {
        if (frame < f(wantAt(i) + 0.3)) return null;
        const t = ramp(frame, wantAt(i) + 0.3, wantAt(i) + 0.9);
        const toY = 175 + i * 62;
        return (
          <React.Fragment key={i}>
            <Pill x={mix(code.x + code.w + 10, 760, t)} y={mix(lineMid(code.y, code.line, want.line), toY, t)} text={want.effect} color={ACCENT} left />
            <div
              style={{
                position: 'absolute', left: 980, top: toY, transform: 'translateY(-50%)', fontFamily: SANS, fontSize: 21,
                fontWeight: 700, color: ACCENT, whiteSpace: 'nowrap', opacity: fadeAt(frame, wantAt(i) + 0.9),
              }}
            >
              → {want.means}
            </div>
          </React.Fragment>
        );
      })}
    </AbsoluteFill>
  );
};

// Scene 2 — handlers wrap the program in layers, and an effect travels out to the one that handles it.

const LAYER_LINES: Lines = [
  [0, 'Handlers wrap the program in layers. You choose the stack when you run it.'],
  [4.8, 'An effect travels outward until a layer that handles it catches it.'],
  [9.4, 'Delay reaches the virtual clock. What does that layer get?'],
];
// From the inside out, as in measure/: scheduled(sim_time_handler(printed(fake_web(monitor(...))))).
const LAYERS = [
  {name: 'fake_web', handles: 'Fetch', color: TEST},
  {name: 'printed', handles: 'Log', color: TEST},
  {name: 'virtual clock', handles: 'Delay · the time', color: TEST},
  {name: 'scheduler', handles: 'Spawn · Gather', color: ACCENT},
];
const LAYER = f(14);
const ONION = {cx: 640, cy: 420, w: 480, size: 19, line: 30, margin: 40};
const onionCode = {x: ONION.cx - ONION.w / 2, y: ONION.cy - (PAD * 2 + CODE.length * ONION.line) / 2};

// The rectangle of layer i, the innermost being 0.
const layerRect = (i: number) => {
  const w = ONION.w + 2 * ONION.margin * (i + 1);
  const h = PAD * 2 + CODE.length * ONION.line + 2 * ONION.margin * (i + 1);
  return {x: ONION.cx - w / 2, y: ONION.cy - h / 2, w, h};
};

// One trip of an effect out to a layer: the code line, the layer, the effect, and the seconds it leaves and lands.
type Trip = {line: number; layer: number; effect: string; leave: number; land: number; reply?: {text: string; from: number; to: number}};
const TRIPS: Trip[] = [
  {line: 3, layer: 1, effect: 'Log(...)', leave: 5.4, land: 6.4, reply: {text: 'ok', from: 7.6, to: 8.2}},
  {line: 4, layer: 2, effect: 'Delay(30 * 60)', leave: 9.0, land: 10.0},
];

const LayerScene: React.FC = () => {
  const frame = useSceneFrame();
  const startX = ONION.cx + ONION.w / 2;
  const band = frame < f(4.8) ? null : frame < f(8.4) ? 3 : mix(3, 4, ramp(frame, 8.4, 8.7));
  const working = (i: number) => TRIPS.some((t) => t.layer === i && frame >= f(t.land) && (t.reply ? frame < f(t.reply.from) : true));
  return (
    <AbsoluteFill>
      <Headlines lines={LAYER_LINES} />
      {LAYERS.map((layer, i) => {
        const r = layerRect(i);
        const appear = 0.6 + i * 0.9;
        const on = working(i);
        return (
          <div
            key={layer.name}
            style={{
              position: 'absolute', left: r.x, top: r.y, width: r.w, height: r.h, boxSizing: 'border-box', borderRadius: 22,
              border: `3px solid ${layer.color}`, ...glow(on, layer.color), opacity: fadeAt(frame, appear, 0.5),
              transform: `scale(${mix(1.04, 1, ramp(frame, appear, appear + 0.5))})`,
            }}
          >
            <div style={{position: 'absolute', left: 16, top: 8, fontFamily: SANS, fontSize: 16, fontWeight: 700, color: on ? LOOK : layer.color, whiteSpace: 'nowrap'}}>
              <span style={{fontFamily: MONO}}>{layer.name}</span>
              <span style={{color: MUTED, fontWeight: 400}}>{`  handles ${layer.handles}`}</span>
            </div>
          </div>
        );
      })}
      <CodeBlock lines={CODE} x={onionCode.x} y={onionCode.y} w={ONION.w} size={ONION.size} line={ONION.line} band={band} />
      {TRIPS.map((trip) => {
        const y = lineMid(onionCode.y, ONION.line, trip.line);
        const edge = ONION.cx + layerRect(trip.layer).w / 2;
        const out = frame >= f(trip.leave) && (!trip.reply || frame < f(trip.reply.from));
        const back = trip.reply && frame >= f(trip.reply.from) && frame < f(trip.reply.to + 0.2);
        return (
          <React.Fragment key={trip.effect}>
            {out && <Pill x={mix(startX, edge, ramp(frame, trip.leave, trip.land))} y={y} text={trip.effect} color={ACCENT} />}
            {back && trip.reply && <Pill x={mix(edge, startX, ramp(frame, trip.reply.from, trip.reply.to))} y={y} text={trip.reply.text} color={TEST} />}
          </React.Fragment>
        );
      })}
    </AbsoluteFill>
  );
};

// Scene 3 — with the effect, the handler gets k, the rest of the program, and decides when it runs.

const REST_LINES: Lines = [
  [0, 'With the effect, the handler also gets k: the rest of your program.'],
  [5.0, 'Holding k, it decides when the rest runs: now, later, at 09:30, or never.'],
  [10.5, 'The virtual clock sets the time to 09:30 and resumes k at once. No real wait.'],
];
const REST = f(16);
const CHOICES = ['right away', 'after a real 30-minute wait', 'at 09:30 on a virtual clock', 'never (cancel the rest)'];
const PICKED = 2;

const RestScene: React.FC = () => {
  const frame = useSceneFrame();
  const code = {x: 70, y: 150, w: 560, size: 22, line: 33};
  const box = {x: 700, y: 150, w: 520, h: 255};
  const away = frame >= f(2.4) && frame < f(13.1);
  const restDim = interpolate(frame, [f(2.4), f(2.9), f(12.6), f(13.1)], [1, 0.3, 0.3, 1], clamp);
  const band = frame < f(13.2) ? 4 : frame < f(14.2) ? mix(4, 5, ramp(frame, 13.2, 13.5)) : mix(5, 6, ramp(frame, 14.2, 14.5));
  // The k card: from the last two code lines to the handler's k slot, and back once the clock resumes it.
  const kFrom = {x: code.x + 24, y: code.y + PAD + 5 * code.line};
  const kSlot = {x: box.x + 90, y: box.y + 150};
  const kGo = ramp(frame, 2.4, 3.4);
  const kBack = ramp(frame, 12.3, 13.1);
  const kx = mix(mix(kFrom.x, kSlot.x, kGo), kFrom.x, kBack);
  const ky = mix(mix(kFrom.y, kSlot.y, kGo), kFrom.y, kBack);
  const seconds = 1800 * ramp(frame, 11.0, 12.0);
  const logged = frame >= f(14.6) ? 2 : 1;
  return (
    <AbsoluteFill>
      <Headlines lines={REST_LINES} />
      <CodeBlock lines={CODE} {...code} band={band} dim={(i) => (i >= 5 ? restDim : 1)} />
      <div
        style={{
          position: 'absolute', left: code.x + 6, top: code.y + PAD + 5 * code.line - 2, width: code.w - 12, height: 2 * code.line + 4,
          boxSizing: 'border-box', border: `3px solid ${ACCENT}`, borderRadius: 8, opacity: fadeAt(frame, 0.8),
        }}
      />
      <div style={{position: 'absolute', left: code.x, top: code.y + 270, fontFamily: SANS, fontSize: 22, fontWeight: 700, color: ACCENT, opacity: fadeAt(frame, 0.8)}}>
        k = the rest of monitor()
        <span style={{color: MUTED, fontWeight: 400, marginLeft: 16, opacity: away ? 1 : 0}}>paused</span>
      </div>
      <div
        style={{
          position: 'absolute', left: box.x, top: box.y, width: box.w, height: box.h, boxSizing: 'border-box', borderRadius: 18,
          background: PANEL, border: `3px solid ${TEST}`, opacity: fadeAt(frame, 0.3),
        }}
      >
        <div style={{position: 'absolute', left: 20, top: 12, fontFamily: SANS, fontSize: 24, fontWeight: 700, color: TEST}}>virtual clock</div>
        <div style={{position: 'absolute', left: 20, top: 44, fontFamily: SANS, fontSize: 16, color: MUTED}}>a handler in the test stack</div>
        <div style={{position: 'absolute', left: 20, top: 92, fontFamily: SANS, fontSize: 18, color: MUTED}}>effect</div>
        <div style={{position: 'absolute', left: 20, top: 140, fontFamily: SANS, fontSize: 18, color: MUTED}}>k</div>
        <div style={{position: 'absolute', right: 26, top: 18, transform: 'scale(0.9)', transformOrigin: 'top right'}}>
          <Clock seconds={seconds} c={TEST} secondHand={false} />
          <div style={{fontFamily: MONO, fontSize: 24, color: TEXT, textAlign: 'center', marginTop: 4}}>{`09:${String(Math.floor(seconds / 60)).padStart(2, '0')}`}</div>
        </div>
      </div>
      <Pill x={box.x + 90} y={box.y + 104} text="Delay(30 * 60)" color={ACCENT} left opacity={fadeAt(frame, 1.6) * (frame < f(12.3) ? 1 : 1 - ramp(frame, 12.3, 12.6))} />
      {frame >= f(1.8) && (
        <div
          style={{
            position: 'absolute', left: kx, top: ky, width: 300, boxSizing: 'border-box', padding: '6px 12px', borderRadius: 10,
            background: '#1d1a33', border: `3px solid ${ACCENT}`, boxShadow: `0 0 18px ${ACCENT}88`, fontFamily: MONO, fontSize: 15,
            lineHeight: '24px', color: TEXT, whiteSpace: 'pre', opacity: fadeAt(frame, 1.8) * (frame < f(13.1) ? 1 : 1 - ramp(frame, 13.1, 13.4)),
          }}
        >
          {'page = yield Fetch(url)\nyield Log(f"...")'}
        </div>
      )}
      <div style={{position: 'absolute', left: 70, top: 458, fontFamily: SANS, fontSize: 20, fontWeight: 700, color: TEXT, opacity: fadeAt(frame, 5.2)}}>
        It can resume k ...
      </div>
      {CHOICES.map((choice, i) => {
        const picked = frame >= f(10.6) && i === PICKED;
        const faded = frame >= f(10.6) && i !== PICKED;
        return (
          <div
            key={choice}
            style={{
              position: 'absolute', left: 70 + (i % 2) * 580, top: 492 + Math.floor(i / 2) * 58, width: 560, height: 46, boxSizing: 'border-box',
              borderRadius: 12, border: `2px solid ${ACCENT}`, ...glow(picked, ACCENT), fontFamily: SANS, fontSize: 20, fontWeight: 700,
              color: picked ? LOOK : TEXT, lineHeight: '42px', paddingLeft: 18, opacity: fadeAt(frame, 5.4 + i * 0.6) * (faded ? 0.4 : 1),
            }}
          >
            {choice}
          </div>
        );
      })}
      <div
        style={{
          position: 'absolute', left: 70, top: 616, width: 560, height: 76, boxSizing: 'border-box', padding: '6px 16px', background: '#0b0d13',
          borderRadius: 10, fontFamily: MONO, fontSize: 20, lineHeight: '31px', color: TEXT, opacity: fadeAt(frame, 0.3),
        }}
      >
        {['09:00 9 bytes', '09:30 9 bytes'].slice(0, logged).map((line) => (
          <div key={line}>{line}</div>
        ))}
      </div>
    </AbsoluteFill>
  );
};

// Scene 4 — a scheduler is a layer too: two monitors park their k's and the clock wakes them in time order.

const SCHEDULE_LINES: Lines = [
  [0, 'A scheduler is a layer too. Spawn starts two monitors at once.'],
  [5.0, 'Each Delay parks its k. The virtual clock keeps them in wake-up order.'],
  [10.0, "No task is ready, so the clock jumps to 09:10 and b's k runs."],
  [15.0, 'Then 09:30 for a. The same four lines, in the same order, every run.'],
];
const SCHEDULE = f(21);
const BOTH = [
  '@do',
  'def both():',
  '    a = yield Spawn(monitor("a.example", 30))',
  '    b = yield Spawn(monitor("b.example", 10))',
  '    yield Gather(a, b)',
];
// The four lines of measure/two_monitors.py, with the second each one is printed.
const PRINTS = [
  {at: 3.0, lane: 'a', minute: 0, line: '09:00 a.example 9 bytes'},
  {at: 3.6, lane: 'b', minute: 0, line: '09:00 b.example 10 bytes'},
  {at: 12.7, lane: 'b', minute: 10, line: '09:10 b.example 10 bytes'},
  {at: 17.6, lane: 'a', minute: 30, line: '09:30 a.example 9 bytes'},
];
const LANE_Y = {a: 350, b: 420} as const;
const laneX = (minute: number) => 110 + (540 * minute) / 30;
const SLOT = (s: number) => ({x: 1040, y: 234 + s * 60});

// Where a parked k card is at a frame: keyframes of [second, point], eased between them.
const along = (frame: number, keys: [number, {x: number; y: number}][]) => {
  const times = keys.map(([s]) => f(s));
  return {
    x: interpolate(frame, times, keys.map(([, p]) => p.x), smooth),
    y: interpolate(frame, times, keys.map(([, p]) => p.y), smooth),
  };
};

const ScheduleScene: React.FC = () => {
  const frame = useSceneFrame();
  const band = frame < f(1.0) ? null : frame < f(2.0) ? 2 : frame < f(3.0) ? mix(2, 3, ramp(frame, 2.0, 2.3)) : mix(3, 4, ramp(frame, 3.0, 3.3));
  const seconds = interpolate(frame, [f(10.5), f(11.5), f(15.3), f(16.5)], [0, 600, 600, 1800], smooth);
  const now = seconds / 60;
  const cardA = along(frame, [[5.5, {x: laneX(0), y: LANE_Y.a}], [6.3, SLOT(0)], [6.8, SLOT(0)], [7.6, SLOT(1)], [12.8, SLOT(1)], [13.3, SLOT(0)], [16.7, SLOT(0)], [17.4, {x: laneX(30), y: LANE_Y.a}]]);
  const cardB = along(frame, [[6.8, {x: laneX(0), y: LANE_Y.b}], [7.6, SLOT(0)], [11.8, SLOT(0)], [12.5, {x: laneX(10), y: LANE_Y.b}]]);
  const card = (label: string, p: {x: number; y: number}, show: boolean, opacity: number) =>
    show && (
      <div
        style={{
          position: 'absolute', left: p.x, top: p.y, transform: 'translate(-50%, -50%)', width: 300, height: 46, boxSizing: 'border-box',
          borderRadius: 10, background: '#1d1a33', border: `3px solid ${ACCENT}`, boxShadow: `0 0 16px ${ACCENT}88`, fontFamily: SANS,
          fontSize: 18, fontWeight: 700, color: TEXT, lineHeight: '40px', textAlign: 'center', opacity,
        }}
      >
        {label}
      </div>
    );
  return (
    <AbsoluteFill>
      <Headlines lines={SCHEDULE_LINES} />
      <CodeBlock lines={BOTH} x={60} y={120} w={620} size={18} line={27} band={band} opacity={fadeAt(frame, 0)} />
      <div
        style={{
          position: 'absolute', left: 720, top: 120, width: 500, height: 300, boxSizing: 'border-box', borderRadius: 18, background: PANEL,
          border: `3px solid ${ACCENT}`, opacity: fadeAt(frame, 0.3),
        }}
      >
        <div style={{position: 'absolute', left: 20, top: 12, fontFamily: SANS, fontSize: 22, fontWeight: 700, color: ACCENT}}>scheduler + virtual clock</div>
        <div style={{position: 'absolute', left: 20, top: 56, transform: 'scale(0.8)', transformOrigin: 'top left'}}>
          <Clock seconds={seconds} c={ACCENT} secondHand={false} />
        </div>
        <div style={{position: 'absolute', left: 20, top: 160, width: 96, textAlign: 'center', fontFamily: MONO, fontSize: 22, color: TEXT}}>
          {`09:${String(Math.floor(now)).padStart(2, '0')}`}
        </div>
        <div style={{position: 'absolute', left: 160, top: 56, fontFamily: SANS, fontSize: 16, color: MUTED}}>parked k's, by wake-up time</div>
      </div>
      {(['a', 'b'] as const).map((lane) => (
        <React.Fragment key={lane}>
          <div style={{position: 'absolute', left: 110, top: LANE_Y[lane] - 34, fontFamily: SANS, fontSize: 17, color: MUTED, opacity: fadeAt(frame, 0.3)}}>
            {`monitor ${lane} · waits ${lane === 'a' ? 30 : 10} min`}
          </div>
          <div style={{position: 'absolute', left: 110, top: LANE_Y[lane], width: 540, height: 2, background: '#3a3f55', opacity: fadeAt(frame, 0.3)}} />
        </React.Fragment>
      ))}
      {[0, 10, 20, 30].map((minute) => (
        <div
          key={minute}
          style={{position: 'absolute', left: laneX(minute), top: 445, transform: 'translateX(-50%)', fontFamily: MONO, fontSize: 16, color: MUTED, opacity: fadeAt(frame, 0.3)}}
        >
          {`09:${String(minute).padStart(2, '0')}`}
        </div>
      ))}
      <div style={{position: 'absolute', left: laneX(now) - 1, top: 338, width: 3, height: 100, background: LOOK, boxShadow: `0 0 10px ${LOOK}`, opacity: fadeAt(frame, 1.0)}} />
      {PRINTS.map((print) => (
        <div
          key={print.line}
          style={{
            position: 'absolute', left: laneX(print.minute) - 9, top: LANE_Y[print.lane as 'a' | 'b'] - 8, width: 18, height: 18, borderRadius: 9,
            background: TEST, boxShadow: `0 0 12px ${TEST}`, opacity: fadeAt(frame, print.at),
          }}
        />
      ))}
      {card("k of a · wakes 09:30", cardA, frame >= f(5.5), frame < f(17.4) ? 1 : 1 - ramp(frame, 17.4, 17.7))}
      {card("k of b · wakes 09:10", cardB, frame >= f(6.8), frame < f(12.5) ? 1 : 1 - ramp(frame, 12.5, 12.8))}
      <div
        style={{
          position: 'absolute', left: 720, top: 440, width: 500, height: 148, boxSizing: 'border-box', padding: '10px 16px', background: '#0b0d13',
          borderRadius: 10, fontFamily: MONO, fontSize: 19, lineHeight: '32px', color: TEXT, opacity: fadeAt(frame, 0.3),
        }}
      >
        {PRINTS.filter((print) => frame >= f(print.at)).map((print) => (
          <div key={print.line}>{print.line}</div>
        ))}
      </div>
      <div style={{position: 'absolute', left: 60, top: 520, width: 620, fontFamily: SANS, fontSize: 22, fontWeight: 700, lineHeight: '32px', color: TEST, opacity: fadeAt(frame, 18.4)}}>
        21 test runs: 0.39–2.45 ms each, the same four lines in the same order.
      </div>
    </AbsoluteFill>
  );
};

const RUNS_INTRO: [string, string] = ['Now swap the stack, keep the code.', 'Test handlers on the left, production handlers on the right.'];

// The fast-forward the runs scene shows: the wall clock's 30 minutes over the seconds of video the wait gets.
const shownSpeedup = (slots: Slot[]) => {
  const real = realOf(slots);
  return Math.round(PROD_WAIT.seconds / ((real(PROD_WAIT.to) - real(PROD_WAIT.from)) / FPS));
};

// The scenes in order; Root.tsx registers HOME.length frames of HOME.Video.
export const HOME = narrate('home', DURATIONS, [
  {length: INTRO, starts: startsOf(INTRO_LINES), ids: ['i1', 'i2'], node: <IntroScene />},
  {length: YIELD, starts: startsOf(YIELD_LINES), ids: ['y1', 'y2', 'y3'], node: <YieldScene />},
  {length: WANT, starts: startsOf(WANT_LINES), ids: ['w1', 'w2'], node: <WantScene />},
  {length: LAYER, starts: startsOf(LAYER_LINES), ids: ['l1', 'l2', 'l3'], node: <LayerScene />},
  {length: REST, starts: startsOf(REST_LINES), ids: ['r1', 'r2', 'r3'], node: <RestScene />},
  {length: SCHEDULE, starts: startsOf(SCHEDULE_LINES), ids: ['s1', 's2', 's3', 's4'], node: <ScheduleScene />},
  {
    length: RUNS, starts: RUN_HEADLINE_STARTS, ids: ['n1', 'n2', 'n3', 'n4', 'n5', 'n6', 'n7', 'n8', 'n9'], node: <Runs intro={RUNS_INTRO} />,
    wrap: (slots, node) => <SpeedupContext.Provider value={shownSpeedup(slots)}>{node}</SpeedupContext.Provider>,
  },
  {length: OUTRO, starts: [0], ids: ['o1'], node: <Outro />},
]);
