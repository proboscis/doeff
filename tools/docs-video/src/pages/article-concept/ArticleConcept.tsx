// The first video of the article "Algebraic effects for Python" (docs/articles/algebraic-effects-in-python.md) and of
// the README, before the card battle: what algebraic effects are, then how doeff writes them. A title card says what
// doeff is and what the video shows. Then the idea with no code: a program says what it needs, a handler outside it
// decides how to get it, answers, and the program goes on; next to an exception, whose handler never goes back. Then
// the same idea in doeff: a small function, order, is read from the top and yields two effects without saying who
// answers; handlers stacked outside it answer, one of them with k, which it resumes, keeps or never resumes, at most
// once; the same function sits unchanged in five stacks; then pip install doeff, and the three things to start with.
// Every code line on screen is a line of measure/order.py (check_code.py checks it), and every result is a line of its
// output. Each scene's motions come one at a time (pace.ts), and each headline waits for its line and its motions.
import React from 'react';
import {AbsoluteFill} from 'remotion';
import {fadeAt, mix, ramp} from '../../kit/motion';
import {narrate} from '../../kit/narrated';
import {chain, heardBy, LINE_FADE, nextLine, PAUSE, sceneEnd, Step} from '../../kit/pace';
import {CodeBlock, Headlines, Lines, lineMid, PAD, startsOf} from '../../kit/parts';
import {ACCENT, BG, f, FAIL, LOOK, MONO, MUTED, PANEL, PROD, SANS, TEST, TEXT} from '../../kit/theme';
import {useSceneFrame} from '../../kit/warp';
import {Geometry, K, Moving, Ring, Tower} from '../article-opening/ArticleOpening';
import DURATIONS from './durations.json';
import NARRATION from './narration.json';

const heard = heardBy(DURATIONS);
// The first motion of a line: PAUSE s after its headline has faded in.
const after = (headline: number) => headline + LINE_FADE + PAUSE;
// A headline as a motion, for PACED.
const headline = (from: number): Step => ({from, to: from + LINE_FADE});

// 1. The title card: what doeff is, and what this video shows.
const T1 = 0;
const T2 = nextLine(heard(T1, 'i1'), {});
const T_STEPS = chain(after(T2), [
  ['idea', 0.3],
  ['doeff', 0.3],
]);
const TITLE_LINES: Lines = [
  [T1, 'doeff: algebraic effects for Python'],
  [T2, 'First what algebraic effects are, then how doeff writes them'],
];
const TITLE = f(sceneEnd(heard(T2, 'i2'), T_STEPS));
// The parts of the video, as the title card lists them: [its step, its number, what it is].
const PARTS: [Step, string, string][] = [
  [T_STEPS.idea, '1', 'Algebraic effects: the idea, without code'],
  [T_STEPS.doeff, '2', 'The same idea, written with doeff'],
];

const Title: React.FC = () => {
  const frame = useSceneFrame();
  return (
    <AbsoluteFill>
      <Headlines lines={TITLE_LINES} />
      <div style={{position: 'absolute', left: 0, right: 0, top: 130, textAlign: 'center', opacity: fadeAt(frame, T1)}}>
        <div style={{fontFamily: SANS, fontSize: 104, fontWeight: 700, color: TEXT}}>doeff</div>
        <div style={{fontFamily: SANS, fontSize: 34, color: ACCENT, marginTop: 6}}>Algebraic effects for Python</div>
      </div>
      {PARTS.map(([step, number, what], i) => (
        <div
          key={number}
          style={{
            position: 'absolute', left: 290, top: 400 + i * 84, width: 700, boxSizing: 'border-box', padding: '14px 24px', borderRadius: 14, background: PANEL,
            border: '2px solid #2a2e40', display: 'flex', alignItems: 'center', gap: 22, opacity: fadeAt(frame, step.from),
          }}
        >
          <div style={{fontFamily: SANS, fontSize: 30, fontWeight: 700, color: LOOK}}>{number}</div>
          <div style={{fontFamily: SANS, fontSize: 26, color: TEXT}}>{what}</div>
        </div>
      ))}
    </AbsoluteFill>
  );
};

// Plain words on the move, in the scenes with no code: centred on a point that moves from (x0, y0) to (x1, y1).
const Words: React.FC<{text: string; color: string; step: Step; until?: number; x0: number; y0: number; x1: number; y1: number}> = ({
  text, color, step, until = Infinity, x0, y0, x1, y1,
}) => {
  const frame = useSceneFrame();
  if (frame < f(step.from) || frame >= f(until)) return null;
  const t = ramp(frame, step.from, step.to);
  return (
    <div
      style={{
        position: 'absolute', left: mix(x0, x1, t), top: mix(y0, y1, t), transform: 'translate(-50%, -50%)', whiteSpace: 'nowrap', fontFamily: SANS,
        fontSize: 21, fontWeight: 700, padding: '6px 16px', borderRadius: 16, background: color, color: BG, boxShadow: `0 0 16px ${color}`,
        opacity: fadeAt(frame, step.from, 0.2),
      }}
    >
      {text}
    </div>
  );
};

