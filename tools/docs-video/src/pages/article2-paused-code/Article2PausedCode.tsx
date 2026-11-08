// The video at the head of the section "How the paused code goes on" of the article "Yield and async"
// (docs/articles/yield-and-async.md): what can happen to code paused at an await or a yield. asyncio's loop has two
// moves; a doeff handler holds k and decides: resume it, throw into it, keep it for later, or never resume it. Neither
// goes on twice, and a program is not a paused run. Every code line on screen is a line of examples/yield_and_async or
// of measure/paused.py (check_code.py checks it); every result and order is a line of measure/paused.py's output.
import React from 'react';
import {AbsoluteFill} from 'remotion';
import {fadeAt} from '../../kit/motion';
import {narrate} from '../../kit/narrated';
import {CodeBlock, Headlines, Lines, PAD, Pill, startsOf} from '../../kit/parts';
import {ACCENT, f, FAIL, LOOK, MONO, MUTED, PANEL, PROD, SANS, TEST, TEXT} from '../../kit/theme';
import {useSceneFrame} from '../../kit/warp';
import {K, Moving} from '../article-opening/ArticleOpening';
import {Box, Label, Lit, mid, Result, Say} from '../article2-opening/Article2Opening';
import DURATIONS from './durations.json';
import NARRATION from './narration.json';

// What a handler can do with k, the rows of the article's table; the scene at work lights its row.
const MOVES: [string, string][] = [
  ['Resume(k, value)', 'go on with a value'],
  ['ResumeThrow(k, error)', 'see an exception at the yield'],
  ['keep k', 'go on later, after other code'],
  ['return value', 'never go on'],
  ['a second Resume', 'no: k is one-shot'],
];
const Moves: React.FC<{lit?: number; from?: number; stagger?: number}> = ({lit = -1, from = 0.1, stagger = 0}) => {
  const frame = useSceneFrame();
  return (
    <div
      style={{
        position: 'absolute', left: 890, top: 100, width: 350, boxSizing: 'border-box', padding: '14px 18px', background: PANEL, borderRadius: 14,
        border: `2px solid ${TEST}`, opacity: fadeAt(frame, from),
      }}
    >
      <div style={{fontFamily: SANS, fontSize: 18, fontWeight: 700, color: MUTED, marginBottom: 8}}>what the handler does with k</div>
      {MOVES.map(([code, what], i) => (
        <div
          key={code}
          style={{
            padding: '8px 12px', marginTop: 6, borderRadius: 10, border: `2px solid ${i === lit ? LOOK : 'transparent'}`,
            boxShadow: i === lit ? `0 0 16px ${LOOK}88` : 'none', opacity: (lit < 0 || i === lit ? 1 : 0.4) * fadeAt(frame, from + stagger * (i + 1)),
          }}
        >
          <div style={{fontFamily: MONO, fontSize: 18, fontWeight: 700, color: i === 4 ? FAIL : TEST}}>{code}</div>
          <div style={{fontFamily: SANS, fontSize: 16, color: TEXT}}>{what}</div>
        </div>
      ))}
    </div>
  );
};

