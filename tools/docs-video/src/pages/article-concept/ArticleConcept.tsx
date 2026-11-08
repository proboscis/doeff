// The first video of the article "Algebraic effects for Python" (docs/articles/algebraic-effects-in-python.md) and of
// the README, before the card battle: what doeff is, in about a minute. A title card says what the video is about; a
// small function, order, is read from the top and yields two effects without saying who answers; the effects travel
// out to handlers stacked outside it, one of them with k, and the handler resumes k, keeps it or never resumes it, at
// most once; the same function sits unchanged in five stacks; then pip install doeff, and the three things to start
// with. Every code line on screen is a line of measure/order.py (check_code.py checks it), and every result is a line
// of its output.
import React from 'react';
import {AbsoluteFill} from 'remotion';
import {fadeAt} from '../../kit/motion';
import {narrate} from '../../kit/narrated';
import {CodeBlock, Headlines, Lines, lineMid, PAD, startsOf} from '../../kit/parts';
import {ACCENT, f, LOOK, MONO, MUTED, PANEL, PROD, SANS, TEST, TEXT} from '../../kit/theme';
import {useSceneFrame} from '../../kit/warp';
import {Geometry, K, Moving, Ring, Tower} from '../article-opening/ArticleOpening';
import DURATIONS from './durations.json';
import NARRATION from './narration.json';

// 1. The title card: what this video is about, held still.
const TITLE_LINES: Lines = [
  [0, 'doeff, a Python library: the idea in about a minute'],
  [6.0, 'doeff: algebraic effects, written in Python'],
];
const TITLE = f(12);

