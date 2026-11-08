// The video at the top of the article "Yield and async" (docs/articles/yield-and-async.md): where the request of
// paused code goes. At an await it goes up to the one event loop at the top; at a yield it goes to the nearest handler
// around it, and each caller installs its own (ada, grace, titled, closed, scheduled around the same greet). Every
// code line on screen is a line of examples/yield_and_async (check_code.py checks it); every result is a line of
// measure/opening.py's output.
import React from 'react';
import {AbsoluteFill} from 'remotion';
import {fadeAt, ramp} from '../../kit/motion';
import {narrate} from '../../kit/narrated';
import {CodeBlock, Headlines, Lines, lineMid, PAD, startsOf} from '../../kit/parts';
import {ACCENT, f, FAIL, LOOK, MONO, MUTED, PANEL, PROD, SANS, TEST, TEXT} from '../../kit/theme';
import {useSceneFrame} from '../../kit/warp';
import {Geometry, K, Moving, Ring, Tower} from '../article-opening/ArticleOpening';
import DURATIONS from './durations.json';
import NARRATION from './narration.json';

// A code box and where its lines are, for lighting them and sending pills from them.
export type Box = {x: number; y: number; w: number; line: number};

// Lights line i of a code box from `from` until `until`, in the "look here" colour or another.
export const Lit: React.FC<{box: Box; i: number; from: number; until?: number; color?: string; strength?: number}> = ({box, i, from, until = Infinity, color = LOOK, strength = 0.22}) => {
  const frame = useSceneFrame();
  if (frame < f(from) || frame >= f(until)) return null;
  return (
    <div
      style={{
        position: 'absolute', left: box.x + 2, top: box.y + PAD + i * box.line, width: box.w - 4, height: box.line, background: color,
        opacity: strength * fadeAt(frame, from, 0.25),
      }}
    />
  );
};

// The vertical middle of line i of a code box.
export const mid = (box: Box, i: number) => lineMid(box.y, box.line, i);

// A label above a box.
export const Label: React.FC<{x: number; y: number; text: string; color: string; from: number; opacity?: number; mono?: boolean}> = ({x, y, text, color, from, opacity = 1, mono = false}) => {
  const frame = useSceneFrame();
  return (
    <div style={{position: 'absolute', left: x, top: y, fontFamily: mono ? MONO : SANS, fontSize: 19, fontWeight: 700, color, whiteSpace: 'nowrap', opacity: opacity * fadeAt(frame, from)}}>
      {text}
    </div>
  );
};

// A line of text that fades in: a caption, a call and its result.
export const Say: React.FC<{x: number; y: number; from: number; children: React.ReactNode; size?: number; color?: string; mono?: boolean; w?: number; center?: boolean; until?: number}> = ({
  x, y, from, children, size = 20, color = TEXT, mono = false, w, center = false, until = Infinity,
}) => {
  const frame = useSceneFrame();
  if (frame >= f(until)) return null;
  return (
    <div
      style={{
        position: 'absolute', left: x, top: y, width: w, textAlign: center ? 'center' : 'left', fontFamily: mono ? MONO : SANS, fontSize: size,
        color, whiteSpace: 'pre', lineHeight: 1.35, opacity: fadeAt(frame, from),
      }}
    >
      {children}
    </div>
  );
};

// A call and what it returned, as measure/opening.py printed it.
export const Result: React.FC<{x: number; y: number; from: number; call: string; value: string; color?: string; size?: number}> = ({x, y, from, call, value, color = LOOK, size = 18}) => (
  <Say x={x} y={y} from={from} mono size={size}>
    <span style={{color: MUTED}}>{call}</span>
    {'\n'}
    <span style={{color}}>{'-> '}{value}</span>
  </Say>
);

// A box drawn around part of a program: a handler a caller installed, or the program itself.
export const Frame: React.FC<{l: number; t: number; r: number; b: number; label: string; color: string; from: number; glow?: boolean; fill?: string; mono?: boolean}> = ({
  l, t, r, b, label, color, from, glow = false, fill = 'transparent', mono = true,
}) => {
  const frame = useSceneFrame();
  return (
    <div
      style={{
        position: 'absolute', left: l, top: t, width: r - l, height: b - t, boxSizing: 'border-box', borderRadius: 14, border: `3px solid ${glow ? LOOK : color}`,
        background: fill, boxShadow: glow ? `0 0 22px ${LOOK}aa` : 'none', opacity: fadeAt(frame, from),
      }}
    >
      <div style={{position: 'absolute', left: 12, top: 6, fontFamily: mono ? MONO : SANS, fontSize: 18, fontWeight: 700, color: glow ? LOOK : color, whiteSpace: 'nowrap'}}>{label}</div>
    </div>
  );
};

