// The style frames of the motion version of article-concept: one still per key moment of the storyboard, drawn with
// the parts of look.tsx. Frame n of the composition `article-concept-style` is style frame n; `node stills.mjs
// article-concept-style <dir> 0 1 …` writes them. Each frame carries the narration line heard at that moment as its
// subtitle, so the still shows the frame as the video will show it. Every number and name on screen is a line of
// measure/order.py's output or of the article's listing of spire.py and its runs.
import React from 'react';
import {AbsoluteFill, useCurrentFrame} from 'remotion';
import {STAGE} from '../../../kit/narrated';
import {Clock} from '../../../kit/parts';
import {Subtitles} from '../../../kit/subtitles';
import {ACCENT, FAIL, LOOK, MUTED, PROD, TEST, TEXT} from '../../../kit/theme';
import {K} from '../../article-opening/ArticleOpening';
import {Arc, arcOf, Backdrop, Capsule, Chapter, CODE, CodeWindow, codeMid, DISPLAY, glass, Handler, INK, Kinetic, LINE, pointOn, Program, RingOf, Rings, rowMid, Seam} from './look';

const SCENES = 8;
const ORDER_CODE = [
  '@do',
  'def order(item: str):',
  '    price = yield Price(item)',
  '    ok = yield Confirm(price)',
  '    return ("bought" if ok else "skipped"), price',
];
const TEA_ROWS = ['takes an order for tea', 'needs: what does tea cost?', 'needs: may I pay that much?', 'buys it, or skips it'];
const SOLID = 'linear-gradient(160deg, #1b1d2c, #0c0d15)';
const caption = (x: number, y: number, w: number, text: string, color: string = TEXT, size = 20, align: 'left' | 'center' | 'right' = 'left'): React.ReactNode => (
  <div style={{position: 'absolute', left: x, top: y, width: w, textAlign: align, fontFamily: DISPLAY, fontSize: size, fontWeight: 600, color}}>{text}</div>
);

// 1. The title: the name, and the round trip the whole video is about.
const TitleA: React.FC = () => {
  const out = 'M 170 330 C 170 40, 1110 40, 1110 330';
  const back = 'M 1110 330 C 1110 620, 170 620, 170 330';
  const effect = pointOn(out, 0.7);
  const answer = pointOn(back, 0.42);
  return (
    <AbsoluteFill>
      <Chapter n={1} of={SCENES} name="doeff" />
      <Arc id="t-out" d={out} color={ACCENT} p={0.7} tail={420} />
      <Arc id="t-back" d={back} color={TEST} p={0.42} tail={420} />
      <div
        style={{
          position: 'absolute', left: 0, right: 0, top: 180, textAlign: 'center', fontFamily: DISPLAY, fontSize: 200, fontWeight: 800, letterSpacing: '-0.05em', lineHeight: 1,
          background: `linear-gradient(100deg, #ffffff 25%, ${ACCENT} 62%, ${TEST} 100%)`, WebkitBackgroundClip: 'text', color: 'transparent', filter: `drop-shadow(0 0 40px ${ACCENT}66)`,
        }}
      >
        doeff
      </div>
      <div style={{position: 'absolute', left: 0, right: 0, top: 402, textAlign: 'center', fontFamily: DISPLAY, fontSize: 40, fontWeight: 600, letterSpacing: '-0.01em', color: TEXT}}>Algebraic effects for Python</div>
      <div style={{position: 'absolute', left: 70, top: 298, padding: '12px 20px', ...glass(`${ACCENT}aa`), background: SOLID, fontFamily: DISPLAY, fontSize: 20, fontWeight: 600, color: TEXT}}>a program</div>
      <div style={{position: 'absolute', left: 1040, top: 298, padding: '12px 20px', ...glass(`${TEST}aa`), background: SOLID, fontFamily: DISPLAY, fontSize: 20, fontWeight: 600, color: TEXT}}>a handler</div>
      <Capsule x={effect.x} y={effect.y} text="an effect" color={ACCENT} mono={false} size={22} />
      <Capsule x={answer.x} y={answer.y} text="an answer" color={TEST} mono={false} size={22} />
    </AbsoluteFill>
  );
};