// 1. asyncio: a paused task, and the two moves of the loop.
const LOOP_LINES: Lines = [[0, "asyncio: the loop has two moves on a paused task"]];
const LOOP = f(10.4);
const WAITING_CODE = [
  'async def waiting(future: asyncio.Future) -> str:',
  '    try:',
  '        return f"got {await future}"',
  '    except asyncio.CancelledError:',
  '        return "CancelledError at the await"',
];
const WAITING: Box = {x: 50, y: 116, w: 620, line: 30};
const LoopMoves: React.FC = () => {
  const frame = useSceneFrame();
  const chip = (y: number, text: string, color: string, from: number, on: number) => (
    <div
      style={{
        position: 'absolute', left: 760, top: y, padding: '8px 18px', borderRadius: 12, border: `3px solid ${frame >= f(on) && frame < f(on + 1.6) ? LOOK : color}`,
        background: '#0b0d13', fontFamily: MONO, fontSize: 20, fontWeight: 700, color, opacity: fadeAt(frame, from),
      }}
    >
      {text}
    </div>
  );
  return (
    <AbsoluteFill>
      <Headlines lines={LOOP_LINES} />
      <Label x={WAITING.x} y={WAITING.y - 32} text="measure/paused.py: a task that waits" color={PROD} from={0.1} />
      <CodeBlock lines={WAITING_CODE} x={WAITING.x} y={WAITING.y} w={WAITING.w} size={18} line={WAITING.line} opacity={fadeAt(frame, 0.1)} />
      <Lit box={WAITING} i={2} from={1.0} until={4.4} />
      <Say x={WAITING.x + 10} y={300} from={1.2} size={19} color={MUTED} until={4.4}>paused at the await</Say>
      <div
        style={{
          position: 'absolute', left: 720, top: 116, width: 510, height: 220, boxSizing: 'border-box', padding: '14px 20px', borderRadius: 14, border: `3px solid ${PROD}`,
          background: PANEL, opacity: fadeAt(frame, 2.0),
        }}
      >
        <div style={{fontFamily: SANS, fontSize: 19, fontWeight: 700, color: PROD}}>the event loop</div>
      </div>
      {chip(176, 'send(None)', TEST, 3.0, 4.0)}
      {chip(250, 'throw(CancelledError)', FAIL, 3.4, 6.8)}
      <Moving text="send(None)" color={TEST} from={4.4} to={5.4} until={5.8} x0={860} y0={196} x1={WAITING.x + 420} y1={mid(WAITING, 2)} />
      <Lit box={WAITING} i={2} from={5.4} until={6.8} color={TEST} />
      <Result x={WAITING.x + 10} y={380} from={5.8} call={'the future is done: set_result("Ada")'} value="'got Ada'" color={TEST} />
      <Moving text="CancelledError" color={FAIL} from={7.0} to={8.0} until={8.4} x0={900} y0={270} x1={WAITING.x + 420} y1={mid(WAITING, 2)} />
      <Lit box={WAITING} i={3} from={8.0} color={FAIL} />
      <Lit box={WAITING} i={4} from={8.4} color={FAIL} />
      <Result x={WAITING.x + 10} y={470} from={8.6} call="cancelled: task.cancel()" value="'CancelledError at the await'" color={FAIL} />
    </AbsoluteFill>
  );
};

// 2-3. greet pauses at its yield; k goes to the handler, which decides. ada resumes k with "Ada".
const GREET_CODE = ['@do', 'def greet():', '    name = yield ReadName()', '    return f"Hello, {name}"'];
const ADA_CODE = ['@handler', 'def ada(effect: ReadName, k):', '    return (yield Resume(k, "Ada"))'];
const GREET: Box = {x: 50, y: 116, w: 780, line: 30};
const HANDLER: Box = {x: 50, y: 340, w: 780, line: 30};
// k drawn over what greet has left to do after line i.
const KBand: React.FC<{box: Box; i: number; rows: number; from: number; until?: number}> = ({box, i, rows, from, until = Infinity}) => {
  const frame = useSceneFrame();
  if (frame >= f(until)) return null;
  return (
    <div
      style={{
        position: 'absolute', left: box.x + 4, top: box.y + PAD + i * box.line, width: box.w - 8, height: rows * box.line, borderRadius: 8, background: K,
        opacity: 0.2 * fadeAt(frame, from, 0.4),
      }}
    />
  );
};