// 0. What the video is about, said over the title and the function it follows, shown still from the first frame.
const INTRO_CODE = ['@do', 'def greet():', '    name = yield ReadName()', '    return f"Hello, {name}"'];
const INTRO_BOX: Box = {x: 340, y: 330, w: 600, line: 30};
const INTRO_LINES: Lines = [[0, '']];
const INTRO = f(10.0);
const Intro: React.FC = () => (
  <AbsoluteFill>
    <Say x={0} w={1280} y={92} from={-1} size={60} center>Yield and async</Say>
    <Say x={0} w={1280} y={186} from={-1} size={24} color={MUTED} center>doeff: algebraic effects for Python</Say>
    <Label x={INTRO_BOX.x} y={INTRO_BOX.y - 34} text="greet: reads a name and returns a greeting" color={ACCENT} from={-1} />
    <CodeBlock lines={INTRO_CODE} x={INTRO_BOX.x} y={INTRO_BOX.y} w={INTRO_BOX.w} size={20} line={INTRO_BOX.line} />
  </AbsoluteFill>
);

// 1-3. The same greeting written twice, with async def and await (left) and with @do and yield (right).
const ASYNC_CODE = [
  'async def read_name_async() -> str:',
  '    await asyncio.sleep(0)',
  '    return "Ada"',
  ' ',
  'async def greet_async() -> str:',
  '    name = await read_name_async()',
  '    return f"Hello, {name}"',
];
const YIELD_CODE = [
  '@dataclass(frozen=True)',
  'class ReadName(EffectBase):',
  '    pass',
  ' ',
  '@do',
  'def greet():',
  '    name = yield ReadName()',
  '    return f"Hello, {name}"',
];
const ADA_CODE = ['@handler', 'def ada(effect: ReadName, k):', '    return (yield Resume(k, "Ada"))'];
const LEFT: Box = {x: 50, y: 116, w: 570, line: 28};
const RIGHT: Box = {x: 660, y: 116, w: 570, line: 28};
const HANDLER: Box = {x: 660, y: 452, w: 570, line: 28};

const Pair: React.FC<{left?: number; right?: number}> = ({left = 1, right = 1}) => {
  const frame = useSceneFrame();
  return (
    <>
      <Label x={LEFT.x} y={LEFT.y - 32} text="async def + await" color={PROD} from={0.1} opacity={left} />
      <CodeBlock lines={ASYNC_CODE} x={LEFT.x} y={LEFT.y} w={LEFT.w} size={18} line={LEFT.line} opacity={fadeAt(frame, 0.1) * left} />
      <Label x={RIGHT.x} y={RIGHT.y - 32} text="@do + yield" color={ACCENT} from={0.1} opacity={right} />
      <CodeBlock lines={YIELD_CODE} x={RIGHT.x} y={RIGHT.y} w={RIGHT.w} size={18} line={RIGHT.line} opacity={fadeAt(frame, 0.1) * right} />
    </>
  );
};

const BOTH_LINES: Lines = [[0, 'Two functions that pause: one with await, one with yield']];
const BOTH = f(9.6);
const Both: React.FC = () => (
  <AbsoluteFill>
    <Headlines lines={BOTH_LINES} />
    <Pair />
    <Lit box={LEFT} i={5} from={2.0} until={3.6} />
    <Lit box={RIGHT} i={6} from={3.6} until={5.4} />
    <Result x={LEFT.x + 10} y={380} from={5.6} call="asyncio.run(greet_async())" value="Hello, Ada" />
    <Result x={RIGHT.x + 10} y={408} from={6.2} call="run(ada(greet()))" value="Hello, Ada" />
    <Say x={0} w={1280} y={520} from={7.6} size={44} color={LOOK} center>So why yield?</Say>
  </AbsoluteFill>
);

