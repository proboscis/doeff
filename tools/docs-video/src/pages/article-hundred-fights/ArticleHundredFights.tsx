// The video at the head of Layer 5 of the article "Algebraic effects for Python" (docs/articles/algebraic-effects-in-python.md):
// win_rate of examples/effects_tour/spire.py running a hundred fights under scheduled, in the order a run has them.
// It opens still, on its title, the code of win_rate and the run under scheduled, while the narration says what the
// video shows and which functions it follows.
// Each Spawn starts a task while scheduled keeps the k of win_rate; no effect of a fight reaches scheduled, so the task
// runs to its end before win_rate goes on, and Gather finds every fight over. Then the wins of the random bot and of the
// Monte Carlo bot, and the Played of all hundred fights reaching one damage_stats outside. Every code line on screen is
// in spire.py and every number is a line of measure/hundred.py's output (check_code.py checks both).
// The motions of a scene come one at a time, each a pause after the one before it has stopped, so the eye can follow
// every one while its line is heard; a scene with more motions than its line has time for goes on after the line.
import React from 'react';
import {AbsoluteFill} from 'remotion';
import {fadeAt, mix, ramp} from '../../kit/motion';
import {LEAD, narrate} from '../../kit/narrated';
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

// ---- The pace. A motion starts PAUSE s after the one before it has stopped; a scene's next line starts PAUSE s after
// both the line before it and its motions have stopped, and a scene holds still for HOLD s (its last 0.4 s fade out)
// after its last line and its last motion.
const PAUSE = 0.9;
const HOLD = 1.6;
type Step = {from: number; to: number};
// Motions end to end from a time: [name, seconds it lasts, the pause before it when it is not PAUSE].
const chain = <K extends string>(start: number, motions: [K, number, number?][]): Record<K, Step> => {
  let at = start;
  const steps = {} as Record<K, Step>;
  motions.forEach(([name, seconds, pause = PAUSE], i) => {
    const from = i === 0 ? at : at + pause;
    steps[name] = {from, to: from + seconds};
    at = from + seconds;
  });
  return steps;
};
const lastOf = (steps: Record<string, Step>) => Math.max(...Object.values(steps).map(({to}) => to));
// When the line with this id, read from a headline at this time, has been heard.
const heardBy = (headline: number, id: keyof typeof DURATIONS) => headline + LEAD + DURATIONS[id];

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

// ---- 0. What the video is about, held still: its title, the code of win_rate and the handler it runs under.
const INTRO_AT = heardBy(0, 'i1') + PAUSE;
const INTRO_LINES: Lines = [[0, 'This video: one battle a hundred times, one task per fight.'], [INTRO_AT, 'win_rate starts the fights; scheduled runs them. We follow Spawn and Gather.']];
const INTRO_SECONDS = heardBy(INTRO_AT, 'i2') + HOLD;
const INTRO_CODE = {x: 90, y: 234, w: 1100, size: 19, line: 32};

const Intro: React.FC = () => (
  <AbsoluteFill>
    <Headlines lines={INTRO_LINES} />
    <div style={{position: 'absolute', left: 0, right: 0, top: 76, textAlign: 'center'}}>
      <div style={{fontFamily: SANS, fontSize: 46, fontWeight: 700, color: TEXT}}>A hundred fights, one task each</div>
      <div style={{fontFamily: SANS, fontSize: 21, color: ACCENT, marginTop: 4}}>doeff: algebraic effects for Python</div>
    </div>
    <div style={{position: 'absolute', left: INTRO_CODE.x, top: INTRO_CODE.y - 32, fontFamily: SANS, fontSize: 19, fontWeight: 700, color: ACCENT}}>spire.py: win_rate</div>
    <CodeBlock lines={WIN_RATE_CODE} x={INTRO_CODE.x} y={INTRO_CODE.y} w={INTRO_CODE.w} size={INTRO_CODE.size} line={INTRO_CODE.line} />
    <div style={{position: 'absolute', left: INTRO_CODE.x, top: 524, fontFamily: SANS, fontSize: 19, fontWeight: 700, color: TEST}}>run under the handler scheduled</div>
    <div style={{position: 'absolute', left: INTRO_CODE.x, top: 558, fontFamily: MONO, fontSize: 19, color: TEXT, whiteSpace: 'pre'}}>
      {RANDOM_EXPR.split('scheduled')[0]}
      <span style={{color: TEST, fontWeight: 700}}>scheduled</span>
      {RANDOM_EXPR.split('scheduled')[1]}
    </div>
  </AbsoluteFill>
);