// 0. What the video is about, said over the title and the figure of k, shown still from the first frame: greet paused
// at its yield, the rest of it drawn as k, and k held by a handler.
const INTRO_GREET: Box = {x: 70, y: 300, w: 620, line: 30};
const INTRO_LINES: Lines = [[0, '']];
const INTRO = f(10.0);
const Intro: React.FC = () => (
  <AbsoluteFill>
    <Say x={0} w={1280} y={92} from={-1} size={52} center>How the paused code goes on</Say>
    <Say x={0} w={1280} y={176} from={-1} size={24} color={MUTED} center>doeff: algebraic effects for Python</Say>
    <CodeBlock lines={GREET_CODE} x={INTRO_GREET.x} y={INTRO_GREET.y} w={INTRO_GREET.w} size={20} line={INTRO_GREET.line} />
    <Lit box={INTRO_GREET} i={2} from={-1} />
    <KBand box={INTRO_GREET} i={3} rows={1} from={-1} />
    <Say x={INTRO_GREET.x + 10} y={INTRO_GREET.y + 160} from={-1} size={20} color={K}>k: the rest of greet, paused at the yield</Say>
    <div
      style={{
        position: 'absolute', left: 820, top: 300, width: 390, height: 140, boxSizing: 'border-box', padding: '14px 20px', borderRadius: 14,
        border: `3px solid ${TEST}`, background: PANEL,
      }}
    >
      <div style={{fontFamily: MONO, fontSize: 22, fontWeight: 700, color: TEST}}>a handler</div>
      <div style={{fontFamily: SANS, fontSize: 19, color: TEXT, marginTop: 10}}>holds k, and decides how greet goes on</div>
    </div>
    <div style={{position: 'absolute', left: INTRO_GREET.x + INTRO_GREET.w, top: mid(INTRO_GREET, 3), width: 820 - INTRO_GREET.x - INTRO_GREET.w, height: 0, borderTop: `3px dashed ${K}`}} />
    <Pill x={760} y={mid(INTRO_GREET, 3)} text="k" color={K} />
  </AbsoluteFill>
);

const HAND_LINES: Lines =[[0, 'doeff: the handler receives k, and decides']];
const HAND = f(9.2);
const Hand: React.FC = () => {
  const frame = useSceneFrame();
  return (
    <AbsoluteFill>
      <Headlines lines={HAND_LINES} />
      <CodeBlock lines={GREET_CODE} x={GREET.x} y={GREET.y} w={GREET.w} size={20} line={GREET.line} opacity={fadeAt(frame, 0.1)} />
      <Lit box={GREET} i={2} from={0.4} />
      <KBand box={GREET} i={3} rows={1} from={1.4} />
      <Say x={GREET.x + 10} y={GREET.y + 160} from={1.6} size={20} color={K}>k: the rest of greet, paused at the yield</Say>
      <div
        style={{
          position: 'absolute', left: HANDLER.x, top: HANDLER.y, width: HANDLER.w, height: 200, boxSizing: 'border-box', padding: '14px 20px', borderRadius: 14,
          border: `3px solid ${TEST}`, background: PANEL, opacity: fadeAt(frame, 1.0),
        }}
      >
        <div style={{fontFamily: MONO, fontSize: 22, fontWeight: 700, color: TEST}}>a handler of ReadName</div>
        <div style={{fontFamily: MONO, fontSize: 19, color: MUTED, marginTop: 6}}>(effect, k)</div>
        <div style={{fontFamily: SANS, fontSize: 22, color: LOOK, marginTop: 50, opacity: fadeAt(frame, 4.6)}}>what happens next is its decision</div>
      </div>
      <Moving text="ReadName()" color={ACCENT} from={1.8} to={2.8} x0={GREET.x + 440} y0={mid(GREET, 2)} x1={HANDLER.x + 420} y1={HANDLER.y + 52} />
      <Moving text="k" color={K} from={2.2} to={3.2} x0={GREET.x + 560} y0={mid(GREET, 3)} x1={HANDLER.x + 600} y1={HANDLER.y + 52} />
      <Moves from={4.6} stagger={0.35} />
    </AbsoluteFill>
  );
};

