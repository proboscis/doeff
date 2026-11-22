// The first video of the article "Algebraic effects for Python" (docs/articles/algebraic-effects-in-python.md) and of
// the README, before the card battle: what algebraic effects are, then how doeff writes them, as a motion video.
// 1. The title: the name, and the round trip the video is about (an effect out to a handler, an answer back).
// 2. The idea with no code. The example is said before it is used: a program that buys tea, small enough to read at a
//    glance, which needs two things from outside itself. Its request crosses the edge of the program to a handler,
//    and the answer comes back to where it asked.
// 3. Next to an exception, whose handler never goes back.
// 4. The same program in doeff: its plain rows become the lines of order, read from the top; each yield hands an
//    effect out, and order never says who answers.
// 5. Handlers are rings around order. One of them gets k, the rest of order; it resumes it, keeps it or never resumes
//    it, at most once.
// 6. The same function, unchanged, in five stacks.
// 7. How to start.
// 8. The article's example before the article uses it: what battle does, and why it was chosen (it needs four things
//    from outside, and each can be answered in many ways).
// Every code line on screen is a line of measure/order.py (check_code.py checks it), and every result is a line of
// its output; the card fight's numbers and handler names are those of the article's listing of spire.py and its runs.
// Each scene's motions come one at a time (pace.ts), and each headline waits for its line and its motions.
import React from 'react';
import {AbsoluteFill} from 'remotion';
import {fadeAt} from '../../kit/motion';
import {narrate} from '../../kit/narrated';
import {chain, heardBy, LINE_FADE, nextLine, PAUSE, sceneEnd, Step} from '../../kit/pace';
import {Clock} from '../../kit/parts';
import {ACCENT, f, FAIL, LOOK, MUTED, PROD, TEST, TEXT} from '../../kit/theme';
import {useSceneFrame} from '../../kit/warp';
import {K} from '../article-opening/ArticleOpening';
import DURATIONS from './durations.json';
import NARRATION from './narration.json';
import {arcOf, Backdrop, Capsule, Chapter, CODE, CodeWindow, codeMid, DISPLAY, glass, Handler, INK, LINE, pointOn, Program, RingOf, Rings, rowMid, Seam} from './style/look';
import {along, Draw, Headline, pop, Rise, Said, saidStarts, Travel, typed} from './style/motion';

const heard = heardBy(DURATIONS);
// The first motion of a line: PAUSE s after its headline has arrived.
const after = (headline: number) => headline + LINE_FADE + PAUSE;
// A headline as a motion, for PACED.
const headline = (from: number): Step => ({from, to: from + LINE_FADE});
const SCENES = 8;
// A panel things pass behind.
const SOLID = 'linear-gradient(160deg, #1b1d2c, #0c0d15)';

const Note: React.FC<{x: number; y: number; w: number; text: string; color?: string; size?: number; align?: 'left' | 'center' | 'right'}> = ({
  x, y, w, text, color = TEXT, size = 20, align = 'left',
}) => <div style={{position: 'absolute', left: x, top: y, width: w, textAlign: align, fontFamily: DISPLAY, fontSize: size, fontWeight: 600, color}}>{text}</div>;

// 1. The title: the name, the round trip, then the two parts of the video.
const T1 = 0;
const T1_STEPS = chain(0.3, [
  ['name', 0.7],
  ['out', 1.1],
  ['back', 1.1],
]);
const T2 = nextLine(heard(T1, 'i1'), T1_STEPS);
const T2_STEPS = chain(after(T2), [
  ['one', 0.5],
  ['two', 0.5],
]);
const TITLE_SAID: Said = [
  [T1, []],
  [T2, [['First'], ['what algebraic effects are,', ACCENT], ['then'], ['how doeff writes them', TEST]]],
];
const TITLE = f(sceneEnd(heard(T2, 'i2'), T2_STEPS));
const TRIP_OUT = 'M 170 330 C 170 40, 1110 40, 1110 330';
const TRIP_BACK = 'M 1110 330 C 1110 620, 170 620, 170 330';

const Station: React.FC<{x: number; n: string; title: string; note: string; lit?: boolean; children: React.ReactNode}> = ({x, n, title, note, lit = false, children}) => (
  <>
    <div style={{position: 'absolute', left: x + 44, top: 118, fontFamily: DISPLAY, fontSize: 230, fontWeight: 800, lineHeight: 1, color: 'transparent', WebkitTextStroke: `2px ${lit ? `${LOOK}99` : 'rgba(255,255,255,0.2)'}`}}>{n}</div>
    <div style={{position: 'absolute', left: x + 70, top: 270, width: 430, height: 300, ...glass(LINE, lit), background: SOLID}}>
      <div style={{padding: '22px 26px 0', fontFamily: DISPLAY, fontSize: 30, fontWeight: 800, letterSpacing: '-0.01em', color: TEXT}}>{title}</div>
      <div style={{padding: '4px 26px 0', fontFamily: DISPLAY, fontSize: 19, color: MUTED}}>{note}</div>
      <div style={{position: 'absolute', left: 26, right: 26, top: 122, bottom: 22}}>{children}</div>
    </div>
  </>
);

