// The video at the head of Layer 5 of the article "Algebraic effects for Python" (docs/articles/algebraic-effects-in-python.md):
// win_rate of examples/effects_tour/spire.py running a hundred fights under scheduled, in the order a run has them.
// Each Spawn starts a task while scheduled keeps the k of win_rate; no effect of a fight reaches scheduled, so the task
// runs to its end before win_rate goes on, and Gather finds every fight over. Then the wins of the random bot and of the
// Monte Carlo bot, and the Played of all hundred fights reaching one damage_stats outside. Every code line on screen is
// in spire.py and every number is a line of measure/hundred.py's output (check_code.py checks both).
import React from 'react';
import {AbsoluteFill} from 'remotion';
import {fadeAt, mix, ramp} from '../../kit/motion';
import {narrate} from '../../kit/narrated';
import {CodeBlock, Headlines, Lines, lineMid, Pill, startsOf} from '../../kit/parts';
import {ACCENT, f, FAIL, LOOK, MONO, MUTED, PANEL, SANS, TEST, TEXT} from '../../kit/theme';
import {useSceneFrame} from '../../kit/warp';
import {Geometry, K, Moving, Ring, Tower} from '../article-opening/ArticleOpening';
import DURATIONS from './durations.json';
import NARRATION from './narration.json';

// The output of measure/hundred.py.
const RANDOM_WINS = 'WWWWW.WWW.WWWWWWW.WWW.WW.W.W.WWWWWWWW.WW.WWW.WW.WW.WW.WW..W.WWWWWW.WWWWWWWW.WWW.WW.W.WWW..W...W.WWWW';
const MEASURED = {random_rate: 0.73, random_wins: 73, monte_carlo_rate: 1.0, effects_in_tasks: 7897, task_changes: 99, threads: 1, played_total: 1096};
const RESULTS_FIRST_THREE = "('win', 4), ('win', 7), ('win', 9)";
const REACHED: [string, number][] = [['Spawn', 100], ['Gather', 1], ['Roll', 0], ['Choose', 0], ['Show', 0], ['Played', 0]];
const DAMAGE: [string, number][] = [['Strike', 3663], ['Defend', 0], ['Bash', 632]];

const RANDOM_EXPR = 'run(scheduled(quiet_stats(win_rate(random_bot, 100))))';
const MONTE_EXPR = 'run(scheduled(quiet_stats(win_rate(monte_carlo(PLAYOUTS), 100))))';
const DAMAGE_EXPR = 'run(scheduled(damage_stats(totals)(win_rate(random_bot, 100))))';

// ---- What every scene shares: win_rate on the left, one box per task on the right.
const WIN_RATE_CODE = [
  '@do',
  'def win_rate(player, fights: int):',
  '    tasks = []',
  '    for seed in range(fights):',
  '        tasks.append((yield Spawn(silent(dice(seed)(player(battle()))))))',
  '    results = yield Gather(*tasks)',
  '    return sum(r == "win" for r, _ in results) / fights',
];
const CODE = {x: 40, y: 104, w: 790, size: 16, line: 28};
const SPAWN_LINE = 4;
const GATHER_LINE = 5;
const RETURN_LINE = 6;
const SPAWN_AT = {x: 358, y: lineMid(CODE.y, CODE.line, SPAWN_LINE)};
const GATHER_AT = {x: 287, y: lineMid(CODE.y, CODE.line, GATHER_LINE)};
const K_AT = {x: 690, y: lineMid(CODE.y, CODE.line, GATHER_LINE)};

const Code: React.FC<{band?: number | null; opacity?: number; kOn?: number}> = ({band = null, opacity = 1, kOn = 0}) => (
  <>
    <div style={{position: 'absolute', left: CODE.x, top: CODE.y - 32, fontFamily: SANS, fontSize: 19, fontWeight: 700, color: ACCENT, opacity}}>spire.py: win_rate</div>
    <CodeBlock lines={WIN_RATE_CODE} x={CODE.x} y={CODE.y} w={CODE.w} size={CODE.size} line={CODE.line} band={band} opacity={opacity} />
    {/* k: everything win_rate has left to do after the Spawn. */}
    <div style={{position: 'absolute', left: CODE.x + 4, top: CODE.y + 12 + 3 * CODE.line, width: CODE.w - 8, height: 4 * CODE.line, borderRadius: 8, background: K, opacity: 0.16 * kOn}} />
  </>
);