const RESUME_LINES: Lines = [[0, 'Resume: greet goes on with a value']];
const RESUME = f(6.0);
const ResumeK: React.FC = () => {
  const frame = useSceneFrame();
  return (
    <AbsoluteFill>
      <Headlines lines={RESUME_LINES} />
      <CodeBlock lines={GREET_CODE} x={GREET.x} y={GREET.y} w={GREET.w} size={20} line={GREET.line} opacity={fadeAt(frame, 0.1)} />
      <KBand box={GREET} i={3} rows={1} from={0} until={2.4} />
      <Lit box={GREET} i={2} from={0} until={2.4} />
      <CodeBlock lines={ADA_CODE} x={HANDLER.x} y={HANDLER.y} w={HANDLER.w} size={20} line={HANDLER.line} opacity={fadeAt(frame, 0.1)} />
      <Lit box={HANDLER} i={2} from={0.6} until={2.4} color={TEST} />
      <Moving text={'Resume(k, "Ada")'} color={TEST} from={1.2} to={2.2} until={2.6} x0={HANDLER.x + 420} y0={mid(HANDLER, 2)} x1={GREET.x + 440} y1={mid(GREET, 2)} />
      <Lit box={GREET} i={3} from={2.4} />
      <Result x={HANDLER.x + 10} y={480} from={3.2} call="run(ada(greet()))" value="Hello, Ada" />
      <Moves lit={0} />
    </AbsoluteFill>
  );
};

// 4. refuse throws into k; greet's own except clause catches it at its yield.
const STRANGER_CODE = [
  '@do',
  'def greet_or_stranger():',
  '    try:',
  '        name = yield ReadName()',
  '    except LookupError as error:',
  '        return f"Hello, stranger ({error})"',
  '    return f"Hello, {name}"',
];
const REFUSE_CODE = ['@handler', 'def refuse(effect: ReadName, k):', '    return (yield ResumeThrow(k, LookupError("no name")))'];
const STRANGER: Box = {x: 50, y: 116, w: 800, line: 28};
const REFUSE: Box = {x: 50, y: 370, w: 800, line: 28};
const THROW_LINES: Lines = [[0, 'ResumeThrow: greet sees the exception at its yield']];
const THROW = f(8.0);
const Throw: React.FC = () => {
  const frame = useSceneFrame();
  return (
    <AbsoluteFill>
      <Headlines lines={THROW_LINES} />
      <CodeBlock lines={STRANGER_CODE} x={STRANGER.x} y={STRANGER.y} w={STRANGER.w} size={18} line={STRANGER.line} opacity={fadeAt(frame, 0.1)} />
      <Lit box={STRANGER} i={3} from={0.2} until={2.4} />
      <CodeBlock lines={REFUSE_CODE} x={REFUSE.x} y={REFUSE.y} w={REFUSE.w} size={18} line={REFUSE.line} opacity={fadeAt(frame, 0.1)} />
      <Lit box={REFUSE} i={2} from={0.6} until={2.4} color={FAIL} />
      <Moving text={'ResumeThrow(k, LookupError("no name"))'} color={FAIL} from={1.0} to={2.2} until={2.6} x0={REFUSE.x + 420} y0={mid(REFUSE, 2)} x1={STRANGER.x + 520} y1={mid(STRANGER, 3)} />
      <Lit box={STRANGER} i={3} from={2.4} until={4.2} color={FAIL} />
      <Lit box={STRANGER} i={4} from={4.2} />
      <Lit box={STRANGER} i={5} from={5.0} />
      <Result x={REFUSE.x + 10} y={500} from={5.6} call="run(refuse(greet_or_stranger()))" value="Hello, stranger (no name)" />
      <Moves lit={1} />
    </AbsoluteFill>
  );
};

