// The video on the What doeff Is page (docs/positioning/what-doeff-is.md): what doeff is, in the page's order. Eight
// scenes, each with headlines a synthetic voice reads (narration.json):
//   1. The one sentence: a program yields effects, a handler receives each with k and decides what it means.
//   2. The mechanism: research yields Spend, and free, refuse, budget(10) and budget(5) give it four meanings.
//   3. Handlers stack: trace takes every effect first and passes it on to budget.
//   4. One mechanism: crawl's state, exceptions, concurrency, observation and I/O each reach their own handler.
//   5. Swapping I/O is one use: another Fetch handler, no Fetch handler, and the uses that have no I/O in them.
//   6. The limits: one-shot continuations, no effects in the types, the cost of one effect.
//   7. Where doeff sits next to effect, effectful, OCaml 5 and Koka.
//   8. The outro: the sentence to describe doeff with.
// Every output, order and time on screen is printed by a program in measure/ (its .log is next to it); the table of
// scene 7 is the page's own, with its sources.
import React from 'react';
import {AbsoluteFill} from 'remotion';
import {fadeAt, mix, ramp} from '../../kit/motion';
import {LEAD, narrate} from '../../kit/narrated';
import {CodeBlock, glow, Headlines, highlightYield, lineMid, Lines, PAD, Pill, startsOf} from '../../kit/parts';
import {ACCENT, f, FAIL, LOOK, MONO, MUTED, PANEL, PROD, SANS, TEST, TEXT} from '../../kit/theme';
import {useSceneFrame} from '../../kit/warp';
import DURATIONS from './durations.json';
import NARRATION from './narration.json';

// Seen between two seconds of a scene: fades in at the first and out at the second.
const between = (frame: number, from: number, to: number, length = 0.3) => fadeAt(frame, from, length) * (1 - fadeAt(frame, to, length));
// Whether the frame is within [from, to) seconds.
const during = (frame: number, from: number, to: number) => frame >= f(from) && frame < f(to);

// A label in the scenes' sans font.
const Label: React.FC<{x: number; y: number; size?: number; color?: string; bold?: boolean; opacity?: number; children: React.ReactNode; center?: boolean; w?: number}> = ({
  x, y, size = 20, color = MUTED, bold = false, opacity = 1, children, center = false, w,
}) => (
  <div
    style={{
      position: 'absolute', left: x, top: y, width: w, fontFamily: SANS, fontSize: size, fontWeight: bold ? 700 : 400, color, opacity,
      whiteSpace: 'nowrap', textAlign: center ? 'center' : 'left', transform: center && w === undefined ? 'translateX(-50%)' : undefined,
    }}
  >
    {children}
  </div>
);

// A box of the diagrams: a panel with a coloured border that glows while it is at work.
const Box: React.FC<{x: number; y: number; w: number; h: number; color: string; on?: boolean; opacity?: number; dashed?: boolean; children?: React.ReactNode}> = ({
  x, y, w, h, color, on = false, opacity = 1, dashed = false, children,
}) => (
  <div
    style={{
      position: 'absolute', left: x, top: y, width: w, height: h, boxSizing: 'border-box', borderRadius: 16, background: PANEL,
      border: `3px ${dashed ? 'dashed' : 'solid'} ${color}`, ...glow(on, color), opacity,
    }}
  >
    {children}
  </div>
);

// A short word in a rounded frame: a use of handlers, or what the handler decides.
const Chip: React.FC<{x: number; y: number; w: number; color: string; opacity: number; children: React.ReactNode; on?: boolean}> = ({x, y, w, color, opacity, children, on = false}) => (
  <div
    style={{
      position: 'absolute', left: x, top: y, width: w, height: 50, boxSizing: 'border-box', borderRadius: 12, border: `2px solid ${color}`, ...glow(on, color),
      background: PANEL, fontFamily: SANS, fontSize: 20, fontWeight: 700, color, lineHeight: '46px', textAlign: 'center', opacity, whiteSpace: 'nowrap',
    }}
  >
    {children}
  </div>
);

// The layers of an onion of handlers around a code box: layer 0 is the innermost.
const onion = (cx: number, cy: number, w: number, h: number, margin: number) => (i: number) => {
  const lw = w + 2 * margin * (i + 1);
  const lh = h + 2 * margin * (i + 1);
  return {x: cx - lw / 2, y: cy - lh / 2, w: lw, h: lh};
};

// A code box's line band moving through a list of [second, line] steps, each move taking 0.3 s.
const bandAt = (frame: number, steps: [number, number][]): number | null => {
  if (steps.length === 0 || frame < f(steps[0][0])) return null;
  return steps.slice(1).reduce((line, [at, to]) => mix(line, to, ramp(frame, at, at + 0.3)), steps[0][1]);
};

// Scene 1 — the one sentence.

const TITLE_LINES: Lines = [
  [0, 'What doeff is: algebraic effects for Python.'],
  [5.2, 'A program yields effects. A handler receives each one with k, the rest of the program.'],
  [11.9, 'What the handler does with k decides what the effect means.'],
];
const TITLE = f(16.5);
const T_CODE = ['@do', 'def research(topics):', '    for topic in topics:', '        yield Spend(3)', '        ...'];
const T = {x: 110, y: 300, w: 440, size: 21, line: 34};
const T_HANDLER = {x: 760, y: 300, w: 410, h: 190};
const DECIDES = ['goes on', 'with what', 'when', 'or not at all'];

const TitleScene: React.FC = () => {
  const frame = useSceneFrame();
  const fly = ramp(frame, 6.0, 7.0);
  const kFly = ramp(frame, 6.6, 7.6);
  const yieldY = lineMid(T.y, T.line, 3);
  const lift = ramp(frame, 5.0, 5.6);
  return (
    <AbsoluteFill>
      <Headlines lines={TITLE_LINES} />
      <div style={{position: 'absolute', left: 0, right: 0, top: 150 + mix(16, 0, ramp(frame, 0.2, 0.8)), textAlign: 'center', opacity: fadeAt(frame, 0.2, 0.5) * (1 - lift)}}>
        <div style={{fontFamily: SANS, fontSize: 80, fontWeight: 700, color: TEXT}}>What doeff Is</div>
        <div style={{fontFamily: SANS, fontSize: 30, color: ACCENT, marginTop: 6}}>algebraic effects for Python</div>
      </div>
      <CodeBlock lines={T_CODE} x={T.x} y={T.y} w={T.w} size={T.size} line={T.line} band={frame >= f(5.6) ? 3 : null} dim={(i) => (i === 4 && frame >= f(6.6) ? 0.4 : 1)} opacity={fadeAt(frame, 5.3)} />
      <Label x={T.x} y={T.y - 34} size={19} bold color={ACCENT} opacity={fadeAt(frame, 5.3)}>a program</Label>
      <Box x={T_HANDLER.x} y={T_HANDLER.y} w={T_HANDLER.w} h={T_HANDLER.h} color={TEST} on={during(frame, 7.0, 11.4)} opacity={fadeAt(frame, 5.6)}>
        <div style={{position: 'absolute', left: 22, top: 14, fontFamily: SANS, fontSize: 26, fontWeight: 700, color: during(frame, 7.0, 11.4) ? LOOK : TEST}}>a handler</div>
        <div style={{position: 'absolute', left: 22, top: 56, fontFamily: MONO, fontSize: 19, color: MUTED}}>receives (effect, k)</div>
      </Box>
      {frame >= f(6.0) && <Pill x={mix(T.x + T.w + 20, T_HANDLER.x + 110, fly)} y={mix(yieldY, T_HANDLER.y + 128, fly)} text="Spend(3)" color={ACCENT} />}
      {frame >= f(6.6) && (
        <div
          style={{
            position: 'absolute', left: mix(T.x + T.w / 2, T_HANDLER.x + 280, kFly), top: mix(lineMid(T.y, T.line, 4), T_HANDLER.y + 128, kFly), transform: 'translate(-50%, -50%)',
            padding: '4px 14px', borderRadius: 10, background: '#1d1a33', border: `3px dashed ${ACCENT}`, fontFamily: MONO, fontSize: 18, color: TEXT, whiteSpace: 'nowrap',
          }}
        >
          k: the rest
        </div>
      )}
      <Label x={640} y={540} size={22} bold color={TEST} center opacity={fadeAt(frame, 12.2)}>
        the handler decides whether the program
      </Label>
      {DECIDES.map((word, i) => (
        <Chip key={word} x={640 - (DECIDES.length * 236 - 20) / 2 + i * 236} y={590} w={216} color={TEST} opacity={fadeAt(frame, 12.6 + i * 0.5)}>
          {word}
        </Chip>
      ))}
    </AbsoluteFill>
  );
};

