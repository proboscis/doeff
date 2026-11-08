// The video at the section "Layer 2" of the article "Time is an effect" (docs/articles/time-and-clocks.md): the same
// sma_cross under other layers, each new handler read from the top: a risk stop that drops k, a sweep in parallel
// tasks, record and replay, live trading over an async client, and a test. Every code line on screen is a line of
// examples/effects_tour/trading.py or test_trading.py (check_code.py checks it); every number is a line of
// measure/stacks.py's output. The scenes are planned like article3-opening's: a slot per sentence, motions 0.9 s apart.
import React from 'react';
import {AbsoluteFill} from 'remotion';
import {fadeAt, ramp} from '../../kit/motion';
import {narrate} from '../../kit/narrated';
import {CodeBlock, Headlines, Lines} from '../../kit/parts';
import {ACCENT, f, FAIL, LOOK, MONO, MUTED, PANEL, PROD, SANS, TEST, TEXT} from '../../kit/theme';
import {useSceneFrame} from '../../kit/warp';
import {Box, Lit, mid, Say} from '../article2-opening/Article2Opening';
import {Geometry, K, Moving} from '../article-opening/ArticleOpening';
import {Explain, Layer, LitRange, Out, Panel, planOf, ringOf, Stack} from '../article3-opening/Article3Opening';
import DURATIONS from './durations.json';
import NARRATION from './narration.json';

const PAGE = 'article3-stacks';

// 1. The opening: what the video shows, what doeff is, and the layers it reads.
const INTRO = planOf(PAGE, 'intro', DURATIONS, [
  ['s1', {title: 0.1}],
  ['s2', {effect: 0.3, answer: 1.2}],
  ['s3', {layers: 0.3, strategy: 1.2}],
]);
const INTRO_LINES: Lines = [[0, 'doeff, part 3: the stacks']];
const NEW_LAYERS = ['risk(2.0)', 'sweep', 'recording', 'replaying', 'live', 'first_trade'];
const Intro: React.FC = () => {
  const {t} = INTRO;
  const frame = useSceneFrame();
  return (
    <AbsoluteFill>
      <Headlines lines={INTRO_LINES} />
      <Say x={0} w={1280} y={96} from={t.title} size={54} center>One strategy, eight stacks</Say>
      <Say x={0} w={1280} y={176} from={t.effect - 0.1} size={20} color={MUTED} center>doeff: algebraic effects for Python</Say>
      <Explain y={250} from={t.effect} back={t.answer} />
      <div style={{position: 'absolute', left: 0, right: 0, top: 420, display: 'flex', justifyContent: 'center', gap: 16, opacity: fadeAt(frame, t.layers)}}>
        {NEW_LAYERS.map((name) => (
          <div key={name} style={{padding: '8px 16px', borderRadius: 12, border: `3px solid ${TEST}`, fontFamily: MONO, fontSize: 19, fontWeight: 700, color: TEST}}>
            {name}
          </div>
        ))}
      </div>
      <div
        style={{
          position: 'absolute', left: 530, top: 520, width: 220, height: 64, boxSizing: 'border-box', borderRadius: 10, border: `3px solid ${ACCENT}`, background: '#0b0d13',
          display: 'flex', alignItems: 'center', justifyContent: 'center', fontFamily: MONO, fontSize: 24, fontWeight: 700, color: TEXT, opacity: fadeAt(frame, t.strategy),
        }}
      >
        sma_cross
      </div>
    </AbsoluteFill>
  );
};

// 2. Layer 1 again: the stack every other one starts from.
const BASE = planOf(PAGE, 'base', DURATIONS, [['s4', {strategy: 0.1, rings: 1.0, pnl: 1.9}]]);
const BASE_LINES: Lines = [[0, 'The same sma_cross in every stack']];
const BASE_G: Geometry = {cx: 360, cy: 380, bw: 220, bh: 90, gap: 44, font: 17};
const Base: React.FC = () => {
  const {t} = BASE;
  return (
    <AbsoluteFill>
      <Headlines lines={BASE_LINES} />
      <Stack
        layers={[{label: 'paper(0.0005)', at: 0, from: t.rings}, {label: 'recorded(BARS)', at: 1, from: t.rings}, {label: 'printed', at: 2, color: PROD, from: t.rings}]}
        g={BASE_G} from={t.strategy}
      />
      <Panel x={760} y={300} w={440} h={150} title="Layer 1: the backtest" from={t.pnl} color={PROD}>
        <div style={{fontFamily: MONO, fontSize: 30, fontWeight: 700, color: LOOK, marginTop: 20}}>pnl 50.79</div>
      </Panel>
    </AbsoluteFill>
  );
};