// ---- 1, 2. One Spawn per seed, and the stack a task carries.
// A Spawn pill's trip from the line to its box, and how long its task then runs.
const FLIGHT = 0.7;
const RUN = 1.0;
const SPAWN_STEPS = chain(0.8, [['line', 0.3], ['spawn0', FLIGHT + RUN], ['spawn1', FLIGHT + RUN], ['spawn2', FLIGHT + RUN], ['spawn3', FLIGHT]]);
const STACK_AT = Math.max(lastOf(SPAWN_STEPS), heardBy(0, 's1')) + PAUSE;
// The last box opens into the stack it carries, then the list of tasks and their dice.
const STACK_STEPS = chain(STACK_AT + PAUSE, [['open', 1.2], ['list', 0.4]]);
const SPAWN_LINES: Lines = [[0, 'win_rate is a program too: one Spawn per seed, one task per Spawn.'], [STACK_AT, 'Each task carries its own stack: its own dice.']];
const SPAWN_SECONDS = Math.max(lastOf(STACK_STEPS), heardBy(STACK_AT, 's2')) + HOLD;
// The first Spawns: [when the pill leaves the line, when its task has ended]. The last task is the one the stack is drawn for.
const FIRST_SPAWNS: [number, number][] = [SPAWN_STEPS.spawn0, SPAWN_STEPS.spawn1, SPAWN_STEPS.spawn2, {...SPAWN_STEPS.spawn3, to: Infinity}].map(({from, to}) => [from, to]);
const ZOOMED = FIRST_SPAWNS.length - 1;
const STACK_G: Geometry = {cx: 245, cy: 520, bw: 180, bh: 64, gap: 34, font: 16};
const OWN_STACK: Ring[] = [{label: 'random_bot'}, {label: `dice(${ZOOMED})`, glow: true}, {label: 'silent'}];
const STACK_BOX = {left: STACK_G.cx - (STACK_G.bw + 6 * STACK_G.gap) / 2, top: STACK_G.cy - (STACK_G.bh + 6 * STACK_G.gap) / 2, w: STACK_G.bw + 6 * STACK_G.gap, h: STACK_G.bh + 6 * STACK_G.gap};

