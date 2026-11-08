// The video at the top of the article "Time is an effect" (docs/articles/time-and-clocks.md): sma_cross read from
// the top, line by line (what the strategy does), then its effects, and the layers put around it one by one: a backtest,
// bar_clock, the virtual clock and the wall clock. The strategy never changes. Every code line on screen is a line of
// examples/effects_tour/trading.py (check_code.py checks it); every number is a line of measure/opening.py's output.
// Each narrated sentence has its own slot, long enough for its line and for its motions, and the motions of a scene
// are at least 0.9 s apart (planOf checks it when the page is loaded).
import React from 'react';
import {AbsoluteFill} from 'remotion';
import {fadeAt, mix, ramp} from '../../kit/motion';
import {LEAD, narrate, Said, TAIL} from '../../kit/narrated';
import {CodeBlock, Headlines, Lines, Pill} from '../../kit/parts';
import {ACCENT, f, LOOK, MONO, MUTED, PANEL, PROD, SANS, TEST, TEXT} from '../../kit/theme';
import {useSceneFrame} from '../../kit/warp';
import {Box, Lit, mid, Say} from '../article2-opening/Article2Opening';
import {Geometry, K, Moving} from '../article-opening/ArticleOpening';
import DURATIONS from './durations.json';
import NARRATION from './narration.json';

// The shortest time between two motions of a scene, in seconds.
export const PACE = 0.9;

// One narrated sentence of a scene: its line id, and its motions by name at seconds after the sentence starts.
export type Beat = [string, Record<string, number>];

// What planOf found for a scene, kept so the pacing of every scene can be read back (and listed in the card's table).
export const PACING: {page: string; scene: string; ids: string[]; seconds: number; moves: number[]}[] = [];

// The plan of a scene: each sentence starts when the one before has been said (with LEAD and TAIL) and its motions
// are done, and the scene holds `hold` seconds after the last. The motion times come back by name, in design
// seconds; the slots are never shorter than their lines, so the warp leaves them as designed.
export const planOf = (page: string, scene: string, said: Said, beats: Beat[], hold = 0.8) => {
  const starts: number[] = [];
  const t: Record<string, number> = {};
  let at = 0;
  for (const [id, moves] of beats) {
    starts.push(at);
    const offsets = Object.values(moves);
    for (const [name, offset] of Object.entries(moves)) t[name] = at + offset;
    at += Math.max(LEAD + (said[id] ?? 0) + TAIL, offsets.length ? Math.max(...offsets) + PACE : 0);
  }
  const moves = Object.values(t).sort((a, b) => a - b);
  moves.slice(1).forEach((move, i) => {
    if (move - moves[i] < PACE - 1e-9) throw new Error(`${page} ${scene}: two motions ${(move - moves[i]).toFixed(2)} s apart, under ${PACE} s`);
  });
  const seconds = at + hold;
  PACING.push({page, scene, ids: beats.map(([id]) => id), seconds, moves});
  return {t, length: f(seconds), starts: starts.map(f), ids: beats.map(([id]) => id), seconds};
};

// Lights lines a..b of a code box (indexes) from `from` until `until`.
export const LitRange: React.FC<{box: Box; a: number; b?: number; from: number; until?: number; color?: string}> = ({box, a, b = a, from, until, color}) => (
  <>
    {Array.from({length: b - a + 1}, (_, k) => (
      <Lit key={k} box={box} i={a + k} from={from} until={until} color={color} />
    ))}
  </>
);

// A layer (a handler) around the strategy, drawn as ring `at` (0 = the innermost); it fades in at `from` and out at
// `until`, and glows between the two times of `glow`.
export type Layer = {label: string; at: number; color?: string; from?: number; until?: number; glow?: [number, number]};

export const ringOf = (g: Geometry, i: number) => {
  const w = g.bw + 2 * g.gap * (i + 1);
  const h = g.bh + 2 * g.gap * (i + 1);
  return {left: g.cx - w / 2, top: g.cy - h / 2, right: g.cx + w / 2, bottom: g.cy + h / 2, w, h};
};

// The strategy in the middle and its layers around it.
export const Stack: React.FC<{layers: Layer[]; g: Geometry; title?: string; from?: number; core?: string; opacity?: number}> = ({
  layers, g, title = 'sma_cross', from = 0.1, core = ACCENT, opacity = 1,
}) => {
  const frame = useSceneFrame();
  return (
    <AbsoluteFill style={{opacity}}>
      {[...layers].sort((a, b) => b.at - a.at).map((layer) => {
        const box = ringOf(g, layer.at);
        const shown = fadeAt(frame, layer.from ?? from, 0.4) * (layer.until === undefined ? 1 : 1 - fadeAt(frame, layer.until, 0.4));
        const lit = layer.glow !== undefined && frame >= f(layer.glow[0]) && frame < f(layer.glow[1]);
        const color = layer.color ?? TEST;
        return (
          <div
            key={`${layer.label}${layer.at}`}
            style={{
              position: 'absolute', left: box.left, top: box.top, width: box.w, height: box.h, boxSizing: 'border-box', borderRadius: 12 + 3 * layer.at,
              border: `3px solid ${lit ? LOOK : color}`, boxShadow: lit ? `0 0 22px ${LOOK}aa` : 'none', opacity: shown,
            }}
          >
            <div style={{position: 'absolute', left: 10, top: g.gap * 0.14, fontFamily: MONO, fontSize: g.font, fontWeight: 700, color: lit ? LOOK : color, whiteSpace: 'nowrap'}}>
              {layer.label}
            </div>
          </div>
        );
      })}
      <div
        style={{
          position: 'absolute', left: g.cx - g.bw / 2, top: g.cy - g.bh / 2, width: g.bw, height: g.bh, boxSizing: 'border-box', borderRadius: 10,
          border: `3px solid ${core}`, background: '#0b0d13', display: 'flex', alignItems: 'center', justifyContent: 'center',
          fontFamily: MONO, fontSize: g.font * 1.4, fontWeight: 700, color: TEXT, opacity: fadeAt(frame, from),
        }}
      >
        {title}
      </div>
    </AbsoluteFill>
  );
};