// 3. Layer 2: risk passes each Order on, notes the entry, and drops k after a trade that lost more than 2.0.
const RISK = planOf(PAGE, 'risk', DURATIONS, [
  ['s5', {ring: 0.2, pass: 1.1, order: 2.0}],
  ['s6', {buy: 0.2, entry: 1.1}],
  ['s7', {check: 0.2, rule: 1.1}],
  ['s8', {resumeLine: 0.2, resume: 1.1}],
  ['s9', {fills: 0.2, fourth: 1.1, lost: 2.0}],
  ['s10', {drop: 0.2, result: 1.1}],
]);
const RISK_LINES: Lines = [[0, 'Layer 2: a risk stop that drops k']];
const GUARD_CODE = [
  '@handler',
  'def guard(effect: Order, k):',
  '    price = yield effect',
  '    if effect.side == "buy":',
  '        entry[0] = price',
  '    elif entry[0] is not None and price - entry[0] < -max_loss:',
  '        return f"risk stop: lost {entry[0] - price:.2f} on one trade"',
  '    return (yield Resume(k, price))',
];
// trading.py's line number of a line of GUARD_CODE.
const R = (line: number) => line - 117;
const GUARD: Box = {x: 600, y: 80, w: 660, line: 28};
const RG: Geometry = {cx: 290, cy: 390, bw: 190, bh: 76, gap: 38, font: 15};
// The orders risk passed on and the fills (measure/stacks.log, s9).
const FILLS: [string, string][] = [['buy ', '102.49'], ['sell', '109.35'], ['buy ', '109.50'], ['sell', '107.00']];
const Risk: React.FC = () => {
  const {t} = RISK;
  const frame = useSceneFrame();
  const layers: Layer[] = [
    {label: 'risk(2.0)', at: 0, from: t.ring, glow: [t.ring, t.drop + 1.0]},
    {label: 'paper(0.0005)', at: 1},
    {label: 'recorded(BARS)', at: 2},
    {label: 'quiet', at: 3},
  ];
  const dropped = frame >= f(t.drop);
  const row = (i: number, from: number) => (
    <div key={i} style={{fontFamily: MONO, fontSize: 19, color: i === 3 ? FAIL : TEXT, whiteSpace: 'pre', marginTop: 4, opacity: fadeAt(frame, from)}}>
      {`Order("${FILLS[i][0].trim()}", 1)`.padEnd(19)}
      <span style={{color: MUTED}}>{'-> '}</span>
      {FILLS[i][1]}
    </div>
  );
  return (
    <AbsoluteFill>
      <Headlines lines={RISK_LINES} />
      <Stack layers={layers} g={RG} />
      <CodeBlock lines={GUARD_CODE} x={GUARD.x} y={GUARD.y} w={GUARD.w} size={15} line={GUARD.line} opacity={fadeAt(frame, t.ring)} />
      <Lit box={GUARD} i={R(119)} from={t.pass} until={t.buy} />
      <LitRange box={GUARD} a={R(120)} b={R(121)} from={t.buy} until={t.check} />
      <LitRange box={GUARD} a={R(122)} b={R(123)} from={t.check} until={t.resumeLine} />
      <Lit box={GUARD} i={R(124)} from={t.resumeLine} until={t.fills} />
      <LitRange box={GUARD} a={R(122)} b={R(123)} from={t.lost} color={FAIL} />
      <Out g={RG} ring={1} text={'Order("buy", 1)'} from={t.order} dy={-26} until={t.resume} />
      <Out g={RG} ring={1} text="102.49" color={TEST} from={t.resume} dy={26} back until={t.resume + 1.0} />
      <Panel x={GUARD.x} y={340} w={GUARD.w} h={296} title="what risk sees" from={t.buy} color={TEST}>
        <div style={{fontFamily: MONO, fontSize: 19, color: TEXT, marginTop: 6, opacity: fadeAt(frame, t.entry)}}>
          <span style={{color: MUTED}}>entry = </span>
          {frame >= f(t.fills) ? '109.50' : '102.49'}
        </div>
        <div style={{fontFamily: SANS, fontSize: 18, color: MUTED, marginTop: 6, opacity: fadeAt(frame, t.rule)}}>a loss over 2.0: return a sentence, not Resume</div>
        <div style={{marginTop: 8}}>
          {[0, 1, 2].map((i) => row(i, t.fills))}
          {row(3, t.fourth)}
        </div>
        <div style={{fontFamily: MONO, fontSize: 19, fontWeight: 700, color: FAIL, marginTop: 8, opacity: fadeAt(frame, t.lost)}}>109.50 - 107.00: lost 2.51</div>
      </Panel>
      <div
        style={{
          position: 'absolute', left: RG.cx - RG.bw / 2 + 8, top: RG.cy - 12, padding: '2px 10px', borderRadius: 8, background: dropped ? '#3a3f55' : K, color: '#0f1117',
          fontFamily: MONO, fontSize: 16, fontWeight: 700, opacity: fadeAt(frame, t.order) * (1 - fadeAt(frame, t.drop + 0.5, 0.5)),
        }}
      >
        k
      </div>
      <Say x={40} y={600} from={t.result} size={21} color={LOOK} mono>{'-> "risk stop: lost 2.51 on one trade"'}</Say>
    </AbsoluteFill>
  );
};