// ---- The pace of scenes 2, 3 and 4. A motion starts PAUSE s after the one before it has stopped, so the eye can follow
// every one; a scene's next line starts PAUSE s after both the line before it and its motions have stopped, and a scene
// holds still for HOLD s after its last line and its last motion.
const PAUSE = 0.9;
const HOLD = 1.6;
type Step = {from: number; to: number};
// Motions end to end from a time: [name, seconds it lasts].
const chain = <K extends string>(start: number, motions: [K, number][]): Record<K, Step> => {
  let at = start;
  const steps = {} as Record<K, Step>;
  motions.forEach(([name, seconds], i) => {
    const from = i === 0 ? at : at + PAUSE;
    steps[name] = {from, to: from + seconds};
    at = from + seconds;
  });
  return steps;
};
const lastOf = (steps: object) => Math.max(...(Object.values(steps) as Step[]).map(({to}) => to));
// When the line with this id, read from a headline at this time, has been heard.
const heardBy = (headline: number, id: keyof typeof DURATIONS) => headline + LEAD + DURATIONS[id];

// Scene 2 — research under four handlers (measure/research.py).

const RESEARCH = ['@do', 'def research(topics):', '    notes = []', '    for topic in topics:', '        yield Spend(3)', '        notes.append(f"notes on {topic}")', '    return notes'];
const M = {x: 60, y: 120, w: 540, size: 19, line: 31};
const MH = {x: 650, y: 120, w: 570, h: 214};
// A Spend's trip from the yield to the handler, the handler's glow as it takes it, an answer's trip back, one move of
// the line band, and k dropped (the code after the yield dims, then the result goes to its row of the table).
const M_FLY = 0.7;
const M_GLOW = 0.5;
const M_BACK = 0.5;
const MOVE = 0.3;
const M_DROP = 0.3 + M_FLY + 0.3;
// The handler's code shows as its line comes; the first motion follows.
const SHOWN = 0.3;
// A run's motions, in seconds from its line: each Spend goes out and the handler glows as it takes it; None comes back
// and research steps on to its next line; at the end the result goes to the table, or the handler drops k.
type RunSteps = {spend1: Step; back1?: Step; spend2?: Step; back2?: Step; result?: Step; drop?: Step};
const RESUMES: RunSteps = chain(SHOWN + PAUSE, [['spend1', M_FLY + M_GLOW], ['back1', M_BACK + MOVE], ['spend2', MOVE + M_FLY + M_GLOW], ['back2', M_BACK + MOVE], ['result', MOVE + 0.4]]);
const REFUSES: RunSteps = chain(SHOWN + PAUSE, [['spend1', M_FLY + M_GLOW], ['drop', M_DROP]]);
const RUNS_OUT: RunSteps = chain(SHOWN + PAUSE, [['spend1', M_FLY + M_GLOW], ['back1', M_BACK + MOVE], ['spend2', MOVE + M_FLY + M_GLOW], ['drop', M_DROP]]);
type RunId = 'm2' | 'm3' | 'm4' | 'm5';
type Run = {name: string; id: RunId; line: string; at: number; limit: number; steps: RunSteps; body: string[]; result: string};
const BUDGET_BODY = ['def on_spend(effect: Spend, k):', '    if effect.cost > left:', '        return "over budget"', '    left -= effect.cost', '    return (yield Resume(k, None))'];
// The four handlers of measure/research.py and what run(...) returned under each (research.log).
const RUN_DEFS: Omit<Run, 'at'>[] = [
  {
    name: 'free', id: 'm2', line: 'free answers each Spend and lets the rest run: notes on both topics.', limit: 0, steps: RESUMES,
    body: ['@do', 'def free(effect: Spend, k):', '    yield Transfer(k, None)'], result: "['notes on tides', 'notes on moons']",
  },
  {name: 'refuse', id: 'm3', line: 'refuse drops k at the first Spend. research stops there.', limit: 0, steps: REFUSES, body: ['@do', 'def refuse(effect: Spend, k):', '    return "refused"'], result: "'refused'"},
  {name: 'budget(10)', id: 'm4', line: 'budget keeps what is left between effects. With 10, both Spends fit.', limit: 10, steps: RESUMES, body: BUDGET_BODY, result: "['notes on tides', 'notes on moons']"},
  {name: 'budget(5)', id: 'm5', line: 'With 5, only 2 is left for the second Spend. budget drops k: "over budget".', limit: 5, steps: RUNS_OUT, body: BUDGET_BODY, result: "'over budget'"},
];
// Before the first run: the code, its yield, the handler box, what it is for, and the table of results.
const MECH_INTRO = chain(0, [['code', 0.4], ['band', MOVE], ['handler', 0.4], ['meaning', 0.4], ['table', 0.4]]);
const RUNS: Run[] = RUN_DEFS.reduce<Run[]>((runs, def) => {
  const before = runs[runs.length - 1];
  const at = before ? Math.max(before.at + lastOf(before.steps), heardBy(before.at, before.id)) : Math.max(lastOf(MECH_INTRO), heardBy(0, 'm1'));
  return [...runs, {...def, at: at + PAUSE}];
}, []);
const LAST_RUN = RUNS[RUNS.length - 1];
const MECH_LINES: Lines = [[0, 'research yields one Spend per topic. It knows nothing about what spending means.'], ...RUNS.map((run): [number, string] => [run.at, run.line])];
const MECH = f(Math.max(LAST_RUN.at + lastOf(LAST_RUN.steps), heardBy(LAST_RUN.at, LAST_RUN.id)) + HOLD);
const ROWS_Y = 446;
const rowY = (i: number) => ROWS_Y + 44 + i * 46 + 18;
// When a run's row of the table shows: as research returns, or as the result the handler returned arrives.
const rowAt = (run: Run) => run.at + (run.steps.result ? run.steps.result.from + MOVE : (run.steps.drop?.from ?? 0) + 0.3 + M_FLY);
const NO_STEPS: RunSteps = {spend1: {from: Infinity, to: Infinity}};