// A pill that goes from the strategy out to ring i of a stack, on a row `dy` below the middle.
export const Out: React.FC<{g: Geometry; ring: number; text: string; color?: string; from: number; dy?: number; until?: number; back?: boolean}> = ({
  g, ring, text, color = ACCENT, from, dy = 0, until, back = false,
}) => {
  const inner = g.cx + g.bw / 2 - 16;
  const outer = ringOf(g, ring).right - 6;
  return (
    <Moving
      text={text} color={color} from={from} to={from + 0.7} until={until ?? from + 1.6}
      x0={back ? outer : inner} y0={g.cy + dy} x1={back ? inner : outer} y1={g.cy + dy}
    />
  );
};

// A panel with a title.
export const Panel: React.FC<{x: number; y: number; w: number; h: number; title: string; from: number; color?: string; children?: React.ReactNode}> = ({x, y, w, h, title, from, color = TEST, children}) => {
  const frame = useSceneFrame();
  return (
    <div
      style={{
        position: 'absolute', left: x, top: y, width: w, height: h, boxSizing: 'border-box', padding: '12px 18px', background: PANEL, borderRadius: 14,
        border: `2px solid ${color}`, opacity: fadeAt(frame, from),
      }}
    >
      <div style={{fontFamily: SANS, fontSize: 17, fontWeight: 700, color: MUTED}}>{title}</div>
      {children}
    </div>
  );
};

// A pill that stays where it arrived: it moves from (x0, y0) to (x1, y1) and stays until `until`.
export const Landed: React.FC<{text: string; color: string; from: number; x0: number; y0: number; x1: number; y1: number; until?: number}> = (props) => (
  <Moving {...props} to={props.from + 0.7} />
);

// The line "doeff: a function yields an effect, a handler answers", drawn small: the opening of both article-3 videos.
export const Explain: React.FC<{y: number; from: number; back: number}> = ({y, from, back}) => {
  const frame = useSceneFrame();
  const fn = {x: 300, w: 220};
  const hd = {x: 760, w: 220};
  const box = (x: number, w: number, text: string, color: string) => (
    <div
      style={{
        position: 'absolute', left: x, top: y, width: w, height: 64, boxSizing: 'border-box', borderRadius: 12, border: `3px solid ${color}`, background: '#0b0d13',
        display: 'flex', alignItems: 'center', justifyContent: 'center', fontFamily: SANS, fontSize: 20, fontWeight: 700, color: TEXT, opacity: fadeAt(frame, from - 0.1),
      }}
    >
      {text}
    </div>
  );
  return (
    <>
      {box(fn.x, fn.w, 'a function', ACCENT)}
      {box(hd.x, hd.w, 'a handler', TEST)}
      <Moving text="yield Tick()" color={ACCENT} from={from} to={from + 0.7} until={back} x0={fn.x + fn.w + 10} y0={y + 20} x1={hd.x - 80} y1={y + 20} />
      <Moving text="the next bar" color={TEST} from={back} to={back + 0.7} x0={hd.x - 80} y0={y + 46} x1={fn.x + fn.w + 100} y1={y + 46} />
    </>
  );
};

const PAGE = 'article3-opening';

// 1. The opening: what the video shows, what doeff is, and the function it reads.
const INTRO = planOf(PAGE, 'intro', DURATIONS, [
  ['o1', {title: 0.1}],
  ['o2', {effect: 0.3, answer: 1.2}],
  ['o3', {strategy: 0.3, virtual: 1.2, wall: 2.1}],
]);
const INTRO_LINES: Lines = [[0, 'doeff, part 3']];
const Intro: React.FC = () => {
  const {t} = INTRO;
  const frame = useSceneFrame();
  const clock = (x: number, text: string, from: number) => (
    <div
      style={{
        position: 'absolute', left: x, top: 470, width: 300, height: 120, boxSizing: 'border-box', borderRadius: 16, border: `3px solid ${TEST}`,
        fontFamily: MONO, fontSize: 18, fontWeight: 700, color: TEST, padding: '8px 14px', opacity: fadeAt(frame, from, 0.4),
      }}
    >
      {text}
    </div>
  );
  return (
    <AbsoluteFill>
      <Headlines lines={INTRO_LINES} />
      <Say x={0} w={1280} y={96} from={t.title} size={54} center>Time is an effect</Say>
      <Say x={0} w={1280} y={176} from={t.effect - 0.1} size={20} color={MUTED} center>doeff: algebraic effects for Python</Say>
      <Explain y={250} from={t.effect} back={t.answer} />
      {clock(300, 'virtual_clock', t.virtual)}
      {clock(680, 'wall_clock', t.wall)}
      <div
        style={{
          position: 'absolute', left: 340 + mix(0, 190, ramp(frame, t.virtual, t.wall)), top: 506, width: 220, height: 64, boxSizing: 'border-box', borderRadius: 10,
          border: `3px solid ${ACCENT}`, background: '#0b0d13', display: 'flex', alignItems: 'center', justifyContent: 'center', fontFamily: MONO, fontSize: 24,
          fontWeight: 700, color: TEXT, opacity: fadeAt(frame, t.strategy),
        }}
      >
        sma_cross
      </div>
    </AbsoluteFill>
  );
};