// 4. Layer 3: sweep spawns a backtest per pair of averages, gathers the results and sorts them.
const SWEEP = planOf(PAGE, 'sweep', DURATIONS, [
  ['s11', {spawnLine: 0.2, spawn: 1.1}],
  ['s12', {gatherLine: 0.2, gather: 1.1, sorted: 2.0}],
  ['s13', {scheduled: 0.2, side: 1.1}],
]);
const SWEEP_LINES: Lines = [[0, 'Layer 3: a parameter sweep in parallel tasks']];
const SWEEP_CODE = [
  '@do',
  'def sweep(bars: list[Bar], grid: list[tuple[int, int]]):',
  '    tasks = []',
  '    for fast, slow in grid:',
  '        tasks.append((yield Spawn(quiet(recorded(bars)(paper(0.0005)(sma_cross(fast, slow)))))))',
  '    results = yield Gather(*tasks)',
  '    return sorted(zip(results, grid), reverse=True)',
];
// trading.py's line number of a line of SWEEP_CODE.
const W = (line: number) => line - 260;
const SW: Box = {x: 30, y: 80, w: 1220, line: 28};
const GRID: [number, number, string][] = [[3, 10, '43.98'], [3, 20, '51.83'], [5, 10, '42.89'], [5, 20, '50.79'], [8, 20, '55.94'], [8, 40, '59.72']];
// The sweep's rows, best first (measure/stacks.log, s12).
const SORTED = ['59.72  (8, 40)', '55.94  (8, 20)', '51.83  (3, 20)', '50.79  (5, 20)', '43.98  (3, 10)', '42.89  (5, 10)'];
const task = (i: number) => ({x: 60 + (i % 3) * 200, y: 340 + Math.floor(i / 3) * 130});
const Sweep: React.FC = () => {
  const {t} = SWEEP;
  const frame = useSceneFrame();
  return (
    <AbsoluteFill>
      <Headlines lines={SWEEP_LINES} />
      <CodeBlock lines={SWEEP_CODE} x={SW.x} y={SW.y} w={SW.w} size={15} line={SW.line} opacity={fadeAt(frame, 0.1)} />
      <LitRange box={SW} a={W(263)} b={W(264)} from={t.spawnLine} until={t.gatherLine} />
      <Lit box={SW} i={W(265)} from={t.gatherLine} until={t.sorted} />
      <Lit box={SW} i={W(266)} from={t.sorted} />
      <div
        style={{
          position: 'absolute', left: 40, top: 310, width: 620, height: 300, boxSizing: 'border-box', borderRadius: 16, border: `2px dashed ${frame >= f(t.scheduled) ? LOOK : MUTED}`,
          boxShadow: frame >= f(t.scheduled) ? `0 0 22px ${LOOK}66` : 'none', opacity: fadeAt(frame, t.spawn),
        }}
      >
        <div style={{position: 'absolute', left: 12, top: 4, fontFamily: MONO, fontSize: 15, fontWeight: 700, color: frame >= f(t.scheduled) ? LOOK : MUTED}}>scheduled</div>
      </div>
      {GRID.map(([fast, slow, pnl], i) => {
        const {x, y} = task(i);
        return (
          <React.Fragment key={i}>
            <Moving text="Spawn" color={ACCENT} from={t.spawn} to={t.spawn + 0.7} until={t.spawn + 0.9} x0={700} y0={mid(SW, W(264))} x1={x + 90} y1={y + 40} />
            <div
              style={{
                position: 'absolute', left: x, top: y, width: 180, height: 90, boxSizing: 'border-box', borderRadius: 10, border: `3px solid ${ACCENT}`, background: '#0b0d13',
                fontFamily: MONO, fontSize: 15, color: TEXT, padding: '10px 10px', whiteSpace: 'nowrap', opacity: fadeAt(frame, t.spawn + 0.6),
              }}
            >
              sma_cross({fast}, {slow})
            </div>
            <Moving text={pnl} color={TEST} from={t.gather} to={t.gather + 0.7} until={t.sorted} x0={x + 90} y0={y + 60} x1={900} y1={420} />
          </React.Fragment>
        );
      })}
      <Panel x={720} y={310} w={530} h={300} title="sorted, best first" from={t.gather} color={TEST}>
        <div style={{fontFamily: MONO, fontSize: 19, lineHeight: '34px', color: TEXT, whiteSpace: 'pre', marginTop: 6, opacity: fadeAt(frame, t.sorted)}}>
          {SORTED.map((row, i) => (
            <div key={row} style={{color: i === 0 ? LOOK : TEXT}}>{row}</div>
          ))}
        </div>
      </Panel>
      <Say x={60} y={620} from={t.side} size={20} color={LOOK}>six tasks, one run, side by side</Say>
    </AbsoluteFill>
  );
};