const MechScene: React.FC = () => {
  const frame = useSceneFrame();
  const current = RUNS.reduce((found, run, i) => (frame >= f(run.at) ? i : found), -1);
  const run = current >= 0 ? RUNS[current] : null;
  const s = run ? run.at : 0;
  const st = run ? run.steps : NO_STEPS;
  const t = (from: number, to: number) => ramp(frame, s + from, s + to);
  const past = (seconds: number | undefined) => run !== null && seconds !== undefined && frame >= f(s + seconds);
  const within = (from: number | undefined, length: number) => run !== null && from !== undefined && during(frame, s + from, s + from + length);
  // The Spends leave the yield; the answers leave the handler.
  const leaves = [st.spend1.from, st.spend2 && st.spend2.from + MOVE];
  const backs = [st.back1?.from, st.back2?.from];
  const arrived = leaves.map((from) => (from === undefined ? undefined : from + M_FLY));
  const answered = backs.map((from) => (from === undefined ? undefined : from + M_BACK));
  const drop = st.drop?.from;
  const dropped = drop === undefined ? 0 : t(drop, drop + 0.3);
  const moves: [number | undefined, number][] = [[answered[0], 5], [st.spend2?.from, 4], [answered[1], 5], [st.result?.from, 6]];
  const band = !run
    ? bandAt(frame, [[MECH_INTRO.band.from, 4]])
    : bandAt(frame, [[s, 4], ...moves.filter((move): move is [number, number] => move[0] !== undefined).map(([at, line]): [number, number] => [s + at, line])]);
  const topic = past(st.spend2?.from) ? 'moons' : 'tides';
  const notes = past(answered[1]) ? "['notes on tides', 'notes on moons']" : past(answered[0]) ? "['notes on tides']" : '[]';
  const left = !run ? 0 : run.limit - (past(arrived[0]) ? 3 : 0) - (st.back2 && past(arrived[1]) ? 3 : 0);
  const working = arrived.some((at) => within(at, M_GLOW));
  const over = run?.limit === 5 && past(arrived[1]);
  return (
    <AbsoluteFill>
      <Headlines lines={MECH_LINES} />
      <CodeBlock lines={RESEARCH} x={M.x} y={M.y} w={M.w} size={M.size} line={M.line} band={band} dim={(i) => (i >= 5 ? 1 - 0.7 * dropped : 1)} opacity={fadeAt(frame, MECH_INTRO.code.from, 0.4)} />
      <div style={{position: 'absolute', left: M.x, top: M.y + PAD * 2 + RESEARCH.length * M.line + 14, fontFamily: MONO, fontSize: 18, lineHeight: '28px', color: TEXT, opacity: fadeAt(frame, MECH_INTRO.code.from, 0.4)}}>
        <div>
          topic = <span style={{color: ACCENT}}>'{topic}'</span>
        </div>
        <div>
          notes = <span style={{color: TEST}}>{notes}</span>
        </div>
      </div>
      <Box x={MH.x} y={MH.y} w={MH.w} h={MH.h} color={TEST} on={working} opacity={fadeAt(frame, MECH_INTRO.handler.from, 0.4)}>
        <div style={{position: 'absolute', left: 20, top: 12, fontFamily: SANS, fontSize: 22, fontWeight: 700, color: working ? LOOK : TEST}}>
          {run ? <span style={{fontFamily: MONO}}>{run.name}</span> : 'a handler of Spend'}
        </div>
        {!run && <div style={{position: 'absolute', left: 20, top: 56, fontFamily: SANS, fontSize: 19, color: MUTED, opacity: fadeAt(frame, MECH_INTRO.meaning.from, 0.4)}}>gives Spend its meaning: research does not</div>}
        {RUNS.map((r, i) => (
          <div key={r.name} style={{position: 'absolute', left: 20, top: 50, fontFamily: MONO, fontSize: 16, lineHeight: '29px', color: TEXT, whiteSpace: 'pre', opacity: i === current ? fadeAt(frame, r.at, SHOWN) : 0}}>
            {r.body.map((line, j) => (
              <div key={j} style={{color: line.startsWith('@') ? ACCENT : TEXT}}>
                {line.includes('yield') ? highlightYield(line) : line}
              </div>
            ))}
          </div>
        ))}
      </Box>
      {run && run.limit > 0 && (
        <div style={{position: 'absolute', left: MH.x, top: MH.y + MH.h + 14, width: MH.w, height: 70, opacity: fadeAt(frame, s, SHOWN)}}>
          <div style={{position: 'absolute', left: 0, top: 0, fontFamily: SANS, fontSize: 19, color: MUTED}}>
            left <span style={{fontFamily: MONO, fontSize: 22, fontWeight: 700, color: over ? FAIL : TEXT, marginLeft: 8}}>{left}</span>
            {over && <span style={{fontFamily: MONO, fontSize: 20, fontWeight: 700, color: FAIL, marginLeft: 22}}>cost 3 &gt; left 2</span>}
          </div>
          {Array.from({length: run.limit}, (_, i) => (
            <div key={i} style={{position: 'absolute', left: i * 52, top: 34, width: 44, height: 24, borderRadius: 6, background: i < left ? TEST : '#2a2e40', boxShadow: i < left ? `0 0 8px ${TEST}88` : 'none'}} />
          ))}
        </div>
      )}
      {/* A pill shows only while it travels: the box it reaches glows, or the line band steps on, as it arrives. */}
      {leaves.map((from, i) =>
        from !== undefined && within(from, M_FLY) ? (
          <Pill key={`spend${i}`} x={mix(M.x + M.w + 20, MH.x + 120, t(from, from + M_FLY))} y={mix(lineMid(M.y, M.line, 4), MH.y + MH.h - 26, t(from, from + M_FLY))} text="Spend(3)" color={ACCENT} />
        ) : null,
      )}
      {backs.map((from, i) =>
        from !== undefined && within(from, M_BACK) ? (
          <Pill key={`back${i}`} x={mix(MH.x + 120, M.x + M.w + 20, t(from, from + M_BACK))} y={mix(MH.y + MH.h - 26, lineMid(M.y, M.line, 4), t(from, from + M_BACK))} text="None" color={TEST} />
        ) : null,
      )}
      {run && drop !== undefined && (
        <Label x={M.x + 330} y={M.y + PAD * 2 + RESEARCH.length * M.line + 16} size={18} bold color={FAIL} opacity={fadeAt(frame, s + drop)}>
          k dropped: never runs
        </Label>
      )}
      <div style={{position: 'absolute', left: 60, top: ROWS_Y, width: 1160, height: 236, boxSizing: 'border-box', padding: '10px 18px', background: '#0b0d13', borderRadius: 12, opacity: fadeAt(frame, MECH_INTRO.table.from, 0.4)}}>
        <div style={{fontFamily: SANS, fontSize: 16, lineHeight: '22px', color: MUTED}}>what research(["tides", "moons"]) returns under each handler</div>
        {RUNS.map((r, i) => (
          <div key={r.name} style={{display: 'flex', height: 46, alignItems: 'center', opacity: fadeAt(frame, rowAt(r))}}>
            <div style={{width: 190, fontFamily: MONO, fontSize: 20, fontWeight: 700, color: i === current ? LOOK : TEST}}>{r.name}</div>
            <div style={{fontFamily: MONO, fontSize: 20, color: TEXT, whiteSpace: 'nowrap'}}>{r.result}</div>
            <div style={{marginLeft: 24, fontFamily: SANS, fontSize: 17, color: MUTED}}>{r.steps.drop ? 'k dropped' : "each Spend's k resumed once"}</div>
          </div>
        ))}
      </div>
      {/* Over the table: the result the handler returned goes to its row. */}
      {run && drop !== undefined && within(drop + 0.3, M_FLY) && (
        <Pill x={mix(MH.x + 200, 420, t(drop + 0.3, drop + 0.3 + M_FLY))} y={mix(MH.y + MH.h - 26, rowY(current), t(drop + 0.3, drop + 0.3 + M_FLY))} text={run.result} color={TEST} />
      )}
    </AbsoluteFill>
  );
};

// Scene 3 — handlers stack: trace inside budget(5) (measure/research.py, the last run).

const STACK_LINES: Lines = [[0, 'Handlers stack. trace takes every effect first, notes it, and passes it on to budget.']];
const ST = {cx: 470, cy: 372, w: 470, size: 16, line: 25, margin: 52};
const stH = PAD * 2 + RESEARCH.length * ST.line;
const stCode = {x: ST.cx - ST.w / 2, y: ST.cy - stH / 2};
const stLayer = onion(ST.cx, ST.cy, ST.w, stH, ST.margin);
const ST_LAYERS = [
  {name: 'handler(trace)', handles: 'takes every effect'},
  {name: 'budget(5)', handles: 'takes Spend'},
];
// A pill's trip from one ring to the next; the ring that takes it glows as it arrives.
const ST_FLY = 0.6;
// Both rings; each Spend out to trace, which notes it, and passed on to budget; None back to research, which goes on to
// the second topic; budget drops k at the second Spend.
const STACK_STEPS = chain(0.2, [['rings', 0.4], ['out1', ST_FLY + M_GLOW], ['pass1', ST_FLY + M_GLOW], ['back1', ST_FLY + MOVE], ['out2', MOVE + ST_FLY + M_GLOW], ['pass2', ST_FLY + M_GLOW], ['drop', 0.5]]);
const STACK = f(Math.max(lastOf(STACK_STEPS), heardBy(0, 's1')) + HOLD);