// 2. sma_cross read from the top, line by line: what the strategy does.
const STRATEGY_CODE = [
  '@do',
  'def sma_cross(fast: int, slow: int):',
  '    closes: list[float] = []',
  '    position = 0',
  '    cash = 0.0',
  '    while (bar := (yield Tick())) is not None:',
  '        closes.append(bar.close)',
  '        if len(closes) < slow:',
  '            continue',
  '        f = sum(closes[-fast:]) / fast',
  '        s = sum(closes[-slow:]) / slow',
  '        if f > s and position == 0:',
  '            price = yield Order("buy", 1)',
  '            position, cash = 1, cash - price',
  '            yield Log(f"t={bar.t} buy  {price:.2f}")',
  '        elif f < s and position == 1:',
  '            price = yield Order("sell", 1)',
  '            position, cash = 0, cash + price',
  '            yield Log(f"t={bar.t} sell {price:.2f}")',
  '    if position:',
  '        cash += yield Order("sell", 1)',
  '    return round(cash, 2)',
];
// trading.py's line number of a line of STRATEGY_CODE.
const S = (line: number) => line - 55;
const CODE: Box = {x: 30, y: 78, w: 620, line: 24};

const READ = planOf(PAGE, 'read', DURATIONS, [
  ['o4', {code: 0.1, signature: 1.0}],
  ['o5', {chart: 0.3}],
  ['o6', {start: 0.2, state: 1.1}],
  ['o7', {loop: 0.2, tick: 1.1, bar: 2.0}],
  ['o8', {keep: 0.2, closes: 1.1}],
  ['o9', {average: 0.2, fast: 1.1, slow: 2.0}],
  ['o10', {buy: 0.2, buyOrder: 1.1, buyLog: 2.0}],
  ['o11', {sell: 0.2, sellOrder: 1.1, sellLog: 2.0}],
  ['o12', {none: 0.2, last: 1.1}],
  ['o13', {result: 0.2, cash: 1.1}],
]);
const READ_LINES: Lines = [[0, 'sma_cross, from the top']];

// The first 30 closes and the two averages from bar 19 on (measure/opening.log, o8 and o9).
const CLOSES = [
  101.34, 102.86, 102.98, 102.24, 101.18, 101.26, 100.27, 98.88, 99.13, 99.31, 99.9, 99.04, 99.1, 99.08, 97.64, 98.21, 98.58, 100.98, 101.24, 101.14,
  102.44, 102.69, 103.68, 103.35, 103.63, 104.74, 105.52, 105.71, 104.62, 105.14,
];
const FAST = [100.03, 100.88, 101.7, 102.24, 102.66, 103.16, 103.62, 104.18, 104.59, 104.84, 105.15];
const SLOW = [100.22, 100.27, 100.26, 100.3, 100.35, 100.48, 100.65, 100.91, 101.26, 101.53, 101.82];
const CHART = {x: 690, y: 78, w: 560, h: 250};
const px = (i: number) => CHART.x + 40 + (i * (CHART.w - 70)) / 29;
const py = (price: number) => CHART.y + CHART.h - 30 - ((price - 97) / 9.5) * (CHART.h - 70);

const Chart: React.FC<{t: Record<string, number>}> = ({t}) => {
  const frame = useSceneFrame();
  // Closes shown: 20 while it waits for `slow` of them, then the rest with the averages.
  const shown = Math.floor(20 * ramp(frame, t.closes, t.closes + 1.4)) + Math.floor(10 * ramp(frame, t.fast, t.fast + 0.8));
  const line = (values: number[], upTo: number) =>
    values.slice(0, upTo).map((v, k) => `${k ? 'L' : 'M'}${px(19 + k).toFixed(1)},${py(v).toFixed(1)}`).join(' ');
  const fastUpTo = Math.ceil(11 * ramp(frame, t.fast, t.fast + 0.8));
  const slowUpTo = Math.ceil(11 * ramp(frame, t.slow, t.slow + 0.8));
  return (
    <>
      <Panel x={CHART.x} y={CHART.y} w={CHART.w} h={CHART.h} title="closes" from={t.chart} color={PROD} />
      <svg style={{position: 'absolute', left: 0, top: 0}} width={1280} height={720}>
        {CLOSES.slice(0, shown).map((close, i) => (
          <circle key={i} cx={px(i)} cy={py(close)} r={4} fill={TEXT} />
        ))}
        <line x1={px(19)} x2={px(19)} y1={CHART.y + 40} y2={CHART.y + CHART.h - 24} stroke={LOOK} strokeWidth={2} strokeDasharray="6 5" opacity={fadeAt(frame, t.closes + 1.4)} />
        <path d={line(SLOW, slowUpTo)} stroke={PROD} strokeWidth={3} fill="none" />
        <path d={line(FAST, fastUpTo)} stroke={K} strokeWidth={3} fill="none" />
        <circle cx={px(20)} cy={py(CLOSES[20])} r={11} fill="none" stroke={TEST} strokeWidth={3} opacity={fadeAt(frame, t.buyOrder)} />
      </svg>
      <Say x={px(19) - 70} y={CHART.y + 38} from={t.closes + 1.4} size={15} color={LOOK}>slow = 20</Say>
      <Say x={CHART.x + 380} y={CHART.y + 12} from={t.fast} size={16} color={K}>fast (5)</Say>
      <Say x={CHART.x + 460} y={CHART.y + 12} from={t.slow} size={16} color={PROD}>slow (20)</Say>
      <Say x={px(20) - 30} y={py(CLOSES[20]) - 44} from={t.buyOrder} size={15} color={TEST}>buy</Say>
    </>
  );
};