type Cell = 'slot' | 'run' | 'ended' | 'win' | 'lose';
const BOX = 34;
const GAP = 6;
const GRID = {x: 850, y: 104};
const boxAt = (i: number) => ({x: GRID.x + (i % 10) * (BOX + GAP), y: GRID.y + Math.floor(i / 10) * (BOX + GAP)});
const boxMid = (i: number) => ({x: boxAt(i).x + BOX / 2, y: boxAt(i).y + BOX / 2});
const LOOKS: Record<Cell, {border: string; background: string; shadow?: string}> = {
  slot: {border: '#20242f', background: 'transparent'},
  run: {border: LOOK, background: `${LOOK}55`, shadow: `0 0 14px ${LOOK}aa`},
  ended: {border: MUTED, background: '#2a2e40'},
  win: {border: TEST, background: `${TEST}55`},
  lose: {border: FAIL, background: `${FAIL}33`},
};
const outcome = (i: number): Cell => (RANDOM_WINS[i] === 'W' ? 'win' : 'lose');

const Grid: React.FC<{cell: (i: number) => Cell; ring?: number | null; opacity?: number}> = ({cell, ring = null, opacity = 1}) => (
  <>
    <div style={{position: 'absolute', left: GRID.x, top: GRID.y - 32, fontFamily: SANS, fontSize: 19, fontWeight: 700, color: MUTED}}>one box per task</div>
    {Array.from({length: 100}, (_, i) => {
      const look = LOOKS[cell(i)];
      return (
        <div
          key={i}
          style={{
            position: 'absolute', left: boxAt(i).x, top: boxAt(i).y, width: BOX, height: BOX, boxSizing: 'border-box', borderRadius: 6, opacity,
            border: `2px solid ${i === ring ? LOOK : look.border}`, background: look.background, boxShadow: look.shadow ?? 'none',
          }}
        />
      );
    })}
  </>
);

// The count under the boxes, and what win_rate returns.
const COUNT_Y = GRID.y + 10 * (BOX + GAP) + 10;
const Count: React.FC<{wins: number; from: number; rate?: string; rateFrom?: number; label: string}> = ({wins, from, rate, rateFrom = Infinity, label}) => {
  const frame = useSceneFrame();
  return (
    <>
      <div style={{position: 'absolute', left: GRID.x, top: COUNT_Y, fontFamily: MONO, fontSize: 30, fontWeight: 700, color: LOOK, opacity: fadeAt(frame, from)}}>
        {wins} / 100 wins
      </div>
      <div style={{position: 'absolute', left: GRID.x, top: COUNT_Y + 46, fontFamily: MONO, fontSize: 19, color: MUTED, opacity: fadeAt(frame, from)}}>{label}</div>
      {rate && (
        <div style={{position: 'absolute', left: GRID.x, top: COUNT_Y + 78, fontFamily: MONO, fontSize: 26, fontWeight: 700, color: TEST, opacity: fadeAt(frame, rateFrom)}}>
          -&gt; {rate}
        </div>
      )}
    </>
  );
};

// One line of code in the lower left, with one part of it marked.
const Expr: React.FC<{text: string; mark?: string; y: number; opacity?: number}> = ({text, mark = '', y, opacity = 1}) => {
  const [before, after] = mark ? text.split(mark) : [text, ''];
  return (
    <div style={{position: 'absolute', left: CODE.x, top: y, fontFamily: MONO, fontSize: 18, color: TEXT, whiteSpace: 'pre', opacity}}>
      {before}
      {mark && <span style={{color: LOOK, fontWeight: 700}}>{mark}</span>}
      {after}
    </div>
  );
};