const StackScene: React.FC = () => {
  const frame = useSceneFrame();
  const {rings, out1, pass1, back1, out2, pass2, drop} = STACK_STEPS;
  const startX = ST.cx + ST.w / 2;
  const edge = (i: number) => ST.cx + stLayer(i).w / 2;
  const y = lineMid(stCode.y, ST.line, 4);
  const outs = [out1.from, out2.from + MOVE];
  const passes = [pass1.from, pass2.from];
  const seenAt = outs.map((at) => at + ST_FLY);
  const seen = seenAt.filter((at) => frame >= f(at)).length;
  const left = frame >= f(pass1.from + ST_FLY) ? 2 : 5;
  const over = frame >= f(pass2.from + ST_FLY);
  const dropped = ramp(frame, drop.from, drop.to);
  const working = [seenAt.some((at) => during(frame, at, at + M_GLOW)), passes.some((at) => during(frame, at + ST_FLY, at + ST_FLY + M_GLOW))];
  const band = bandAt(frame, [[rings.from, 4], [back1.from + ST_FLY, 5], [out2.from, 4]]);
  return (
    <AbsoluteFill>
      <Headlines lines={STACK_LINES} />
      {ST_LAYERS.map((layer, i) => {
        const r = stLayer(i);
        return (
          <div
            key={layer.name}
            style={{
              position: 'absolute', left: r.x, top: r.y, width: r.w, height: r.h, boxSizing: 'border-box', borderRadius: 22,
              border: `3px solid ${TEST}`, ...glow(working[i], TEST), opacity: fadeAt(frame, rings.from, 0.4),
            }}
          >
            <div style={{position: 'absolute', left: 16, top: 12, fontSize: 17, fontWeight: 700, color: working[i] ? LOOK : TEST, whiteSpace: 'nowrap'}}>
              <span style={{fontFamily: MONO}}>{layer.name}</span>
              <span style={{fontFamily: SANS, fontWeight: 400, marginLeft: 14, color: MUTED}}>{layer.handles}</span>
            </div>
          </div>
        );
      })}
      <CodeBlock lines={RESEARCH} x={stCode.x} y={stCode.y} w={ST.w} size={ST.size} line={ST.line} band={band} dim={(i) => (i >= 5 ? 1 - 0.7 * dropped : 1)} />
      {/* A pill shows only while it travels. */}
      {outs.map((at) => during(frame, at, at + ST_FLY) && <Pill key={`out${at}`} x={mix(startX + 10, edge(0) - 10, ramp(frame, at, at + ST_FLY))} y={y} text="Spend(3)" color={ACCENT} />)}
      {passes.map((at) => during(frame, at, at + ST_FLY) && <Pill key={`pass${at}`} x={mix(edge(0) + 6, edge(1) - 10, ramp(frame, at, at + ST_FLY))} y={y} text="Pass: Spend(3)" color={ACCENT} />)}
      {during(frame, back1.from, back1.from + ST_FLY) && <Pill x={mix(edge(1) - 10, startX + 10, ramp(frame, back1.from, back1.from + ST_FLY))} y={y} text="None" color={TEST} />}
      <Box x={960} y={140} w={260} h={250} color={ACCENT} opacity={fadeAt(frame, rings.from, 0.4)}>
        <div style={{position: 'absolute', left: 16, top: 12, fontFamily: MONO, fontSize: 18, fontWeight: 700, color: ACCENT}}>seen</div>
        {Array.from({length: seen}, (_, i) => (
          <div key={i} style={{position: 'absolute', left: 16, top: 52 + i * 34, fontFamily: MONO, fontSize: 18, color: TEXT, opacity: fadeAt(frame, seenAt[i])}}>
            Spend(cost=3)
          </div>
        ))}
        <div style={{position: 'absolute', left: 16, top: 150, fontFamily: SANS, fontSize: 18, color: MUTED}}>
          budget: left <span style={{fontFamily: MONO, fontWeight: 700, color: over ? FAIL : TEXT}}>{left}</span>
        </div>
        <div style={{position: 'absolute', left: 16, top: 184, fontFamily: MONO, fontSize: 17, fontWeight: 700, color: FAIL, opacity: fadeAt(frame, pass2.from + ST_FLY)}}>cost 3 &gt; left 2</div>
      </Box>
      <div style={{position: 'absolute', left: 0, right: 0, top: 640, textAlign: 'center', fontFamily: MONO, fontSize: 19, color: TEXT, opacity: fadeAt(frame, rings.from, 0.4)}}>
        run(budget(5)(handler(trace)(research(["tides", "moons"]))))
        <span style={{color: ACCENT, fontWeight: 700, opacity: fadeAt(frame, drop.from, drop.to - drop.from)}}>{"  → 'over budget'"}</span>
      </div>
    </AbsoluteFill>
  );
};

// Scene 4 — one mechanism: crawl's effects, each to the handler that gives it its meaning (measure/one_mechanism.py).

const CRAWL = [
  '@do',
  'def size(url):',
  '    page = yield Fetch(url)',
  '    if not page:',
  '        raise ValueError(f"empty page: {url}")',
  '    yield Put("fetched", (yield Get("fetched")) + 1)',
  '    yield Tell(f"fetched {url}")',
  '    return len(page)',
  '@do',
  'def crawl(urls):',
  '    tasks = []',
  '    for url in urls:',
  '        tasks.append((yield Spawn(Try(size(url)))))',
  '    return (yield Gather(*tasks))',
  '@do',
  'def report(urls):',
  '    sizes, log = yield Listen(crawl(urls))',
  '    return sizes, log, (yield Get("fetched"))',
];
const O = {x: 40, y: 98, w: 640, size: 15, line: 21};
// The handlers of the page's program, innermost first; what each gives a meaning to, and the kind of effect it is.
const STACK_ROWS = [
  {name: 'local_pages', takes: 'Fetch', what: 'I/O', live: 'live_pages'},
  {name: 'try_handler', takes: 'Try', what: 'exceptions'},
  {name: 'listen_handler', takes: 'Listen', what: 'observation'},
  {name: 'writer', takes: 'Tell', what: 'log lines'},
  {name: 'state({"fetched": 0})', takes: 'Get · Put', what: 'state'},
  {name: 'scheduled', takes: 'Spawn · Gather', what: 'concurrency'},
];
const RW = {x: 760, y: 98, w: 460, h: 58, gap: 6};
const rowTop = (i: number) => RW.y + i * (RW.h + RW.gap);
const rowMid = (i: number) => rowTop(i) + RW.h / 2;
// The pace of the scene: the handler rows come one by one; each effect's trip out to its row is one motion, and the next
// trip leaves TRIP_PAUSE s after it has arrived (the row stays lit in between). The empty page's raise is a motion of
// its own before its ValueError travels.
const FLY = 0.55;
const TRIP_PAUSE = 1.0;
const RAISE = 0.4;
const ROWS_AT = STACK_ROWS.map((_, i) => 0.8 + i * (0.4 + PAUSE));
const O2 = Math.max(ROWS_AT[ROWS_AT.length - 1] + 0.4, heardBy(0, 'o1')) + PAUSE;
const O3 = heardBy(O2, 'o2') + PAUSE;
// The effects in the order the run yielded them (one_mechanism.log): the code line, the handler row, and the second
// it leaves.
type Trip = {text: string; line: number; row: number; at: number; color?: string; raised?: boolean};
const TRIP_DEFS: Omit<Trip, 'at'>[] = [
  {text: 'Listen', line: 16, row: 2},
  {text: 'Spawn', line: 12, row: 5},
  {text: 'Try', line: 12, row: 1},
  {text: "Fetch('…a.example')", line: 2, row: 0},
  {text: "Get('fetched')", line: 5, row: 4},
  {text: "Put('fetched', 1)", line: 5, row: 4},
  {text: 'Tell', line: 6, row: 3},
  {text: 'Spawn', line: 12, row: 5},
  {text: 'Try', line: 12, row: 1},
  {text: "Fetch('…b.example')", line: 2, row: 0},
  {text: 'ValueError', line: 4, row: 1, color: FAIL, raised: true},
  {text: 'Gather', line: 13, row: 5},
  {text: "Get('fetched')", line: 17, row: 4},
];
const TRIPS: Trip[] = TRIP_DEFS.reduce<Trip[]>((trips, def) => {
  const before = trips[trips.length - 1];
  const free = before ? before.at + FLY + TRIP_PAUSE : O3 + 0.3;
  return [...trips, {...def, at: def.raised ? free + RAISE + TRIP_PAUSE : free}];
}, []);
const arrives = (i: number) => TRIPS[i].at + FLY;
const RAISED = TRIPS.findIndex((trip) => trip.raised);
const RAISE_AT = TRIPS[RAISED].at - TRIP_PAUSE - RAISE;
const O4 = Math.max(RAISE_AT - 0.3, heardBy(O3, 'o3') + PAUSE);
const RESULTS_AT = arrives(TRIPS.length - 1) + PAUSE;
const O5 = Math.max(RESULTS_AT + 0.4, heardBy(O4, 'o4')) + PAUSE;
const ONE_LINES: Lines = [
  [0, 'State, exceptions, concurrency, observation, I/O: effects of one kind.'],
  [O2, 'crawl fetches pages in parallel tasks, counts them, and logs each fetch.'],
  [O3, 'Each effect travels out to the handler that gives it its meaning.'],
  [O4, 'The empty page raises in its task. Try turns the error into a value.'],
  [O5, 'The code names its effects. Where it runs decides the handlers.'],
];
const ONE = f(Math.max(O5 + 0.4 + 0.3, heardBy(O5, 'o5')) + HOLD);
const RESULTS = {x: 40, y: 542, w: 1180, h: 142};