// 1b. The two parts of the video, as two stations on one line.
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
const TitleB: React.FC = () => (
  <AbsoluteFill>
    <Chapter n={1} of={SCENES} name="doeff" />
    <Kinetic words={[['First'], ['what algebraic effects are,', ACCENT], ['then'], ['how doeff writes them', TEST]]} size={36} />
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
    <Station x={650} n="2" title="The same idea in doeff" note="a function, yield, handlers">
      {[['@do', 60, ACCENT], ['def order(item):', 200, TEXT], ['    price = yield Price(item)', 330, TEXT], ['    ok = yield Confirm(price)', 330, TEXT]].map(([text, , color], i) => (
        <div key={i} style={{height: 36, fontFamily: CODE, fontSize: 19, color: color as string, whiteSpace: 'pre', opacity: 0.9}}>
          {(text as string).split('yield').map((part, j) => (
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
  </AbsoluteFill>
);

// 2. The idea: the example is said before it is used, then its request crosses the edge of the program and comes back.
const P = {x: 90, y: 220, w: 500};
const IdeaA: React.FC = () => {
  const price = arcOf(P.x + P.w, rowMid(P.y, 1), 790, 262, -40);
  const yes = arcOf(P.x + P.w, rowMid(P.y, 2), 760, 392, 30);
  return (
    <AbsoluteFill>
      <Chapter n={2} of={SCENES} name="The idea" />
      <Kinetic words={[['A program needs things from'], ['outside itself', TEST]]} />
      <Seam x={670} top={160} bottom={690} />
      <Program title="a program: buying tea" x={P.x} y={P.y} w={P.w} rows={TEA_ROWS.map((text, i) => ({text, lit: i === 1 || i === 2 ? ACCENT : undefined}))} />
      <Arc id="i-price" d={price} color={ACCENT} dashed />
      <Arc id="i-yes" d={yes} color={ACCENT} dashed />
      <Capsule x={900} y={262} text="a price?" color={TEXT} mono={false} size={26} ghost />
      <Capsule x={950} y={392} text="a yes or no from a person?" color={TEXT} mono={false} size={22} ghost />
      <Capsule x={880} y={520} text="the time?" color={MUTED} mono={false} size={20} ghost />
      <div style={{position: 'absolute', left: P.x, top: 500, width: P.w, padding: '14px 22px', ...glass(`${LOOK}66`)}}>
        <div style={{fontFamily: DISPLAY, fontSize: 13, fontWeight: 600, letterSpacing: '0.18em', color: LOOK}}>THE EXAMPLE, AND WHY</div>
        <div style={{fontFamily: DISPLAY, fontSize: 20, fontWeight: 600, color: TEXT, marginTop: 6}}>small enough to read at a glance,</div>
        <div style={{fontFamily: DISPLAY, fontSize: 20, fontWeight: 600, color: TEXT}}>and it needs two things from outside</div>
      </div>
    </AbsoluteFill>
  );
};
const IdeaB: React.FC = () => {
  const askY = rowMid(P.y, 1);
  const out = arcOf(P.x + P.w, askY, 1010, 262, -150);
  const back = arcOf(1010, 352, P.x + P.w + 8, askY + 16, 170);
  const three = pointOn(back, 0.58);
  return (
    <AbsoluteFill>
      <Chapter n={2} of={SCENES} name="The idea" />
      <Kinetic words={[['The handler'], ['answers.', TEST], ['The program'], ['goes on', LOOK], ['from where it asked.']]} size={36} />
      <Seam x={670} top={160} bottom={690} />
      <Program title="a program: buying tea" x={P.x} y={P.y} w={P.w} rows={TEA_ROWS.map((text, i) => ({text, lit: i === 1 ? ACCENT : undefined}))} />
      <Arc id="ib-out" d={out} color={ACCENT} done />
      <Arc id="ib-back" d={back} color={TEST} p={0.58} tail={300} />
      <Handler x={820} y={262} w={380} name="a handler" note="looks it up in a price list" lit />
      <Capsule x={1010} y={232} text="what does tea cost?" color={ACCENT} mono={false} size={17} />
      <Capsule x={three.x} y={three.y} text="3" color={TEST} size={30} />
      {caption(P.x, 500, P.w, 'it goes on from where it asked, with 3', LOOK, 22)}
    </AbsoluteFill>
  );
};

// 3. Next to an exception: both go out to a handler, and only the effect comes back.
const Compare: React.FC = () => {
  const top = 160;
  const bottom = 410;
  const raise = arcOf(560, rowMid(top, 1), 830, 232, -70);
  const lost = arcOf(830, 262, 566, rowMid(top, 2), 60);
  const ask = arcOf(560, rowMid(bottom, 1), 830, 482, -70);
  const answer = arcOf(830, 512, 566, rowMid(bottom, 2), 60);
  const cross = pointOn(lost, 0.5);
  const three = pointOn(answer, 0.55);
  return (
    <AbsoluteFill>
      <Chapter n={3} of={SCENES} name="Exception or effect" />
      <Kinetic words={[['An exception'], ['never goes back.', FAIL], ['An effect'], ['does.', TEST]]} />
      <Program title="an exception" x={90} y={top} w={470} rows={[{text: 'reads the order'}, {text: 'raises: something went wrong', lit: FAIL}, {text: 'the rest of the program', out: true}]} />
      <Handler x={830} y={200} w={360} name="catches it" note="a handler outside the program" />
      <Arc id="c-raise" d={raise} color={FAIL} done />
      <Arc id="c-lost" d={lost} color={FAIL} dashed />
      <svg width="1280" height="720" style={{position: 'absolute'}}>
        <circle cx={cross.x} cy={cross.y} r="19" fill={INK} stroke={FAIL} strokeWidth="2.5" />
        <path d={`M ${cross.x - 8} ${cross.y - 8} L ${cross.x + 8} ${cross.y + 8} M ${cross.x + 8} ${cross.y - 8} L ${cross.x - 8} ${cross.y + 8}`} stroke={FAIL} strokeWidth="3.5" strokeLinecap="round" />
      </svg>
      {caption(830, 312, 360, 'no way back: the rest never runs', FAIL, 19)}
      <Program title="an effect" x={90} y={bottom} w={470} rows={[{text: 'takes an order for tea'}, {text: 'needs: what does tea cost?', lit: ACCENT}, {text: 'the rest of the program', lit: LOOK}]} />
      <Handler x={830} y={450} w={360} name="answers it" note="a handler outside the program" lit />
      <Arc id="c-ask" d={ask} color={ACCENT} done />
      <Arc id="c-answer" d={answer} color={TEST} p={0.55} tail={200} />
      <Capsule x={three.x} y={three.y} text="3" color={TEST} size={26} />
      {caption(830, 562, 360, 'goes back: the rest runs, with 3', LOOK, 19)}
    </AbsoluteFill>
  );
};

// 4. The same idea in doeff: the plain rows have become the lines of order, and each yield hands an effect out.
const FN = {x: 70, y: 190, w: 740, size: 22, line: 44};
const Fn: React.FC = () => {
  const priceY = codeMid(FN.y, FN.line, 2);
  const confirmY = codeMid(FN.y, FN.line, 3);
  return (
    <AbsoluteFill>
      <Chapter n={4} of={SCENES} name="In doeff" />
      <Kinetic words={[['In doeff, it'], ['yields', ACCENT], ['an effect where it needs something']]} size={38} />
      <CodeWindow file="order.py" lines={ORDER_CODE} x={FN.x} y={FN.y} w={FN.w} size={FN.size} line={FN.line} scan={2} />
      <Arc id="f-price" d={arcOf(520, priceY, 930, 300, -30)} color={ACCENT} done />
      <Arc id="f-confirm" d={arcOf(540, confirmY, 916, 440, -44)} color={ACCENT} dashed />
      <Capsule x={1030} y={300} text="Price(item)" color={ACCENT} size={21} />
      {caption(880, 328, 300, 'what does it cost?', TEXT, 19, 'center')}
      <Capsule x={1030} y={440} text="Confirm(price)" color={ACCENT} size={21} ghost />
      {caption(880, 468, 300, 'may I pay that much?', MUTED, 19, 'center')}
      <div style={{position: 'absolute', left: 250, top: 540, width: 780, padding: '16px 0', textAlign: 'center', ...glass(`${LOOK}66`), fontFamily: DISPLAY, fontSize: 28, fontWeight: 800, color: TEXT}}>
        <span style={{color: LOOK}}>Who answers?</span> order never says.
      </div>
    </AbsoluteFill>
  );
};

// 5. Handlers are rings around order. Confirm goes out to yes, and k, the rest of order, goes with it.
const HC = {x: 50, y: 210, w: 650, size: 18, line: 38};
const STACK: RingOf[] = [{label: 'yes'}, {label: 'prices'}];
const HandlingA: React.FC = () => {
  const confirm = arcOf(424, codeMid(HC.y, HC.line, 3), 825, 360, -130);
  const k = arcOf(HC.x + HC.w - 14, codeMid(HC.y, HC.line, 4), 825, 420, 100);
  const c = pointOn(confirm, 0.64);
  const kAt = pointOn(k, 0.5);
  return (
    <AbsoluteFill>
      <Chapter n={5} of={SCENES} name="Handlers and k" />
      <Kinetic words={[['With the effect, the handler gets'], ['k', K], [': the rest of the function']]} size={36} />
      <CodeWindow file="order.py" lines={ORDER_CODE} x={HC.x} y={HC.y} w={HC.w} size={HC.size} line={HC.line} scan={3} k={[4, 4]} kColor={K} />
      {caption(HC.x, 486, HC.w, 'k: the rest of order, paused at the yield', K, 20)}
      {caption(770, 212, 400, 'handlers, stacked outside order', MUTED, 17, 'center')}
      <Rings cx={970} cy={392} core="order()" rings={STACK} w={190} h={80} gap={50} lit={0} />
      <Arc id="h-confirm" d={confirm} color={ACCENT} p={0.64} tail={240} />
      <Arc id="h-k" d={k} color={K} p={0.5} tail={200} />
      <Capsule x={c.x} y={c.y} text="Confirm(3)" color={ACCENT} size={19} />
      <Capsule x={kAt.x} y={kAt.y} text="k" color={K} size={24} />
    </AbsoluteFill>
  );
};

// 5b. What the handler can do with k, side by side, and the one rule.
const Fate: React.FC<{x: number; how: string; color: string; what: string; result?: string; children: React.ReactNode}> = ({x, how, color, what, result, children}) => (
  <div style={{position: 'absolute', left: x, top: 200, width: 360, height: 310, ...glass(`${color}88`, color === LOOK)}}>
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
const HandlingB: React.FC = () => (
  <AbsoluteFill>
    <Chapter n={5} of={SCENES} name="Handlers and k" />
    <Kinetic words={[['k', K], ['is resumed'], ['at most once', LOOK]]} size={46} />
    <div style={{position: 'absolute', right: 70, top: 528, fontFamily: DISPLAY, fontSize: 160, fontWeight: 800, lineHeight: 1, color: 'transparent', WebkitTextStroke: `2px ${LOOK}44`}}>1×</div>
    <Fate x={60} how="Resume(k, True)" color={LOOK} what="order goes on from its yield" result="('bought', 3)">
      <Block x={0} label="order()" color={ACCENT} />
      <Block x={220} label="yes" color={TEST} />
      <svg width="316" height="120" style={{position: 'absolute', overflow: 'visible'}}>
        <path d="M 220 60 L 104 60" stroke={K} strokeWidth="3" strokeLinecap="round" />
        <path d="M 116 50 L 102 60 L 116 70" fill="none" stroke={K} strokeWidth="3" strokeLinecap="round" />
      </svg>
      <Capsule x={160} y={60} text="k" color={K} size={20} />
    </Fate>
    <Fate x={460} how="keep k" color={TEST} what="order waits until the handler goes back to it">
      <Block x={0} label="order()" color={ACCENT} />
      <Block x={220} label="yes" color={TEST} />
      <div style={{position: 'absolute', left: 132, top: -4, transform: 'scale(0.46)', transformOrigin: 'top left'}}>
        <Clock seconds={2100} c={TEST} secondHand />
      </div>
      <Capsule x={160} y={84} text="k" color={K} size={18} />
    </Fate>
    <Fate x={860} how="never resume k" color={MUTED} what="order stops at its yield" result="'stopped'">
      <Block x={0} label="order()" color={ACCENT} />
      <Block x={220} label="stop" color={TEST} />
      <Capsule x={160} y={60} text="k" color={K} size={20} ghost />
    </Fate>
  </AbsoluteFill>
);

// 6. The same function in five stacks.
const STACKS: [string, RingOf[]][] = [
  ['tests', [{label: 'yes'}, {label: 'prices'}]],
  ['replay', [{label: 'replay(tape)'}]],
  ['a bot', [{label: 'bot'}, {label: 'prices'}]],
  ['many at once', [{label: 'yes'}, {label: 'prices'}, {label: 'scheduled'}]],
  ['production', [{label: 'ask a person', color: PROD}, {label: 'shop prices', color: PROD}]],
];
const Stacks: React.FC = () => (
  <AbsoluteFill>
    <Chapter n={6} of={SCENES} name="Swap the stack" />
    <Kinetic words={[['Swap the'], ['stack', TEST], [': the same function runs another way']]} size={38} />
    <div style={{position: 'absolute', left: 20, right: 20, top: 369, height: 3, background: `linear-gradient(90deg, transparent, ${ACCENT}, ${ACCENT}, transparent)`, boxShadow: `0 0 26px ${ACCENT}`}} />
    {STACKS.map(([name, rings], i) => {
      const cx = 148 + i * 246;
      return (
        <React.Fragment key={name}>
          <Rings cx={cx} cy={370} core="order()" rings={rings} w={116} h={52} gap={21} font={12} />
          {caption(cx - 120, 500, 240, name, TEXT, 22, 'center')}
        </React.Fragment>
      );
    })}
    <div style={{position: 'absolute', left: 0, right: 0, top: 572, textAlign: 'center', fontFamily: DISPLAY, fontSize: 34, fontWeight: 800, color: TEXT}}>
      <span style={{fontFamily: CODE}}>order()</span>: <span style={{color: LOOK, textShadow: `0 0 26px ${LOOK}88`}}>0 lines changed</span>
    </div>
  </AbsoluteFill>
);

// 7. How to start.
const THREE: [string, string, string][] = [
  ['@do', ACCENT, 'makes a generator function a program'],
  ['yield', ACCENT, 'hands an effect out and gets the answer'],
  ['a handler', TEST, 'answers effects, and holds k'],
];
const Start: React.FC = () => (
  <AbsoluteFill>
    <Chapter n={7} of={SCENES} name="Start" />
    <Kinetic words={[['Three things:'], ['@do,', ACCENT], ['yield,', ACCENT], ['a'], ['handler', TEST]]} />
    <div style={{position: 'absolute', left: 320, top: 180, width: 640, ...glass(), overflow: 'hidden'}}>
      <div style={{height: 34, display: 'flex', alignItems: 'center', gap: 8, padding: '0 16px', borderBottom: `1px solid ${LINE}`, background: 'rgba(0,0,0,0.25)'}}>
        {['#ff6b81', '#ffb86b', '#3ecfb2'].map((color) => <div key={color} style={{width: 11, height: 11, borderRadius: '50%', background: color, opacity: 0.85}} />)}
      </div>
      <div style={{padding: '20px 28px', fontFamily: CODE, fontSize: 36, fontWeight: 700, color: TEST, display: 'flex', alignItems: 'center'}}>
        <span style={{color: MUTED, marginRight: 18}}>$</span>pip install doeff
        <span style={{display: 'inline-block', width: 18, height: 40, marginLeft: 10, background: LOOK, boxShadow: `0 0 18px ${LOOK}`}} />
      </div>
    </div>
    {THREE.map(([name, color, what], i) => (
      <div key={name} style={{position: 'absolute', left: 110 + i * 360, top: 370, width: 340, height: 190, padding: '22px 26px', ...glass(`${color}88`)}}>
        <div style={{position: 'absolute', right: 22, top: 14, fontFamily: DISPLAY, fontSize: 54, fontWeight: 800, color: 'transparent', WebkitTextStroke: `1.5px ${LOOK}88`}}>{i + 1}</div>
        <div style={{fontFamily: CODE, fontSize: 42, fontWeight: 700, color, textShadow: `0 0 22px ${color}77`}}>{name}</div>
        <div style={{fontFamily: DISPLAY, fontSize: 20, fontWeight: 600, color: TEXT, marginTop: 14, lineHeight: 1.3}}>{what}</div>
      </div>
    ))}
  </AbsoluteFill>
);

// 8. The card fight, before the article uses it: what battle does. The hand and the numbers are the first line the
// scripted run of the article shows: T1 you 40 (0 block) | enemy 44 | 3 energy | Defend Strike Defend Defend Strike.
const HAND = ['Defend', 'Strike', 'Defend', 'Defend', 'Strike'];
const CARD: Record<string, [string, string]> = {Strike: [PROD, 'deal 6'], Defend: [K, 'block 5']};
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
const FightA: React.FC = () => (
  <AbsoluteFill>
    <Chapter n={8} of={SCENES} name="Next: the card fight" />
    <Kinetic words={[["The article's example:"], ['battle()', ACCENT], [', one small card fight']]} size={38} />
    <Side x={70} name="YOU" hp={40} color={TEST}>
      energy
      {[0, 1, 2].map((i) => <div key={i} style={{width: 18, height: 18, borderRadius: '50%', background: LOOK, boxShadow: `0 0 12px ${LOOK}`}} />)}
    </Side>
    <Side x={890} name="ENEMY" hp={44} color={FAIL}>dice decide its move</Side>
    <svg width="1280" height="720" style={{position: 'absolute'}}>
      <ellipse cx="640" cy="262" rx="172" ry="58" fill="none" stroke="rgba(255,255,255,0.3)" strokeWidth="2" strokeDasharray="3 9" strokeLinecap="round" />
    </svg>
    {caption(540, 252, 200, 'EACH TURN', MUTED, 14, 'center')}
    <Capsule x={520} y={214} text="draw a hand" color={TEXT} mono={false} size={16} ghost />
    <Capsule x={764} y={214} text="play cards" color={TEXT} mono={false} size={16} ghost />
    <Capsule x={640} y={322} text="the enemy hits back" color={TEXT} mono={false} size={16} ghost />
    {caption(440, 352, 400, 'until one side is out of HP', LOOK, 18, 'center')}
    {HAND.map((name, i) => {
      const [color, does] = CARD[name];
      const off = i - 2;
      return (
        <div
          key={i}
          style={{
            position: 'absolute', left: 640 + off * 132 - 62, top: 418 + off * off * 7, width: 124, height: 172, transform: `rotate(${off * 5}deg)`, ...glass(`${color}cc`),
            background: `linear-gradient(170deg, ${color}40, rgba(12,13,20,0.92) 55%)`, boxShadow: `0 18px 40px rgba(0,0,0,0.55), 0 0 22px ${color}44`,
          }}
        >
          <div style={{position: 'absolute', left: 10, top: 10, width: 30, height: 30, borderRadius: '50%', background: LOOK, color: INK, fontFamily: CODE, fontSize: 18, fontWeight: 700, display: 'flex', alignItems: 'center', justifyContent: 'center'}}>1</div>
          <div style={{position: 'absolute', left: 0, right: 0, top: 62, textAlign: 'center', fontFamily: DISPLAY, fontSize: 23, fontWeight: 800, color: TEXT}}>{name}</div>
          <div style={{position: 'absolute', left: 0, right: 0, top: 104, textAlign: 'center', fontFamily: DISPLAY, fontSize: 17, fontWeight: 600, color}}>{does}</div>
        </div>
      );
    })}
    <div style={{position: 'absolute', left: 0, right: 0, top: 648, textAlign: 'center', fontFamily: CODE, fontSize: 16, color: MUTED}}>T1 you 40 (0 block) | enemy 44 | 3 energy | Defend Strike Defend Defend Strike</div>
  </AbsoluteFill>
);

// 8b. Why battle is the example: four things it needs from outside, and each can be answered in many ways. The
// handlers named are the ones the article stacks around battle.
const NEEDS: {effect: string; what: string; answers: string[]; right: boolean; top: boolean}[] = [
  {effect: 'Roll(n)', what: 'dice', answers: ['dice(7)', 'replay(tape)'], right: false, top: true},
  {effect: 'Choose(...)', what: 'which card?', answers: ['scripted', 'random_bot', 'monte_carlo', 'human'], right: true, top: true},
  {effect: 'Show(text)', what: 'a screen', answers: ['printed', 'silent'], right: false, top: false},
  {effect: 'Played(card, damage)', what: 'a count', answers: ['quiet_stats', 'damage_stats'], right: true, top: false},
];
const FightB: React.FC = () => (
  <AbsoluteFill>
    <Chapter n={8} of={SCENES} name="Next: the card fight" />
    <Kinetic words={[['Why this example:'], ['four', ACCENT], ['things it needs from'], ['outside', TEST]]} size={38} />
    {NEEDS.map(({effect, what, answers, right, top}, i) => {
      const x = right ? 850 : 70;
      const y = top ? 176 : 452;
      const cx = x + 180;
      const half = (effect.length * 11.4 + 30) / 2 + 8;
      return (
        <React.Fragment key={effect}>
          <Arc id={`n-${i}`} d={arcOf(640 + (right ? 112 : -112), top ? 372 : 412, cx + (right ? -half : half), y + 28, 0)} color={ACCENT} done />
          <Capsule x={cx} y={y + 28} text={effect} color={ACCENT} size={19} />
          {caption(x, y + 62, 360, what, TEXT, 21, 'center')}
          <div style={{position: 'absolute', left: x, top: y + 98, width: 360, display: 'flex', flexWrap: 'wrap', justifyContent: 'center', gap: 8}}>
            {answers.map((answer) => (
              <div key={answer} style={{padding: '5px 12px', borderRadius: 12, border: `1.5px solid ${answer === 'human' ? PROD : TEST}`, background: `${answer === 'human' ? PROD : TEST}1c`, fontFamily: CODE, fontSize: 15, fontWeight: 700, color: answer === 'human' ? PROD : TEST}}>
                {answer}
              </div>
            ))}
          </div>
        </React.Fragment>
      );
    })}
    <Rings cx={640} cy={392} core="battle()" rings={[]} w={224} h={92} font={20} note="one function" />
    <div style={{position: 'absolute', left: 0, right: 0, top: 640, textAlign: 'center', fontFamily: DISPLAY, fontSize: 28, fontWeight: 800, color: TEXT}}>
      one function · six stacks · <span style={{color: LOOK, textShadow: `0 0 26px ${LOOK}88`}}>0 lines changed</span>
    </div>
  </AbsoluteFill>
);

// The style frames in the order of the video: [its name in the storyboard, what it draws, the narration line heard].
export const FRAMES: [string, React.FC, string][] = [
  ['title', TitleA, 'doeff is a Python library for algebraic effects.'],
  ['title-parts', TitleB, 'This video shows what algebraic effects are, and then how you write them with doeff.'],
  ['idea-example', IdeaA, 'Our example is tiny: a program that buys tea. It needs two things from outside: a price, and a yes.'],
  ['idea-answer', IdeaB, 'The handler answers, and the program goes on from where it asked.'],
  ['compare', Compare, 'An effect handler sends an answer back, and the program picks up where it left off.'],
  ['function', Fn, 'Where it needs something, it yields an effect. First Price: what does the item cost?'],
  ['handlers-k', HandlingA, 'With Confirm, the handler also gets k: the rest of order, paused at the yield.'],
  ['k-once', HandlingB, 'It could also keep k for later, or never resume it. Either way, k is resumed at most once.'],
  ['stacks', Stacks, 'Tests, replay, a bot, many at once, production. order does not change a line.'],
  ['start', Start, 'To start, pip install doeff. Then you need three things: @do, yield, and a handler.'],
  ['fight-what', FightA, 'Each turn you draw a hand and play cards, then the enemy hits back, until one side is out of HP.'],
  ['fight-why', FightB, 'It is a good example: it needs four things from outside. Dice, a choice of card, a screen, a count.'],
];

const Video: React.FC = () => {
  const frame = useCurrentFrame();
  const [, Frame, line] = FRAMES[Math.min(frame, FRAMES.length - 1)];
  return (
    <AbsoluteFill>
      <Backdrop t={frame * 11} />
      <AbsoluteFill style={{transform: `scale(${STAGE})`, transformOrigin: 'top center'}}>
        <Frame />
      </AbsoluteFill>
      <Subtitles cues={[{id: 'style', from: 0, to: FRAMES.length, text: line}]} />
    </AbsoluteFill>
  );
};

export const ARTICLE_CONCEPT_STYLE = {id: 'article-concept-style', length: FRAMES.length, ends: [FRAMES.length], cues: [], Video};