// scheduled, in the lower left: holds what waits.
const SCHEDULED = {x: 40, y: 345, w: 350, h: 150};
const PARKED = {x: SCHEDULED.x + SCHEDULED.w / 2, y: SCHEDULED.y + 96};
const Scheduled: React.FC<{caption: string}> = ({caption}) => (
  <div
    style={{
      position: 'absolute', left: SCHEDULED.x, top: SCHEDULED.y, width: SCHEDULED.w, height: SCHEDULED.h, boxSizing: 'border-box', borderRadius: 14,
      border: `3px solid ${TEST}`, background: PANEL, padding: '10px 18px',
    }}
  >
    <div style={{fontFamily: MONO, fontSize: 22, fontWeight: 700, color: TEST}}>scheduled</div>
    <div style={{fontFamily: SANS, fontSize: 16, color: MUTED, marginTop: 2}}>{caption}</div>
  </div>
);

// ---- 1, 2. One Spawn per seed, and the stack a task carries.
const SPAWN_LINES: Lines = [[0, 'win_rate is a program too: one Spawn per seed, one task per Spawn.'], [7, 'Each task carries its own stack: its own dice.']];
const SPAWN_SECONDS = 13;
// The first Spawns: [when the pill leaves the line, when its task has ended]. The last task is the one the stack is drawn for.
const FIRST_SPAWNS: [number, number][] = [[1.2, 2.8], [2.9, 4.5], [4.6, 6.2], [6.3, Infinity]];
const ZOOMED = FIRST_SPAWNS.length - 1;
const STACK_G: Geometry = {cx: 245, cy: 520, bw: 180, bh: 64, gap: 34, font: 16};
const OWN_STACK: Ring[] = [{label: 'random_bot'}, {label: `dice(${ZOOMED})`, glow: true}, {label: 'silent'}];
const STACK_BOX = {left: STACK_G.cx - (STACK_G.bw + 6 * STACK_G.gap) / 2, top: STACK_G.cy - (STACK_G.bh + 6 * STACK_G.gap) / 2, w: STACK_G.bw + 6 * STACK_G.gap, h: STACK_G.bh + 6 * STACK_G.gap};

const Spawning: React.FC = () => {
  const frame = useSceneFrame();
  const at = (seconds: number) => frame >= f(seconds);
  const zoom = ramp(frame, 7.3, 8.1);
  const mid = boxMid(ZOOMED);
  const seed = FIRST_SPAWNS.reduce((found, [from], i) => (at(from) ? i : found), 0);
  return (
    <AbsoluteFill>
      <Headlines lines={SPAWN_LINES} />
      <Code band={at(0.8) ? SPAWN_LINE : null} opacity={fadeAt(frame, 0.1)} />
      <div style={{position: 'absolute', left: 420, top: lineMid(CODE.y, CODE.line, 3) - 13, fontFamily: MONO, fontSize: 18, color: LOOK, opacity: fadeAt(frame, 1.0)}}>seed = {seed}</div>
      <Grid cell={(i) => {
        const spawn = FIRST_SPAWNS[i];
        if (!spawn || !at(spawn[0] + 0.7)) return 'slot';
        return at(spawn[1]) ? 'ended' : 'run';
      }} />
      {FIRST_SPAWNS.map(([from], i) => (
        <Moving key={i} text="Spawn" color={ACCENT} from={from} to={from + 0.7} until={from + 0.8} x0={SPAWN_AT.x} y0={SPAWN_AT.y} x1={boxMid(i).x} y1={boxMid(i).y} />
      ))}
      {/* The box opens into the stack it carries. */}
      <div
        style={{
          position: 'absolute', boxSizing: 'border-box', borderRadius: 10, border: `3px solid ${LOOK}`, left: mix(mid.x - BOX / 2, STACK_BOX.left, zoom), top: mix(mid.y - BOX / 2, STACK_BOX.top, zoom),
          width: mix(BOX, STACK_BOX.w, zoom), height: mix(BOX, STACK_BOX.h, zoom), opacity: at(7.3) ? 1 - fadeAt(frame, 8.1, 0.3) : 0,
        }}
      />
      <Tower rings={OWN_STACK} g={STACK_G} opacity={fadeAt(frame, 8.1, 0.4)} />
      <div style={{position: 'absolute', left: 480, top: 390, width: 350, opacity: fadeAt(frame, 8.5)}}>
        <div style={{fontFamily: SANS, fontSize: 18, fontWeight: 700, color: MUTED, marginBottom: 10}}>each task, its own generator</div>
        {[0, 1, 2, ZOOMED].map((n) => (
          <div key={n} style={{fontFamily: MONO, fontSize: 20, lineHeight: '36px', color: n === ZOOMED ? LOOK : TEXT, whiteSpace: 'pre'}}>
            task {n}{'   '}dice({n})
          </div>
        ))}
        <div style={{fontFamily: MONO, fontSize: 20, lineHeight: '36px', color: MUTED}}>...</div>
      </div>
    </AbsoluteFill>
  );
};