// The handler rows on the right of scenes 4 and 5. notes: what each row shows next to its name at this frame.
const HandlerRows: React.FC<{frame: number; working: (row: number) => boolean; notes: (row: number) => React.ReactNode; first: React.ReactNode; firstDashed?: boolean; at: (row: number) => number}> = ({
  frame, working, notes, first, firstDashed = false, at,
}) => (
  <>
    <Label x={RW.x} y={RW.y - 26} size={16} opacity={fadeAt(frame, at(0))}>handlers, innermost first</Label>
    {STACK_ROWS.map((row, i) => (
      <Box key={row.name} x={RW.x} y={rowTop(i)} w={RW.w} h={RW.h} color={TEST} on={working(i)} dashed={i === 0 && firstDashed} opacity={fadeAt(frame, at(i), 0.4)}>
        <div style={{position: 'absolute', left: 14, top: 6, fontFamily: MONO, fontSize: 17, fontWeight: 700, color: working(i) ? LOOK : TEST, whiteSpace: 'nowrap'}}>{i === 0 ? first : row.name}</div>
        <div style={{position: 'absolute', left: 14, top: 31, fontFamily: MONO, fontSize: 14, color: MUTED, whiteSpace: 'nowrap'}}>{i === 0 && firstDashed ? '' : row.takes}</div>
        <div style={{position: 'absolute', right: 14, top: 6, fontFamily: SANS, fontSize: 17, fontWeight: 700, color: ACCENT, whiteSpace: 'nowrap'}}>{i === 0 && firstDashed ? '' : row.what}</div>
        <div style={{position: 'absolute', right: 14, top: 31, fontFamily: MONO, fontSize: 14, color: TEXT, whiteSpace: 'nowrap'}}>{notes(i)}</div>
      </Box>
    ))}
  </>
);

const OneScene: React.FC = () => {
  const frame = useSceneFrame();
  const fromX = O.x + O.w + 8;
  const past = (seconds: number) => frame >= f(seconds);
  // A row is lit from the arrival of a trip to it until the next trip leaves.
  const working = (row: number) => TRIPS.some((trip) => trip.row === row && during(frame, trip.at + FLY, trip.at + FLY + TRIP_PAUSE));
  const lineOf = TRIPS.reduce((found: number | null, trip) => (past(trip.at - 0.1) ? trip.line : found), null);
  // Each task runs from its Try, the first effect it yields, until the next Spawn or the Gather.
  const task = during(frame, TRIPS[2].at, TRIPS[7].at) ? 'task a' : during(frame, TRIPS[8].at, TRIPS[11].at) ? 'task b' : '';
  const notes = (row: number): React.ReactNode =>
    row === 0
      ? past(arrives(9)) ? "b → ''" : past(arrives(3)) ? "a → '<p>hello</p>'" : ''
      : row === 1
        ? past(arrives(RAISED)) ? 'b → Err(ValueError)' : ''
        : row === 3
          ? past(arrives(6)) ? "'fetched https://a.example'" : ''
          : row === 4
            ? `fetched = ${past(arrives(5)) ? 1 : 0}`
            : row === 5
              ? past(arrives(11)) ? 'gathered a, b' : past(arrives(7)) ? 'tasks a, b' : past(arrives(1)) ? 'task a' : ''
              : '';
  const named = fadeAt(frame, O5 + 0.4);
  return (
    <AbsoluteFill>
      <Headlines lines={ONE_LINES} />
      <CodeBlock lines={CRAWL} x={O.x} y={O.y} w={O.w} size={O.size} line={O.line} band={past(TRIPS[0].at - 0.1) && !past(RESULTS_AT) ? lineOf : null} opacity={fadeAt(frame, 0.3)} />
      {during(frame, RAISE_AT, TRIPS[RAISED + 1].at) && (
        <div
          style={{
            position: 'absolute', left: O.x + 6, top: O.y + PAD + 4 * O.line - 2, width: O.w - 12, height: O.line + 4, boxSizing: 'border-box', borderRadius: 6,
            border: `3px solid ${FAIL}`, boxShadow: `0 0 14px ${FAIL}`, opacity: between(frame, RAISE_AT, TRIPS[RAISED + 1].at - 0.3, RAISE),
          }}
        />
      )}
      <Label x={O.x + 230} y={lineMid(O.y, O.line, 1) - 11} size={17} bold color={LOOK} opacity={task ? 1 : 0}>
        {task}
      </Label>
      <div style={{position: 'absolute', left: O.x - 4, top: O.y - 4, width: O.w + 8, height: PAD * 2 + CRAWL.length * O.line + 8, boxSizing: 'border-box', borderRadius: 16, border: `3px solid ${ACCENT}`, boxShadow: `0 0 18px ${ACCENT}88`, opacity: named}} />
      <HandlerRows frame={frame} working={working} notes={notes} first={STACK_ROWS[0].name} at={(i) => ROWS_AT[i]} />
      {ROWS_AT.map((at, i) => (
        <div key={i} style={{position: 'absolute', left: RW.x - 10, top: rowTop(i) + 4, width: RW.w + 20, height: RW.h - 8, borderRadius: 14, border: `3px solid ${LOOK}`, opacity: between(frame, at, O2 - 0.3) * 0.55}} />
      ))}
      {/* A pill shows only while it travels; the row it reaches lights as it arrives. */}
      {TRIPS.map((trip) =>
        during(frame, trip.at, trip.at + FLY) ? (
          <Pill key={`${trip.text}${trip.at}`} left x={mix(fromX, RW.x + 10, ramp(frame, trip.at, trip.at + FLY))} y={mix(lineMid(O.y, O.line, trip.line), rowMid(trip.row), ramp(frame, trip.at, trip.at + FLY))} text={trip.text} color={trip.color ?? ACCENT} />
        ) : null,
      )}
      <div style={{position: 'absolute', left: RESULTS.x, top: RESULTS.y, width: RESULTS.w, height: RESULTS.h, boxSizing: 'border-box', padding: '12px 20px', background: '#0b0d13', borderRadius: 12, opacity: fadeAt(frame, RESULTS_AT, 0.4)}}>
        <div style={{fontFamily: SANS, fontSize: 16, color: MUTED, marginBottom: 8}}>sizes, log, fetched = run(scheduled(state(writer(listen_handler(try_handler(local_pages(report(urls))))))))</div>
        <div style={{fontFamily: MONO, fontSize: 19, lineHeight: '32px', color: TEXT, whiteSpace: 'nowrap'}}>
          <div>
            <span style={{color: MUTED, display: 'inline-block', width: 110}}>sizes</span>[<span style={{color: TEST}}>Ok(12)</span>, <span style={{color: FAIL}}>Err(ValueError('empty page: https://b.example'))</span>]
          </div>
          <div>
            <span style={{color: MUTED, display: 'inline-block', width: 110}}>log</span>[Tell('fetched https://a.example')]
          </div>
          <div>
            <span style={{color: MUTED, display: 'inline-block', width: 110}}>fetched</span>1
          </div>
        </div>
      </div>
      <Label x={O.x + 10} y={O.y + PAD * 2 + CRAWL.length * O.line + 8} size={18} bold color={ACCENT} opacity={named}>
        names the effects it needs, and nothing else
      </Label>
      <Label x={RW.x} y={rowTop(6) + 6} size={18} bold color={TEST} opacity={named}>
        chosen where the program runs
      </Label>
    </AbsoluteFill>
  );
};

// Scene 5 — swapping I/O is one use (measure/one_mechanism.py, the run without local_pages).