// 2. At an await, the call names the code that answers: it runs right there, in the same chain of calls.
const AWAIT_LINES: Lines = [[0, 'await: the call names the code that answers']];
const AWAIT = f(8.6);
const Await: React.FC = () => {
  const frame = useSceneFrame();
  const chain = (from: number, text: string, i: number) => (
    <div
      style={{
        position: 'absolute', left: LEFT.x + 10 + i * 230, top: 470, padding: '8px 16px', borderRadius: 10, border: `2px solid ${PROD}`, background: PANEL,
        fontFamily: MONO, fontSize: 18, color: TEXT, opacity: fadeAt(frame, from),
      }}
    >
      {text}
    </div>
  );
  return (
    <AbsoluteFill>
      <Headlines lines={AWAIT_LINES} />
      <Pair right={0.25} />
      <Lit box={LEFT} i={5} from={0.4} />
      <Moving text="read_name_async()" color={PROD} from={1.4} to={2.6} until={3.0} x0={LEFT.x + 420} y0={mid(LEFT, 5)} x1={LEFT.x + 420} y1={mid(LEFT, 0)} />
      <Lit box={LEFT} i={1} from={2.8} until={5.4} />
      <Lit box={LEFT} i={2} from={3.4} until={5.4} />
      <Moving text={'"Ada"'} color={TEST} from={4.4} to={5.4} until={6.4} x0={LEFT.x + 300} y0={mid(LEFT, 2)} x1={LEFT.x + 300} y1={mid(LEFT, 5)} />
      <Say x={LEFT.x + 10} y={380} from={2.8} size={21}>the answer is code, and it runs right here</Say>
      <Say x={LEFT.x + 10} y={430} from={5.0} size={18} color={MUTED}>one chain of calls:</Say>
      {chain(5.2, 'greet_async', 0)}
      <Say x={LEFT.x + 196} y={474} from={5.6} size={22} color={MUTED}>{'->'}</Say>
      {chain(5.6, 'read_name_async', 1)}
    </AbsoluteFill>
  );
};

// 3. At a yield, the call names an operation; greet pauses and the nearest handler around it answers.
const YIELD_LINES: Lines = [[0, 'yield: the call names an operation, and a handler answers']];
const YIELD = f(10.6);
const Yield: React.FC = () => {
  const frame = useSceneFrame();
  const kTop = RIGHT.y + PAD + 7 * RIGHT.line;
  return (
    <AbsoluteFill>
      <Headlines lines={YIELD_LINES} />
      <Pair left={0.25} />
      <Lit box={RIGHT} i={6} from={0.4} />
      {/* k: what greet has left to do, once it is paused at the yield. */}
      <div style={{position: 'absolute', left: RIGHT.x + 4, top: kTop, width: RIGHT.w - 8, height: RIGHT.line, borderRadius: 8, background: K, opacity: 0.2 * fadeAt(frame, 5.6, 0.4)}} />
      <Say x={LEFT.x + 10} y={392} from={2.6} size={24}>
        <span style={{fontFamily: MONO, color: ACCENT, fontWeight: 700}}>ReadName()</span> is a value,{'\n'}not code: it says what, not how
      </Say>
      <Say x={LEFT.x + 10} y={492} from={5.6} size={20} color={K}>k: the rest of greet, paused</Say>
      <Label x={HANDLER.x} y={HANDLER.y - 32} text="the nearest handler around greet" color={TEST} from={6.6} />
      <CodeBlock lines={ADA_CODE} x={HANDLER.x} y={HANDLER.y} w={HANDLER.w} size={18} line={HANDLER.line} opacity={fadeAt(frame, 6.6)} />
      <Moving text="ReadName()" color={ACCENT} from={5.4} to={6.6} until={8.0} x0={RIGHT.x + 420} y0={mid(RIGHT, 6)} x1={HANDLER.x + 420} y1={mid(HANDLER, 1)} />
      <Moving text="k" color={K} from={5.8} to={7.0} until={8.0} x0={RIGHT.x + 500} y0={mid(RIGHT, 7)} x1={HANDLER.x + 520} y1={mid(HANDLER, 1)} />
      <Lit box={HANDLER} i={2} from={7.6} until={9.2} color={TEST} />
      <Moving text={'Resume(k, "Ada")'} color={TEST} from={8.0} to={9.0} until={9.6} x0={HANDLER.x + 330} y0={mid(HANDLER, 2)} x1={RIGHT.x + 420} y1={mid(RIGHT, 6)} />
      <Lit box={RIGHT} i={7} from={9.2} />
    </AbsoluteFill>
  );
};