const STATE = {x: 690, y: 344, w: 560, h: 84};
const YIELDS = {x: 690, y: 444, w: 560, h: 192};
// Row r of the yields panel.
const yRow = (r: number) => YIELDS.y + 56 + r * 30;
// About how wide a pill with this text is (19 px bold monospace and its padding), to line pills up on their left edge.
export const pillWidth = (text: string) => text.length * 11.5 + 28;
const Read: React.FC = () => {
  const {t} = READ;
  const frame = useSceneFrame();
  const chip = (text: string, x: number, glowFrom = Infinity) => (
    <span
      style={{
        position: 'absolute', left: x, top: 40, padding: '4px 12px', borderRadius: 10, border: `2px solid ${frame >= f(glowFrom) ? LOOK : '#2a2e40'}`,
        fontFamily: MONO, fontSize: 17, color: TEXT, whiteSpace: 'nowrap', opacity: fadeAt(frame, t.state),
      }}
    >
      {text}
    </span>
  );
  const into = (text: string, line: number, row: number, from: number, color = ACCENT) => (
    <Landed text={text} color={color} from={from} x0={CODE.x + CODE.w - 90} y0={mid(CODE, S(line))} x1={YIELDS.x + 30 + pillWidth(text) / 2} y1={yRow(row)} />
  );
  return (
    <AbsoluteFill>
      <Headlines lines={READ_LINES} />
      <CodeBlock lines={STRATEGY_CODE} x={CODE.x} y={CODE.y} w={CODE.w} size={15} line={CODE.line} opacity={fadeAt(frame, t.code)} />
      <LitRange box={CODE} a={S(55)} b={S(56)} from={t.signature} until={t.start} />
      <LitRange box={CODE} a={S(57)} b={S(59)} from={t.start} until={t.loop} />
      <Lit box={CODE} i={S(60)} from={t.loop} until={t.keep} />
      <LitRange box={CODE} a={S(61)} b={S(63)} from={t.keep} until={t.average} />
      <LitRange box={CODE} a={S(64)} b={S(65)} from={t.average} until={t.buy} />
      <LitRange box={CODE} a={S(66)} b={S(69)} from={t.buy} until={t.sell} />
      <LitRange box={CODE} a={S(70)} b={S(73)} from={t.sell} until={t.none} />
      <Lit box={CODE} i={S(60)} from={t.none} until={t.last} />
      <LitRange box={CODE} a={S(74)} b={S(75)} from={t.last} until={t.result} />
      <Lit box={CODE} i={S(76)} from={t.result} />
      <Chart t={t} />
      <Panel x={STATE.x} y={STATE.y} w={STATE.w} h={STATE.h} title="what it keeps" from={t.state}>
        {chip('closes = []', 18)}
        {chip('position = 0', 200)}
        {chip('cash = 0.0', 382, t.cash)}
      </Panel>
      <Panel x={YIELDS.x} y={YIELDS.y} w={YIELDS.w} h={YIELDS.h} title="what it yields, in order" from={t.loop} color={ACCENT} />
      {into('Tick()', 60, 0, t.tick)}
      <Moving text="Bar(t=0, close=101.34)" color={TEST} from={t.bar} to={t.bar + 0.7} until={t.bar + 1.3} x0={YIELDS.x + 380} y0={yRow(0)} x1={CODE.x + CODE.w - 140} y1={mid(CODE, S(60))} />
      {into('Order("buy", 1)', 67, 1, t.buyOrder)}
      {into('Log("t=20 buy  …")', 69, 2, t.buyLog)}
      {into('Order("sell", 1)', 71, 3, t.sellOrder)}
      {into('Log("t=57 sell …")', 73, 4, t.sellLog)}
      <Moving text="None" color={TEST} from={t.none} to={t.none + 0.7} until={t.none + 1.0} x0={YIELDS.x + 380} y0={yRow(0)} x1={CODE.x + CODE.w - 140} y1={mid(CODE, S(60))} />
    </AbsoluteFill>
  );
};

// 3. Tick, Order and Log are values; the handlers around the strategy give them their meaning.
const VALUES = planOf(PAGE, 'values', DURATIONS, [
  ['o14', {tick: 0.2, order: 1.1, log: 2.0, clock: 2.9}],
  ['o15', {handlers: 0.3}],
]);
const VALUES_LINES: Lines = [[0, 'Effects are values; handlers give them meaning']];
const VG: Geometry = {cx: 330, cy: 380, bw: 220, bh: 90, gap: 46, font: 17};
const Values: React.FC = () => {
  const {t} = VALUES;
  const frame = useSceneFrame();
  const x0 = VG.cx + VG.bw / 2;
  const pills: [string, number, string][] = [
    ['Tick()', t.tick, ACCENT],
    ['Order("buy", 1)', t.order, ACCENT],
    ['Log("t=20 buy  102.49")', t.log, ACCENT],
  ];
  return (
    <AbsoluteFill>
      <Headlines lines={VALUES_LINES} />
      <Stack layers={[{label: 'handlers: ?', at: 0, color: MUTED, from: t.handlers}, {label: '', at: 1, color: MUTED, from: t.handlers}]} g={VG} />
      {pills.map(([text, from, color], i) => (
        <Landed key={text} text={text} color={color} from={from} x0={x0} y0={VG.cy} x1={760} y1={250 + i * 70} />
      ))}
      <Say x={720} y={470} from={t.clock} size={18} color={MUTED}>not yielded by sma_cross:</Say>
      <div style={{position: 'absolute', left: 0, top: 0, opacity: 0.45}}>
        <Pill x={780} y={530} text="Delay" color={ACCENT} opacity={fadeAt(frame, t.clock)} />
        <Pill x={900} y={530} text="GetTime" color={ACCENT} opacity={fadeAt(frame, t.clock)} />
      </div>
      <Say x={720} y={580} from={t.handlers} size={20} color={TEST}>{'what they mean is up to the handlers'}</Say>
    </AbsoluteFill>
  );
};