const SWAP_LINES: Lines = [
  [0, 'Swapping I/O is one use: a network handler answers Fetch the same way.'],
  [7.5, 'Leave the Fetch handler out, and each task gets an UnhandledEffect.'],
  [15.5, 'It is one use, not what doeff is. research does no I/O at all.'],
];
const SWAP = f(23);
const LIVE = ['@do', 'def live_pages(effect: Fetch, k):', '    with urllib.request.urlopen(effect.url) as response:', '        page = response.read().decode()', '    return (yield Resume(k, page))'];
const USES = [
  {name: 'budgets', color: TEST},
  {name: 'early exit', color: TEST},
  {name: 'errors', color: TEST},
  {name: 'the scheduler', color: TEST},
  {name: 'observation', color: TEST},
  {name: 'state', color: TEST},
  {name: 'swapping I/O', color: PROD},
];

const SwapScene: React.FC = () => {
  const frame = useSceneFrame();
  const live = ramp(frame, 0.6, 1.4);
  const gone = ramp(frame, 7.8, 8.4);
  const away = 1 - fadeAt(frame, 15.4, 0.5);
  const first = gone > 0.5 ? <span style={{color: MUTED}}>no handler for Fetch</span> : live > 0.5 ? STACK_ROWS[0].live : STACK_ROWS[0].name;
  const fetchTrips = [8.6, 9.6];
  return (
    <AbsoluteFill>
      <Headlines lines={SWAP_LINES} />
      <div style={{position: 'absolute', inset: 0, opacity: away}}>
        <CodeBlock lines={CRAWL} x={O.x} y={O.y} w={O.w} size={O.size} line={O.line} band={null} />
        <div style={{position: 'absolute', left: O.x - 4, top: O.y - 4, width: O.w + 8, height: PAD * 2 + CRAWL.length * O.line + 8, boxSizing: 'border-box', borderRadius: 16, border: `3px solid ${TEST}`, opacity: between(frame, 2.4, 7.4)}} />
        <Label x={O.x + 10} y={O.y + PAD * 2 + CRAWL.length * O.line + 8} size={18} bold color={TEST} opacity={between(frame, 2.4, 7.4)}>
          size, crawl and report: unchanged, nothing patched
        </Label>
        <HandlerRows frame={frame} working={(row) => row === 0 && during(frame, 0.6, 2.0)} notes={() => ''} first={first} firstDashed={gone > 0.5} at={() => -1} />
        <CodeBlock lines={LIVE} x={RW.x - 40} y={rowTop(6) + 4} w={RW.w + 40} size={13} line={20} band={null} opacity={between(frame, 1.2, 7.6)} />
        {fetchTrips.map((at, i) =>
          during(frame, at, at + 1.4) ? (
            <Pill key={at} x={mix(O.x + O.w + 58, 1236, ramp(frame, at, at + 1.1))} y={mix(lineMid(O.y, O.line, 2), rowMid(0) + i * 26, ramp(frame, at, at + 0.4))} text={`Fetch('…${i === 0 ? 'a' : 'b'}.example')`} color={ramp(frame, at + 0.6, at + 0.9) > 0.5 ? FAIL : ACCENT} opacity={1 - ramp(frame, at + 1.1, at + 1.4)} />
          ) : null,
        )}
        <div style={{position: 'absolute', left: RESULTS.x, top: RESULTS.y, width: RESULTS.w, height: RESULTS.h, boxSizing: 'border-box', padding: '12px 20px', background: '#0b0d13', borderRadius: 12, opacity: fadeAt(frame, 10.8)}}>
          <div style={{fontFamily: SANS, fontSize: 16, color: MUTED, marginBottom: 8}}>the same run without a Fetch handler: Try turns each UnhandledEffect into an Err</div>
          <div style={{fontFamily: MONO, fontSize: 17, lineHeight: '30px', color: TEXT, whiteSpace: 'nowrap'}}>
            <div>
              <span style={{color: MUTED, display: 'inline-block', width: 110}}>sizes</span>
              <span style={{color: FAIL}}>Err(UnhandledEffect)</span>: no handler found for effect: Fetch (Fetch(url='https://a.example'))
            </div>
            <div>
              <span style={{color: MUTED, display: 'inline-block', width: 110}} />
              <span style={{color: FAIL}}>Err(UnhandledEffect)</span>: no handler found for effect: Fetch (Fetch(url='https://b.example'))
            </div>
            <div>
              <span style={{color: MUTED, display: 'inline-block', width: 110}}>log</span>[]<span style={{color: MUTED, display: 'inline-block', width: 110, marginLeft: 60}}>fetched</span>0
            </div>
          </div>
        </div>
      </div>
      <div style={{position: 'absolute', inset: 0, opacity: fadeAt(frame, 15.6, 0.5)}}>
        <Box x={440} y={200} w={400} h={110} color={TEST} on={during(frame, 16.0, 17.0)}>
          <div style={{position: 'absolute', left: 0, right: 0, top: 14, textAlign: 'center', fontFamily: SANS, fontSize: 26, fontWeight: 700, color: TEST}}>a handler</div>
          <div style={{position: 'absolute', left: 0, right: 0, top: 58, textAlign: 'center', fontFamily: MONO, fontSize: 21, color: ACCENT}}>(effect, k)</div>
        </Box>
        <Label x={640} y={340} size={21} color={MUTED} center>
          what handlers do: most of it has no I/O in it
        </Label>
        {USES.map((use, i) => {
          const row = i < 4 ? 0 : 1;
          const inRow = row === 0 ? 4 : 3;
          const k = row === 0 ? i : i - 4;
          return (
            <Chip key={use.name} x={640 - (inRow * 250 - 20) / 2 + k * 250} y={400 + row * 76} w={230} color={use.color} opacity={fadeAt(frame, 16.2 + i * 0.35)} on={use.color === PROD && during(frame, 19.2, 21.0)}>
              {use.name}
            </Chip>
          );
        })}
        <Label x={640} y={580} size={20} color={TEXT} center opacity={fadeAt(frame, 19.0)}>
          <span style={{fontFamily: MONO}}>research</span> does no I/O: its handlers decide whether it goes on, stops or fails
        </Label>
      </div>
    </AbsoluteFill>
  );
};

// Scene 6 — the limits: one-shot continuations (measure/one_shot.py), no effect types, the cost of an effect
// (measure/effect_cost.py).

const LIMIT_LINES: Lines = [
  [0, 'The limits. Continuations are one-shot: a handler resumes each k at most once.'],
  [5.9, 'Resume k for tea, then for coffee: the second Resume raises a RuntimeError.'],
  [12.5, 'What stands in for it: run the whole program again, once per choice. A program is a value.'],
  [19.4, 'Effects are not in the types. A missing handler shows only when the program runs.'],
  [25.2, 'And an effect costs a few microseconds: nothing next to I/O, visible in a tight loop.'],
];
const LIMITS = f(32.5);
const ORDER = ['@do', 'def order():', '    drink = yield Choose(("tea", "coffee"))', '    return f"{drink} please"'];
const EVERY = ['@do(non_tail=True)', 'def every_option(effect: Choose, k):', '    answers = []', '    for option in effect.options:', '        answers.append((yield Resume(k, option)))', '    return answers'];
const L1 = {x: 60, y: 112, w: 580, size: 17, line: 28};
const L2 = {x: 60, y: 266, w: 580, size: 17, line: 28};
const KBOX = {x: 700, y: 112, w: 520, h: 136};
const CHOICE = {x: 700, y: 266, w: 520, h: 196};
// What one effect and one plain call cost, in microseconds: the lowest and highest of 15 runs (effect_cost.log).
const COST = {effect: [2.4, 3.77], call: [0.073, 0.195]};
const PX = 85;