const Title: React.FC = () => {
  const frame = useSceneFrame();
  const shine = along(frame, T1_STEPS.name);
  return (
    <AbsoluteFill>
      <Chapter n={1} of={SCENES} name="doeff" />
      <Headline lines={TITLE_SAID} size={36} />
      <Rise at={0} out={T2} y={0}>
        <Rise at={T1_STEPS.name.from} y={40} scale={0.9} origin={[640, 300]}>
          <div
            style={{
              position: 'absolute', left: 0, right: 0, top: 180, textAlign: 'center', fontFamily: DISPLAY, fontSize: 200, fontWeight: 800, letterSpacing: '-0.05em', lineHeight: 1,
              background: `linear-gradient(100deg, #ffffff ${25 - 60 * (1 - shine)}%, ${ACCENT} ${62 - 40 * (1 - shine)}%, ${TEST} 100%)`, WebkitBackgroundClip: 'text', color: 'transparent',
              filter: `drop-shadow(0 0 40px ${ACCENT}66)`,
            }}
          >
            doeff
          </div>
        </Rise>
        <Rise at={T1_STEPS.name.from + 0.35} y={16}>
          <div style={{position: 'absolute', left: 0, right: 0, top: 402, textAlign: 'center', fontFamily: DISPLAY, fontSize: 40, fontWeight: 600, letterSpacing: '-0.01em', color: TEXT}}>Algebraic effects for Python</div>
        </Rise>
        <Travel id="t-out" d={TRIP_OUT} color={ACCENT} step={T1_STEPS.out} text="an effect" mono={false} size={22} tail={420} until={T1_STEPS.out.to + 0.3} />
        <Travel id="t-back" d={TRIP_BACK} color={TEST} step={T1_STEPS.back} text="an answer" mono={false} size={22} tail={420} until={T1_STEPS.back.to + 0.3} />
        <Rise at={T1_STEPS.name.from + 0.5} y={0}>
          <div style={{position: 'absolute', left: 70, top: 298, padding: '12px 20px', ...glass(`${ACCENT}aa`), background: SOLID, fontFamily: DISPLAY, fontSize: 20, fontWeight: 600, color: TEXT}}>a program</div>
          <div style={{position: 'absolute', left: 1040, top: 298, padding: '12px 20px', ...glass(`${TEST}aa`), background: SOLID, fontFamily: DISPLAY, fontSize: 20, fontWeight: 600, color: TEXT}}>a handler</div>
        </Rise>
      </Rise>
      <Rise at={T2_STEPS.one.from} y={40} scale={0.92} origin={[345, 420]}>
        <Station x={60} n="1" title="Algebraic effects" note="the idea, without code" lit>
          <div style={{position: 'absolute', left: 0, top: 44, width: 120, height: 70, borderRadius: 12, border: `2px solid ${ACCENT}`, background: `${ACCENT}22`}} />
          <div style={{position: 'absolute', right: 0, top: 44, width: 120, height: 70, borderRadius: 12, border: `2px solid ${TEST}`, background: `${TEST}22`}} />
          <svg width="378" height="156" style={{position: 'absolute', overflow: 'visible'}}>
            <path d="M 120 62 C 170 0, 210 0, 258 62" fill="none" stroke={ACCENT} strokeWidth="3" strokeLinecap="round" />
            <path d="M 258 96 C 210 156, 170 156, 120 96" fill="none" stroke={TEST} strokeWidth="3" strokeLinecap="round" />
            <circle cx="205" cy="16" r="8" fill={ACCENT} />
            <circle cx="172" cy="140" r="8" fill={TEST} />
          </svg>
        </Station>
      </Rise>
      <Rise at={T2_STEPS.two.from} y={40} scale={0.92} origin={[935, 420]}>
        <Station x={650} n="2" title="The same idea in doeff" note="a function, yield, handlers">
          {['@do', 'def order(item):', '    price = yield Price(item)', '    ok = yield Confirm(price)'].map((text, i) => (
            <div key={i} style={{height: 36, fontFamily: CODE, fontSize: 19, color: i === 0 ? ACCENT : TEXT, whiteSpace: 'pre', opacity: 0.9}}>
              {text.split('yield').map((part, j) => (
                <React.Fragment key={j}>
                  {j > 0 && <span style={{color: ACCENT, fontWeight: 700, textShadow: `0 0 16px ${ACCENT}`}}>yield</span>}
                  {part}
                </React.Fragment>
              ))}
            </div>
          ))}
        </Station>
        <svg width="1280" height="720" style={{position: 'absolute'}}>
          <path d="M 564 420 L 716 420" stroke={LOOK} strokeWidth="3" strokeDasharray="2 10" strokeLinecap="round" />
          <path d="M 704 410 L 718 420 L 704 430" fill="none" stroke={LOOK} strokeWidth="3" strokeLinecap="round" />
        </svg>
      </Rise>
    </AbsoluteFill>
  );
};

// 2. The idea, with no code. The example first: what it is and why it was chosen. Then its request goes out across the
// edge of the program to a handler, and the answer comes back to where it asked.
const TEA_ROWS = ['takes an order for tea', 'needs: what does tea cost?', 'needs: may I pay that much?', 'buys it, or skips it'];
const P = {x: 90, y: 220, w: 500};
const ASK_Y = rowMid(P.y, 1);
const A1 = 0;
const A1_STEPS = chain(after(A1), [
  ['seam', 0.5],
  ['price', 0.3],
  ['person', 0.3],
  ['time', 0.3],
]);
const TEA = nextLine(heard(A1, 'a1'), A1_STEPS);
const TEA_STEPS = chain(after(TEA), [
  ['program', 0.5],
  ['why', 0.4],
  ['needs', 0.6],
]);
const A2 = nextLine(heard(TEA, 't1'), TEA_STEPS);
const A2_STEPS = chain(after(A2), [
  ['effect', 0.5],
  ['label', 0.3],
]);
const A3 = nextLine(heard(A2, 'a2'), A2_STEPS);
const A3_STEPS = chain(after(A3), [
  ['handler', 0.4],
  ['out', 1.0],
  ['how', 0.3],
]);
const A4 = nextLine(heard(A3, 'a3'), A3_STEPS);
const A4_STEPS = chain(after(A4), [
  ['back', 1.0],
  ['goes', 0.4],
]);
const IDEA_SAID: Said = [
  [A1, [['A program needs things from'], ['outside itself', TEST]]],
  [TEA, [['Our example:'], ['a program that buys tea', LOOK]]],
  [A2, [['The program only says'], ['what it needs:', ACCENT], ['an effect', ACCENT]]],
  [A3, [['A handler,', TEST], ['outside the program, decides'], ['how', TEST]]],
  [A4, [['The handler'], ['answers.', TEST], ['The program'], ['goes on', LOOK], ['from where it asked.']]],
];
const IDEA = f(sceneEnd(heard(A4, 'a4'), A4_STEPS));
const ASK_OUT = arcOf(565, ASK_Y, 1010, 236, -150);
const ASK_BACK = arcOf(1010, 356, P.x + P.w + 30, ASK_Y + 4, 150);