// 4. Layer 1: recorded answers Tick, paper fills Order, printed writes the log: the backtest.
const BACKTEST = planOf(PAGE, 'backtest', DURATIONS, [
  ['o16', {recorded: 0.2, tick: 1.1, bar: 2.0}],
  ['o17', {paper: 0.2, order: 1.1, fill: 2.0}],
  ['o18', {printed: 0.2, trades: 1.1, pnl: 2.0}],
]);
const BACKTEST_LINES: Lines = [[0, 'Layer 1: a backtest over recorded bars']];
const BG3: Geometry = {cx: 330, cy: 400, bw: 220, bh: 90, gap: 44, font: 17};
// The 15 log lines of the backtest (measure/opening.log, o18).
export const TRADES = [
  't=20 buy  102.49', 't=57 sell 109.35', 't=60 buy  109.50', 't=66 sell 107.00', 't=76 buy  108.89', 't=80 sell 106.45', 't=118 buy  100.77',
  't=187 sell 118.23', 't=197 buy  121.40', 't=217 sell 122.38', 't=223 buy  126.35', 't=228 sell 124.01', 't=230 buy  126.27', 't=231 sell 124.94',
  't=232 buy  127.63',
];
const Backtest: React.FC = () => {
  const {t} = BACKTEST;
  const frame = useSceneFrame();
  const layers: Layer[] = [
    {label: 'paper(0.0005)', at: 0, from: t.paper, glow: [t.paper, t.printed]},
    {label: 'recorded(BARS)', at: 1, from: t.recorded, glow: [t.recorded, t.paper]},
    {label: 'printed', at: 2, from: t.printed, color: PROD, glow: [t.printed, t.pnl]},
  ];
  return (
    <AbsoluteFill>
      <Headlines lines={BACKTEST_LINES} />
      <Stack layers={layers} g={BG3} />
      <Out g={BG3} ring={1} text="Tick()" from={t.tick} dy={-24} />
      <Out g={BG3} ring={1} text="Bar(t=0, …)" color={TEST} from={t.bar} dy={24} back />
      <Out g={BG3} ring={0} text={'Order("buy", 1)'} from={t.order} dy={-24} />
      <Out g={BG3} ring={0} text="102.49" color={TEST} from={t.fill} dy={24} back />
      <Say x={70} y={640} from={t.recorded + 0.3} size={17} color={MUTED}>300 saved bars</Say>
      <Panel x={720} y={90} w={530} h={545} title="printed" from={t.printed} color={PROD}>
        <div style={{fontFamily: MONO, fontSize: 16, lineHeight: '25px', color: TEXT, whiteSpace: 'pre', marginTop: 8, opacity: fadeAt(frame, t.trades)}}>
          {TRADES.join('\n')}
        </div>
        <div style={{fontFamily: MONO, fontSize: 26, fontWeight: 700, color: LOOK, marginTop: 10, opacity: fadeAt(frame, t.pnl)}}>pnl 50.79</div>
      </Panel>
    </AbsoluteFill>
  );
};