// 5. Layer 4: recording keeps every answer; replaying answers from the tape, with no feed and no exchange.
const TAPE = planOf(PAGE, 'record_replay', DURATIONS, [
  ['s14', {ring: 0.2, pass: 1.1, keep: 2.0, back: 2.9}],
  ['s15', {replay: 0.2, pop: 1.1}],
  ['s16', {first: 0.2, again: 1.1, same: 2.0}],
]);
const TAPE_LINES: Lines = [[0, 'Layer 4: record and replay']];
const RECORD_CODE = ['@handler', 'def record(effect: Tick | Order, k):', '    answer = yield effect', '    tape.append(answer)', '    return (yield Resume(k, answer))'];
const REPLAY_CODE = ['@handler', 'def replay(effect: Tick | Order, k):', '    return (yield Resume(k, tape.pop(0)))'];
const RC: Box = {x: 620, y: 78, w: 640, line: 26};
const PC: Box = {x: 620, y: 360, w: 640, line: 26};
const REC_G: Geometry = {cx: 300, cy: 260, bw: 170, bh: 56, gap: 26, font: 14};
const REP_G: Geometry = {cx: 300, cy: 545, bw: 170, bh: 56, gap: 26, font: 14};
const Tape: React.FC = () => {
  const {t} = TAPE;
  const frame = useSceneFrame();
  const kept = Math.round(317 * ramp(frame, t.keep, t.keep + 1.6));
  return (
    <AbsoluteFill>
      <Headlines lines={TAPE_LINES} />
      <Stack
        layers={[
          {label: 'recording(tape)', at: 0, from: t.ring, glow: [t.ring, t.replay]},
          {label: 'paper(0.0005)', at: 1},
          {label: 'recorded(BARS)', at: 2},
          {label: 'quiet', at: 3},
        ]}
        g={REC_G}
      />
      <Stack layers={[{label: 'replaying(tape)', at: 0, from: t.replay, glow: [t.replay, t.first]}, {label: 'quiet', at: 1, from: t.replay}]} g={REP_G} from={t.replay} />
      <Say x={60} y={REP_G.cy - 140} from={t.replay} size={16} color={MUTED}>no feed, no exchange</Say>
      <CodeBlock lines={RECORD_CODE} x={RC.x} y={RC.y} w={RC.w} size={16} line={RC.line} opacity={fadeAt(frame, t.ring)} />
      <Lit box={RC} i={2} from={t.pass} until={t.keep} />
      <Lit box={RC} i={3} from={t.keep} until={t.back} />
      <Lit box={RC} i={4} from={t.back} until={t.replay} />
      <Out g={REC_G} ring={1} text="Tick()" from={t.pass} dy={-14} until={t.keep} />
      <Panel x={RC.x} y={250} w={RC.w} h={90} title="tape" from={t.ring} color={K}>
        <div style={{position: 'absolute', right: 22, top: 18, fontFamily: MONO, fontSize: 34, fontWeight: 700, color: LOOK, opacity: fadeAt(frame, t.keep)}}>{kept} answers</div>
      </Panel>
      <CodeBlock lines={REPLAY_CODE} x={PC.x} y={PC.y} w={PC.w} size={16} line={PC.line} opacity={fadeAt(frame, t.replay)} />
      <Lit box={PC} i={2} from={t.pop} />
      <Panel x={PC.x} y={480} w={PC.w} h={156} title="pnl" from={t.first} color={TEST}>
        <div style={{fontFamily: MONO, fontSize: 20, color: TEXT, whiteSpace: 'pre', marginTop: 8}}>
          <div style={{opacity: fadeAt(frame, t.first)}}><span style={{color: MUTED}}>{'recorded   '}</span><span style={{color: LOOK, fontWeight: 700}}>50.79</span></div>
          <div style={{opacity: fadeAt(frame, t.again)}}><span style={{color: MUTED}}>{'replayed   '}</span><span style={{color: LOOK, fontWeight: 700}}>50.79</span></div>
          <div style={{opacity: fadeAt(frame, t.same)}}><span style={{color: MUTED}}>{'same: '}</span>True<span style={{color: MUTED}}>{'  tape: '}</span>317 answers</div>
        </div>
      </Panel>
    </AbsoluteFill>
  );
};