const LimitScene: React.FC = () => {
  const frame = useSceneFrame();
  const yieldY = lineMid(L1.y, L1.line, 2);
  const resumeY = lineMid(L2.y, L2.line, 4);
  const kState = frame >= f(9.4) ? 'already consumed' : frame >= f(7.0) ? "resumed with 'tea'" : frame >= f(1.6) ? 'paused at the yield' : '';
  const firstAway = 1 - fadeAt(frame, 19.2, 0.5);
  const orderBand = bandAt(frame, [[1.0, 2], [7.0, 3]]);
  const everyBand = bandAt(frame, [[1.6, 4]]);
  const runs = [
    {label: 'choosing(0)', answer: "'tea please'", at: 13.6},
    {label: 'choosing(1)', answer: "'coffee please'", at: 15.2},
  ];
  return (
    <AbsoluteFill>
      <Headlines lines={LIMIT_LINES} />
      <div style={{position: 'absolute', inset: 0, opacity: firstAway}}>
        <CodeBlock lines={ORDER} {...L1} band={orderBand} opacity={fadeAt(frame, 0.2)} />
        <CodeBlock lines={EVERY} {...L2} band={everyBand} opacity={fadeAt(frame, 0.6)} />
        <Box x={KBOX.x} y={KBOX.y} w={KBOX.w} h={KBOX.h} color={frame >= f(9.4) ? FAIL : ACCENT} dashed on={during(frame, 9.4, 10.4)} opacity={fadeAt(frame, 1.4)}>
          <div style={{position: 'absolute', left: 20, top: 14, fontFamily: MONO, fontSize: 22, fontWeight: 700, color: ACCENT}}>k = the rest of order()</div>
          <div style={{position: 'absolute', left: 20, top: 56, fontFamily: SANS, fontSize: 21, color: frame >= f(9.4) ? FAIL : TEXT}}>{kState}</div>
          <div style={{position: 'absolute', left: 20, top: 92, fontFamily: SANS, fontSize: 17, color: MUTED}}>resumed at most once</div>
        </Box>
        {during(frame, 1.0, 2.0) && <Pill x={mix(L1.x + L1.w + 20, L2.x + L2.w + 20, ramp(frame, 1.0, 1.6))} y={mix(yieldY, resumeY, ramp(frame, 1.0, 1.6))} text={'Choose(("tea", "coffee"))'} color={ACCENT} opacity={1 - ramp(frame, 1.7, 2.0)} />}
        {during(frame, 6.3, 7.2) && <Pill x={mix(L2.x + L2.w + 20, L1.x + L1.w + 20, ramp(frame, 6.3, 6.9))} y={mix(resumeY, yieldY, ramp(frame, 6.3, 6.9))} text="'tea'" color={TEST} opacity={1 - ramp(frame, 6.9, 7.2)} />}
        {during(frame, 7.6, 8.5) && <Pill x={mix(L1.x + L1.w + 20, L2.x + L2.w + 20, ramp(frame, 7.6, 8.2))} y={mix(lineMid(L1.y, L1.line, 3), resumeY, ramp(frame, 7.6, 8.2))} text="'tea please'" color={TEST} opacity={1 - ramp(frame, 8.2, 8.5)} />}
        {during(frame, 8.8, 9.9) && <Pill x={mix(L2.x + L2.w + 20, KBOX.x + 60, ramp(frame, 8.8, 9.4))} y={mix(resumeY, KBOX.y + KBOX.h - 10, ramp(frame, 8.8, 9.4))} text="Resume(k, 'coffee')" color={frame >= f(9.4) ? FAIL : TEST} opacity={1 - ramp(frame, 9.6, 9.9)} />}
        <div style={{position: 'absolute', left: 60, top: 490, width: 1160, fontFamily: MONO, fontSize: 18, fontWeight: 700, color: FAIL, opacity: fadeAt(frame, 9.8)}}>
          raises RuntimeError: … continuation already consumed (one-shot violation)
        </div>
        <Box x={CHOICE.x} y={CHOICE.y} w={CHOICE.w} h={CHOICE.h} color={TEST} opacity={fadeAt(frame, 12.6)}>
          <div style={{position: 'absolute', left: 20, top: 12, fontFamily: SANS, fontSize: 20, fontWeight: 700, color: TEST}}>one run per choice</div>
          <div style={{position: 'absolute', left: 20, top: 44, fontFamily: MONO, fontSize: 16, lineHeight: '22px', color: TEXT, whiteSpace: 'pre'}}>
            <div>[run(choosing(index)(order()))</div>
            <div> for index in range(2)]</div>
          </div>
          {runs.map((r, i) => (
            <div key={r.label} style={{position: 'absolute', left: 20, top: 102 + i * 44, height: 36, width: 480, opacity: fadeAt(frame, r.at - 0.4)}}>
              <span style={{fontFamily: MONO, fontSize: 17, color: TEST}}>{r.label}</span>
              <div style={{position: 'absolute', left: 140, top: 9, width: 160, height: 12, borderRadius: 6, background: '#2a2e40'}} />
              <div style={{position: 'absolute', left: 140, top: 9, width: 160 * ramp(frame, r.at, r.at + 1.0), height: 12, borderRadius: 6, background: ACCENT, boxShadow: `0 0 8px ${ACCENT}`}} />
              <span style={{position: 'absolute', left: 316, top: 0, fontFamily: MONO, fontSize: 17, color: TEXT, opacity: fadeAt(frame, r.at + 1.0)}}>{r.answer}</span>
            </div>
          ))}
        </Box>
        <div style={{position: 'absolute', left: 60, top: 560, width: 1160, textAlign: 'center', fontFamily: MONO, fontSize: 22, fontWeight: 700, color: ACCENT, opacity: fadeAt(frame, 16.8)}}>
          → ['tea please', 'coffee please']
        </div>
        <Label x={640} y={604} size={19} color={MUTED} center opacity={fadeAt(frame, 17.2)}>
          each run repeats the work before the choice
        </Label>
      </div>
      <div style={{position: 'absolute', inset: 0, opacity: fadeAt(frame, 19.5, 0.5)}}>
        <Box x={60} y={140} w={560} h={300} color={ACCENT} on={during(frame, 19.6, 25.0)}>
          <div style={{position: 'absolute', left: 22, top: 16, fontFamily: SANS, fontSize: 24, fontWeight: 700, color: ACCENT}}>Effects are not in the types</div>
          <div style={{position: 'absolute', left: 22, top: 62, fontFamily: SANS, fontSize: 19, lineHeight: '28px', color: TEXT}}>
            <div>The type checker does not check that the</div>
            <div>installed handlers answer every effect.</div>
          </div>
          <div style={{position: 'absolute', left: 22, top: 150, fontFamily: SANS, fontSize: 19, color: MUTED}}>a missing handler, when the program runs:</div>
          <div style={{position: 'absolute', left: 22, top: 184, fontFamily: MONO, fontSize: 18, fontWeight: 700, color: FAIL, opacity: fadeAt(frame, 21.0)}}>UnhandledEffect</div>
          <div style={{position: 'absolute', left: 22, top: 220, fontFamily: MONO, fontSize: 15, color: MUTED, opacity: fadeAt(frame, 21.4)}}>no handler found for effect: Fetch</div>
        </Box>
        <Box x={660} y={140} w={560} h={300} color={TEST} on={during(frame, 25.4, 31.0)} opacity={fadeAt(frame, 25.2)}>
          <div style={{position: 'absolute', left: 22, top: 16, fontFamily: SANS, fontSize: 24, fontWeight: 700, color: TEST}}>What one effect costs</div>
          {[
            {name: 'one effect', range: COST.effect, text: '2.40–3.77 µs', color: ACCENT, y: 80},
            {name: 'one plain call', range: COST.call, text: '0.07–0.20 µs', color: TEST, y: 160},
          ].map((bar) => (
            <div key={bar.name} style={{position: 'absolute', left: 22, top: bar.y, width: 516}}>
              <div style={{fontFamily: SANS, fontSize: 18, color: TEXT}}>{bar.name}</div>
              <div style={{position: 'absolute', left: 0, top: 32, width: Math.max(4, bar.range[1] * PX * ramp(frame, 25.6, 26.6)), height: 22, borderRadius: 6, background: '#2a2e40'}} />
              <div style={{position: 'absolute', left: 0, top: 32, width: Math.max(4, bar.range[0] * PX * ramp(frame, 25.6, 26.6)), height: 22, borderRadius: 6, background: bar.color, boxShadow: `0 0 8px ${bar.color}88`}} />
              <div style={{position: 'absolute', left: Math.max(40, bar.range[1] * PX) + 16, top: 30, fontFamily: MONO, fontSize: 19, fontWeight: 700, color: bar.color, whiteSpace: 'nowrap', opacity: fadeAt(frame, 26.4)}}>{bar.text}</div>
            </div>
          ))}
          <div style={{position: 'absolute', left: 22, top: 252, fontFamily: SANS, fontSize: 15, color: MUTED}}>CPU time, 100,000 effects or calls a run, 15 runs</div>
        </Box>
        <Label x={640} y={480} size={21} color={TEXT} center opacity={fadeAt(frame, 27.6)}>
          next to a network call it does not show; in a tight numeric loop it does
        </Label>
      </div>
    </AbsoluteFill>
  );
};