// 5. bar_clock: before it passes each Tick on, it yields Delay(60); the clock outside decides what waiting means.
const BAR_CLOCK = planOf(PAGE, 'bar_clock', DURATIONS, [
  ['o19', {minute: 0.2, ring: 1.1}],
  ['o20', {first: 0.2, tick: 1.1, delay: 2.0}],
  ['o21', {then: 0.2, pass: 1.1, bar: 2.0, k: 2.9}],
]);
const BAR_CLOCK_LINES: Lines = [[0, 'A bar a minute: bar_clock asks to wait first']];
const BAR_CLOCK_CODE = [
  'def bar_clock(seconds: float):',
  '    @handler',
  '    def wait_for_bar(effect: Tick, k):',
  '        yield Delay(seconds)',
  '        return (yield Resume(k, (yield effect)))',
];
const BC: Box = {x: 620, y: 90, w: 630, line: 30};
const BCG: Geometry = {cx: 300, cy: 420, bw: 200, bh: 80, gap: 40, font: 16};
const BarClock: React.FC = () => {
  const {t} = BAR_CLOCK;
  const frame = useSceneFrame();
  const layers: Layer[] = [
    {label: 'bar_clock(60)', at: 0, from: t.ring, glow: [t.ring, t.pass]},
    {label: 'paper', at: 1},
    {label: 'recorded(BARS)', at: 2, glow: [t.pass, t.bar + 0.6]},
  ];
  const outer = ringOf(BCG, 2).right;
  return (
    <AbsoluteFill>
      <Headlines lines={BAR_CLOCK_LINES} />
      <Stack layers={layers} g={BCG} />
      <Say x={60} y={150} from={t.minute} size={22} color={MUTED}>a market: one bar every 60 s</Say>
      <CodeBlock lines={BAR_CLOCK_CODE} x={BC.x} y={BC.y} w={BC.w} size={17} line={BC.line} opacity={fadeAt(frame, t.ring)} />
      <Lit box={BC} i={3} from={t.first} until={t.then} />
      <Lit box={BC} i={4} from={t.then} />
      <Out g={BCG} ring={0} text="Tick()" from={t.tick} dy={-20} until={t.pass} />
      <Moving text="Delay(60)" color={ACCENT} from={t.delay} to={t.delay + 0.8} until={Infinity} x0={ringOf(BCG, 0).right} y0={BCG.cy - 70} x1={outer + 120} y1={BCG.cy - 70} />
      <Say x={outer + 40} y={BCG.cy - 40} from={t.delay + 0.5} size={17} color={LOOK}>{'waiting: up to\nthe clock outside'}</Say>
      <Moving text="Tick()" color={ACCENT} from={t.pass} to={t.pass + 0.7} until={t.bar} x0={ringOf(BCG, 0).right - 6} y0={BCG.cy - 20} x1={outer - 6} y1={BCG.cy - 20} />
      <Out g={BCG} ring={2} text="Bar(t=0, …)" color={TEST} from={t.bar} dy={22} back until={t.k + 0.3} />
      <div
        style={{
          position: 'absolute', left: BC.x, top: 470, width: BC.w, boxSizing: 'border-box', padding: '14px 18px', background: PANEL, borderRadius: 14,
          border: `2px solid ${K}`, opacity: fadeAt(frame, t.k),
        }}
      >
        <div style={{fontFamily: SANS, fontSize: 19, color: TEXT}}>
          <span style={{fontFamily: MONO, color: K, fontWeight: 700}}>Resume(k, bar)</span>: the strategy goes on from its yield
        </div>
        <div style={{fontFamily: SANS, fontSize: 17, color: MUTED, marginTop: 6}}>this k is used once; the next Tick brings a new k</div>
      </div>
      <div
        style={{
          position: 'absolute', left: BCG.cx - BCG.bw / 2 + 6, top: BCG.cy - 12, padding: '2px 10px', borderRadius: 8, background: K, color: '#0f1117',
          fontFamily: MONO, fontSize: 16, fontWeight: 700, opacity: fadeAt(frame, t.tick) * (1 - fadeAt(frame, t.k, 0.4)),
        }}
      >
        k
      </div>
    </AbsoluteFill>
  );
};

// 6. Layer 5: the virtual clock parks the waiting task, and its idle task jumps the time to the next wake-up.
const VIRTUAL = planOf(PAGE, 'virtual_clock', DURATIONS, [
  ['o22', {ring: 0.2, getTime: 1.1, now: 2.0}],
  ['o23', {promise: 0.2, delay: 1.1, heap: 2.0, park: 2.9}],
  ['o24', {advance: 0.2, jump: 1.1, wake: 2.0}],
  ['o25', {run: 0.2, log: 1.1, stamp: 2.0, line: 2.9}],
  ['o26', {rest: 0.2, end: 2.0, wall: 2.9}],
]);
const VIRTUAL_LINES: Lines = [[0, 'Layer 5: a virtual clock']];
const CLOCK_CODE = [
  '@handler',
  'def clock(effect: Delay | GetTime, k):',
  '    nonlocal advancing',
  '    if isinstance(effect, GetTime):',
  '        return (yield Resume(k, now))',
  '    promise = yield CreatePromise()',
  '    heapq.heappush(sleepers, (now + timedelta(seconds=effect.seconds), next(order), promise))',
  '    if not advancing:',
  '        advancing = True',
  '        yield Spawn(advance(), priority=TaskPriority.IDLE, daemon=True)',
  '    yield Wait(promise.future)',
  '    return (yield Resume(k, None))',
];
// trading.py's line number of a line of CLOCK_CODE.
const C = (line: number) => line - 218;
const CC: Box = {x: 578, y: 78, w: 694, line: 22};
const VG6: Geometry = {cx: 290, cy: 410, bw: 160, bh: 62, gap: 25, font: 14};
// Minutes past 09:00 as HH:MM.
export const hhmm = (minutes: number) => {
  const m = Math.floor(minutes);
  return `${String(9 + Math.floor(m / 60)).padStart(2, '0')}:${String(m % 60).padStart(2, '0')}`;
};
const Virtual: React.FC = () => {
  const {t} = VIRTUAL;
  const frame = useSceneFrame();
  const layers: Layer[] = [
    {label: 'bar_clock(60)', at: 0},
    {label: 'finished_at', at: 1},
    {label: 'paper', at: 2},
    {label: 'recorded(BARS)', at: 3},
    {label: 'stamped', at: 4, color: PROD, from: t.ring, glow: [t.log, t.line]},
    {label: 'virtual_clock(OPEN)', at: 5, from: t.ring, glow: [t.ring, t.advance]},
  ];
  // The clock's minutes past 09:00: 1 at the jump, 21 by the first stamped trade, 301 (14:01) at the end.
  const minutes =
    frame < f(t.jump) ? 0 : frame < f(t.run) ? ramp(frame, t.jump, t.jump + 0.4) : frame < f(t.rest) ? 1 + 20 * ramp(frame, t.run, t.run + 0.8) : 21 + 280 * ramp(frame, t.rest, t.rest + 1.6);
  const parked = frame >= f(t.park) && frame < f(t.wake);
  // The heap holds the next wake-up: 09:01 after the first Delay, 09:02 once the woken strategy asks again, and so on to the end.
  const heap = frame < f(t.heap) ? '' : frame < f(t.jump) ? '09:01' : frame < f(t.wake) ? '' : frame < f(t.run) ? '09:02' : minutes < 301 ? hhmm(minutes + 1) : '';
  const row = (y: number, name: string, value: React.ReactNode, from: number) => (
    <div style={{position: 'absolute', left: 18, top: y, fontFamily: MONO, fontSize: 18, color: TEXT, whiteSpace: 'pre', opacity: fadeAt(frame, from)}}>
      <span style={{color: MUTED}}>{name}</span>
      {value}
    </div>
  );
  return (
    <AbsoluteFill>
      <Headlines lines={VIRTUAL_LINES} />
      <div
        style={{
          position: 'absolute', left: 22, top: 170, width: 536, height: 480, boxSizing: 'border-box', borderRadius: 16, border: `2px dashed ${MUTED}`, opacity: fadeAt(frame, t.ring),
        }}
      >
        <div style={{position: 'absolute', left: 12, top: 4, fontFamily: MONO, fontSize: 15, fontWeight: 700, color: MUTED}}>scheduled</div>
      </div>
      <Stack layers={layers} g={VG6} core={parked ? '#3a3f55' : ACCENT} />
      <CodeBlock lines={CLOCK_CODE} x={CC.x} y={CC.y} w={CC.w} size={12} line={CC.line} opacity={fadeAt(frame, t.ring)} />
      <LitRange box={CC} a={C(221)} b={C(222)} from={t.getTime} until={t.promise} />
      <LitRange box={CC} a={C(223)} b={C(224)} from={t.promise} until={t.park} />
      <Lit box={CC} i={C(228)} from={t.park} until={t.wake} />
      <Lit box={CC} i={C(229)} from={t.wake} until={t.run} />
      <Out g={VG6} ring={5} text="Delay(60)" from={t.delay} dy={-16} until={t.heap + 0.4} />
      <Out g={VG6} ring={4} text="Log" from={t.log} dy={14} until={t.stamp} />
      <Moving text="GetTime()" color={ACCENT} from={t.stamp} to={t.stamp + 0.6} until={t.line} x0={ringOf(VG6, 4).right - 10} y0={VG6.cy - 40} x1={ringOf(VG6, 5).right - 6} y1={VG6.cy - 40} />
      <Panel x={CC.x} y={378} w={CC.w} h={258} title="the clock" from={t.ring} color={TEST}>
        <div style={{position: 'absolute', right: 22, top: 10, fontFamily: MONO, fontSize: 44, fontWeight: 700, color: LOOK, opacity: fadeAt(frame, t.now)}}>{hhmm(minutes)}</div>
        {row(70, 'heap   ', heap ? `[${heap}]` : '[]', t.heap)}
        {row(104, 'tasks  ', <span style={{color: parked ? '#5a5f75' : TEXT}}>strategy{parked ? ' (waiting)' : ''}</span>, t.ring)}
        {row(132, '       ', <span style={{color: frame >= f(t.jump) && frame < f(t.wake) ? LOOK : MUTED}}>advance (idle)</span>, t.advance)}
        {row(176, 'log    ', <span style={{color: PROD}}>09:21 t=20 buy  102.49</span>, t.line)}
        {row(204, 'end    ', <span style={{color: LOOK}}>pnl 50.79 at 14:01</span>, t.end)}
        {row(232, 'took   ', <span style={{color: LOOK}}>0.03 s of wall time</span>, t.wall)}
      </Panel>
    </AbsoluteFill>
  );
};