// 4. In asyncio, every request goes to the one receiver at the top; the coroutines in between pass it through.
const LOOP_LINES: Lines = [[0, 'asyncio: every request goes up to one place, the event loop']];
const LOOP = f(8.8);
const LOOP_G: Geometry = {cx: 360, cy: 410, bw: 270, bh: 100, gap: 62, font: 18};
const LOOP_RINGS: Ring[] = [{label: 'twice_async', color: MUTED}, {label: 'asyncio.run: the event loop', color: PROD}];
// The top border of ring i of a tower.
const ringTop = (g: Geometry, i: number) => g.cy - (g.bh + 2 * g.gap * (i + 1)) / 2;
const Loop: React.FC = () => {
  const frame = useSceneFrame();
  const x = LOOP_G.cx + 150;
  return (
    <AbsoluteFill>
      <Headlines lines={LOOP_LINES} />
      <Tower rings={LOOP_RINGS} g={LOOP_G} opacity={fadeAt(frame, 0.1)} title="greet_async()" />
      <Moving text={'Ask("name")'} color={ACCENT} from={1.6} to={3.4} x0={x} y0={LOOP_G.cy - LOOP_G.bh / 2} x1={x} y1={ringTop(LOOP_G, 1) + 14} />
      <Say x={LOOP_G.cx - (LOOP_G.bw + 2 * LOOP_G.gap) / 2 + 14} y={ringTop(LOOP_G, 0) + 42} from={5.8} size={17} color={LOOK}>passes it through</Say>
      <div
        style={{
          position: 'absolute', left: 740, top: 170, width: 490, height: 400, boxSizing: 'border-box', padding: '18px 24px', background: PANEL, borderRadius: 14,
          border: `2px solid ${PROD}`, opacity: fadeAt(frame, 3.6),
        }}
      >
        <div style={{fontFamily: SANS, fontSize: 19, fontWeight: 700, color: MUTED}}>who answers? whoever calls send()</div>
      </div>
      <Result x={764} y={240} from={4.2} call={'drive(twice_async(), {"name": "Ada"})'} value="('Hello, Ada', 'Hello, Ada')" />
      <Result x={764} y={340} from={5.4} call="asyncio.run(twice_async())" value="RuntimeError: Task got bad yield" color={FAIL} />
      <Say x={764} y={440} from={6.6} size={19} color={TEXT} w={440}>
        {'one receiver at the top: the loop\nanswers Futures, and nothing else'}
      </Say>
    </AbsoluteFill>
  );
};