const Spawning: React.FC = () => {
  const frame = useSceneFrame();
  const at = (seconds: number) => frame >= f(seconds);
  const {open, list} = STACK_STEPS;
  const opened = open.from + 0.8;
  const zoom = ramp(frame, open.from, opened);
  const mid = boxMid(ZOOMED);
  const seed = FIRST_SPAWNS.reduce((found, [from], i) => (at(from) ? i : found), 0);
  return (
    <AbsoluteFill>
      <Headlines lines={SPAWN_LINES} />
      <Code band={at(SPAWN_STEPS.line.from) ? SPAWN_LINE : null} opacity={fadeAt(frame, 0.1)} />
      <div style={{position: 'absolute', left: 420, top: lineMid(CODE.y, CODE.line, 3) - 13, fontFamily: MONO, fontSize: 18, color: LOOK, opacity: fadeAt(frame, SPAWN_STEPS.line.from)}}>seed = {seed}</div>
      <Grid cell={(i) => {
        const spawn = FIRST_SPAWNS[i];
        if (!spawn || !at(spawn[0] + FLIGHT)) return 'slot';
        return at(spawn[1]) ? 'ended' : 'run';
      }} />
      {FIRST_SPAWNS.map(([from], i) => (
        <Moving key={i} text="Spawn" color={ACCENT} from={from} to={from + FLIGHT} until={from + FLIGHT + 0.1} x0={SPAWN_AT.x} y0={SPAWN_AT.y} x1={boxMid(i).x} y1={boxMid(i).y} />
      ))}
      {/* The box opens into the stack it carries. */}
      <div
        style={{
          position: 'absolute', boxSizing: 'border-box', borderRadius: 10, border: `3px solid ${LOOK}`, left: mix(mid.x - BOX / 2, STACK_BOX.left, zoom), top: mix(mid.y - BOX / 2, STACK_BOX.top, zoom),
          width: mix(BOX, STACK_BOX.w, zoom), height: mix(BOX, STACK_BOX.h, zoom), opacity: at(open.from) ? 1 - fadeAt(frame, opened, 0.3) : 0,
        }}
      />
      <Tower rings={OWN_STACK} g={STACK_G} opacity={fadeAt(frame, opened, 0.4)} />
      <div style={{position: 'absolute', left: 480, top: 390, width: 350, opacity: fadeAt(frame, list.from, list.to - list.from)}}>
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
// One Spawn step by step, then the rest at once. The task of the last Spawn ends and the thread goes back to win_rate;
// k shows; the next Spawn leaves the line; scheduled takes k; the thread runs the new task; who answers a fight's
// effects; none of them is scheduled; the task ends, k comes back and the thread goes on in win_rate (after a longer
// pause, while the line says the task runs to its end). Then scheduled takes k for the rest of the tasks, which run
// one after another faster and faster, in the order the run has them (spawn order); k and the thread go on to Gather;
// what the run counted.
const TURN_STEPS = chain(0.6, [
  ['back', 0.5], ['kShown', 0.3], ['spawn', 0.6], ['kHeld', 0.6], ['run', 0.4], ['who', 0.3], ['none', 0.3], ['resumed', 0.5, 2.4],
  ['kHeldAll', 0.4], ['rest', 5.0], ['gather', 0.4], ['counted', 0.3],
]);
const TURN_SECONDS = Math.max(lastOf(TURN_STEPS), heardBy(0, 's3')) + HOLD;
const SLOW = FIRST_SPAWNS.length;
const FAST_FROM = SLOW + 1;
// The rest: the thread leaves the line, then reaches each box in turn.
const FAST_START = TURN_STEPS.rest.from + 0.3;
const FAST_END = TURN_STEPS.rest.to;
const started = (seed: number) =>
  seed < FAST_FROM ? TURN_STEPS.run.to : FAST_START + (FAST_END - FAST_START) * ((seed - FAST_FROM) / (100 - FAST_FROM)) ** 0.7;
const ended = (seed: number) => (seed < FAST_FROM ? TURN_STEPS.resumed.from : started(seed + 1));

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
const {back, kShown, spawn, kHeld, run, who, none, resumed, kHeldAll, rest, gather, counted} = TURN_STEPS;
const AT_GATHER: [number, number] = [GATHER_AT.x + 90, GATHER_AT.y - 26];
const THREAD_KEYS: Key[] = [
  [0, ...aboveBox(ZOOMED)],
  [back.from, ...aboveBox(ZOOMED)],
  [back.to, ...AT_LINE],
  [run.from, ...AT_LINE],
  [run.to, ...aboveBox(SLOW)],
  [resumed.from, ...aboveBox(SLOW)],
  [resumed.to, ...AT_LINE],
  [rest.from, ...AT_LINE],
  ...Array.from({length: 100 - FAST_FROM}, (_, n): Key => [started(FAST_FROM + n), ...aboveBox(FAST_FROM + n)]),
  [gather.from, ...aboveBox(99)],
  [gather.to, ...AT_GATHER],
];
const K_KEYS: Key[] = [
  [0, K_AT.x, K_AT.y],
  [kHeld.from, K_AT.x, K_AT.y],
  [kHeld.to, PARKED.x, PARKED.y],
  [resumed.from, PARKED.x, PARKED.y],
  [resumed.to, K_AT.x, K_AT.y],
  [kHeldAll.from, K_AT.x, K_AT.y],
  [kHeldAll.to, PARKED.x, PARKED.y],
  [gather.from, PARKED.x, PARKED.y],
  [gather.to, K_AT.x, K_AT.y],
];
const WHO: [string, string][] = [['Roll', 'dice(seed)'], ['Choose', 'random_bot'], ['Show', 'silent'], ['Played', 'quiet_stats']];

const Turns: React.FC = () => {
  const frame = useSceneFrame();
  const t = frame / f(1);
  const at = (seconds: number) => t >= seconds;
  const cell = (i: number): Cell => {
    if (i < SLOW) return i === ZOOMED && !at(back.from) ? 'run' : 'ended';
    if (!at(started(i))) return 'slot';
    return at(ended(i)) ? 'ended' : 'run';
  };
  const running = Array.from({length: 100}, (_, i) => i).find((i) => cell(i) === 'run');
  const [, threadX, threadY] = along(THREAD_KEYS, t);
  const [, kX, kY] = along(K_KEYS, t);
  const kOn = Math.min(1, Math.hypot(kX - K_AT.x, kY - K_AT.y) / 60);
  return (
    <AbsoluteFill>
      <Headlines lines={TURN_LINES} />
      <Code band={at(gather.to) ? GATHER_LINE : SPAWN_LINE} kOn={kOn} />
      <Grid cell={cell} />
      <div style={{position: 'absolute', left: GRID.x, top: COUNT_Y, fontFamily: MONO, fontSize: 24, color: LOOK}}>{at(rest.to) ? 'all 100 ended' : running === undefined ? 'win_rate running' : `task ${running} running`}</div>
      <Scheduled caption="keeps k, the rest of win_rate, while a task runs" />
      <div
        style={{
          position: 'absolute', left: 410, top: SCHEDULED.y, width: 420, height: SCHEDULED.h + 26, boxSizing: 'border-box', borderRadius: 14, padding: '8px 18px',
          border: '2px solid #2a2e40', background: PANEL, opacity: fadeAt(frame, who.from),
        }}
      >
        <div style={{fontFamily: SANS, fontSize: 16, fontWeight: 700, color: MUTED, marginBottom: 2}}>who answers a fight's effects</div>
        {WHO.map(([effect, answer]) => (
          <div key={effect} style={{fontFamily: MONO, fontSize: 18, lineHeight: '26px', whiteSpace: 'pre', color: TEXT}}>
            <span style={{color: ACCENT, fontWeight: 700}}>{effect.padEnd(8)}</span>
            {answer}
          </div>
        ))}
        <div style={{fontFamily: SANS, fontSize: 17, lineHeight: '28px', fontWeight: 700, color: LOOK, opacity: fadeAt(frame, none.from)}}>none of them is scheduled</div>
      </div>
      <Moving text="Spawn" color={ACCENT} from={spawn.from} to={spawn.to} until={spawn.to + 0.1} x0={SPAWN_AT.x} y0={SPAWN_AT.y} x1={boxMid(SLOW).x} y1={boxMid(SLOW).y} />
      {/* The thread: where the one running thing is. */}
      <Pill x={threadX} y={threadY} text="thread" color={LOOK} />
      <Pill x={kX} y={kY} text="k" color={K} opacity={fadeAt(frame, kShown.from)} />
      <div style={{position: 'absolute', left: CODE.x, top: 540, width: 790, fontFamily: MONO, fontSize: 21, lineHeight: '38px', color: TEXT, opacity: fadeAt(frame, counted.from)}}>
        <div>{MEASURED.effects_in_tasks.toLocaleString('en-US')} effects in the tasks</div>
        <div>{MEASURED.task_changes} changes of task, {MEASURED.threads} thread</div>
        <div style={{color: LOOK}}>reached scheduled: {REACHED.filter(([, n]) => n > 0).map(([name, n]) => `${name} ${n}`).join(', ')}</div>
      </div>
    </AbsoluteFill>
  );
};

// ---- 4, 5. Gather, then the count.
// Gather travels to scheduled; the results come back; their first three; the boxes take their outcomes. Then win_rate's
// call; the count of the wins; what win_rate returns.
const GATHER_STEPS = chain(0.6, [['gather', 0.8], ['results', 0.9], ['listed', 0.3], ['sweep', 1.9]]);
const COUNT_AT = Math.max(lastOf(GATHER_STEPS), heardBy(0, 's4')) + PAUSE;
const COUNT_STEPS = chain(COUNT_AT + PAUSE, [['call', 0.3], ['counting', 2.5], ['rate', 0.3]]);
const GATHER_LINES: Lines = [[0, 'Gather gives back the results. Every fight has already ended.'], [COUNT_AT, 'On the same hundred seeds: 73 wins for the random bot.']];
const GATHER_SECONDS = Math.max(lastOf(COUNT_STEPS), heardBy(COUNT_AT, 's5')) + HOLD;
const SWEEP = GATHER_STEPS.sweep;
const COUNTING = COUNT_STEPS.counting;
const swept = (frame: number, {from, to}: {from: number; to: number}) => Math.floor(ramp(frame, from, to) * 100);
const winsWithin = (wins: string, n: number) => [...wins].slice(0, n).filter((won) => won === 'W').length;

const Gathering: React.FC = () => {
  const frame = useSceneFrame();
  const at = (seconds: number) => frame >= f(seconds);
  const {gather, results, listed} = GATHER_STEPS;
  const coloured = swept(frame, SWEEP);
  const counted = swept(frame, COUNTING);
  return (
    <AbsoluteFill>
      <Headlines lines={GATHER_LINES} />
      <Code band={at(COUNT_AT) ? RETURN_LINE : GATHER_LINE} />
      <Grid cell={(i) => (i < coloured ? outcome(i) : 'ended')} ring={at(COUNTING.from) && !at(COUNTING.to) ? counted : null} />
      <Scheduled caption="every task is over: nothing to wait for" />
      <Moving text="Gather" color={ACCENT} from={gather.from} to={gather.to} until={gather.to + 0.3} x0={GATHER_AT.x} y0={GATHER_AT.y} x1={PARKED.x} y1={PARKED.y} />
      <Moving text="results" color={TEST} from={results.from} to={results.to} until={results.to + 0.3} x0={PARKED.x} y0={PARKED.y} x1={GATHER_AT.x} y1={GATHER_AT.y} />
      <Expr text={`results = [${RESULTS_FIRST_THREE}, ...]`} y={515} opacity={fadeAt(frame, listed.from)} />
      <Expr text={RANDOM_EXPR} mark="random_bot" y={575} opacity={fadeAt(frame, COUNT_STEPS.call.from)} />
      <Count wins={winsWithin(RANDOM_WINS, counted)} from={COUNTING.from} rate={MEASURED.random_rate.toFixed(2)} rateFrom={COUNT_STEPS.rate.from} label="win_rate(random_bot, 100)" />
    </AbsoluteFill>
  );
};

// ---- 6. One argument changed.
const SWAP_LINES: Lines = [[0, 'One argument changed: the Monte Carlo bot wins all 100.']];
// The argument changes; the run again, its wins filling the boxes; what win_rate returns; the two counts side by side,
// one at a time; what did not change.
const SWAP_STEPS = chain(0.8, [['swap', 0.5], ['rerun', 2.6], ['rate', 0.3], ['random', 0.3], ['monte', 0.3], ['unchanged', 0.3]]);
const SWAP_SECONDS = Math.max(lastOf(SWAP_STEPS), heardBy(0, 's6')) + HOLD;
const MONTE_SWEEP = SWAP_STEPS.rerun;

const Swapping: React.FC = () => {
  const frame = useSceneFrame();
  const {swap: swapping, rate, random, monte, unchanged} = SWAP_STEPS;
  const swap = fadeAt(frame, swapping.from, swapping.to - swapping.from);
  const won = swept(frame, MONTE_SWEEP);
  const rerun = frame >= f(MONTE_SWEEP.from);
  return (
    <AbsoluteFill>
      <Headlines lines={SWAP_LINES} />
      <Code />
      <Grid cell={(i) => (rerun ? (i < won ? 'win' : 'slot') : outcome(i))} />
      <Expr text={RANDOM_EXPR} mark="random_bot" y={395} opacity={1 - swap} />
      <Expr text={MONTE_EXPR} mark="monte_carlo(PLAYOUTS)" y={395} opacity={swap} />
      <div style={{position: 'absolute', left: CODE.x, top: 460, fontFamily: MONO, fontSize: 22, lineHeight: '42px', whiteSpace: 'pre', color: TEXT, opacity: fadeAt(frame, random.from)}}>
        <div>random_bot             {MEASURED.random_wins} / 100</div>
        <div style={{color: LOOK, opacity: fadeAt(frame, monte.from)}}>monte_carlo(PLAYOUTS)  100 / 100</div>
      </div>
      <div style={{position: 'absolute', left: CODE.x, top: 570, fontFamily: SANS, fontSize: 21, color: MUTED, opacity: fadeAt(frame, unchanged.from)}}>win_rate: 0 lines changed. battle: 0 lines changed.</div>
      <div style={{opacity: rerun ? 1 : 0}}>
        <Count wins={won} from={MONTE_SWEEP.from} rate={MEASURED.monte_carlo_rate.toFixed(1)} rateFrom={rate.from} label="win_rate(monte_carlo(3), 100)" />
      </div>
    </AbsoluteFill>
  );
};

// ---- 7. One handler outside hears all hundred fights.
const OUT_LINES: Lines = [[0, 'Played travels past the Spawn: one handler hears every fight.']];
const FLIGHTS = 22;
// One Played's trip, and the start of one trip after the one before it, while the rest travel.
const TRIP = 1.0;
const EVERY = 0.24;
// What a task's stack holds; the edge of the tasks; the one handler outside; the first Played travels to it alone;
// then the rest of them, one leaving every EVERY s; how many it heard.
const OUT_STEPS = chain(0.5, [['stack', 0.3], ['edge', 0.3], ['totals', 0.3], ['first', TRIP], ['rest', (FLIGHTS - 2) * EVERY + TRIP], ['heard', 0.3]]);
const OUT_SECONDS = Math.max(lastOf(OUT_STEPS), heardBy(0, 's7')) + HOLD;
const TOTALS = {x: 40, y: 450, w: 500, h: 216};
// Which boxes the pills leave from: spread over the grid, in no pattern a reader would look for meaning in.
const flight = (n: number) => {
  const i = (n * 37 + 11) % 100;
  const from = n === 0 ? OUT_STEPS.first.from : OUT_STEPS.rest.from + (n - 1) * EVERY;
  return {i, from, to: from + TRIP};
};

const Outside: React.FC = () => {
  const frame = useSceneFrame();
  const {stack, edge, totals, first, rest, heard} = OUT_STEPS;
  const share = ramp(frame, first.to, rest.to);
  return (
    <AbsoluteFill>
      <Headlines lines={OUT_LINES} />
      <Code opacity={0.45} />
      <Grid cell={outcome} opacity={0.55} />
      <Expr text={DAMAGE_EXPR} mark="damage_stats(totals)" y={345} />
      <div style={{position: 'absolute', left: CODE.x, top: 392, width: 790, fontFamily: SANS, fontSize: 18, color: MUTED, opacity: fadeAt(frame, stack.from)}}>
        A task's stack is <span style={{fontFamily: MONO, color: TEXT}}>random_bot, dice(seed), silent</span>: none of them takes <span style={{fontFamily: MONO, color: ACCENT}}>Played</span>.
      </div>
      {/* The edge of the tasks: past it, the handlers around win_rate. */}
      <div style={{position: 'absolute', left: 836, top: GRID.y - 8, height: 416, borderLeft: `2px dashed ${MUTED}`, opacity: fadeAt(frame, edge.from)}} />
      <div style={{position: 'absolute', left: GRID.x, top: COUNT_Y, fontFamily: SANS, fontSize: 18, color: MUTED, opacity: fadeAt(frame, edge.from)}}>
        dashed line: past the <span style={{fontFamily: MONO, color: TEXT}}>Spawn</span>
      </div>
      <div
        style={{
          position: 'absolute', left: TOTALS.x, top: TOTALS.y, width: TOTALS.w, height: TOTALS.h, boxSizing: 'border-box', borderRadius: 14, border: `3px solid ${LOOK}`,
          boxShadow: `0 0 22px ${LOOK}55`, background: PANEL, padding: '12px 22px', opacity: fadeAt(frame, totals.from),
        }}
      >
        <div style={{fontFamily: MONO, fontSize: 22, fontWeight: 700, color: LOOK, marginBottom: 8}}>damage_stats(totals)</div>
        {DAMAGE.map(([card, total]) => (
          <div key={card} style={{fontFamily: MONO, fontSize: 28, lineHeight: '44px', whiteSpace: 'pre', color: TEXT}}>
            {card.padEnd(8)}<span style={{fontWeight: 700}}>{String(Math.round(total * share)).padStart(5)}</span>
          </div>
        ))}
      </div>
      <div style={{position: 'absolute', left: 570, top: TOTALS.y + 70, width: 260, fontFamily: SANS, fontSize: 21, lineHeight: '30px', color: TEXT, opacity: fadeAt(frame, heard.from)}}>
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
  {length: f(INTRO_SECONDS), starts: startsOf(INTRO_LINES), ids: ['i1', 'i2'], node: <Intro />},
  {length: f(SPAWN_SECONDS), starts: startsOf(SPAWN_LINES), ids: ['s1', 's2'], node: <Spawning />},
  {length: f(TURN_SECONDS), starts: startsOf(TURN_LINES), ids: ['s3'], node: <Turns />},
  {length: f(GATHER_SECONDS), starts: startsOf(GATHER_LINES), ids: ['s4', 's5'], node: <Gathering />},
  {length: f(SWAP_SECONDS), starts: startsOf(SWAP_LINES), ids: ['s6'], node: <Swapping />},
  {length: f(OUT_SECONDS), starts: startsOf(OUT_LINES), ids: ['s7'], node: <Outside />},
]);