// ---- 3. The tasks take turns on one thread.
const TURN_LINES: Lines = [[0, "One thread. scheduled holds win_rate's k; the task runs to its end."]];
const TURN_SECONDS = 14;
// The tasks shown one by one, then the rest faster and faster, in the order the run has them (spawn order).
const SLOW_FROM = FIRST_SPAWNS.length;
const SLOW_START = 1.5;
const SLOW_STEP = 1.7;
const FAST_FROM = SLOW_FROM + 3;
const FAST_START = SLOW_START + 3 * SLOW_STEP;
const FAST_END = 11.2;
const started = (seed: number) =>
  seed < FAST_FROM ? SLOW_START + (seed - SLOW_FROM) * SLOW_STEP + 0.5 : FAST_START + (FAST_END - FAST_START) * ((seed - FAST_FROM) / (100 - FAST_FROM)) ** 0.7;
const ended = (seed: number) => (seed < FAST_FROM ? started(seed) + 0.7 : started(seed + 1));

type Key = [number, number, number];
const along = (keys: Key[], t: number) => {
  const next = keys.findIndex(([at]) => at > t);
  if (next === -1) return keys[keys.length - 1];
  if (next === 0) return keys[0];
  const [a, ax, ay] = keys[next - 1];
  const [b, bx, by] = keys[next];
  const part = (t - a) / (b - a);
  return [t, mix(ax, bx, part), mix(ay, by, part)];
};

const AT_LINE: [number, number] = [SPAWN_AT.x + 90, SPAWN_AT.y - 26];
const aboveBox = (seed: number): [number, number] => [boxMid(seed).x, boxMid(seed).y - 26];
const THREAD_KEYS: Key[] = [
  [0, ...aboveBox(ZOOMED)],
  [0.6, ...aboveBox(ZOOMED)],
  [1.1, ...AT_LINE],
  ...[0, 1, 2].flatMap((n): Key[] => {
    const seed = SLOW_FROM + n;
    return [[started(seed) - 0.2, ...AT_LINE], [started(seed), ...aboveBox(seed)], [ended(seed), ...aboveBox(seed)], [ended(seed) + 0.3, ...AT_LINE]];
  }),
  ...Array.from({length: 100 - FAST_FROM}, (_, n): Key => [started(FAST_FROM + n), ...aboveBox(FAST_FROM + n)]),
  [FAST_END, ...aboveBox(99)],
  [FAST_END + 0.4, GATHER_AT.x + 90, GATHER_AT.y - 26],
];
const K_KEYS: Key[] = [
  [0, K_AT.x, K_AT.y],
  ...[0, 1, 2].flatMap((n): Key[] => {
    const seed = SLOW_FROM + n;
    return [[started(seed) - 0.5, K_AT.x, K_AT.y], [started(seed) - 0.1, PARKED.x, PARKED.y], [ended(seed), PARKED.x, PARKED.y], [ended(seed) + 0.3, K_AT.x, K_AT.y]];
  }),
  [FAST_START - 0.1, K_AT.x, K_AT.y],
  [FAST_START + 0.3, PARKED.x, PARKED.y],
  [FAST_END, PARKED.x, PARKED.y],
  [FAST_END + 0.4, K_AT.x, K_AT.y],
];
const WHO: [string, string][] = [['Roll', 'dice(seed)'], ['Choose', 'random_bot'], ['Show', 'silent'], ['Played', 'quiet_stats']];