// 5. scheduled keeps the k of each task and goes back to it when the task can go on: the measured order.
const TOGETHER_CODE = [
  '@do',
  'def together():',
  '    first = yield Spawn(greet())',
  '    second = yield Spawn(grace(greet()))',
  '    return (yield Gather(first, second))',
];
const TOGETHER: Box = {x: 50, y: 116, w: 800, line: 28};
const KEEP_LINES: Lines = [[0, 'Keep k: the task goes on later, after other code']];
const KEEP = f(9.8);
// measure/paused.py, "p5 the same, traced": the order the two tasks start, are answered and end.
const ORDER: [number, string][] = [
  [3.6, 'task 1 starts'], [4.0, 'ada answers task 1'], [4.4, 'task 1 ends'],
  [5.8, 'task 2 starts'], [6.2, 'grace answers task 2'], [6.6, 'task 2 ends'],
];
const Keep: React.FC = () => {
  const frame = useSceneFrame();
  const slot = (x: number, label: string) => (
    <div
      style={{
        position: 'absolute', left: x, top: 340, width: 220, height: 70, boxSizing: 'border-box', borderRadius: 10, border: '2px dashed #2a2e40',
        fontFamily: SANS, fontSize: 16, color: MUTED, padding: '6px 10px', opacity: fadeAt(frame, 1.6),
      }}
    >
      {label}
    </div>
  );
  return (
    <AbsoluteFill>
      <Headlines lines={KEEP_LINES} />
      <CodeBlock lines={TOGETHER_CODE} x={TOGETHER.x} y={TOGETHER.y} w={TOGETHER.w} size={18} line={TOGETHER.line} opacity={fadeAt(frame, 0.1)} />
      <Lit box={TOGETHER} i={2} from={3.0} until={4.8} />
      <Lit box={TOGETHER} i={3} from={5.2} until={7.0} />
      <Lit box={TOGETHER} i={4} from={7.6} />
      <div
        style={{
          position: 'absolute', left: 50, top: 290, width: 520, height: 140, boxSizing: 'border-box', padding: '10px 18px', borderRadius: 14, border: `3px solid ${TEST}`,
          background: PANEL, opacity: fadeAt(frame, 1.4),
        }}
      >
        <div style={{fontFamily: MONO, fontSize: 19, fontWeight: 700, color: TEST}}>scheduled</div>
      </div>
      {slot(70, 'k of task 1')}
      {slot(330, 'k of task 2')}
      <Moving text="k" color={K} from={3.4} to={3.9} until={4.6} x0={600} y0={mid(TOGETHER, 2)} x1={180} y1={385} />
      <Moving text="k" color={K} from={5.6} to={6.1} until={6.8} x0={600} y0={mid(TOGETHER, 3)} x1={440} y1={385} />
      <div style={{position: 'absolute', left: 600, top: 290, width: 270, fontFamily: SANS, fontSize: 17, color: MUTED, opacity: fadeAt(frame, 3.2)}}>in the order measured:</div>
      {ORDER.map(([from, text], i) => (
        <Say key={text} x={600} y={318 + i * 28} from={from} size={17} mono color={text.includes('answers') ? TEST : TEXT}>{text}</Say>
      ))}
      <Result x={60} y={470} from={7.8} call="run(scheduled(ada(together())))" value="['Hello, Ada', 'Hello, Grace']" />
      <Moves lit={2} />
    </AbsoluteFill>
  );
};