// A program drawn as its steps in plain words, one row each: [the row's text, when it lights, its colour when lit].
type Row = {text: string; lit?: number; litColor?: string; out?: number};
const ROW = 50;
const Steps: React.FC<{title: string; rows: Row[]; x: number; y: number; w: number; from: number}> = ({title, rows, x, y, w, from}) => {
  const frame = useSceneFrame();
  return (
    <div style={{position: 'absolute', left: x, top: y, width: w, opacity: fadeAt(frame, from)}}>
      <div style={{fontFamily: SANS, fontSize: 20, fontWeight: 700, color: ACCENT, marginBottom: 8}}>{title}</div>
      <div style={{position: 'relative', boxSizing: 'border-box', padding: `${PAD}px 0`, borderRadius: 14, background: '#0b0d13', border: `3px solid ${ACCENT}`}}>
        {rows.map((row, i) => {
          const lit = row.lit !== undefined ? fadeAt(frame, row.lit) : 0;
          const out = row.out !== undefined ? fadeAt(frame, row.out) : 0;
          return (
            <div key={i} style={{position: 'relative', height: ROW, display: 'flex', alignItems: 'center', paddingLeft: 24}}>
              <div style={{position: 'absolute', inset: 0, background: row.litColor ?? LOOK, opacity: 0.22 * lit * (1 - out)}} />
              <div style={{position: 'relative', fontFamily: SANS, fontSize: 22, color: TEXT, opacity: 1 - 0.65 * out, textDecoration: out > 0.5 ? 'line-through' : 'none'}}>
                {row.text}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
};

// The middle of row i of a Steps box at y: below its title (28 px) and the box's border and padding.
const rowMid = (y: number, i: number) => y + 28 + 3 + PAD + i * ROW + ROW / 2;

// A handler drawn as a box outside the program: its name, where it is, and what it does once it is at work.
const Outside: React.FC<{x: number; y: number; w: number; name: string; note: string; how?: string; howFrom?: number; from: number; lit: boolean}> = ({
  x, y, w, name, note, how, howFrom = Infinity, from, lit,
}) => {
  const frame = useSceneFrame();
  return (
    <div
      style={{
        position: 'absolute', left: x, top: y, width: w, boxSizing: 'border-box', padding: '14px 22px', borderRadius: 14, background: PANEL,
        border: `3px solid ${lit ? LOOK : TEST}`, boxShadow: lit ? `0 0 22px ${LOOK}aa` : 'none', opacity: fadeAt(frame, from),
      }}
    >
      <div style={{fontFamily: SANS, fontSize: 24, fontWeight: 700, color: lit ? LOOK : TEST}}>{name}</div>
      <div style={{fontFamily: SANS, fontSize: 18, color: MUTED, marginTop: 4}}>{note}</div>
      {how && <div style={{fontFamily: SANS, fontSize: 20, color: TEXT, marginTop: 10, opacity: fadeAt(frame, howFrom)}}>{how}</div>}
    </div>
  );
};

// 2. The idea, with no code: the program says what it needs; a handler outside it decides how, answers, and the
// program goes on from where it asked.
const A1 = 0;
const A1_STEPS = chain(after(A1), [
  ['program', 0.3],
  ['price', 0.3],
  ['person', 0.3],
  ['time', 0.3],
]);
const A2 = nextLine(heard(A1, 'a1'), A1_STEPS);
const A2_STEPS = chain(after(A2), [
  ['ask', 0.3],
  ['effect', 0.3],
  ['request', 0.3],
]);
const A3 = nextLine(heard(A2, 'a2'), A2_STEPS);
const A3_STEPS = chain(after(A3), [
  ['handler', 0.3],
  ['out', 0.9],
  ['how', 0.3],
]);
const A4 = nextLine(heard(A3, 'a3'), A3_STEPS);
const A4_STEPS = chain(after(A4), [
  ['back', 0.9],
  ['goes', 0.3],
]);
const IDEA_LINES: Lines = [
  [A1, 'Algebraic effects: a program needs things from outside itself.'],
  [A2, 'The program only says what it needs: an effect.'],
  [A3, 'A handler, outside the program, decides how to get it.'],
  [A4, 'The handler answers. The program goes on from where it asked.'],
];
const IDEA = f(sceneEnd(heard(A4, 'a4'), A4_STEPS));
const P_X = 80;
const P_Y = 150;
const P_W = 520;
const H_X = 790;
const H_Y = 250;
const H_W = 410;
// What a program may need from outside itself: [its step, the words].
const NEEDS: [Step, string][] = [
  [A1_STEPS.price, 'a price'],
  [A1_STEPS.person, 'a yes or no from a person'],
  [A1_STEPS.time, 'the time'],
];

const Idea: React.FC = () => {
  const frame = useSceneFrame();
  const at = (seconds: number) => frame >= f(seconds);
  const askY = rowMid(P_Y, 1);
  return (
    <AbsoluteFill>
      <Headlines lines={IDEA_LINES} />
      <Steps
        title="a program"
        x={P_X}
        y={P_Y}
        w={P_W}
        from={A1_STEPS.program.from}
        rows={[
          {text: 'takes an order for tea'},
          {text: 'needs: what does tea cost?', lit: A2_STEPS.ask.from, litColor: ACCENT},
          {text: 'buys it at that price', lit: A4_STEPS.goes.from},
        ]}
      />
      {NEEDS.map(([step, text], i) => (
        <div
          key={text}
          style={{
            position: 'absolute', left: H_X, top: 170 + i * 76, padding: '10px 20px', borderRadius: 14, border: `2px dashed ${MUTED}`, fontFamily: SANS, fontSize: 22,
            color: TEXT, opacity: fadeAt(frame, step.from) * (1 - fadeAt(frame, A2, LINE_FADE)),
          }}
        >
          {text}?
        </div>
      ))}
      <div style={{position: 'absolute', left: P_X, top: P_Y + 230, width: P_W, fontFamily: SANS, fontSize: 21, color: ACCENT, opacity: fadeAt(frame, A2_STEPS.effect.from)}}>
        an effect: what it needs, not how to get it
      </div>
      <div style={{position: 'absolute', left: P_X, top: P_Y + 266, width: P_W, fontFamily: SANS, fontSize: 21, color: LOOK, opacity: fadeAt(frame, A4_STEPS.goes.from)}}>
        it goes on from where it asked, with 3
      </div>
      <Outside
        x={H_X}
        y={H_Y}
        w={H_W}
        name="a handler"
        note="outside the program"
        how="looks it up in a price list"
        howFrom={A3_STEPS.how.from}
        from={A3_STEPS.handler.from}
        lit={at(A3_STEPS.out.to) && !at(A4_STEPS.back.to)}
      />
      {/* The request waits at the program's edge, goes out to the handler, and the answer comes back to where it asked. */}
      <Words
        text="what does tea cost?"
        color={ACCENT}
        step={A2_STEPS.request}
        until={A3_STEPS.out.from}
        x0={P_X + P_W + 90}
        y0={askY}
        x1={P_X + P_W + 90}
        y1={askY}
      />
      <Words text="what does tea cost?" color={ACCENT} step={A3_STEPS.out} until={A4_STEPS.back.from} x0={P_X + P_W + 90} y0={askY} x1={H_X + H_W / 2} y1={H_Y - 30} />
      <Words text="3" color={TEST} step={A4_STEPS.back} until={A4_STEPS.goes.to + 0.6} x0={H_X + H_W / 2} y0={H_Y - 30} x1={P_X + P_W - 40} y1={askY} />
    </AbsoluteFill>
  );
};

// 3. The idea next to an exception: both go out to a handler outside the program, but only the effect comes back.
const B1 = 0;
const B1_STEPS = chain(after(B1), [
  ['lane', 0.3],
  ['raise', 0.3],
  ['throw', 0.9],
]);
const B2 = nextLine(heard(B1, 'a5'), B1_STEPS);
const B2_STEPS = chain(after(B2), [['lost', 0.3]]);
const B3 = nextLine(heard(B2, 'a6'), B2_STEPS);
const B3_STEPS = chain(after(B3), [
  ['lane', 0.3],
  ['ask', 0.3],
  ['out', 0.9],
  ['back', 0.9],
  ['on', 0.3],
]);
const B4 = nextLine(heard(B3, 'a7'), B3_STEPS);
const B4_STEPS = chain(after(B4), [
  ['what', 0.3],
  ['how', 0.3],
]);
const COMPARE_LINES: Lines = [
  [B1, 'Like an exception: raised inside, caught by a handler outside.'],
  [B2, 'But after the catch, the program never goes back.'],
  [B3, 'An effect handler answers, and the program picks up where it left off.'],
  [B4, 'The program says what it needs. The handlers decide how.'],
];
const COMPARE = f(sceneEnd(heard(B4, 'a8'), B4_STEPS));
const TOP = 96;
const BOTTOM = 330;
const C_X = 80;
const C_W = 520;
const C_HX = 800;
const C_HW = 400;

const Compare: React.FC = () => {
  const frame = useSceneFrame();
  const at = (seconds: number) => frame >= f(seconds);
  const raiseY = rowMid(TOP, 1);
  const askY = rowMid(BOTTOM, 1);
  return (
    <AbsoluteFill>
      <Headlines lines={COMPARE_LINES} />
      <Steps
        title="an exception"
        x={C_X}
        y={TOP}
        w={C_W}
        from={B1_STEPS.lane.from}
        rows={[{text: 'reads the order'}, {text: 'raises: something went wrong', lit: B1_STEPS.raise.from, litColor: FAIL}, {text: 'the rest of the program', out: B2_STEPS.lost.from}]}
      />
      <Outside x={C_HX} y={TOP + 40} w={C_HW} name="catches it" note="a handler outside the program" from={B1_STEPS.lane.from} lit={at(B1_STEPS.throw.to)} />
      <div style={{position: 'absolute', left: C_HX, top: TOP + 160, width: C_HW, fontFamily: SANS, fontSize: 21, color: FAIL, opacity: fadeAt(frame, B2_STEPS.lost.from)}}>
        never goes back: the rest never runs
      </div>
      <Words text="something went wrong" color={FAIL} step={B1_STEPS.throw} x0={C_X + C_W - 60} y0={raiseY} x1={C_HX + C_HW / 2} y1={TOP + 26} />
      <Steps
        title="an effect"
        x={C_X}
        y={BOTTOM}
        w={C_W}
        from={B3_STEPS.lane.from}
        rows={[{text: 'takes an order for tea'}, {text: 'needs: what does tea cost?', lit: B3_STEPS.ask.from, litColor: ACCENT}, {text: 'the rest of the program', lit: B3_STEPS.on.from}]}
      />
      <Outside
        x={C_HX}
        y={BOTTOM + 40}
        w={C_HW}
        name="answers it"
        note="a handler outside the program"
        from={B3_STEPS.lane.from}
        lit={at(B3_STEPS.out.to) && !at(B3_STEPS.back.to)}
      />
      <div style={{position: 'absolute', left: C_HX, top: BOTTOM + 160, width: C_HW, fontFamily: SANS, fontSize: 21, color: LOOK, opacity: fadeAt(frame, B3_STEPS.on.from)}}>
        goes back: the rest runs, with 3
      </div>
      <Words text="what does tea cost?" color={ACCENT} step={B3_STEPS.out} until={B3_STEPS.back.from} x0={C_X + C_W - 60} y0={askY} x1={C_HX + C_HW / 2} y1={BOTTOM + 26} />
      <Words text="3" color={TEST} step={B3_STEPS.back} until={B3_STEPS.on.to + 0.6} x0={C_HX + C_HW / 2} y0={BOTTOM + 26} x1={C_X + C_W - 40} y1={askY} />
      <div style={{position: 'absolute', left: C_X, top: 570, width: C_W, fontFamily: SANS, fontSize: 24, fontWeight: 700, color: ACCENT, opacity: fadeAt(frame, B4_STEPS.what.from)}}>
        the program: what it needs
      </div>
      <div style={{position: 'absolute', left: C_HX, top: 570, width: C_HW, fontFamily: SANS, fontSize: 24, fontWeight: 700, color: TEST, opacity: fadeAt(frame, B4_STEPS.how.from)}}>
        the handlers: how
      </div>
    </AbsoluteFill>
  );
};

// The small function of the video, as measure/order.py writes it.
const ORDER_CODE = [
  '@do',
  'def order(item: str):',
  '    price = yield Price(item)',
  '    ok = yield Confirm(price)',
  '    return ("bought" if ok else "skipped"), price',
];
const PRICE = 2;
const CONFIRM = 3;
const RETURN = 4;

// The lines of the code box lit at a time: [from, the lines], each entry until the next one.
type Lit = [number, number[]][];
const LitLines: React.FC<{lit: Lit; x: number; y: number; w: number; line: number}> = ({lit, x, y, w, line}) => {
  const frame = useSceneFrame();
  const now = lit.reduce<[number, number[]] | null>((found, entry) => (frame >= f(entry[0]) ? entry : found), null);
  if (!now) return null;
  return (
    <>
      {now[1].map((i) => (
        <div
          key={`${now[0]}-${i}`}
          style={{position: 'absolute', left: x + 2, top: y + PAD + i * line, width: w - 4, height: line, background: LOOK, opacity: 0.22 * fadeAt(frame, now[0], 0.25)}}
        />
      ))}
    </>
  );
};

// 4. The same idea in doeff: the function read from the top. It yields its effects, and never says who answers.
const F1 = 0;
const F1_STEPS = chain(after(F1), [['code', 0.3]]);
const F2 = nextLine(heard(F1, 'b1'), F1_STEPS);
const F2_STEPS = chain(after(F2), [
  ['price', 0.3],
  ['ask', 0.3],
]);
const F3 = nextLine(heard(F2, 'b2'), F2_STEPS);
const F3_STEPS = chain(after(F3), [
  ['confirm', 0.3],
  ['ask', 0.3],
  ['ret', 0.3],
  ['who', 0.3],
]);
const FN_LINES: Lines = [
  [F1, 'In doeff: a Python function, marked @do.'],
  [F2, 'It yields an effect where it needs something.'],
  [F3, 'It never says who answers.'],
];
const FN = f(sceneEnd(heard(F3, 'b3'), F3_STEPS));
const FN_X = 60;
const FN_Y = 150;
const FN_W = 720;
const FN_LINE = 40;
const FN_LIT: Lit = [
  [F1_STEPS.code.from, [0, 1]],
  [F2_STEPS.price.from, [PRICE]],
  [F3_STEPS.confirm.from, [CONFIRM]],
  [F3_STEPS.ret.from, [RETURN]],
  [F3_STEPS.who.from, [PRICE, CONFIRM]],
];
// One row per effect as its line is lit: [from, the effect, what it asks].
const ASKS: [number, string, string][] = [
  [F2_STEPS.ask.from, 'Price(item)', 'what does it cost?'],
  [F3_STEPS.ask.from, 'Confirm(price)', 'may I pay that much?'],
];

const Function: React.FC = () => {
  const frame = useSceneFrame();
  return (
    <AbsoluteFill>
      <Headlines lines={FN_LINES} />
      <div style={{position: 'absolute', left: FN_X, top: FN_Y - 32, fontFamily: SANS, fontSize: 20, fontWeight: 700, color: ACCENT, opacity: fadeAt(frame, F1_STEPS.code.from)}}>
        order.py: order
      </div>
      <CodeBlock lines={ORDER_CODE} x={FN_X} y={FN_Y} w={FN_W} size={22} line={FN_LINE} opacity={fadeAt(frame, F1_STEPS.code.from)} />
      <LitLines lit={FN_LIT} x={FN_X} y={FN_Y} w={FN_W} line={FN_LINE} />
      <div
        style={{
          position: 'absolute', left: 820, top: FN_Y, width: 400, boxSizing: 'border-box', padding: '16px 22px', background: PANEL, borderRadius: 14,
          border: '2px solid #2a2e40', opacity: fadeAt(frame, F2_STEPS.ask.from),
        }}
      >
        <div style={{fontFamily: SANS, fontSize: 18, fontWeight: 700, color: MUTED, marginBottom: 12}}>the effects order yields</div>
        {ASKS.map(([from, effect, what]) => (
          <div key={effect} style={{height: 66, opacity: fadeAt(frame, from)}}>
            <div style={{fontFamily: MONO, fontSize: 21, fontWeight: 700, color: ACCENT}}>{effect}</div>
            <div style={{fontFamily: SANS, fontSize: 18, color: TEXT}}>{what}</div>
          </div>
        ))}
        <div style={{fontFamily: SANS, fontSize: 20, color: LOOK, marginTop: 6, opacity: fadeAt(frame, F3_STEPS.who.from)}}>who answers? order never says.</div>
      </div>
    </AbsoluteFill>
  );
};

// 5. Handlers stacked outside order answer; one handler also gets k, resumes it, and k is resumed at most once.
const H1 = 0;
const H1_STEPS = chain(after(H1), [
  ['boxes', 0.3],
  ['price', 0.9],
  ['answer', 0.9],
]);
const H2 = nextLine(heard(H1, 'b4'), H1_STEPS);
const H2_STEPS = chain(after(H2), [
  ['confirm', 0.9],
  ['k', 0.9],
]);
const H3 = nextLine(heard(H2, 'b5'), H2_STEPS);
const H3_STEPS = chain(after(H3), [
  ['resume', 0.9],
  ['ret', 0.3],
]);
const H4 = nextLine(heard(H3, 'b6'), H3_STEPS);
const H4_STEPS = chain(after(H4), [
  ['keep', 0.3],
  ['never', 0.3],
  ['once', 0.3],
]);
const HANDLE_LINES: Lines = [
  [H1, 'Handlers stacked outside the function answer.'],
  [H2, 'With the effect, a handler gets k: the rest of the function.'],
  [H3, 'It resumes k, and order returns.'],
  [H4, 'Or it keeps k for later, or never resumes it. k is resumed at most once.'],
];
const HANDLE = f(sceneEnd(heard(H4, 'b7'), H4_STEPS));
const CODE_X = 60;
const CODE_Y = 150;
const CODE_W = 620;
const LINE = 36;
const ASK_X = CODE_X + 400;
// The two handlers, the one that answers Confirm stacked on the one that answers Price.
const CONFIRMS = {x: 780, y: 150, w: 440, h: 96};
const PRICES = {x: 780, y: 262, w: 440, h: 96};
const CHOICES_Y = 392;
const HANDLE_LIT: Lit = [
  [H1_STEPS.boxes.from, [PRICE]],
  [H1_STEPS.answer.to, []],
  [H2_STEPS.confirm.from, [CONFIRM]],
  [H3_STEPS.ret.from, [RETURN]],
];
// The three things the handler can do with k: [from, how it is written, what order does].
const CHOICES: [number, string, string][] = [
  [H3_STEPS.ret.from, 'Resume(k, True)', "order goes on: ('bought', 3)"],
  [H4_STEPS.keep.from, 'keep k', 'order waits until the handler goes back to it'],
  [H4_STEPS.never.from, 'never resume k', "order stops at its yield: 'stopped'"],
];

const HandlerBox: React.FC<{box: typeof CONFIRMS; name: string; what: string; from: number; lit: boolean}> = ({box, name, what, from, lit}) => {
  const frame = useSceneFrame();
  return (
    <div
      style={{
        position: 'absolute', left: box.x, top: box.y, width: box.w, height: box.h, boxSizing: 'border-box', borderRadius: 14, padding: '12px 20px',
        background: PANEL, border: `3px solid ${lit ? LOOK : TEST}`, boxShadow: lit ? `0 0 22px ${LOOK}aa` : 'none', opacity: fadeAt(frame, from),
      }}
    >
      <div style={{fontFamily: MONO, fontSize: 22, fontWeight: 700, color: lit ? LOOK : TEST}}>{name}</div>
      <div style={{fontFamily: SANS, fontSize: 17, color: MUTED, marginTop: 4}}>{what}</div>
    </div>
  );
};

const Handling: React.FC = () => {
  const frame = useSceneFrame();
  const at = (seconds: number) => frame >= f(seconds);
  const priceY = lineMid(CODE_Y, LINE, PRICE);
  const confirmY = lineMid(CODE_Y, LINE, CONFIRM);
  const kTop = CODE_Y + PAD + (CONFIRM + 1) * LINE;
  const kFrom = H2_STEPS.k.from;
  const done = H3_STEPS.ret.from;
  const kRegion = at(kFrom) && !at(done);
  return (
    <AbsoluteFill>
      <Headlines lines={HANDLE_LINES} />
      <div style={{position: 'absolute', left: CODE_X, top: CODE_Y - 32, fontFamily: SANS, fontSize: 20, fontWeight: 700, color: ACCENT, opacity: fadeAt(frame, H1_STEPS.boxes.from)}}>
        order.py: order
      </div>
      <CodeBlock lines={ORDER_CODE} x={CODE_X} y={CODE_Y} w={CODE_W} size={18} line={LINE} opacity={fadeAt(frame, H1_STEPS.boxes.from)} />
      <LitLines lit={HANDLE_LIT} x={CODE_X} y={CODE_Y} w={CODE_W} line={LINE} />
      {/* k: everything order has left to do after the Confirm. */}
      <div
        style={{
          position: 'absolute', left: CODE_X + 4, top: kTop, width: CODE_W - 8, height: LINE, borderRadius: 10, background: K,
          opacity: kRegion ? 0.2 * fadeAt(frame, kFrom, 0.4) : 0,
        }}
      />
      <div style={{position: 'absolute', left: CODE_X, top: CODE_Y + PAD * 2 + ORDER_CODE.length * LINE + 16, width: CODE_W, fontFamily: SANS, fontSize: 21, color: K, opacity: fadeAt(frame, kFrom) * (1 - fadeAt(frame, done, 0.3))}}>
        k: the rest of order, paused at the yield
      </div>
      <div style={{position: 'absolute', left: CODE_X, top: CODE_Y + PAD * 2 + ORDER_CODE.length * LINE + 56, width: CODE_W, fontFamily: SANS, fontSize: 24, fontWeight: 700, color: LOOK, opacity: fadeAt(frame, H4_STEPS.once.from)}}>
        k is resumed at most once
      </div>
      <div style={{position: 'absolute', left: CONFIRMS.x, top: CONFIRMS.y - 32, fontFamily: SANS, fontSize: 18, color: MUTED, opacity: fadeAt(frame, H1_STEPS.boxes.from)}}>
        handlers, stacked outside order
      </div>
      <HandlerBox box={CONFIRMS} name="yes" what="answers Confirm" from={H1_STEPS.boxes.from} lit={at(H2_STEPS.confirm.to) && !at(done)} />
      <HandlerBox box={PRICES} name="prices" what="answers Price from a table" from={H1_STEPS.boxes.from} lit={at(H1_STEPS.price.to) && !at(H1_STEPS.answer.to)} />
      {/* Price goes out to prices, and its answer comes back to the yield. */}
      <Moving text="Price('tea')" color={ACCENT} from={H1_STEPS.price.from} to={H1_STEPS.price.to} until={H1_STEPS.answer.from} x0={ASK_X} y0={priceY} x1={PRICES.x + 300} y1={PRICES.y + 48} />
      <Moving text="3" color={TEST} from={H1_STEPS.answer.from} to={H1_STEPS.answer.to} until={H1_STEPS.answer.to + 0.6} x0={PRICES.x + 300} y0={PRICES.y + 48} x1={ASK_X} y1={priceY} />
      {/* Confirm goes out to yes, and k with it. */}
      <Moving text="Confirm(3)" color={ACCENT} from={H2_STEPS.confirm.from} to={H2_STEPS.confirm.to} until={H3_STEPS.resume.from} x0={ASK_X} y0={confirmY} x1={CONFIRMS.x + 250} y1={CONFIRMS.y + 30} />
      <Moving text="k" color={K} from={kFrom} to={H2_STEPS.k.to} until={H3_STEPS.resume.from} x0={CODE_X + CODE_W - 60} y0={kTop + LINE / 2} x1={CONFIRMS.x + 390} y1={CONFIRMS.y + 30} />
      {/* The handler resumes k: k goes back, once, and order runs to its return. */}
      <Moving text="Resume(k, True)" color={TEST} from={H3_STEPS.resume.from} to={H3_STEPS.resume.to} until={done + 0.6} x0={CONFIRMS.x + 300} y0={CONFIRMS.y + 48} x1={ASK_X} y1={confirmY} />
      <div
        style={{
          position: 'absolute', left: CONFIRMS.x, top: CHOICES_Y, width: CONFIRMS.w, boxSizing: 'border-box', padding: '14px 20px', background: PANEL, borderRadius: 14,
          border: '2px solid #2a2e40', opacity: fadeAt(frame, done),
        }}
      >
        <div style={{fontFamily: SANS, fontSize: 18, fontWeight: 700, color: MUTED, marginBottom: 10}}>what yes can do with k</div>
        {CHOICES.map(([from, how, what], i) => (
          <div key={how} style={{height: 64, opacity: fadeAt(frame, from)}}>
            <div style={{fontFamily: MONO, fontSize: 20, fontWeight: 700, color: i === 0 ? LOOK : TEST}}>{how}</div>
            <div style={{fontFamily: SANS, fontSize: 17, color: TEXT}}>{what}</div>
          </div>
        ))}
      </div>
    </AbsoluteFill>
  );
};

// 6. The same function in five stacks, side by side.
const S1 = 0;
const S1_STEPS = chain(after(S1), [['tests', 0.3]]);
const S2 = nextLine(heard(S1, 's1'), S1_STEPS);
const S2_STEPS = chain(after(S2), [
  ['replay', 0.3],
  ['bot', 0.3],
  ['many', 0.3],
  ['production', 0.3],
  ['zero', 0.3],
]);
const STACK_LINES: Lines = [
  [S1, 'Swap the stack of handlers: the same function runs another way.'],
  [S2, 'One function, unchanged, under many stacks.'],
];
const STACKS_LENGTH = f(sceneEnd(heard(S2, 's2'), S2_STEPS));
const SMALL = (cx: number): Geometry => ({cx, cy: 300, bw: 130, bh: 64, gap: 22, font: 13});
const STACKS: [Step, string, Ring[]][] = [
  [S1_STEPS.tests, 'tests', [{label: 'yes'}, {label: 'prices'}]],
  [S2_STEPS.replay, 'replay', [{label: 'replay(tape)'}]],
  [S2_STEPS.bot, 'a bot', [{label: 'bot'}, {label: 'prices'}]],
  [S2_STEPS.many, 'many at once', [{label: 'yes'}, {label: 'prices'}, {label: 'scheduled'}]],
  [S2_STEPS.production, 'production', [{label: 'ask a person', color: PROD}, {label: 'shop prices', color: PROD}]],
];

const Stacks: React.FC = () => {
  const frame = useSceneFrame();
  return (
    <AbsoluteFill>
      <Headlines lines={STACK_LINES} />
      {STACKS.map(([step, name, rings], i) => {
        const cx = 140 + i * 250;
        return (
          <React.Fragment key={name}>
            <Tower rings={rings} g={SMALL(cx)} opacity={fadeAt(frame, step.from)} title="order()" />
            <div style={{position: 'absolute', left: cx - 110, width: 220, top: 460, textAlign: 'center', fontFamily: SANS, fontSize: 22, color: MUTED, opacity: fadeAt(frame, step.from)}}>
              {name}
            </div>
          </React.Fragment>
        );
      })}
      <div style={{position: 'absolute', left: 0, right: 0, top: 545, textAlign: 'center', fontFamily: SANS, fontSize: 26, color: LOOK, opacity: fadeAt(frame, S2_STEPS.zero.from)}}>
        order: 0 lines changed
      </div>
    </AbsoluteFill>
  );
};

// 7. How to start, and what the article does next.
const E1 = 0;
// The three things come as the line says them, after pip install doeff.
const E1_STEPS = chain(after(E1), [
  ['pip', 0.3],
  ['do', 0.3, 3.0],
  ['yield', 0.3],
  ['handler', 0.3],
]);
const E2 = nextLine(heard(E1, 'e1'), E1_STEPS);
const E2_STEPS = chain(after(E2), [['next', 0.3]]);
const START_LINES: Lines = [
  [E1, 'pip install doeff, then three things: @do, yield, a handler.'],
  [E2, 'Next: one card fight, run under six stacks.'],
];
const START = f(sceneEnd(heard(E2, 'e2'), E2_STEPS));
const THREE: [Step, string, string][] = [
  [E1_STEPS.do, '@do', 'makes a generator function a program'],
  [E1_STEPS.yield, 'yield', 'hands an effect out and gets the answer'],
  [E1_STEPS.handler, 'a handler', 'answers effects, and holds k'],
];

const Start: React.FC = () => {
  const frame = useSceneFrame();
  const next = fadeAt(frame, E2_STEPS.next.from);
  return (
    <AbsoluteFill>
      <Headlines lines={START_LINES} />
      <div style={{opacity: 1 - 0.65 * next}}>
        <div
          style={{
            position: 'absolute', left: 340, top: 130, width: 600, boxSizing: 'border-box', padding: '18px 0', borderRadius: 14, background: '#0b0d13',
            border: '2px solid #2a2e40', textAlign: 'center', fontFamily: MONO, fontSize: 36, color: TEST, opacity: fadeAt(frame, E1_STEPS.pip.from),
          }}
        >
          pip install doeff
        </div>
        {THREE.map(([step, name, what], i) => (
          <div
            key={name}
            style={{
              position: 'absolute', left: 110 + i * 360, top: 270, width: 340, boxSizing: 'border-box', padding: '16px 20px', borderRadius: 14, background: PANEL,
              border: `2px solid ${ACCENT}`, opacity: fadeAt(frame, step.from),
            }}
          >
            <div style={{fontFamily: MONO, fontSize: 30, fontWeight: 700, color: ACCENT}}>{name}</div>
            <div style={{fontFamily: SANS, fontSize: 18, color: TEXT, marginTop: 6}}>{what}</div>
          </div>
        ))}
      </div>
      <div style={{position: 'absolute', left: 0, right: 0, top: 470, textAlign: 'center', opacity: next}}>
        <div style={{fontFamily: SANS, fontSize: 22, color: MUTED}}>next in this article</div>
        <div style={{fontFamily: MONO, fontSize: 34, fontWeight: 700, color: TEXT, marginTop: 8}}>battle(): one card fight, six stacks</div>
      </div>
    </AbsoluteFill>
  );
};

export const ARTICLE_CONCEPT = narrate('article-concept', DURATIONS, NARRATION, [
  {length: TITLE, starts: startsOf(TITLE_LINES), ids: ['i1', 'i2'], node: <Title />},
  {length: IDEA, starts: startsOf(IDEA_LINES), ids: ['a1', 'a2', 'a3', 'a4'], node: <Idea />},
  {length: COMPARE, starts: startsOf(COMPARE_LINES), ids: ['a5', 'a6', 'a7', 'a8'], node: <Compare />},
  {length: FN, starts: startsOf(FN_LINES), ids: ['b1', 'b2', 'b3'], node: <Function />},
  {length: HANDLE, starts: startsOf(HANDLE_LINES), ids: ['b4', 'b5', 'b6', 'b7'], node: <Handling />},
  {length: STACKS_LENGTH, starts: startsOf(STACK_LINES), ids: ['s1', 's2'], node: <Stacks />},
  {length: START, starts: startsOf(START_LINES), ids: ['e1', 'e2'], node: <Start />},
]);

// The motions of each scene, headlines included, and its seconds: pace.mjs prints the gaps between them.
const paced = (lines: Lines, steps: Record<string, Step>[], length: number) => ({
  motions: [...lines.map(([from]) => headline(from)), ...steps.flatMap((s) => Object.values(s))],
  seconds: length / 60,
});
export const PACED: Record<string, {motions: Step[]; seconds: number}> = {
  Title: paced(TITLE_LINES, [T_STEPS], TITLE),
  Idea: paced(IDEA_LINES, [A1_STEPS, A2_STEPS, A3_STEPS, A4_STEPS], IDEA),
  Compare: paced(COMPARE_LINES, [B1_STEPS, B2_STEPS, B3_STEPS, B4_STEPS], COMPARE),
  Function: paced(FN_LINES, [F1_STEPS, F2_STEPS, F3_STEPS], FN),
  Handling: paced(HANDLE_LINES, [H1_STEPS, H2_STEPS, H3_STEPS, H4_STEPS], HANDLE),
  Stacks: paced(STACK_LINES, [S1_STEPS, S2_STEPS], STACKS_LENGTH),
  Start: paced(START_LINES, [E1_STEPS, E2_STEPS], START),
};