const Turns: React.FC = () => {
  const frame = useSceneFrame();
  const t = frame / f(1);
  const at = (seconds: number) => t >= seconds;
  const cell = (i: number): Cell => {
    if (i < SLOW_FROM) return i === ZOOMED && !at(0.8) ? 'run' : 'ended';
    if (!at(started(i))) return 'slot';
    return at(ended(i)) ? 'ended' : 'run';
  };
  const running = Array.from({length: 100}, (_, i) => i).find((i) => cell(i) === 'run');
  const [, threadX, threadY] = along(THREAD_KEYS, t);
  const [, kX, kY] = along(K_KEYS, t);
  const kHeld = Math.min(1, Math.hypot(kX - K_AT.x, kY - K_AT.y) / 60);
  return (
    <AbsoluteFill>
      <Headlines lines={TURN_LINES} />
      <Code band={at(FAST_END + 0.3) ? GATHER_LINE : SPAWN_LINE} kOn={kHeld} />
      <Grid cell={cell} />
      <div style={{position: 'absolute', left: GRID.x, top: COUNT_Y, fontFamily: MONO, fontSize: 24, color: LOOK}}>{at(FAST_END) ? 'all 100 ended' : running === undefined ? 'win_rate running' : `task ${running} running`}</div>
      <Scheduled caption="keeps k, the rest of win_rate, while a task runs" />
      <div
        style={{
          position: 'absolute', left: 410, top: SCHEDULED.y, width: 420, height: SCHEDULED.h + 26, boxSizing: 'border-box', borderRadius: 14, padding: '8px 18px',
          border: '2px solid #2a2e40', background: PANEL, opacity: fadeAt(frame, 2.4),
        }}
      >
        <div style={{fontFamily: SANS, fontSize: 16, fontWeight: 700, color: MUTED, marginBottom: 2}}>who answers a fight's effects</div>
        {WHO.map(([effect, answer]) => (
          <div key={effect} style={{fontFamily: MONO, fontSize: 18, lineHeight: '26px', whiteSpace: 'pre', color: TEXT}}>
            <span style={{color: ACCENT, fontWeight: 700}}>{effect.padEnd(8)}</span>
            {answer}
          </div>
        ))}
        <div style={{fontFamily: SANS, fontSize: 17, lineHeight: '28px', fontWeight: 700, color: LOOK, opacity: fadeAt(frame, 4.4)}}>none of them is scheduled</div>
      </div>
      {[0, 1, 2].map((n) => {
        const seed = SLOW_FROM + n;
        const from = started(seed) - 0.5;
        return <Moving key={seed} text="Spawn" color={ACCENT} from={from} to={from + 0.5} until={from + 0.55} x0={SPAWN_AT.x} y0={SPAWN_AT.y} x1={boxMid(seed).x} y1={boxMid(seed).y} />;
      })}
      {/* The thread: where the one running thing is. */}
      <Pill x={threadX} y={threadY} text="thread" color={LOOK} />
      <Pill x={kX} y={kY} text="k" color={K} opacity={fadeAt(frame, 0.9)} />
      <div style={{position: 'absolute', left: CODE.x, top: 540, width: 790, fontFamily: MONO, fontSize: 21, lineHeight: '38px', color: TEXT, opacity: fadeAt(frame, FAST_END + 0.1)}}>
        <div>{MEASURED.effects_in_tasks.toLocaleString('en-US')} effects in the tasks</div>
        <div>{MEASURED.task_changes} changes of task, {MEASURED.threads} thread</div>
        <div style={{color: LOOK}}>reached scheduled: {REACHED.filter(([, n]) => n > 0).map(([name, n]) => `${name} ${n}`).join(', ')}</div>
      </div>
    </AbsoluteFill>
  );
};

// ---- 4, 5. Gather, then the count.
const GATHER_LINES: Lines = [[0, 'Gather gives back the results. Every fight has already ended.'], [4.5, 'On the same hundred seeds: 73 wins for the random bot.']];
const GATHER_SECONDS = 9;
const SWEEP = {from: 2.4, to: 4.3};
const COUNTING = {from: 4.8, to: 7.2};
const swept = (frame: number, {from, to}: {from: number; to: number}) => Math.floor(ramp(frame, from, to) * 100);
const winsWithin = (wins: string, n: number) => [...wins].slice(0, n).filter((won) => won === 'W').length;