// 5. In doeff, each caller installs handlers around any part of a program; titled in between changes the answer.
const NEST_LINES: Lines = [[0, 'doeff: each caller installs handlers around any part']];
const NEST = f(12.2);
const G1 = {l: 175, t: 340, r: 375, b: 420};
const GRACE = {l: 405, t: 300, r: 640, b: 450};
const G2 = {l: 425, t: 350, r: 620, b: 420};
const Greet: React.FC<{box: {l: number; t: number; r: number; b: number}; from: number}> = ({box, from}) => {
  const frame = useSceneFrame();
  return (
    <div
      style={{
        position: 'absolute', left: box.l, top: box.t, width: box.r - box.l, height: box.b - box.t, boxSizing: 'border-box', borderRadius: 10, border: `3px solid ${ACCENT}`,
        background: '#0b0d13', display: 'flex', alignItems: 'center', justifyContent: 'center', fontFamily: MONO, fontSize: 22, fontWeight: 700, color: TEXT,
        opacity: fadeAt(frame, from),
      }}
    >
      greet()
    </div>
  );
};
const Nest: React.FC = () => {
  const frame = useSceneFrame();
  const titled = frame >= f(4.6);
  const x1 = (G1.l + G1.r) / 2;
  const x2 = (G2.l + G2.r) / 2;
  return (
    <AbsoluteFill>
      <Headlines lines={NEST_LINES} />
      <Frame l={85} t={124} r={725} b={610} label="ada" color={TEST} from={0.2} />
      <Frame l={115} t={168} r={695} b={576} label="titled" color={TEST} from={4.6} glow={titled && frame < f(9.0)} />
      <Frame l={145} t={212} r={665} b={540} label="twice()" color={ACCENT} from={0.2} fill="#11141d" />
      <Greet box={G1} from={0.3} />
      <Frame l={GRACE.l} t={GRACE.t} r={GRACE.r} b={GRACE.b} label="grace" color={TEST} from={1.2} glow={frame >= f(1.2) && frame < f(2.6)} />
      <Greet box={G2} from={0.3} />
      {/* Without titled: the first ReadName goes out to ada, the second stops at grace. */}
      <Moving text="ReadName()" color={ACCENT} from={2.2} to={3.0} until={3.6} x0={x1} y0={G1.t} x1={x1} y1={124} />
      <Moving text="ReadName()" color={ACCENT} from={2.4} to={3.0} until={3.6} x0={x2} y0={G2.t} x1={x2} y1={GRACE.t} />
      {/* With titled: the first stops at titled, which asks ada, and answers Dr. Ada. */}
      <Moving text="ReadName()" color={ACCENT} from={5.8} to={6.5} until={6.9} x0={x1} y0={G1.t} x1={x1} y1={168} />
      <Moving text="ReadName()" color={ACCENT} from={6.9} to={7.4} until={7.7} x0={x1} y0={168} x1={x1} y1={124} />
      <Moving text={'"Ada"'} color={TEST} from={7.7} to={8.1} until={8.4} x0={x1} y0={124} x1={x1} y1={168} />
      <Moving text={'"Dr. Ada"'} color={TEST} from={8.4} to={9.0} until={9.5} x0={x1} y0={168} x1={x1} y1={G1.t} />
      <div
        style={{
          position: 'absolute', left: 760, top: 150, width: 470, height: 400, boxSizing: 'border-box', padding: '18px 24px', background: PANEL, borderRadius: 14,
          border: `2px solid ${TEST}`, opacity: fadeAt(frame, 0.4),
        }}
      >
        <div style={{fontFamily: SANS, fontSize: 19, fontWeight: 700, color: MUTED}}>run(...)</div>
      </div>
      <Result x={784} y={210} from={3.4} call="ada(twice())" value="('Hello, Ada', 'Hello, Grace')" color={TEXT} />
      <Result x={784} y={310} from={9.0} call="ada(titled(twice()))" value="('Hello, Dr. Ada', 'Hello, Grace')" />
      <Say x={784} y={430} from={10.0} size={22} color={LOOK}>greet: 0 lines changed</Say>
    </AbsoluteFill>
  );
};

// 6. A handler holds k: it resumes it, keeps it to run tasks, or ends the run without it.
const HOLD_LINES: Lines = [[0, 'A handler holds k: it resumes it, keeps it, or ends the run without it']];
const HOLD = f(8.8);
const CARDS: {x: number; name: string; code: string; how: string; value: string; from: number; drop?: boolean}[] = [
  {x: 50, name: 'ada', code: 'yield Resume(k, "Ada")', how: 'resumes k now', value: 'Hello, Ada', from: 3.0},
  {x: 450, name: 'scheduled(ada(...))', code: 'yield Spawn(greet())', how: 'keeps the k of each task', value: "['Hello, Ada', 'Hello, Grace']", from: 4.4},
  {x: 850, name: 'closed', code: 'return "closed today"', how: 'never resumes k', value: 'closed today', from: 5.8, drop: true},
];
const Hold: React.FC = () => {
  const frame = useSceneFrame();
  return (
    <AbsoluteFill>
      <Headlines lines={HOLD_LINES} />
      <div
        style={{
          position: 'absolute', left: 390, top: 100, width: 500, height: 92, boxSizing: 'border-box', borderRadius: 12, border: `3px solid ${ACCENT}`, background: '#0b0d13',
          display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', opacity: fadeAt(frame, 0.1),
        }}
      >
        <div style={{fontFamily: MONO, fontSize: 20, color: TEXT}}>
          greet, paused at <span style={{color: ACCENT, fontWeight: 700}}>yield ReadName()</span>
        </div>
        <div style={{fontFamily: SANS, fontSize: 18, color: K, marginTop: 4, opacity: fadeAt(frame, 1.0)}}>k = the rest of greet</div>
      </div>
      {CARDS.map(({x, name, code, how, value, from, drop}, i) => {
        const on = frame >= f(from);
        return (
          <React.Fragment key={name}>
            <div
              style={{
                position: 'absolute', left: x, top: 250, width: 380, height: 300, boxSizing: 'border-box', padding: '16px 20px', borderRadius: 14, background: PANEL,
                border: `3px solid ${on ? LOOK : TEST}`, boxShadow: on && frame < f(from + 1.4) ? `0 0 22px ${LOOK}aa` : 'none', opacity: fadeAt(frame, 1.6 + i * 0.2),
              }}
            >
              <div style={{fontFamily: MONO, fontSize: 21, fontWeight: 700, color: TEST}}>{name}</div>
              <div style={{fontFamily: MONO, fontSize: 17, color: MUTED, marginTop: 10}}>{code}</div>
              <div style={{fontFamily: SANS, fontSize: 20, color: TEXT, marginTop: 70, opacity: fadeAt(frame, from)}}>{how}</div>
              <div style={{fontFamily: MONO, fontSize: 16, color: LOOK, marginTop: 14, opacity: fadeAt(frame, from + 0.6)}}>{'-> '}{value}</div>
            </div>
            <Moving
              text="k" color={drop && frame >= f(from + 0.6) ? '#3a3f55' : K} from={1.8 + i * 0.2} to={2.8 + i * 0.2} until={drop ? from + 1.4 : Infinity}
              x0={640} y0={196} x1={x + 190} y1={370}
            />
          </React.Fragment>
        );
      })}
    </AbsoluteFill>
  );
};