// 7. Layer 6: the wall clock in place of the virtual one; the same Delay becomes a real wait on asyncio.
const WALL = planOf(PAGE, 'wall_clock', DURATIONS, [
  ['o27', {swap: 0.2, getTime: 1.1}],
  ['o28', {delayLine: 0.2, delay: 1.1, sleep: 2.0, resume: 2.9}],
  ['o29', {awaitRing: 0.2, loop: 1.1}],
  ['o30', {bars: 0.2, over: 3.0}],
  ['o31', {virtual: 0.2, wall: 1.1}],
]);
const WALL_LINES: Lines = [[0, 'Layer 6: the wall clock, 60 bars 10 ms apart']];
const WALL_CODE = [
  '@handler',
  'def wall_clock(effect: Delay | GetTime, k):',
  '    if isinstance(effect, GetTime):',
  '        return (yield Resume(k, datetime.now(timezone.utc)))',
  '    yield Await(asyncio.sleep(effect.seconds))',
  '    return (yield Resume(k, None))',
];
const WC: Box = {x: 600, y: 82, w: 650, line: 26};
const WG: Geometry = {cx: 280, cy: 410, bw: 160, bh: 62, gap: 22, font: 14};
const Wall: React.FC = () => {
  const {t} = WALL;
  const frame = useSceneFrame();
  const layers: Layer[] = [
    {label: 'bar_clock(0.01)', at: 0},
    {label: 'finished_at', at: 1},
    {label: 'paper', at: 2},
    {label: 'recorded(BARS[:60])', at: 3},
    {label: 'quiet', at: 4},
    {label: 'virtual_clock(OPEN)', at: 5, until: t.swap},
    {label: 'wall_clock', at: 5, color: PROD, from: t.swap + 0.3, glow: [t.swap + 0.3, t.awaitRing]},
    {label: 'await_handler', at: 6, color: PROD, from: t.awaitRing, glow: [t.awaitRing, t.bars]},
  ];
  const loopX = ringOf(WG, 6).right + 20;
  const bars = Math.floor(60 * ramp(frame, t.bars, t.over));
  const elapsed = 0.61 * ramp(frame, t.bars, t.over);
  return (
    <AbsoluteFill>
      <Headlines lines={WALL_LINES} />
      <Stack layers={layers} g={WG} />
      <CodeBlock lines={WALL_CODE} x={WC.x} y={WC.y} w={WC.w} size={15} line={WC.line} opacity={fadeAt(frame, t.swap)} />
      <LitRange box={WC} a={2} b={3} from={t.getTime} until={t.delayLine} />
      <Lit box={WC} i={4} from={t.delayLine} until={t.resume} />
      <Lit box={WC} i={5} from={t.resume} until={t.bars} />
      <Out g={WG} ring={5} text="Delay(0.01)" from={t.delay} dy={-16} until={t.sleep} />
      <Moving text="Await(asyncio.sleep(0.01))" color={PROD} from={t.sleep} to={t.sleep + 0.7} until={t.resume + 0.6} x0={ringOf(WG, 5).right - 40} y0={WG.cy + 40} x1={ringOf(WG, 6).right + 20} y1={WG.cy + 40} />
      <div
        style={{
          position: 'absolute', left: loopX, top: 330, width: 1260 - loopX, height: 300, boxSizing: 'border-box', borderRadius: 14, border: `3px dashed ${PROD}`,
          background: PANEL, padding: '12px 18px', opacity: fadeAt(frame, t.loop),
        }}
      >
        <div style={{fontFamily: SANS, fontSize: 18, fontWeight: 700, color: PROD}}>an asyncio loop</div>
        <div style={{fontFamily: MONO, fontSize: 18, color: TEXT, marginTop: 18, opacity: fadeAt(frame, t.bars)}}>bars: {bars} / 60</div>
        <div style={{fontFamily: MONO, fontSize: 18, color: TEXT, marginTop: 8, opacity: fadeAt(frame, t.bars)}}>wall time: {elapsed.toFixed(2)} s</div>
        <div style={{fontFamily: SANS, fontSize: 20, fontWeight: 700, color: LOOK, marginTop: 8, opacity: fadeAt(frame, t.over)}}>over 0.6 s, really waited for</div>
        <div style={{fontFamily: MONO, fontSize: 18, color: TEXT, marginTop: 22, whiteSpace: 'pre', opacity: fadeAt(frame, t.virtual)}}>
          <span style={{color: MUTED}}>{'virtual_clock  '}</span>pnl <span style={{color: LOOK, fontWeight: 700}}>6.85</span>
        </div>
        <div style={{fontFamily: MONO, fontSize: 18, color: TEXT, marginTop: 6, whiteSpace: 'pre', opacity: fadeAt(frame, t.wall)}}>
          <span style={{color: MUTED}}>{'wall_clock     '}</span>pnl <span style={{color: LOOK, fontWeight: 700}}>6.85</span>
        </div>
      </div>
    </AbsoluteFill>
  );
};