// Scene 7 — where doeff sits (the page's table, "Where doeff sits").

const WHERE_LINES: Lines = [
  [0, 'Next to other effect systems: in effect and effectful, a handler never gets the continuation.'],
  [7.1, 'OCaml 5 is the closest: one-shot continuations, and no effects in the types.'],
  [14.4, 'Koka resumes one continuation many times and checks effects in its types. doeff has neither.'],
];
const WHERE = f(21);
const COLUMNS = [
  {name: '', w: 250},
  {name: 'what a handler receives', w: 420},
  {name: 'resume more than once', w: 250},
  {name: 'effects in the types', w: 240},
];
type Row = {name: string; about: string; cells: [string, string, string]; at: number; on: [number, number]};
const TABLE: Row[] = [
  {name: 'effect 1.1.0', about: 'Python', cells: ['the intent', 'no continuation', 'no'], at: 0.8, on: [0.8, 7.0]},
  {name: 'effectful 0.4.0', about: 'Python', cells: ["the operation's arguments", 'no continuation', 'no'], at: 1.2, on: [0.8, 7.0]},
  {name: 'OCaml 5', about: '', cells: ['the effect and its continuation', 'no: linear', 'no'], at: 7.1, on: [7.1, 14.3]},
  {name: 'Koka', about: '', cells: ['the operation and resume', 'yes', 'yes'], at: 14.4, on: [14.4, 21]},
  {name: 'doeff 0.7.0', about: 'Python', cells: ['the effect and its continuation k', 'no: one-shot', 'no'], at: 0.3, on: [-1, -1]},
];
const TB = {x: 60, y: 120, head: 44, row: 76};
const colX = (i: number) => TB.x + COLUMNS.slice(0, i).reduce((sum, c) => sum + c.w, 0);

const WhereScene: React.FC = () => {
  const frame = useSceneFrame();
  return (
    <AbsoluteFill>
      <Headlines lines={WHERE_LINES} />
      {COLUMNS.map((c, i) => (
        <Label key={i} x={colX(i) + 16} y={TB.y + 10} size={17} bold color={MUTED} opacity={fadeAt(frame, 0.2)}>
          {c.name}
        </Label>
      ))}
      {TABLE.map((row, r) => {
        const y = TB.y + TB.head + r * TB.row + (r === TABLE.length - 1 ? 16 : 0);
        const on = during(frame, row.on[0], row.on[1]);
        const isDoeff = r === TABLE.length - 1;
        return (
          <div
            key={row.name}
            style={{
              position: 'absolute', left: TB.x, top: y, width: 1160, height: TB.row - 8, boxSizing: 'border-box', borderRadius: 12, background: PANEL,
              border: `3px solid ${isDoeff ? ACCENT : '#2a2e40'}`, ...(on ? glow(true, TEST) : isDoeff ? {boxShadow: `0 0 14px ${ACCENT}66`} : {}), opacity: fadeAt(frame, row.at, 0.4),
            }}
          >
            <div style={{position: 'absolute', left: 16, top: 10, fontFamily: MONO, fontSize: 20, fontWeight: 700, color: isDoeff ? ACCENT : on ? LOOK : TEXT, whiteSpace: 'nowrap'}}>{row.name}</div>
            <div style={{position: 'absolute', left: 16, top: 38, fontFamily: SANS, fontSize: 15, color: MUTED}}>{row.about}</div>
            {row.cells.map((cell, c) => (
              <div
                key={c}
                style={{
                  position: 'absolute', left: colX(c + 1) - TB.x + 16, top: 20, fontFamily: SANS, fontSize: 19, whiteSpace: 'nowrap',
                  color: cell === 'yes' ? (on ? LOOK : TEST) : cell.startsWith('no') ? MUTED : TEXT, fontWeight: cell === 'yes' ? 700 : 400,
                }}
              >
                {cell}
              </div>
            ))}
          </div>
        );
      })}
      <div style={{position: 'absolute', left: TB.x + 1160 - 8, top: TB.y + TB.head + 2 * TB.row + 20, width: 22, height: 2 * TB.row + 16, boxSizing: 'border-box', border: `3px solid ${LOOK}`, borderLeft: 'none', borderRadius: '0 12px 12px 0', opacity: between(frame, 8.0, 14.2)}} />
      <Label x={640} y={TB.y + TB.head + 5 * TB.row + 40} size={21} bold color={LOOK} center opacity={between(frame, 8.4, 14.2)}>
        OCaml 5 and doeff: the continuation, one-shot, no effect types
      </Label>
      <Label x={640} y={TB.y + TB.head + 5 * TB.row + 40} size={21} bold color={ACCENT} center opacity={fadeAt(frame, 16.4)}>
        doeff: algebraic effects with one-shot continuations, as in OCaml 5
      </Label>
    </AbsoluteFill>
  );
};

// The outro: how to describe doeff (the page's last section).

const OUTRO_LINES: Lines = [[0, 'doeff: algebraic effects for Python. Your code yields effects, and handlers decide what they mean.']];
const OUTRO = f(9.5);
const OUTRO_USES = ['budgets', 'errors', 'concurrency', 'observation', 'state', 'I/O'];

const OutroScene: React.FC = () => {
  const frame = useSceneFrame();
  return (
    <AbsoluteFill>
      <Headlines lines={OUTRO_LINES} />
      <div style={{position: 'absolute', left: 0, right: 0, top: 160 + mix(16, 0, ramp(frame, 0.2, 0.8)), textAlign: 'center', opacity: fadeAt(frame, 0.2, 0.5)}}>
        <div style={{fontFamily: SANS, fontSize: 64, fontWeight: 700, color: TEXT}}>doeff</div>
        <div style={{fontFamily: SANS, fontSize: 30, color: ACCENT, marginTop: 4}}>Algebraic effects for Python</div>
      </div>
      <Label x={640} y={372} size={24} color={TEXT} center opacity={fadeAt(frame, 1.0)}>
        Your code yields effects, and handlers decide what they mean.
      </Label>
      <Label x={640} y={446} size={18} color={MUTED} center opacity={fadeAt(frame, 2.4)}>
        its uses, named as uses
      </Label>
      {OUTRO_USES.map((use, i) => (
        <Chip key={use} x={640 - (OUTRO_USES.length * 190 - 14) / 2 + i * 190} y={480} w={176} color={use === 'I/O' ? PROD : TEST} opacity={fadeAt(frame, 2.6 + i * 0.25)}>
          {use}
        </Chip>
      ))}
      <div style={{position: 'absolute', left: 0, right: 0, top: 590, textAlign: 'center', fontFamily: MONO, fontSize: 24, color: ACCENT, opacity: fadeAt(frame, 4.4)}}>pip install doeff</div>
    </AbsoluteFill>
  );
};

// The scenes in order; Root.tsx registers WHAT_DOEFF_IS.length frames of WHAT_DOEFF_IS.Video.
export const WHAT_DOEFF_IS = narrate('what-doeff-is', DURATIONS, NARRATION, [
  {length: TITLE, starts: startsOf(TITLE_LINES), ids: ['t1', 't2', 't3'], node: <TitleScene />},
  {length: MECH, starts: startsOf(MECH_LINES), ids: ['m1', 'm2', 'm3', 'm4', 'm5'], node: <MechScene />},
  {length: STACK, starts: startsOf(STACK_LINES), ids: ['s1'], node: <StackScene />},
  {length: ONE, starts: startsOf(ONE_LINES), ids: ['o1', 'o2', 'o3', 'o4', 'o5'], node: <OneScene />},
  {length: SWAP, starts: startsOf(SWAP_LINES), ids: ['u1', 'u2', 'u3'], node: <SwapScene />},
  {length: LIMITS, starts: startsOf(LIMIT_LINES), ids: ['l1', 'l2', 'l3', 'l4', 'l5'], node: <LimitScene />},
  {length: WHERE, starts: startsOf(WHERE_LINES), ids: ['w1', 'w2', 'w3'], node: <WhereScene />},
  {length: OUTRO, starts: [0], ids: ['z1'], node: <OutroScene />},
]);