const Gathering: React.FC = () => {
  const frame = useSceneFrame();
  const at = (seconds: number) => frame >= f(seconds);
  const coloured = swept(frame, SWEEP);
  const counted = swept(frame, COUNTING);
  return (
    <AbsoluteFill>
      <Headlines lines={GATHER_LINES} />
      <Code band={at(4.5) ? RETURN_LINE : GATHER_LINE} />
      <Grid cell={(i) => (i < coloured ? outcome(i) : 'ended')} ring={at(COUNTING.from) && !at(COUNTING.to) ? counted : null} />
      <Scheduled caption="every task is over: nothing to wait for" />
      <Moving text="Gather" color={ACCENT} from={0.4} to={1.2} until={1.5} x0={GATHER_AT.x} y0={GATHER_AT.y} x1={PARKED.x} y1={PARKED.y} />
      <Moving text="results" color={TEST} from={1.5} to={2.4} until={2.7} x0={PARKED.x} y0={PARKED.y} x1={GATHER_AT.x} y1={GATHER_AT.y} />
      <Expr text={`results = [${RESULTS_FIRST_THREE}, ...]`} y={515} opacity={fadeAt(frame, 2.5)} />
      <Expr text={RANDOM_EXPR} mark="random_bot" y={575} opacity={fadeAt(frame, 4.9)} />
      <Count wins={winsWithin(RANDOM_WINS, counted)} from={COUNTING.from} rate={MEASURED.random_rate.toFixed(2)} rateFrom={COUNTING.to + 0.1} label="win_rate(random_bot, 100)" />
    </AbsoluteFill>
  );
};

// ---- 6. One argument changed.
const SWAP_LINES: Lines = [[0, 'One argument changed: the Monte Carlo bot wins all 100.']];
const SWAP_SECONDS = 7;
const MONTE_SWEEP = {from: 2.0, to: 4.6};

const Swapping: React.FC = () => {
  const frame = useSceneFrame();
  const swap = fadeAt(frame, 1.2, 0.5);
  const won = swept(frame, MONTE_SWEEP);
  const rerun = frame >= f(1.6);
  return (
    <AbsoluteFill>
      <Headlines lines={SWAP_LINES} />
      <Code />
      <Grid cell={(i) => (rerun ? (i < won ? 'win' : 'slot') : outcome(i))} />
      <Expr text={RANDOM_EXPR} mark="random_bot" y={395} opacity={1 - swap} />
      <Expr text={MONTE_EXPR} mark="monte_carlo(PLAYOUTS)" y={395} opacity={swap} />
      <div style={{position: 'absolute', left: CODE.x, top: 460, fontFamily: MONO, fontSize: 22, lineHeight: '42px', whiteSpace: 'pre', color: TEXT, opacity: fadeAt(frame, 2.0)}}>
        <div>random_bot             {MEASURED.random_wins} / 100</div>
        <div style={{color: LOOK, opacity: fadeAt(frame, MONTE_SWEEP.to)}}>monte_carlo(PLAYOUTS)  100 / 100</div>
      </div>
      <div style={{position: 'absolute', left: CODE.x, top: 570, fontFamily: SANS, fontSize: 21, color: MUTED, opacity: fadeAt(frame, 2.6)}}>win_rate: 0 lines changed. battle: 0 lines changed.</div>
      <div style={{opacity: rerun ? 1 : 0}}>
        <Count wins={won} from={MONTE_SWEEP.from} rate={MEASURED.monte_carlo_rate.toFixed(1)} rateFrom={MONTE_SWEEP.to + 0.1} label="win_rate(monte_carlo(3), 100)" />
      </div>
    </AbsoluteFill>
  );
};

// ---- 7. One handler outside hears all hundred fights.
const OUT_LINES: Lines = [[0, 'Played travels past the Spawn: one handler hears every fight.']];
const OUT_SECONDS = 10.5;
const FLOW = {from: 1.4, to: 7.4};
const FLIGHTS = 22;
const TOTALS = {x: 40, y: 450, w: 500, h: 216};
// Which boxes the pills leave from: spread over the grid, in no pattern a reader would look for meaning in.
const flight = (n: number) => {
  const i = (n * 37 + 11) % 100;
  const from = FLOW.from + (n * (FLOW.to - FLOW.from - 1.0)) / (FLIGHTS - 1);
  return {i, from, to: from + 1.0};
};