// 6. Production: live awaits an async exchange client for Tick and Order; await_handler runs the awaits.
const LIVE = planOf(PAGE, 'live', DURATIONS, [
  ['s17', {rings: 0.2, tickLine: 1.1, next: 2.0}],
  ['s18', {orderLine: 0.2, place: 1.1}],
  ['s19', {awaitRing: 0.2, loop: 1.1}],
  ['s20', {buy: 0.2, sell: 1.1, pnl: 2.0}],
]);
const LIVE_LINES: Lines = [[0, 'Production: live over an async exchange']];
const TRADE_CODE = [
  '@handler',
  'def trade(effect: Tick | Order, k):',
  '    if isinstance(effect, Tick):',
  '        bar = yield Await(exchange.next_bar())',
  '        last[0] = bar.close if bar else last[0]',
  '        return (yield Resume(k, bar))',
  '    price = yield Await(exchange.place(effect.side, effect.qty, last[0]))',
  '    return (yield Resume(k, price))',
];
// trading.py's line number of a line of TRADE_CODE.
const L = (line: number) => line - 285;
const TC: Box = {x: 600, y: 78, w: 660, line: 27};
const LG: Geometry = {cx: 280, cy: 390, bw: 190, bh: 76, gap: 40, font: 15};
const Live: React.FC = () => {
  const {t} = LIVE;
  const frame = useSceneFrame();
  const client = {x: 600, y: 340, w: 660, h: 296};
  const outer = ringOf(LG, 0).right;
  return (
    <AbsoluteFill>
      <Headlines lines={LIVE_LINES} />
      <Stack
        layers={[
          {label: 'live(exchange)', at: 0, color: PROD, from: t.rings, glow: [t.tickLine, t.awaitRing]},
          {label: 'printed', at: 1, color: PROD, from: t.rings},
          {label: 'await_handler', at: 2, color: PROD, from: t.rings, glow: [t.awaitRing, t.buy]},
        ]}
        g={LG}
      />
      <Say x={60} y={140} from={t.rings} size={17} color={MUTED}>no feed, no paper fills, no clock: one async client</Say>
      <CodeBlock lines={TRADE_CODE} x={TC.x} y={TC.y} w={TC.w} size={14} line={TC.line} opacity={fadeAt(frame, t.rings)} />
      <Lit box={TC} i={L(288)} from={t.tickLine} until={t.orderLine} />
      <Lit box={TC} i={L(291)} from={t.orderLine} until={t.awaitRing} />
      <Moving text="Await(exchange.next_bar())" color={PROD} from={t.next} to={t.next + 0.8} until={t.next + 1.4} x0={outer - 60} y0={LG.cy - 30} x1={client.x + 220} y1={client.y + 120} />
      <Moving text="Await(exchange.place(…))" color={PROD} from={t.place} to={t.place + 0.8} until={t.place + 1.4} x0={outer - 60} y0={LG.cy + 30} x1={client.x + 220} y1={client.y + 160} />
      <div
        style={{
          position: 'absolute', left: client.x, top: client.y, width: client.w, height: client.h, boxSizing: 'border-box', borderRadius: 14, border: `3px dashed ${PROD}`,
          background: PANEL, padding: '12px 18px', opacity: fadeAt(frame, t.rings),
        }}
      >
        <div style={{fontFamily: SANS, fontSize: 18, fontWeight: 700, color: PROD}}>FakeExchange: an async client, 10 ms a call</div>
        <div style={{fontFamily: SANS, fontSize: 17, color: MUTED, marginTop: 4, opacity: fadeAt(frame, t.loop)}}>run on an asyncio loop, behind await_handler</div>
        <div style={{fontFamily: MONO, fontSize: 20, color: TEXT, marginTop: 96, whiteSpace: 'pre'}}>
          <div style={{opacity: fadeAt(frame, t.buy)}}>t=20 buy  102.44</div>
          <div style={{opacity: fadeAt(frame, t.sell)}}>t=57 sell 109.40</div>
          <div style={{fontSize: 26, fontWeight: 700, color: LOOK, marginTop: 8, opacity: fadeAt(frame, t.pnl)}}>pnl 6.96</div>
        </div>
      </div>
    </AbsoluteFill>
  );
};