// 7. Async libraries still work: Await hands one coroutine to a handler, and the loop runs behind that handler.
const BRIDGE_LINES: Lines = [[0, 'Async libraries: the event loop moves behind a handler']];
const BRIDGE = f(9.6);
const BRIDGE_G: Geometry = {cx: 330, cy: 400, bw: 250, bh: 96, gap: 44, font: 16};
const BRIDGE_RINGS: Ring[] = [{label: 'listen_handler'}, {label: 'await_handler', glow: true}, {label: 'scheduled'}];
// The right border of ring i of a tower.
const ringRight = (g: Geometry, i: number) => g.cx + (g.bw + 2 * g.gap * (i + 1)) / 2;
// The chunks of tokens() as collect reads them, one Await each; None is the end.
const CHUNKS = ['"Hello"', '","', '""', '" world"', 'None'];
const TEXTS = ['Hello', 'Hello,', 'Hello,', 'Hello, world', 'Hello, world'];
const Bridge: React.FC = () => {
  const frame = useSceneFrame();
  const out = ringRight(BRIDGE_G, 1);
  const loop = {x: 680, y: 300, w: 270, h: 200};
  const trips = CHUNKS.map((chunk, n) => ({chunk, from: 2.6 + n * 0.9}));
  const seen = trips.filter(({from}) => frame >= f(from + 0.3)).length;
  const back = trips.filter(({from}) => frame >= f(from + 0.8)).length;
  return (
    <AbsoluteFill>
      <Headlines lines={BRIDGE_LINES} />
      <Tower rings={BRIDGE_RINGS} g={BRIDGE_G} opacity={fadeAt(frame, 0.1)} title="collect(...)" />
      <div
        style={{
          position: 'absolute', left: loop.x, top: loop.y, width: loop.w, height: loop.h, boxSizing: 'border-box', borderRadius: 14, border: `3px dashed ${PROD}`,
          background: PANEL, padding: '14px 18px', opacity: fadeAt(frame, 1.2),
        }}
      >
        <div style={{fontFamily: SANS, fontSize: 18, fontWeight: 700, color: PROD}}>an asyncio loop</div>
        <div style={{fontFamily: SANS, fontSize: 16, color: MUTED, marginTop: 6}}>in the background</div>
        <div style={{fontFamily: MONO, fontSize: 16, color: TEXT, marginTop: 30}}>tokens()</div>
      </div>
      <div style={{position: 'absolute', left: out, top: BRIDGE_G.cy - 1, width: loop.x - out, height: 0, borderTop: `3px dashed ${PROD}`, opacity: fadeAt(frame, 1.2)}} />
      {trips.map(({chunk, from}) => (
        <React.Fragment key={from}>
          <Moving text="Await" color={ACCENT} from={from} to={from + 0.4} until={from + 0.45} x0={BRIDGE_G.cx + BRIDGE_G.bw / 2 - 20} y0={BRIDGE_G.cy - 30} x1={loop.x + 40} y1={BRIDGE_G.cy - 30} />
          <Moving text={chunk} color={TEST} from={from + 0.45} to={from + 0.8} until={from + 0.85} x0={loop.x + 40} y0={BRIDGE_G.cy + 30} x1={BRIDGE_G.cx + BRIDGE_G.bw / 2 - 20} y1={BRIDGE_G.cy + 30} />
        </React.Fragment>
      ))}
      <div
        style={{
          position: 'absolute', left: 980, top: 300, width: 250, height: 200, boxSizing: 'border-box', padding: '14px 18px', background: PANEL, borderRadius: 14,
          border: `2px solid ${TEST}`, opacity: fadeAt(frame, 1.6),
        }}
      >
        <div style={{fontFamily: SANS, fontSize: 17, fontWeight: 700, color: MUTED}}>Awaits Listen saw</div>
        <div style={{fontFamily: MONO, fontSize: 40, fontWeight: 700, color: LOOK, marginTop: 4}}>{seen}</div>
        <div style={{fontFamily: SANS, fontSize: 17, fontWeight: 700, color: MUTED, marginTop: 10}}>text</div>
        <div style={{fontFamily: MONO, fontSize: 20, color: TEXT, marginTop: 4}}>{back ? `'${TEXTS[back - 1]}'` : "''"}</div>
      </div>
      <Say x={690} y={540} from={7.6} size={20} color={LOOK} w={540}>{'the loop is behind await_handler,\nnot above the program'}</Say>
    </AbsoluteFill>
  );
};