const Idea: React.FC = () => {
  const frame = useSceneFrame();
  const at = (seconds: number) => frame >= f(seconds);
  const seam = along(frame, A1_STEPS.seam);
  const rows = TEA_ROWS.map((text, i) => ({
    text,
    lit: i === 1 && at(TEA_STEPS.needs.from) ? ACCENT : i === 2 && at(TEA_STEPS.needs.from) && !at(A2) ? ACCENT : i === 2 && at(A4_STEPS.goes.from) ? LOOK : undefined,
  }));
  return (
    <AbsoluteFill>
      <Chapter n={2} of={SCENES} name="The idea" />
      <Headline lines={IDEA_SAID} />
      {at(A1_STEPS.seam.from) && (
        <div style={{position: 'absolute', inset: 0, clipPath: `inset(0 0 ${(1 - seam) * 100}% 0)`}}>
          <Seam x={670} top={160} bottom={690} />
        </div>
      )}
      <Rise at={A1_STEPS.price.from} out={A3} y={14}>
        <Capsule x={900} y={262} text="a price?" color={TEXT} mono={false} size={26} ghost />
      </Rise>
      <Rise at={A1_STEPS.person.from} out={A3} y={14}>
        <Capsule x={950} y={392} text="a yes or no from a person?" color={TEXT} mono={false} size={22} ghost />
      </Rise>
      <Rise at={A1_STEPS.time.from} out={TEA} y={14}>
        <Capsule x={880} y={520} text="the time?" color={MUTED} mono={false} size={20} ghost />
      </Rise>
      <Rise at={TEA_STEPS.program.from} y={40} scale={0.94} origin={[P.x + P.w / 2, P.y + 120]}>
        <Program title="a program: buying tea" x={P.x} y={P.y} w={P.w} rows={rows} />
      </Rise>
      <Rise at={TEA_STEPS.why.from} out={A2} y={20}>
        <div style={{position: 'absolute', left: P.x, top: 500, width: P.w, padding: '14px 22px', ...glass(`${LOOK}66`)}}>
          <div style={{fontFamily: DISPLAY, fontSize: 13, fontWeight: 600, letterSpacing: '0.18em', color: LOOK}}>THE EXAMPLE, AND WHY</div>
          <div style={{fontFamily: DISPLAY, fontSize: 20, fontWeight: 600, color: TEXT, marginTop: 6}}>small enough to read at a glance,</div>
          <div style={{fontFamily: DISPLAY, fontSize: 20, fontWeight: 600, color: TEXT}}>and it needs two things from outside</div>
        </div>
      </Rise>
      <Draw d={arcOf(P.x + P.w, ASK_Y, 790, 262, -40)} color={ACCENT} step={TEA_STEPS.needs} dashed out={A2_STEPS.effect.from} />
      <Draw d={arcOf(P.x + P.w, rowMid(P.y, 2), 760, 392, 30)} color={ACCENT} step={TEA_STEPS.needs} dashed out={A2_STEPS.effect.from} />
      {/* The request, at the program's edge: what it needs, as a thing that can travel. */}
      <Rise at={A2_STEPS.effect.from} out={A3_STEPS.out.from - 0.3} y={0} scale={0.5} origin={[565, ASK_Y]}>
        <Capsule x={565} y={ASK_Y} text="what does tea cost?" color={ACCENT} mono={false} size={16} />
      </Rise>
      <Rise at={A2_STEPS.label.from} out={A4_STEPS.goes.from} y={14}>
        <Note x={P.x} y={500} w={P.w} text="an effect: what it needs, not how to get it" color={ACCENT} size={22} />
      </Rise>
      <Rise at={A3_STEPS.handler.from} y={30} scale={0.94} origin={[1010, 300]}>
        <Handler x={820} y={262} w={380} name="a handler" note={at(A3_STEPS.how.from) ? 'looks it up in a price list' : 'outside the program'} lit={at(A3_STEPS.out.to) && !at(A4_STEPS.back.to)} />
      </Rise>
      <Travel id="a-out" d={ASK_OUT} color={ACCENT} step={A3_STEPS.out} text="what does tea cost?" mono={false} size={16} until={A4_STEPS.back.from} />
      <Travel id="a-back" d={ASK_BACK} color={TEST} step={A4_STEPS.back} text="3" size={28} tail={300} until={A4_STEPS.goes.to + 0.8} />
      <Rise at={A4_STEPS.goes.from} y={14}>
        <Note x={P.x} y={500} w={P.w} text="it goes on from where it asked, with 3" color={LOOK} size={22} />
      </Rise>
    </AbsoluteFill>
  );
};