// 8. Every stack around the same function: only the clock changed.
const SAME = planOf(PAGE, 'same', DURATIONS, [['o32', {backtest: 0.2, virtual: 1.1, wall: 2.0, note: 2.9}]], 1.4);
const SAME_LINES: Lines = [[0, 'One strategy, three clocks']];
const tiny = (cx: number): Geometry => ({cx, cy: 360, bw: 120, bh: 50, gap: 16, font: 12});
const Same: React.FC = () => {
  const {t} = SAME;
  const stacks: [number, string, Layer[], number][] = [
    [210, 'no clock', [{label: 'paper', at: 0}, {label: 'recorded', at: 1}, {label: 'printed', at: 2, color: PROD}], t.backtest],
    [640, 'virtual clock', [{label: 'bar_clock', at: 0}, {label: 'finished_at', at: 1}, {label: 'paper', at: 2}, {label: 'recorded', at: 3}, {label: 'stamped', at: 4, color: PROD}, {label: 'virtual_clock', at: 5}], t.virtual],
    [1070, 'wall clock', [{label: 'bar_clock', at: 0}, {label: 'finished_at', at: 1}, {label: 'paper', at: 2}, {label: 'recorded', at: 3}, {label: 'quiet', at: 4}, {label: 'wall_clock', at: 5, color: PROD}, {label: 'await_handler', at: 6, color: PROD}], t.wall],
  ];
  return (
    <AbsoluteFill>
      <Headlines lines={SAME_LINES} />
      {stacks.map(([cx, name, layers, from]) => (
        <React.Fragment key={name}>
          <Stack layers={layers.map((layer) => ({...layer, from}))} g={tiny(cx)} from={from} />
          <Say x={cx - 150} w={300} y={580} from={from} size={18} color={MUTED} center>{name}</Say>
        </React.Fragment>
      ))}
      <Say x={0} w={1280} y={630} from={t.note} size={26} color={LOOK} center>sma_cross: 0 lines changed</Say>
    </AbsoluteFill>
  );
};

export const ARTICLE3_OPENING = narrate(PAGE, DURATIONS, NARRATION, [
  {length: INTRO.length, starts: INTRO.starts, ids: INTRO.ids, node: <Intro />},
  {length: READ.length, starts: READ.starts, ids: READ.ids, node: <Read />},
  {length: VALUES.length, starts: VALUES.starts, ids: VALUES.ids, node: <Values />},
  {length: BACKTEST.length, starts: BACKTEST.starts, ids: BACKTEST.ids, node: <Backtest />},
  {length: BAR_CLOCK.length, starts: BAR_CLOCK.starts, ids: BAR_CLOCK.ids, node: <BarClock />},
  {length: VIRTUAL.length, starts: VIRTUAL.starts, ids: VIRTUAL.ids, node: <Virtual />},
  {length: WALL.length, starts: WALL.starts, ids: WALL.ids, node: <Wall />},
  {length: SAME.length, starts: SAME.starts, ids: SAME.ids, node: <Same />},
]);