// 8. async and await: one handler, fixed at the top. doeff: every caller installs their own.
const CLOSE_LINES: Lines = [[0, 'One handler fixed at the top, or a handler from every caller']];
const CLOSE = f(10.0);
const TINY = (cx: number): Geometry => ({cx, cy: 300, bw: 96, bh: 54, gap: 19, font: 13});
const OWN: Ring[][] = [[{label: 'ada'}], [{label: 'grace'}], [{label: 'titled'}, {label: 'ada'}], [{label: 'closed'}], [{label: 'ada'}, {label: 'scheduled'}]];
const Close: React.FC = () => {
  const frame = useSceneFrame();
  return (
    <AbsoluteFill>
      <Headlines lines={CLOSE_LINES} />
      <Tower rings={[{label: 'the event loop', color: PROD}]} g={{cx: 190, cy: 300, bw: 150, bh: 60, gap: 22, font: 13}} opacity={fadeAt(frame, 0.3)} title="async" />
      <Say x={40} w={300} y={400} from={0.3} size={18} color={MUTED} center>one handler, at the top</Say>
      <div style={{position: 'absolute', left: 372, top: 170, width: 0, height: 270, borderLeft: '2px solid #2a2e40', opacity: fadeAt(frame, 4.6)}} />
      {OWN.map((rings, i) => (
        <Tower key={i} rings={rings} g={TINY(480 + i * 176)} opacity={fadeAt(frame, 4.8 + i * 0.35)} title="greet()" />
      ))}
      <Say x={400} w={840} y={400} from={5.0} size={18} color={MUTED} center>a handler from each caller</Say>
      <div style={{position: 'absolute', left: 0, right: 0, top: 470, textAlign: 'center', opacity: fadeAt(frame, 7.8)}}>
        <div style={{fontFamily: SANS, fontSize: 60, fontWeight: 700, color: TEXT}}>doeff</div>
        <div style={{fontFamily: MONO, fontSize: 24, color: TEST, marginTop: 8}}>pip install doeff</div>
      </div>
    </AbsoluteFill>
  );
};

export const ARTICLE2_OPENING = narrate('article2-opening', DURATIONS, NARRATION, [
  {length: INTRO, starts: startsOf(INTRO_LINES), ids: ['o0'], node: <Intro />},
  {length: BOTH, starts: startsOf(BOTH_LINES), ids: ['o1'], node: <Both />},
  {length: AWAIT, starts: startsOf(AWAIT_LINES), ids: ['o2'], node: <Await />},
  {length: YIELD, starts: startsOf(YIELD_LINES), ids: ['o3'], node: <Yield />},
  {length: LOOP, starts: startsOf(LOOP_LINES), ids: ['o4'], node: <Loop />},
  {length: NEST, starts: startsOf(NEST_LINES), ids: ['o5'], node: <Nest />},
  {length: HOLD, starts: startsOf(HOLD_LINES), ids: ['o6'], node: <Hold />},
  {length: BRIDGE, starts: startsOf(BRIDGE_LINES), ids: ['o7'], node: <Bridge />},
  {length: CLOSE, starts: startsOf(CLOSE_LINES), ids: ['o8'], node: <Close />},
]);