// 3. Next to an exception: both go out to a handler outside the program, and only the effect comes back.
const TOP = 160;
const BOTTOM = 410;
const B1 = 0;
const B1_STEPS = chain(after(B1), [
  ['lane', 0.5],
  ['raise', 0.3],
  ['throw', 0.9],
]);
const B2 = nextLine(heard(B1, 'a5'), B1_STEPS);
const B2_STEPS = chain(after(B2), [['lost', 0.9]]);
const B3 = nextLine(heard(B2, 'a6'), B2_STEPS);
const B3_STEPS = chain(after(B3), [
  ['lane', 0.5],
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
const COMPARE_SAID: Said = [
  [B1, [['You know half of this:'], ['an exception', FAIL]]],
  [B2, [['After the catch, it'], ['never goes back', FAIL]]],
  [B3, [['An effect handler'], ['answers,', TEST], ['and the program'], ['picks up', LOOK], ['where it left off']]],
  [B4, [['The program says'], ['what.', ACCENT], ['The handlers decide'], ['how.', TEST]]],
];
const COMPARE = f(sceneEnd(heard(B4, 'a8'), B4_STEPS));
const RAISE = arcOf(560, rowMid(TOP, 1), 796, 232, -70);
const NO_WAY = arcOf(830, 262, 566, rowMid(TOP, 2), 60);
const ASK = arcOf(560, rowMid(BOTTOM, 1), 790, 482, -70);
const ANSWER = arcOf(830, 512, 575, rowMid(BOTTOM, 2), 60);

const Compare: React.FC = () => {
  const frame = useSceneFrame();
  const at = (seconds: number) => frame >= f(seconds);
  const cross = pointOn(NO_WAY, 0.5);
  return (
    <AbsoluteFill>
      <Chapter n={3} of={SCENES} name="Exception or effect" />
      <Headline lines={COMPARE_SAID} />
      <Rise at={B1_STEPS.lane.from} y={30}>
        <Program
          title="an exception" x={90} y={TOP} w={470}
          rows={[{text: 'reads the order'}, {text: 'raises: something went wrong', lit: at(B1_STEPS.raise.from) ? FAIL : undefined}, {text: 'the rest of the program', out: at(B2_STEPS.lost.from + 0.5)}]}
        />
        <Handler x={830} y={200} w={360} name="catches it" note="a handler outside the program" lit={at(B1_STEPS.throw.to) && !at(B2_STEPS.lost.to)} />
      </Rise>
      <Travel id="c-raise" d={RAISE} color={FAIL} step={B1_STEPS.throw} text="error" mono={false} size={16} until={B2_STEPS.lost.from} />
      {/* The way back is never taken: it stops halfway. */}
      <Draw d={NO_WAY} color={FAIL} step={{from: B2_STEPS.lost.from, to: B2_STEPS.lost.from + 0.5}} dashed upTo={0.5} />
      <Rise at={B2_STEPS.lost.from + 0.5} y={0} scale={0.4} origin={[cross.x, cross.y]}>
        <svg width="1280" height="720" style={{position: 'absolute'}}>
          <circle cx={cross.x} cy={cross.y} r="19" fill={INK} stroke={FAIL} strokeWidth="2.5" />
          <path d={`M ${cross.x - 8} ${cross.y - 8} L ${cross.x + 8} ${cross.y + 8} M ${cross.x + 8} ${cross.y - 8} L ${cross.x - 8} ${cross.y + 8}`} stroke={FAIL} strokeWidth="3.5" strokeLinecap="round" />
        </svg>
        <Note x={830} y={312} w={360} text="no way back: the rest never runs" color={FAIL} size={19} />
      </Rise>
      <Rise at={B3_STEPS.lane.from} y={30}>
        <Program
          title="an effect" x={90} y={BOTTOM} w={470}
          rows={[{text: 'takes an order for tea'}, {text: 'needs: what does tea cost?', lit: at(B3_STEPS.ask.from) ? ACCENT : undefined}, {text: 'the rest of the program', lit: at(B3_STEPS.on.from) ? LOOK : undefined}]}
        />
        <Handler x={830} y={450} w={360} name="answers it" note="a handler outside the program" lit={at(B3_STEPS.out.to) && !at(B3_STEPS.back.to)} />
      </Rise>
      <Travel id="c-ask" d={ASK} color={ACCENT} step={B3_STEPS.out} text="cost?" mono={false} size={16} until={B3_STEPS.back.from} />
      <Travel id="c-answer" d={ANSWER} color={TEST} step={B3_STEPS.back} text="3" size={24} tail={200} until={B3_STEPS.on.to + 0.8} />
      <Rise at={B3_STEPS.on.from} y={12}>
        <Note x={830} y={562} w={360} text="goes back: the rest runs, with 3" color={LOOK} size={19} />
      </Rise>
      <Rise at={B4_STEPS.what.from} y={14}>
        <Note x={90} y={634} w={470} text="the program: what it needs" color={ACCENT} size={24} />
      </Rise>
      <Rise at={B4_STEPS.how.from} y={14}>
        <Note x={830} y={634} w={360} text="the handlers: how" color={TEST} size={24} />
      </Rise>
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

// 4. The same program in doeff: its rows become the lines of order, read from the top. Each yield hands an effect out.
const FN = {x: 70, y: 190, w: 740, size: 22, line: 44};
const F1 = 0;
const F1_STEPS = chain(after(F1), [['code', 0.8]]);
const F2 = nextLine(heard(F1, 'b1'), F1_STEPS);
const F2_STEPS = chain(after(F2), [
  ['price', 0.4],
  ['ask', 0.8],
]);
const F3 = nextLine(heard(F2, 'b2'), F2_STEPS);
const F3_STEPS = chain(after(F3), [
  ['confirm', 0.4],
  ['ask', 0.8],
  ['ret', 0.4],
  ['who', 0.5],
]);
const FN_SAID: Said = [
  [F1, [['In doeff: a Python function, marked'], ['@do', ACCENT]]],
  [F2, [['It'], ['yields', ACCENT], ['an effect where it needs something']]],
  [F3, [['It never says'], ['who answers', LOOK]]],
];
const FN_LENGTH = f(sceneEnd(heard(F3, 'b3'), F3_STEPS));

const Fn: React.FC = () => {
  const frame = useSceneFrame();
  const code = along(frame, F1_STEPS.code);
  const scan = 1 + along(frame, F2_STEPS.price) + along(frame, F3_STEPS.confirm) + along(frame, F3_STEPS.ret);
  return (
    <AbsoluteFill>
      <Chapter n={4} of={SCENES} name="In doeff" />
      <Headline lines={FN_SAID} />
      {/* The tea program of the idea, in the place its code takes. */}
      <div style={{position: 'absolute', inset: 0, opacity: 1 - code, transform: `translateY(${-code * 24}px)`}}>
        <Program title="a program: buying tea" x={FN.x} y={FN.y + 44} w={FN.w} rows={TEA_ROWS.map((text) => ({text}))} />
      </div>
      <div style={{position: 'absolute', inset: 0, opacity: code, transform: `translateY(${(1 - code) * 24}px)`}}>
        <CodeWindow file="order.py" lines={ORDER_CODE} x={FN.x} y={FN.y} w={FN.w} size={FN.size} line={FN.line} scan={frame >= f(F1_STEPS.code.to) ? scan : undefined} />
      </div>
      <Travel id="f-price" d={arcOf(520, codeMid(FN.y, FN.line, PRICE), 1030, 300, -30)} color={ACCENT} step={F2_STEPS.ask} text="Price(item)" size={21} />
      <Rise at={F2_STEPS.ask.to - 0.1} y={10}>
        <Note x={880} y={328} w={300} text="what does it cost?" size={19} align="center" />
      </Rise>
      <Travel id="f-confirm" d={arcOf(540, codeMid(FN.y, FN.line, CONFIRM), 1030, 440, -20)} color={ACCENT} step={F3_STEPS.ask} text="Confirm(price)" size={21} />
      <Rise at={F3_STEPS.ask.to - 0.1} y={10}>
        <Note x={880} y={468} w={300} text="may I pay that much?" size={19} align="center" />
      </Rise>
      <Rise at={F3_STEPS.who.from} y={30} scale={0.94} origin={[640, 570]}>
        <div style={{position: 'absolute', left: 250, top: 540, width: 780, padding: '16px 0', textAlign: 'center', ...glass(`${LOOK}66`), fontFamily: DISPLAY, fontSize: 28, fontWeight: 800, color: TEXT}}>
          <span style={{color: LOOK}}>Who answers?</span> order never says.
        </div>
      </Rise>
    </AbsoluteFill>
  );
};

// 5. Handlers are rings around order. Price goes out to prices and its answer comes back; Confirm goes out to yes,
// and k, the rest of order, goes with it; yes resumes k, once. Then what a handler can do with k, side by side.
const HC = {x: 50, y: 210, w: 650, size: 18, line: 38};
const RING = {cx: 970, cy: 392};
const STACK: RingOf[] = [{label: 'yes'}, {label: 'prices'}];
const H1 = 0;
const H1_STEPS = chain(after(H1), [
  ['rings', 0.6],
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
  ['ret', 0.4],
]);
const H4 = nextLine(heard(H3, 'b6'), H3_STEPS);
const H4_STEPS = chain(after(H4), [
  ['resume', 0.5],
  ['keep', 0.5],
  ['never', 0.5],
  ['once', 0.6],
]);
const HANDLE_SAID: Said = [
  [H1, [['Handlers', TEST], ['sit outside the function, and answer']]],
  [H2, [['With the effect, the handler gets'], ['k', K], [': the rest of the function']]],
  [H3, [['It'], ['resumes k,', K], ['and order returns']]],
  [H4, [['Or it keeps'], ['k', K], ['for later, or never resumes it']]],
];
const HANDLE = f(sceneEnd(heard(H4, 'b7'), H4_STEPS));
const PRICE_Y = codeMid(HC.y, HC.line, PRICE);
const CONFIRM_Y = codeMid(HC.y, HC.line, CONFIRM);

const Fate: React.FC<{x: number; how: string; color: string; what: string; result?: string; children: React.ReactNode}> = ({x, how, color, what, result, children}) => (
  <div style={{position: 'absolute', left: x, top: 190, width: 360, height: 310, ...glass(`${color}88`, color === LOOK)}}>
    <div style={{padding: '18px 22px 0', fontFamily: CODE, fontSize: 22, fontWeight: 700, color}}>{how}</div>
    <div style={{position: 'absolute', left: 22, right: 22, top: 66, height: 120}}>{children}</div>
    <div style={{position: 'absolute', left: 22, right: 22, top: 200, fontFamily: DISPLAY, fontSize: 18, fontWeight: 600, color: TEXT, lineHeight: 1.3}}>{what}</div>
    {result && <div style={{position: 'absolute', left: 22, bottom: 18, fontFamily: CODE, fontSize: 22, fontWeight: 700, color: LOOK}}>{result}</div>}
  </div>
);
const Block: React.FC<{x: number; label: string; color: string}> = ({x, label, color}) => (
  <div style={{position: 'absolute', left: x, top: 34, width: 96, height: 52, boxSizing: 'border-box', borderRadius: 12, border: `2px solid ${color}`, background: `${color}22`, display: 'flex', alignItems: 'center', justifyContent: 'center', fontFamily: CODE, fontSize: 15, fontWeight: 700, color}}>
    {label}
  </div>
);

const Handling: React.FC = () => {
  const frame = useSceneFrame();
  const at = (seconds: number) => frame >= f(seconds);
  const scan = PRICE + along(frame, {from: H2_STEPS.confirm.from, to: H2_STEPS.confirm.from + 0.3}) + along(frame, H3_STEPS.ret);
  const lit = at(H1_STEPS.price.to) && !at(H1_STEPS.answer.to) ? 1 : at(H2_STEPS.confirm.to) && !at(H3_STEPS.ret.from) ? 0 : undefined;
  const held = at(H2_STEPS.k.from) && !at(H3_STEPS.ret.from);
  const sweep = along(frame, {from: H4_STEPS.keep.from, to: H4_STEPS.keep.from + 2.4});
  return (
    <AbsoluteFill>
      <Chapter n={5} of={SCENES} name="Handlers and k" />
      <Headline lines={HANDLE_SAID} size={36} />
      <Rise at={0} out={H4} y={0}>
        <CodeWindow file="order.py" lines={ORDER_CODE} x={HC.x} y={HC.y} w={HC.w} size={HC.size} line={HC.line} scan={scan} k={held ? [RETURN, RETURN] : undefined} kColor={K} />
        <Rise at={H1_STEPS.rings.from} y={0} scale={0.8} origin={[RING.cx, RING.cy]}>
          <Note x={770} y={212} w={400} text="handlers, stacked outside order" color={MUTED} size={17} align="center" />
          <Rings cx={RING.cx} cy={RING.cy} core="order()" rings={STACK} w={190} h={80} gap={50} lit={lit} />
        </Rise>
        {/* Price goes out to prices, and its answer comes back to the yield. */}
        <Travel id="h-price" d={arcOf(430, PRICE_Y, 775, 290, -90)} color={ACCENT} step={H1_STEPS.price} text="Price('tea')" until={H1_STEPS.answer.from} leave />
        <Travel id="h-three" d={arcOf(775, 320, 490, PRICE_Y + 2, 70)} color={TEST} step={H1_STEPS.answer} text="3" size={22} until={H1_STEPS.answer.to + 0.8} leave />
        {/* Confirm goes out to yes, and k with it. */}
        <Travel id="h-confirm" d={arcOf(424, CONFIRM_Y, 825, 360, -130)} color={ACCENT} step={H2_STEPS.confirm} text="Confirm(3)" until={H3_STEPS.resume.from} leave />
        <Travel id="h-k" d={arcOf(HC.x + HC.w - 14, codeMid(HC.y, HC.line, RETURN), 825, 420, 100)} color={K} step={H2_STEPS.k} text="k" size={24} tail={200} until={H3_STEPS.resume.from} leave />
        <Rise at={H2_STEPS.k.from} out={H3_STEPS.ret.from} y={10}>
          <Note x={HC.x} y={486} w={HC.w} text="k: the rest of order, paused at the yield" color={K} />
        </Rise>
        {/* yes resumes k: order goes on from its yield, to its return. */}
        <Travel id="h-resume" d={arcOf(825, 390, 610, CONFIRM_Y + 2, 40)} color={TEST} step={H3_STEPS.resume} text="Resume(k, True)" size={17} until={H3_STEPS.ret.to + 0.8} leave />
        <Rise at={H3_STEPS.ret.from + 0.3} y={10}>
          <Note x={HC.x} y={486} w={HC.w} text="order returns ('bought', 3)" color={LOOK} size={22} />
        </Rise>
      </Rise>
      <Rise at={H4_STEPS.resume.from} y={40} scale={0.92} origin={[240, 345]}>
        <Fate x={60} how="Resume(k, True)" color={LOOK} what="order goes on from its yield" result="('bought', 3)">
          <Block x={0} label="order()" color={ACCENT} />
          <Block x={220} label="yes" color={TEST} />
          <svg width="316" height="120" style={{position: 'absolute', overflow: 'visible'}}>
            <path d="M 220 60 L 104 60" stroke={K} strokeWidth="3" strokeLinecap="round" />
            <path d="M 116 50 L 102 60 L 116 70" fill="none" stroke={K} strokeWidth="3" strokeLinecap="round" />
          </svg>
          <Capsule x={160} y={60} text="k" color={K} size={20} />
        </Fate>
      </Rise>
      <Rise at={H4_STEPS.keep.from} y={40} scale={0.92} origin={[640, 345]}>
        <Fate x={460} how="keep k" color={TEST} what="order waits until the handler goes back to it">
          <Block x={0} label="order()" color={ACCENT} />
          <Block x={220} label="yes" color={TEST} />
          <div style={{position: 'absolute', left: 132, top: -4, transform: 'scale(0.46)', transformOrigin: 'top left'}}>
            <Clock seconds={900 + sweep * 2400} c={TEST} secondHand={false} />
          </div>
          <Capsule x={160} y={84} text="k" color={K} size={18} />
        </Fate>
      </Rise>
      <Rise at={H4_STEPS.never.from} y={40} scale={0.92} origin={[1040, 345]}>
        <Fate x={860} how="never resume k" color={MUTED} what="order stops at its yield" result="'stopped'">
          <Block x={0} label="order()" color={ACCENT} />
          <Block x={220} label="stop" color={TEST} />
          <Capsule x={160} y={60} text="k" color={K} size={20} ghost />
        </Fate>
      </Rise>
      <Rise at={H4_STEPS.once.from} y={30} scale={0.9} origin={[640, 580]}>
        <div style={{position: 'absolute', left: 0, right: 0, top: 544, textAlign: 'center', fontFamily: DISPLAY, fontSize: 50, fontWeight: 800, letterSpacing: '-0.02em', color: TEXT}}>
          <span style={{color: K, textShadow: `0 0 26px ${K}88`}}>k</span> is resumed <span style={{color: LOOK, textShadow: `0 0 30px ${LOOK}88`}}>at most once</span>
        </div>
      </Rise>
    </AbsoluteFill>
  );
};

// 6. The same function in five stacks, side by side.
const S1 = 0;
const S1_STEPS = chain(after(S1), [
  ['line', 0.5],
  ['tests', 0.5],
]);
const S2 = nextLine(heard(S1, 's1'), S1_STEPS);
const S2_STEPS = chain(after(S2), [
  ['replay', 0.5],
  ['bot', 0.5],
  ['many', 0.5],
  ['production', 0.5],
  ['zero', 0.6],
]);
const STACK_SAID: Said = [
  [S1, [['Swap the'], ['stack:', TEST], ['the same function runs another way']]],
  [S2, [['One function,'], ['unchanged,', LOOK], ['under many stacks']]],
];
const STACKS_LENGTH = f(sceneEnd(heard(S2, 's2'), S2_STEPS));
const STACKS: [Step, string, RingOf[]][] = [
  [S1_STEPS.tests, 'tests', [{label: 'yes'}, {label: 'prices'}]],
  [S2_STEPS.replay, 'replay', [{label: 'replay(tape)'}]],
  [S2_STEPS.bot, 'a bot', [{label: 'bot'}, {label: 'prices'}]],
  [S2_STEPS.many, 'many at once', [{label: 'yes'}, {label: 'prices'}, {label: 'scheduled'}]],
  [S2_STEPS.production, 'production', [{label: 'ask a person', color: PROD}, {label: 'shop prices', color: PROD}]],
];

const Stacks: React.FC = () => {
  const frame = useSceneFrame();
  const line = along(frame, S1_STEPS.line);
  return (
    <AbsoluteFill>
      <Chapter n={6} of={SCENES} name="Swap the stack" />
      <Headline lines={STACK_SAID} />
      {/* One function runs through every stack. */}
      <div style={{position: 'absolute', left: 640 - 620 * line, width: 1240 * line, top: 369, height: 3, background: `linear-gradient(90deg, transparent, ${ACCENT}, ${ACCENT}, transparent)`, boxShadow: `0 0 26px ${ACCENT}`, opacity: fadeAt(frame, S1_STEPS.line.from, 0.2)}} />
      {STACKS.map(([step, name, rings], i) => {
        const cx = 148 + i * 246;
        return (
          <Rise key={name} at={step.from} y={0} scale={0.6} origin={[cx, 370]}>
            <Rings cx={cx} cy={370} core="order()" rings={rings} w={116} h={52} gap={21} font={12} />
            <Note x={cx - 120} y={500} w={240} text={name} size={22} align="center" />
          </Rise>
        );
      })}
      <Rise at={S2_STEPS.zero.from} y={30} scale={0.9} origin={[640, 590]}>
        <div style={{position: 'absolute', left: 0, right: 0, top: 568, textAlign: 'center', fontFamily: DISPLAY, fontSize: 38, fontWeight: 800, color: TEXT}}>
          <span style={{fontFamily: CODE}}>order()</span>: <span style={{color: LOOK, textShadow: `0 0 26px ${LOOK}88`}}>0 lines changed</span>
        </div>
      </Rise>
    </AbsoluteFill>
  );
};

// 7. How to start.
const E1 = 0;
// The three things come as the line says them, after pip install doeff.
const E1_STEPS = chain(after(E1), [
  ['pip', 0.9],
  ['do', 0.4, 1.6],
  ['yield', 0.4],
  ['handler', 0.4],
]);
const START_SAID: Said = [[E1, [['To start:'], ['three things', LOOK]]]];
const START = f(sceneEnd(heard(E1, 'e1'), E1_STEPS));
const THREE: [Step, string, string, string][] = [
  [E1_STEPS.do, '@do', ACCENT, 'makes a generator function a program'],
  [E1_STEPS.yield, 'yield', ACCENT, 'hands an effect out and gets the answer'],
  [E1_STEPS.handler, 'a handler', TEST, 'answers effects, and holds k'],
];

const Start: React.FC = () => {
  const frame = useSceneFrame();
  const blink = Math.floor(frame / 30) % 2 === 0 || frame < f(E1_STEPS.pip.to);
  return (
    <AbsoluteFill>
      <Chapter n={7} of={SCENES} name="Start" />
      <Headline lines={START_SAID} />
      <div style={{position: 'absolute', left: 320, top: 180, width: 640, ...glass(), overflow: 'hidden'}}>
        <div style={{height: 34, display: 'flex', alignItems: 'center', gap: 8, padding: '0 16px', borderBottom: `1px solid ${LINE}`, background: 'rgba(0,0,0,0.25)'}}>
          {['#ff6b81', '#ffb86b', '#3ecfb2'].map((color) => <div key={color} style={{width: 11, height: 11, borderRadius: '50%', background: color, opacity: 0.85}} />)}
        </div>
        <div style={{padding: '20px 28px', height: 44, fontFamily: CODE, fontSize: 36, fontWeight: 700, color: TEST, display: 'flex', alignItems: 'center', whiteSpace: 'pre'}}>
          <span style={{color: MUTED, marginRight: 18}}>$</span>
          {typed(frame, 'pip install doeff', E1_STEPS.pip)}
          <span style={{display: 'inline-block', width: 18, height: 40, marginLeft: 10, background: LOOK, boxShadow: `0 0 18px ${LOOK}`, opacity: blink ? 1 : 0}} />
        </div>
      </div>
      {THREE.map(([step, name, color, what], i) => (
        <Rise key={name} at={step.from} y={40} scale={0.9} origin={[280 + i * 360, 465]}>
          <div style={{position: 'absolute', left: 110 + i * 360, top: 370, width: 340, height: 190, padding: '22px 26px', ...glass(`${color}88`)}}>
            <div style={{position: 'absolute', right: 22, top: 14, fontFamily: DISPLAY, fontSize: 54, fontWeight: 800, color: 'transparent', WebkitTextStroke: `1.5px ${LOOK}88`}}>{i + 1}</div>
            <div style={{fontFamily: CODE, fontSize: 42, fontWeight: 700, color, textShadow: `0 0 22px ${color}77`}}>{name}</div>
            <div style={{fontFamily: DISPLAY, fontSize: 20, fontWeight: 600, color: TEXT, marginTop: 14, lineHeight: 1.3}}>{what}</div>
          </div>
        </Rise>
      ))}
    </AbsoluteFill>
  );
};

// 8. The article's example, before the article uses it: what battle does (the hand and the numbers are the first line
// the article's scripted run shows: T1 you 40 (0 block) | enemy 44 | 3 energy | Defend Strike Defend Defend Strike),
// then why it was chosen: four things it needs from outside, each answered by the handlers the article stacks.
const C1 = 0;
const C1_STEPS = chain(after(C1), [['sides', 0.6]]);
const C2 = nextLine(heard(C1, 'c1'), C1_STEPS);
const C2_STEPS = chain(after(C2), [
  ['hand', 0.9],
  ['turn', 1.4],
  ['until', 0.4],
]);
const C3 = nextLine(heard(C2, 'c2'), C2_STEPS);
const C3_STEPS = chain(after(C3), [
  ['hub', 0.6],
  ['roll', 0.7],
  ['choose', 0.7],
  ['show', 0.7],
  ['played', 0.7],
]);
const C4 = nextLine(heard(C3, 'c3'), C3_STEPS);
const C4_STEPS = chain(after(C4), [
  ['dice', 0.4],
  ['card', 0.4],
  ['screen', 0.4],
  ['count', 0.4],
]);
const C5 = nextLine(heard(C4, 'c4'), C4_STEPS);
const C5_STEPS = chain(after(C5), [['six', 0.6]]);
const FIGHT_SAID: Said = [
  [C1, [["The article's example:"], ['battle(),', ACCENT], ['one small card fight']]],
  [C2, [['Each turn:'], ['draw,', K], ['play,', PROD], ['and the enemy hits back', FAIL]]],
  [C3, [['Why this example:'], ['four', ACCENT], ['things it needs from'], ['outside', TEST]]],
  [C4, [['Each can be answered in'], ['many ways', TEST]]],
  [C5, [['The article runs'], ['battle()', ACCENT], ['under six stacks']]],
];
const FIGHT = f(sceneEnd(heard(C5, 'e2'), C5_STEPS));
const HAND = ['Defend', 'Strike', 'Defend', 'Defend', 'Strike'];
const CARD: Record<string, [string, string]> = {Strike: [PROD, 'deal 6'], Defend: [K, 'block 5']};
const KAPPA = 0.5523;
// The round of a turn: an ellipse from its left end, over the top.
const ROUND = ((cx: number, cy: number, rx: number, ry: number) =>
  `M ${cx - rx} ${cy} C ${cx - rx} ${cy - KAPPA * ry}, ${cx - KAPPA * rx} ${cy - ry}, ${cx} ${cy - ry} C ${cx + KAPPA * rx} ${cy - ry}, ${cx + rx} ${cy - KAPPA * ry}, ${cx + rx} ${cy} ` +
  `C ${cx + rx} ${cy + KAPPA * ry}, ${cx + KAPPA * rx} ${cy + ry}, ${cx} ${cy + ry} C ${cx - KAPPA * rx} ${cy + ry}, ${cx - rx} ${cy + KAPPA * ry}, ${cx - rx} ${cy}`)(640, 262, 172, 58);
// The parts of a turn on the round: [where its label sits, its text, its colour, how far round the turn it comes].
const TURN: [number, number, string, string, number][] = [
  [500, 214, 'draw a hand', K, 0.1],
  [780, 214, 'play cards', PROD, 0.38],
  [640, 320, 'the enemy hits back', FAIL, 0.72],
];
// What battle needs from outside: [its step, the effect, what it is, who can answer, the step they come at, right, top].
const NEEDS: [Step, string, string, string[], Step, boolean, boolean][] = [
  [C3_STEPS.roll, 'Roll(n)', 'dice', ['dice(7)', 'replay(tape)'], C4_STEPS.dice, false, true],
  [C3_STEPS.choose, 'Choose(...)', 'which card?', ['scripted', 'random_bot', 'monte_carlo', 'human'], C4_STEPS.card, true, true],
  [C3_STEPS.show, 'Show(text)', 'a screen', ['printed', 'silent'], C4_STEPS.screen, false, false],
  [C3_STEPS.played, 'Played(card, damage)', 'a count', ['quiet_stats', 'damage_stats'], C4_STEPS.count, true, false],
];

const Side: React.FC<{x: number; name: string; hp: number; color: string; children: React.ReactNode}> = ({x, name, hp, color, children}) => (
  <div style={{position: 'absolute', left: x, top: 190, width: 320, height: 150, padding: '16px 22px', ...glass(`${color}88`)}}>
    <div style={{display: 'flex', justifyContent: 'space-between', alignItems: 'baseline'}}>
      <div style={{fontFamily: DISPLAY, fontSize: 15, fontWeight: 600, letterSpacing: '0.18em', color}}>{name}</div>
      <div style={{fontFamily: CODE, fontSize: 26, fontWeight: 700, color: TEXT}}>{hp} HP</div>
    </div>
    <div style={{height: 12, borderRadius: 6, marginTop: 10, background: `linear-gradient(90deg, ${color}, ${color}aa)`, boxShadow: `0 0 16px ${color}88`}} />
    <div style={{marginTop: 14, fontFamily: DISPLAY, fontSize: 18, fontWeight: 600, color: TEXT, display: 'flex', alignItems: 'center', gap: 10}}>{children}</div>
  </div>
);

const Fight: React.FC = () => {
  const frame = useSceneFrame();
  const turn = along(frame, C2_STEPS.turn);
  const dot = pointOn(ROUND, turn);
  return (
    <AbsoluteFill>
      <Chapter n={8} of={SCENES} name="Next: the card fight" />
      <Headline lines={FIGHT_SAID} />
      {/* What battle does: the table. */}
      <Rise at={0} out={C3} y={0}>
        <Rise at={C1_STEPS.sides.from} y={30} scale={0.94} origin={[230, 265]}>
          <Side x={70} name="YOU" hp={40} color={TEST}>
            energy
            {[0, 1, 2].map((i) => <div key={i} style={{width: 18, height: 18, borderRadius: '50%', background: LOOK, boxShadow: `0 0 12px ${LOOK}`}} />)}
          </Side>
        </Rise>
        <Rise at={C1_STEPS.sides.from + 0.15} y={30} scale={0.94} origin={[1050, 265]}>
          <Side x={890} name="ENEMY" hp={44} color={FAIL}>dice decide its move</Side>
        </Rise>
        {HAND.map((name, i) => {
          const [color, does] = CARD[name];
          const off = i - 2;
          const dealt = pop(frame, C2_STEPS.hand.from + i * 0.12);
          return (
            <div
              key={i}
              style={{
                position: 'absolute', left: 640 + off * 132 * dealt - 62, top: 418 + off * off * 7 + (1 - dealt) * 260, width: 124, height: 172, transform: `rotate(${off * 5 * dealt}deg)`, ...glass(`${color}cc`),
                background: `linear-gradient(170deg, ${color}40, rgba(12,13,20,0.92) 55%)`, boxShadow: `0 18px 40px rgba(0,0,0,0.55), 0 0 22px ${color}44`, opacity: fadeAt(frame, C2_STEPS.hand.from + i * 0.12, 0.2),
              }}
            >
              <div style={{position: 'absolute', left: 10, top: 10, width: 30, height: 30, borderRadius: '50%', background: LOOK, color: INK, fontFamily: CODE, fontSize: 18, fontWeight: 700, display: 'flex', alignItems: 'center', justifyContent: 'center'}}>1</div>
              <div style={{position: 'absolute', left: 0, right: 0, top: 62, textAlign: 'center', fontFamily: DISPLAY, fontSize: 23, fontWeight: 800, color: TEXT}}>{name}</div>
              <div style={{position: 'absolute', left: 0, right: 0, top: 104, textAlign: 'center', fontFamily: DISPLAY, fontSize: 17, fontWeight: 600, color}}>{does}</div>
            </div>
          );
        })}
        <Rise at={C2_STEPS.hand.to} y={0}>
          <div style={{position: 'absolute', left: 0, right: 0, top: 648, textAlign: 'center', fontFamily: CODE, fontSize: 16, color: MUTED}}>T1 you 40 (0 block) | enemy 44 | 3 energy | Defend Strike Defend Defend Strike</div>
        </Rise>
        <Draw d={ROUND} color="rgba(255,255,255,0.45)" step={C2_STEPS.turn} dashed width={2} />
        {frame >= f(C2_STEPS.turn.from) && frame < f(C2_STEPS.turn.to + 0.3) && (
          <div style={{position: 'absolute', left: dot.x - 7, top: dot.y - 7, width: 14, height: 14, borderRadius: '50%', background: LOOK, boxShadow: `0 0 18px ${LOOK}`, opacity: 1 - fadeAt(frame, C2_STEPS.turn.to, 0.3)}} />
        )}
        <Rise at={C2_STEPS.turn.from} y={0}>
          <Note x={540} y={252} w={200} text="EACH TURN" color={MUTED} size={14} align="center" />
        </Rise>
        {TURN.map(([x, y, text, color, part]) => (
          <Rise key={text} at={C2_STEPS.turn.from + part * 1.4} y={0} scale={0.7} origin={[x, y]}>
            <Capsule x={x} y={y} text={text} color={color} mono={false} size={16} />
          </Rise>
        ))}
        <Rise at={C2_STEPS.until.from} y={10}>
          <Note x={440} y={352} w={400} text="until one side is out of HP" color={LOOK} size={18} align="center" />
        </Rise>
      </Rise>
      {/* Why battle: what it needs from outside, and who can answer. */}
      <Rise at={C3_STEPS.hub.from} y={0} scale={0.7} origin={[640, 392]}>
        <Rings cx={640} cy={392} core="battle()" rings={[]} w={224} h={92} font={20} note="one function" />
      </Rise>
      {NEEDS.map(([step, effect, what, answers, answered, right, top], i) => {
        // Above the hub, who can answer sits over the effect, and below it under; the way out never crosses them.
        const x = right ? 800 : 40;
        const cx = x + 220;
        const at = top ? 300 : 480;
        return (
          <React.Fragment key={effect}>
            <Travel id={`n-${i}`} d={arcOf(640 + (right ? 112 : -112), top ? 372 : 412, cx, at, 0)} color={ACCENT} step={step} text={effect} size={19} tail={200} />
            <Rise at={step.to - 0.1} y={10}>
              <Note x={x} y={top ? at - 70 : at + 34} w={440} text={what} size={21} align="center" />
            </Rise>
            <div style={{position: 'absolute', left: x, top: top ? at - 116 : at + 70, width: 440, display: 'flex', flexWrap: 'wrap', justifyContent: 'center', gap: 8}}>
              {answers.map((answer, j) => {
                const from = answered.from + j * 0.08;
                const color = answer === 'human' ? PROD : TEST;
                return (
                  <div
                    key={answer}
                    style={{
                      padding: '5px 12px', borderRadius: 12, border: `1.5px solid ${color}`, background: `${color}1c`, fontFamily: CODE, fontSize: 15, fontWeight: 700, color,
                      opacity: fadeAt(frame, from, 0.2), transform: `translateY(${(1 - pop(frame, from)) * 14}px)`,
                    }}
                  >
                    {answer}
                  </div>
                );
              })}
            </div>
          </React.Fragment>
        );
      })}
      <Rise at={C5_STEPS.six.from} y={30} scale={0.9} origin={[640, 656]}>
        <div style={{position: 'absolute', left: 0, right: 0, top: 640, textAlign: 'center', fontFamily: DISPLAY, fontSize: 28, fontWeight: 800, color: TEXT}}>
          one function · six stacks · <span style={{color: LOOK, textShadow: `0 0 26px ${LOOK}88`}}>0 lines changed</span>
        </div>
      </Rise>
    </AbsoluteFill>
  );
};

export const ARTICLE_CONCEPT = narrate(
  'article-concept',
  DURATIONS,
  NARRATION,
  [
    {length: TITLE, starts: saidStarts(TITLE_SAID), ids: ['i1', 'i2'], node: <Title />},
    {length: IDEA, starts: saidStarts(IDEA_SAID), ids: ['a1', 't1', 'a2', 'a3', 'a4'], node: <Idea />},
    {length: COMPARE, starts: saidStarts(COMPARE_SAID), ids: ['a5', 'a6', 'a7', 'a8'], node: <Compare />},
    {length: FN_LENGTH, starts: saidStarts(FN_SAID), ids: ['b1', 'b2', 'b3'], node: <Fn />},
    {length: HANDLE, starts: saidStarts(HANDLE_SAID), ids: ['b4', 'b5', 'b6', 'b7'], node: <Handling />},
    {length: STACKS_LENGTH, starts: saidStarts(STACK_SAID), ids: ['s1', 's2'], node: <Stacks />},
    {length: START, starts: saidStarts(START_SAID), ids: ['e1'], node: <Start />},
    {length: FIGHT, starts: saidStarts(FIGHT_SAID), ids: ['c1', 'c2', 'c3', 'c4', 'e2'], node: <Fight />},
  ],
  // The backdrop sits behind every scene, and the camera moves through the cuts.
  {backdrop: () => <Backdrop />, dolly: true},
);

// The motions of each scene, headlines included, and its seconds: pace.mjs prints the gaps between them.
const paced = (lines: Said, steps: Record<string, Step>[], length: number) => ({
  motions: [...lines.map(([from]) => headline(from)), ...steps.flatMap((s) => Object.values(s))],
  seconds: length / 60,
});
export const PACED: Record<string, {motions: Step[]; seconds: number}> = {
  Title: paced(TITLE_SAID.slice(1), [T1_STEPS, T2_STEPS], TITLE),
  Idea: paced(IDEA_SAID, [A1_STEPS, TEA_STEPS, A2_STEPS, A3_STEPS, A4_STEPS], IDEA),
  Compare: paced(COMPARE_SAID, [B1_STEPS, B2_STEPS, B3_STEPS, B4_STEPS], COMPARE),
  Function: paced(FN_SAID, [F1_STEPS, F2_STEPS, F3_STEPS], FN_LENGTH),
  Handling: paced(HANDLE_SAID, [H1_STEPS, H2_STEPS, H3_STEPS, H4_STEPS], HANDLE),
  Stacks: paced(STACK_SAID, [S1_STEPS, S2_STEPS], STACKS_LENGTH),
  Start: paced(START_SAID, [E1_STEPS], START),
  Fight: paced(FIGHT_SAID, [C1_STEPS, C2_STEPS, C3_STEPS, C4_STEPS, C5_STEPS], FIGHT),
};