// 7. A test is one more stack: first_trade asks the time at the first Log and drops k, so the run ends there.
const TEST_PLAN = planOf(PAGE, 'test', DURATIONS, [
  ['s21', {code: 0.1, runLine: 1.0, stack: 1.9}],
  ['s22', {handler: 0.2, log: 1.1, time: 2.0, now: 2.9}],
  ['s23', {drop: 0.2, result: 1.1, assert: 2.0}],
]);
const TEST_LINES: Lines = [[0, 'Tests are one of the stacks']];
const TEST_CODE = [
  'def test_the_first_trade_happens_at_the_minute_of_its_bar():',
  '    @handler',
  '    def first_trade(effect: Log, k):',
  '        return (yield GetTime()) - OPEN, effect.line',
  ' ',
  '    first = run(scheduled(virtual_clock(OPEN)(first_trade(timed_backtest(BARS, 60)))))',
  '    assert first == (timedelta(minutes=21), "t=20 buy  102.49")',
];
// test_trading.py's line number of a line of TEST_CODE.
const T = (line: number) => line - 17;
const TB: Box = {x: 30, y: 74, w: 1220, line: 28};
const TG: Geometry = {cx: 290, cy: 480, bw: 160, bh: 54, gap: 22, font: 13};
const Test: React.FC = () => {
  const {t} = TEST_PLAN;
  const frame = useSceneFrame();
  const layers: Layer[] = [
    {label: 'bar_clock(60)', at: 0},
    {label: 'finished_at', at: 1},
    {label: 'paper', at: 2},
    {label: 'recorded(BARS)', at: 3},
    {label: 'first_trade', at: 4, color: PROD, glow: [t.handler, t.result]},
    {label: 'virtual_clock(OPEN)', at: 5},
  ];
  const dropped = frame >= f(t.drop);
  return (
    <AbsoluteFill>
      <Headlines lines={TEST_LINES} />
      <CodeBlock lines={TEST_CODE} x={TB.x} y={TB.y} w={TB.w} size={15} line={TB.line} opacity={fadeAt(frame, t.code)} />
      <Lit box={TB} i={T(22)} from={t.runLine} until={t.handler} />
      <LitRange box={TB} a={T(18)} b={T(20)} from={t.handler} until={t.assert} />
      <Lit box={TB} i={T(23)} from={t.assert} />
      <Stack layers={layers.map((layer) => ({...layer, from: t.stack}))} g={TG} from={t.stack} core={dropped ? '#3a3f55' : ACCENT} />
      <Out g={TG} ring={4} text={'Log("t=20 buy …")'} from={t.log} dy={-14} until={t.time} />
      <Moving text="GetTime()" color={ACCENT} from={t.time} to={t.time + 0.7} until={t.now} x0={ringOf(TG, 4).right - 20} y0={TG.cy - 40} x1={ringOf(TG, 5).right + 50} y1={TG.cy - 40} />
      <Moving text="09:21" color={TEST} from={t.now} to={t.now + 0.7} until={t.drop + 0.6} x0={ringOf(TG, 5).right + 50} y0={TG.cy + 40} x1={ringOf(TG, 4).right - 20} y1={TG.cy + 40} />
      <div
        style={{
          position: 'absolute', left: TG.cx - TG.bw / 2 + 6, top: TG.cy - 12, padding: '2px 10px', borderRadius: 8, background: dropped ? '#3a3f55' : K, color: '#0f1117',
          fontFamily: MONO, fontSize: 15, fontWeight: 700, opacity: fadeAt(frame, t.log) * (1 - fadeAt(frame, t.drop + 0.4, 0.5)),
        }}
      >
        k
      </div>
      <Panel x={640} y={330} w={610} h={300} title="first" from={t.stack} color={TEST}>
        <div style={{fontFamily: SANS, fontSize: 18, color: MUTED, marginTop: 10, opacity: fadeAt(frame, t.drop)}}>k is never resumed: the strategy ends at its first trade</div>
        <div style={{fontFamily: MONO, fontSize: 20, color: TEXT, marginTop: 24, whiteSpace: 'pre', opacity: fadeAt(frame, t.result)}}>
          {'(timedelta(minutes=21),\n "t=20 buy  102.49")'}
        </div>
        <div style={{fontFamily: SANS, fontSize: 20, fontWeight: 700, color: LOOK, marginTop: 20, opacity: fadeAt(frame, t.assert)}}>09:21: 21 minutes after the open</div>
      </Panel>
    </AbsoluteFill>
  );
};