const Title: React.FC = () => {
  const frame = useSceneFrame();
  return (
    <AbsoluteFill>
      <Headlines lines={TITLE_LINES} />
      <div style={{position: 'absolute', left: 0, right: 0, top: 200, textAlign: 'center', opacity: fadeAt(frame, 0.2, 0.5)}}>
        <div style={{fontFamily: SANS, fontSize: 104, fontWeight: 700, color: TEXT}}>doeff</div>
        <div style={{fontFamily: SANS, fontSize: 34, color: ACCENT, marginTop: 6}}>Algebraic effects for Python</div>
      </div>
      <div style={{position: 'absolute', left: 0, right: 0, top: 470, textAlign: 'center', fontFamily: SANS, fontSize: 26, color: MUTED, opacity: fadeAt(frame, 6.2, 0.5)}}>
        A function yields effects. Handlers outside it answer, and decide how it goes on.
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

// 2. The function read from the top: it asks for what it needs as effects, and never says who answers.
const FN_LINES: Lines = [[0, 'A function yields effects: what it needs, not who answers.']];
const FN = f(9);
const FN_X = 60;
const FN_Y = 150;
const FN_W = 720;
const FN_LINE = 40;
const FN_LIT: Lit = [
  [0.3, [0, 1]],
  [1.4, [PRICE]],
  [2.6, [CONFIRM]],
  [3.8, [RETURN]],
  [5.0, [PRICE, CONFIRM]],
];
// One row per effect as its line is lit: [from, the effect, what it asks].
const ASKS: [number, string, string][] = [
  [1.4, 'Price(item)', 'what does it cost?'],
  [2.6, 'Confirm(price)', 'may I pay that much?'],
];

const Function: React.FC = () => {
  const frame = useSceneFrame();
  return (
    <AbsoluteFill>
      <Headlines lines={FN_LINES} />
      <div style={{position: 'absolute', left: FN_X, top: FN_Y - 32, fontFamily: SANS, fontSize: 20, fontWeight: 700, color: ACCENT, opacity: fadeAt(frame, 0.1)}}>
        order.py: order
      </div>
      <CodeBlock lines={ORDER_CODE} x={FN_X} y={FN_Y} w={FN_W} size={22} line={FN_LINE} opacity={fadeAt(frame, 0.1)} />
      <LitLines lit={FN_LIT} x={FN_X} y={FN_Y} w={FN_W} line={FN_LINE} />
      <div
        style={{
          position: 'absolute', left: 820, top: FN_Y, width: 400, boxSizing: 'border-box', padding: '16px 22px', background: PANEL, borderRadius: 14,
          border: '2px solid #2a2e40', opacity: fadeAt(frame, 1.4),
        }}
      >
        <div style={{fontFamily: SANS, fontSize: 18, fontWeight: 700, color: MUTED, marginBottom: 12}}>the effects order yields</div>
        {ASKS.map(([from, effect, what]) => (
          <div key={effect} style={{height: 66, opacity: fadeAt(frame, from)}}>
            <div style={{fontFamily: MONO, fontSize: 21, fontWeight: 700, color: ACCENT}}>{effect}</div>
            <div style={{fontFamily: SANS, fontSize: 18, color: TEXT}}>{what}</div>
          </div>
        ))}
        <div style={{fontFamily: SANS, fontSize: 20, color: LOOK, marginTop: 6, opacity: fadeAt(frame, 5.0)}}>who answers? order never says.</div>
      </div>
    </AbsoluteFill>
  );
};

// 3. The effects travel out to handlers stacked outside order; one handler also gets k, and decides what to do with it.
const HANDLE_LINES: Lines = [
  [0, 'Handlers stacked outside the function answer.'],
  [6.0, 'With the effect, a handler gets k: the rest of the function.'],
  [11.0, 'It resumes k, keeps k for later, or never resumes it.'],
  [15.5, 'Whichever it picks, k is resumed at most once.'],
];
const HANDLE = f(20);
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
  [0.3, [PRICE]],
  [4.8, []],
  [6.3, [CONFIRM]],
  [17.5, [RETURN]],
];
// The three things the handler can do with k: [from, how it is written, what order does].
const CHOICES: [number, string, string][] = [
  [11.3, 'Resume(k, True)', "order goes on: ('bought', 3)"],
  [12.2, 'keep k', 'order waits until the handler goes back to it'],
  [13.1, 'never resume k', "order stops at its yield: 'stopped'"],
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
  const kRegion = at(6.9) && !at(17.5);
  return (
    <AbsoluteFill>
      <Headlines lines={HANDLE_LINES} />
      <div style={{position: 'absolute', left: CODE_X, top: CODE_Y - 32, fontFamily: SANS, fontSize: 20, fontWeight: 700, color: ACCENT, opacity: fadeAt(frame, 0.1)}}>
        order.py: order
      </div>
      <CodeBlock lines={ORDER_CODE} x={CODE_X} y={CODE_Y} w={CODE_W} size={18} line={LINE} opacity={fadeAt(frame, 0.1)} />
      <LitLines lit={HANDLE_LIT} x={CODE_X} y={CODE_Y} w={CODE_W} line={LINE} />
      {/* k: everything order has left to do after the Confirm. */}
      <div
        style={{
          position: 'absolute', left: CODE_X + 4, top: kTop, width: CODE_W - 8, height: LINE, borderRadius: 10, background: K,
          opacity: kRegion ? 0.2 * fadeAt(frame, 6.9, 0.4) : 0,
        }}
      />
      <div style={{position: 'absolute', left: CODE_X, top: CODE_Y + PAD * 2 + ORDER_CODE.length * LINE + 16, width: CODE_W, fontFamily: SANS, fontSize: 21, color: K, opacity: fadeAt(frame, 6.9) * (1 - fadeAt(frame, 17.5, 0.3))}}>
        k: the rest of order, paused at the yield
      </div>
      <div style={{position: 'absolute', left: CODE_X, top: CODE_Y + PAD * 2 + ORDER_CODE.length * LINE + 56, width: CODE_W, fontFamily: SANS, fontSize: 24, fontWeight: 700, color: LOOK, opacity: fadeAt(frame, 15.7)}}>
        k is resumed at most once
      </div>
      <div style={{position: 'absolute', left: CONFIRMS.x, top: CONFIRMS.y - 32, fontFamily: SANS, fontSize: 18, color: MUTED, opacity: fadeAt(frame, 0.3)}}>
        handlers, stacked outside order
      </div>
      <HandlerBox box={CONFIRMS} name="yes" what="answers Confirm" from={1.2} lit={at(7.8) && !at(17.5)} />
      <HandlerBox box={PRICES} name="prices" what="answers Price from a table" from={0.3} lit={at(3.0) && !at(4.8)} />
      {/* Price goes out to prices, and its answer comes back to the yield. */}
      <Moving text="Price('tea')" color={ACCENT} from={2.1} to={3.0} until={3.9} x0={ASK_X} y0={priceY} x1={PRICES.x + 300} y1={PRICES.y + 48} />
      <Moving text="3" color={TEST} from={3.9} to={4.8} until={5.4} x0={PRICES.x + 300} y0={PRICES.y + 48} x1={ASK_X} y1={priceY} />
      {/* Confirm goes out to yes, and k with it. */}
      <Moving text="Confirm(3)" color={ACCENT} from={6.9} to={7.8} until={16.6} x0={ASK_X} y0={confirmY} x1={CONFIRMS.x + 250} y1={CONFIRMS.y + 30} />
      <Moving text="k" color={K} from={8.7} to={9.6} until={16.6} x0={CODE_X + CODE_W - 60} y0={kTop + LINE / 2} x1={CONFIRMS.x + 390} y1={CONFIRMS.y + 30} />
      {/* The handler resumes k: k goes back, once, and order runs to its return. */}
      <Moving text="Resume(k, True)" color={TEST} from={16.6} to={17.5} until={18.4} x0={CONFIRMS.x + 300} y0={CONFIRMS.y + 48} x1={ASK_X} y1={confirmY} />
      <div
        style={{
          position: 'absolute', left: CONFIRMS.x, top: CHOICES_Y, width: CONFIRMS.w, boxSizing: 'border-box', padding: '14px 20px', background: PANEL, borderRadius: 14,
          border: '2px solid #2a2e40', opacity: fadeAt(frame, 11.3),
        }}
      >
        <div style={{fontFamily: SANS, fontSize: 18, fontWeight: 700, color: MUTED, marginBottom: 10}}>what yes can do with k</div>
        {CHOICES.map(([from, how, what], i) => (
          <div key={how} style={{height: 64, opacity: fadeAt(frame, from)}}>
            <div style={{fontFamily: MONO, fontSize: 20, fontWeight: 700, color: i === 0 && at(17.5) ? LOOK : TEST}}>{how}</div>
            <div style={{fontFamily: SANS, fontSize: 17, color: TEXT}}>{what}</div>
          </div>
        ))}
      </div>
    </AbsoluteFill>
  );
};

// 4. The same function in five stacks, side by side.
const STACK_LINES: Lines = [[0, 'One function, unchanged, under many stacks of handlers.']];
const STACKS_LENGTH = f(10);
const SMALL = (cx: number): Geometry => ({cx, cy: 300, bw: 130, bh: 64, gap: 22, font: 13});
const STACKS: [string, Ring[]][] = [
  ['tests', [{label: 'yes'}, {label: 'prices'}]],
  ['replay', [{label: 'replay(tape)'}]],
  ['a bot', [{label: 'bot'}, {label: 'prices'}]],
  ['many at once', [{label: 'yes'}, {label: 'prices'}, {label: 'scheduled'}]],
  ['production', [{label: 'ask a person', color: PROD}, {label: 'shop prices', color: PROD}]],
];

const Stacks: React.FC = () => {
  const frame = useSceneFrame();
  return (
    <AbsoluteFill>
      <Headlines lines={STACK_LINES} />
      {STACKS.map(([name, rings], i) => {
        const cx = 140 + i * 250;
        const from = 3.0 + i * 0.9;
        return (
          <React.Fragment key={name}>
            <Tower rings={rings} g={SMALL(cx)} opacity={fadeAt(frame, from)} title="order()" />
            <div style={{position: 'absolute', left: cx - 110, width: 220, top: 460, textAlign: 'center', fontFamily: SANS, fontSize: 22, color: MUTED, opacity: fadeAt(frame, from)}}>
              {name}
            </div>
          </React.Fragment>
        );
      })}
      <div style={{position: 'absolute', left: 0, right: 0, top: 545, textAlign: 'center', fontFamily: SANS, fontSize: 26, color: LOOK, opacity: fadeAt(frame, 7.5)}}>
        order: 0 lines changed
      </div>
    </AbsoluteFill>
  );
};

// 5. How to start, and what the article does next.
const START_LINES: Lines = [
  [0, 'pip install doeff, then three things: @do, yield, a handler.'],
  [7.5, 'Next: one card fight, run under six stacks.'],
];
const START = f(12.5);
const THREE: [number, string, string][] = [
  [3.9, '@do', 'makes a generator function a program'],
  [4.8, 'yield', 'hands an effect out and gets the answer'],
  [5.7, 'a handler', 'answers effects, and holds k'],
];

const Start: React.FC = () => {
  const frame = useSceneFrame();
  const next = fadeAt(frame, 7.7);
  return (
    <AbsoluteFill>
      <Headlines lines={START_LINES} />
      <div style={{opacity: 1 - 0.65 * next}}>
        <div
          style={{
            position: 'absolute', left: 340, top: 130, width: 600, boxSizing: 'border-box', padding: '18px 0', borderRadius: 14, background: '#0b0d13',
            border: '2px solid #2a2e40', textAlign: 'center', fontFamily: MONO, fontSize: 36, color: TEST, opacity: fadeAt(frame, 0.3),
          }}
        >
          pip install doeff
        </div>
        {THREE.map(([from, name, what], i) => (
          <div
            key={name}
            style={{
              position: 'absolute', left: 110 + i * 360, top: 270, width: 340, boxSizing: 'border-box', padding: '16px 20px', borderRadius: 14, background: PANEL,
              border: `2px solid ${ACCENT}`, opacity: fadeAt(frame, from),
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
  {length: TITLE, starts: startsOf(TITLE_LINES), ids: ['c1', 'c2'], node: <Title />},
  {length: FN, starts: startsOf(FN_LINES), ids: ['c3'], node: <Function />},
  {length: HANDLE, starts: startsOf(HANDLE_LINES), ids: ['c4', 'c5', 'c6', 'c7'], node: <Handling />},
  {length: STACKS_LENGTH, starts: startsOf(STACK_LINES), ids: ['c8'], node: <Stacks />},
  {length: START, starts: startsOf(START_LINES), ids: ['c9', 'c10'], node: <Start />},
]);