// 6. closed never resumes k: the run ends with its value, and the paused generators' finally blocks run.
const TWICE_CODE = [
  '@do',
  'def twice_finally():',
  '    try:',
  '        first = yield greet_finally()',
  '        ...',
  '    finally:',
  '        print("p6 twice: finally")',
];
const GREETF_CODE = [
  '@do',
  'def greet_finally():',
  '    try:',
  '        name = yield ReadName()',
  '        ...',
  '    finally:',
  '        print("p6 greet: finally")',
];
const CLOSED_CODE = ['@handler', 'def closed(effect: ReadName, k):  # never resume k', '    return "closed today"'];
const TWICE: Box = {x: 50, y: 116, w: 400, line: 26};
const GREETF: Box = {x: 470, y: 116, w: 400, line: 26};
const CLOSED: Box = {x: 50, y: 360, w: 820, line: 28};
const DROP_LINES: Lines = [[0, "Never resume k: the run ends with the handler's value"]];
const DROP = f(10.0);
const Drop: React.FC = () => {
  const frame = useSceneFrame();
  return (
    <AbsoluteFill>
      <Headlines lines={DROP_LINES} />
      <Label x={TWICE.x} y={TWICE.y - 32} text="measure/paused.py" color={MUTED} from={0.1} />
      <CodeBlock lines={TWICE_CODE} x={TWICE.x} y={TWICE.y} w={TWICE.w} size={15} line={TWICE.line} opacity={fadeAt(frame, 0.1)} />
      <CodeBlock lines={GREETF_CODE} x={GREETF.x} y={GREETF.y} w={GREETF.w} size={15} line={GREETF.line} opacity={fadeAt(frame, 0.1)} />
      <Lit box={TWICE} i={3} from={0.2} until={6.4} />
      <Lit box={GREETF} i={3} from={0.2} until={6.4} />
      <CodeBlock lines={CLOSED_CODE} x={CLOSED.x} y={CLOSED.y} w={CLOSED.w} size={18} line={CLOSED.line} opacity={fadeAt(frame, 0.1)} />
      <Moving text="k" color={frame >= f(2.2) ? '#3a3f55' : K} from={0.8} to={1.8} until={3.0} x0={GREETF.x + 300} y0={mid(GREETF, 4)} x1={CLOSED.x + 600} y1={mid(CLOSED, 1)} />
      <Lit box={CLOSED} i={2} from={2.4} color={TEST} />
      <Result x={CLOSED.x + 10} y={480} from={3.6} call="run(closed(twice_finally()))" value="closed today" />
      {/* measure/paused.py: twice's finally ran first, then greet's. */}
      <Lit box={TWICE} i={6} from={6.6} color={TEST} strength={0.3} />
      <Lit box={GREETF} i={6} from={7.2} color={TEST} strength={0.3} />
      <Say x={CLOSED.x + 10} y={560} from={6.6} size={19} color={TEST}>the paused generators are closed: both finally blocks ran</Say>
      <Moves lit={3} />
    </AbsoluteFill>
  );
};

// 7. Neither goes on twice: a second Resume and a second drive of a spent coroutine are both RuntimeError.
const RESUME2_CODE = ['@handler', 'def resume_twice(effect: ReadName, k):', '    yield Resume(k, "Ada")', '    return (yield Resume(k, "Ada"))'];
const DRIVE2_CODE = ['greeting = greet_async()', 'print(drive(greeting, {"name": "Ada"}))', 'drive(greeting, {"name": "Ada"})'];
const RESUME2: Box = {x: 50, y: 150, w: 400, line: 28};
const DRIVE2: Box = {x: 470, y: 150, w: 400, line: 28};
const ONCE_LINES: Lines = [[0, 'Neither goes on twice']];
const ONCE = f(7.0);
const Once: React.FC = () => {
  const frame = useSceneFrame();
  return (
    <AbsoluteFill>
      <Headlines lines={ONCE_LINES} />
      <Label x={RESUME2.x} y={RESUME2.y - 32} text="doeff: k" color={ACCENT} from={0.1} />
      <CodeBlock lines={RESUME2_CODE} x={RESUME2.x} y={RESUME2.y} w={RESUME2.w} size={15} line={RESUME2.line} opacity={fadeAt(frame, 0.1)} />
      <Label x={DRIVE2.x} y={DRIVE2.y - 32} text="asyncio: a coroutine object" color={PROD} from={0.1} />
      <CodeBlock lines={DRIVE2_CODE} x={DRIVE2.x} y={DRIVE2.y} w={DRIVE2.w} size={15} line={DRIVE2.line} opacity={fadeAt(frame, 0.1)} />
      <Lit box={RESUME2} i={2} from={0.6} until={1.4} color={TEST} />
      <Lit box={RESUME2} i={3} from={1.4} color={FAIL} />
      <Lit box={DRIVE2} i={1} from={0.9} until={1.7} color={TEST} />
      <Lit box={DRIVE2} i={2} from={1.7} color={FAIL} />
      <Say x={RESUME2.x + 10} y={320} from={1.8} size={30} mono color={FAIL}>RuntimeError</Say>
      <Say x={DRIVE2.x + 10} y={320} from={2.1} size={30} mono color={FAIL}>RuntimeError</Say>
      <Say x={50} y={420} from={3.2} size={24} color={LOOK}>k is one-shot, like a coroutine object</Say>
      <Moves lit={4} />
    </AbsoluteFill>
  );
};