// 8. The article's table: eight stacks, one strategy.
const ROWS: [string, string][] = [
  ['backtest', 'paper(0.0005), recorded(BARS), printed'],
  ['risk stop', 'risk(2.0) inside paper, quiet'],
  ['sweep', 'each backtest a task, under scheduled'],
  ['recorded', 'recording(tape) inside paper'],
  ['replayed', 'replaying(tape): no feed, no exchange'],
  ['virtual clock', 'bar_clock(60), stamped, virtual_clock(OPEN)'],
  ['wall clock', 'wall_clock and await_handler'],
  ['production', 'live(exchange) under await_handler'],
];
const TABLE = planOf(PAGE, 'table', DURATIONS, [['s24', Object.fromEntries([...ROWS.map(([run], i) => [run, 0.2 + i * 0.9]), ['note', 0.2 + ROWS.length * 0.9]])]], 1.4);
const TABLE_LINES: Lines = [[0, 'The stacks side by side']];
const Table: React.FC = () => {
  const {t} = TABLE;
  const frame = useSceneFrame();
  return (
    <AbsoluteFill>
      <Headlines lines={TABLE_LINES} />
      <div style={{position: 'absolute', left: 140, top: 90, width: 1000}}>
        {ROWS.map(([run, layers]) => (
          <div key={run} style={{display: 'flex', fontFamily: MONO, fontSize: 20, lineHeight: '50px', borderBottom: '1px solid #2a2e40', opacity: fadeAt(frame, t[run])}}>
            <div style={{width: 230, color: TEST, fontWeight: 700}}>{run}</div>
            <div style={{color: TEXT}}>{layers}</div>
          </div>
        ))}
      </div>
      <Say x={0} w={1280} y={530} from={t.note} size={28} color={LOOK} center>sma_cross: the same in all eight</Say>
    </AbsoluteFill>
  );
};

export const ARTICLE3_STACKS = narrate(PAGE, DURATIONS, NARRATION, [
  {length: INTRO.length, starts: INTRO.starts, ids: INTRO.ids, node: <Intro />},
  {length: BASE.length, starts: BASE.starts, ids: BASE.ids, node: <Base />},
  {length: RISK.length, starts: RISK.starts, ids: RISK.ids, node: <Risk />},
  {length: SWEEP.length, starts: SWEEP.starts, ids: SWEEP.ids, node: <Sweep />},
  {length: TAPE.length, starts: TAPE.starts, ids: TAPE.ids, node: <Tape />},
  {length: LIVE.length, starts: LIVE.starts, ids: LIVE.ids, node: <Live />},
  {length: TEST_PLAN.length, starts: TEST_PLAN.starts, ids: TEST_PLAN.ids, node: <Test />},
  {length: TABLE.length, starts: TABLE.starts, ids: TABLE.ids, node: <Table />},
]);