const Outside: React.FC = () => {
  const frame = useSceneFrame();
  const share = ramp(frame, FLOW.from + 0.8, FLOW.to);
  return (
    <AbsoluteFill>
      <Headlines lines={OUT_LINES} />
      <Code opacity={0.45} />
      <Grid cell={outcome} opacity={0.55} />
      <Expr text={DAMAGE_EXPR} mark="damage_stats(totals)" y={345} />
      <div style={{position: 'absolute', left: CODE.x, top: 392, width: 790, fontFamily: SANS, fontSize: 18, color: MUTED, opacity: fadeAt(frame, 0.8)}}>
        A task's stack is <span style={{fontFamily: MONO, color: TEXT}}>random_bot, dice(seed), silent</span>: none of them takes <span style={{fontFamily: MONO, color: ACCENT}}>Played</span>.
      </div>
      {/* The edge of the tasks: past it, the handlers around win_rate. */}
      <div style={{position: 'absolute', left: 836, top: GRID.y - 8, height: 416, borderLeft: `2px dashed ${MUTED}`, opacity: fadeAt(frame, 0.8)}} />
      <div style={{position: 'absolute', left: GRID.x, top: COUNT_Y, fontFamily: SANS, fontSize: 18, color: MUTED, opacity: fadeAt(frame, 0.8)}}>
        dashed line: past the <span style={{fontFamily: MONO, color: TEXT}}>Spawn</span>
      </div>
      <div
        style={{
          position: 'absolute', left: TOTALS.x, top: TOTALS.y, width: TOTALS.w, height: TOTALS.h, boxSizing: 'border-box', borderRadius: 14, border: `3px solid ${LOOK}`,
          boxShadow: `0 0 22px ${LOOK}55`, background: PANEL, padding: '12px 22px', opacity: fadeAt(frame, 0.4),
        }}
      >
        <div style={{fontFamily: MONO, fontSize: 22, fontWeight: 700, color: LOOK, marginBottom: 8}}>damage_stats(totals)</div>
        {DAMAGE.map(([card, total]) => (
          <div key={card} style={{fontFamily: MONO, fontSize: 28, lineHeight: '44px', whiteSpace: 'pre', color: TEXT}}>
            {card.padEnd(8)}<span style={{fontWeight: 700}}>{String(Math.round(total * share)).padStart(5)}</span>
          </div>
        ))}
      </div>
      <div style={{position: 'absolute', left: 570, top: TOTALS.y + 70, width: 260, fontFamily: SANS, fontSize: 21, lineHeight: '30px', color: TEXT, opacity: fadeAt(frame, FLOW.to)}}>
        <span style={{fontFamily: MONO, fontWeight: 700, color: ACCENT}}>{MEASURED.played_total.toLocaleString('en-US')} Played</span> from 100 fights, in one handler
      </div>
      {Array.from({length: FLIGHTS}, (_, n) => {
        const {i, from, to} = flight(n);
        return <Moving key={n} text="Played" color={ACCENT} from={from} to={to} until={to + 0.05} x0={boxMid(i).x} y0={boxMid(i).y} x1={TOTALS.x + TOTALS.w - 60} y1={TOTALS.y + 30} />;
      })}
    </AbsoluteFill>
  );
};

export const ARTICLE_HUNDRED_FIGHTS = narrate('article-hundred-fights', DURATIONS, NARRATION, [
  {length: f(SPAWN_SECONDS), starts: startsOf(SPAWN_LINES), ids: ['s1', 's2'], node: <Spawning />},
  {length: f(TURN_SECONDS), starts: startsOf(TURN_LINES), ids: ['s3'], node: <Turns />},
  {length: f(GATHER_SECONDS), starts: startsOf(GATHER_LINES), ids: ['s4', 's5'], node: <Gathering />},
  {length: f(SWAP_SECONDS), starts: startsOf(SWAP_LINES), ids: ['s6'], node: <Swapping />},
  {length: f(OUT_SECONDS), starts: startsOf(OUT_LINES), ids: ['s7'], node: <Outside />},
]);