// 8. greet() gives a program, not a paused run: each run starts a new generator.
const AGAIN_LINES: Lines = [[0, 'A program is not a paused run']];
const AGAIN = f(10.4);
const RUNS: {x: number; call: string; value: string; from: number}[] = [
  {x: 140, call: 'run(ada(hello))', value: 'Hello, Ada', from: 5.6},
  {x: 700, call: 'run(grace(hello))', value: 'Hello, Grace', from: 7.4},
];
const Again: React.FC = () => {
  const frame = useSceneFrame();
  return (
    <AbsoluteFill>
      <Headlines lines={AGAIN_LINES} />
      <div
        style={{
          position: 'absolute', left: 440, top: 110, width: 400, height: 70, boxSizing: 'border-box', borderRadius: 12, border: `3px solid ${ACCENT}`, background: '#0b0d13',
          display: 'flex', alignItems: 'center', justifyContent: 'center', fontFamily: MONO, fontSize: 26, fontWeight: 700, color: TEXT, opacity: fadeAt(frame, 0.2),
        }}
      >
        hello = greet()
      </div>
      <Say x={0} w={1280} y={196} from={1.2} size={21} color={MUTED} center>a program: a value that describes the run, not a run</Say>
      {RUNS.map(({x, call, value, from}) => (
        <React.Fragment key={call}>
          <Moving text="hello" color={ACCENT} from={from - 1.6} to={from - 0.6} until={from - 0.4} x0={640} y0={180} x1={x + 220} y1={300} />
          <div
            style={{
              position: 'absolute', left: x, top: 290, width: 440, height: 240, boxSizing: 'border-box', padding: '16px 22px', borderRadius: 14, border: `3px solid ${TEST}`,
              background: PANEL, opacity: fadeAt(frame, 3.6),
            }}
          >
            <div style={{fontFamily: MONO, fontSize: 22, fontWeight: 700, color: TEST}}>{call}</div>
            <div style={{fontFamily: SANS, fontSize: 19, color: K, marginTop: 30, opacity: fadeAt(frame, from - 0.4)}}>a new generator for this run</div>
            <div style={{fontFamily: MONO, fontSize: 26, fontWeight: 700, color: LOOK, marginTop: 30, opacity: fadeAt(frame, from)}}>{'-> '}{value}</div>
          </div>
        </React.Fragment>
      ))}
    </AbsoluteFill>
  );
};

export const ARTICLE2_PAUSED_CODE = narrate('article2-paused-code', DURATIONS, NARRATION, [
  {length: INTRO, starts: startsOf(INTRO_LINES), ids: ['p0'], node: <Intro />},
  {length: LOOP, starts: startsOf(LOOP_LINES), ids: ['p1'], node: <LoopMoves />},
  {length: HAND, starts: startsOf(HAND_LINES), ids: ['p2'], node: <Hand />},
  {length: RESUME, starts: startsOf(RESUME_LINES), ids: ['p3'], node: <ResumeK />},
  {length: THROW, starts: startsOf(THROW_LINES), ids: ['p4'], node: <Throw />},
  {length: KEEP, starts: startsOf(KEEP_LINES), ids: ['p5'], node: <Keep />},
  {length: DROP, starts: startsOf(DROP_LINES), ids: ['p6'], node: <Drop />},
  {length: ONCE, starts: startsOf(ONCE_LINES), ids: ['p7'], node: <Once />},
  {length: AGAIN, starts: startsOf(AGAIN_LINES), ids: ['p8'], node: <Again />},
]);
