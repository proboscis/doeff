// The video on the Effect Combinations page (docs/18-effect-combinations.md): what one effect does to the effects
// performed inside it. Six scenes and an outro, each with headlines a synthetic voice reads (narration.json):
//   1. The matrix: the outer effect on each row, the inner one on each column; each cell is a rule.
//   2. Try and Listen nested both ways around a failing program: the outer one decides what the caller gets.
//   3. Local and Try restore the environment; the store keeps every write.
//   4. WithObserve sees every effect in its body, spawned children included, and changes none of them.
//   5. Gather: one store, one task at a time; a read-modify-write across a suspension loses updates.
//   6. A failing child: Gather raises its error, the siblings are not cancelled and their writes stay.
// Every value on screen is printed by a program in measure/ (its output is in the .log next to it).
import React from 'react';
import {AbsoluteFill, Easing, interpolate} from 'remotion';
import {fadeAt, mix, ramp} from '../../kit/motion';
import {narrate} from '../../kit/narrated';
import {CodeBlock, glow, Headlines, lineMid, Lines, PAD, Pill, startsOf} from '../../kit/parts';
import {ACCENT, f, FAIL, LOOK, MONO, MUTED, PANEL, PROD, SANS, TEST, TEXT} from '../../kit/theme';
import {useSceneFrame} from '../../kit/warp';
import DURATIONS from './durations.json';

// A label in the page's sans font, placed at (x, y).
const Label: React.FC<{x: number; y: number; color?: string; size?: number; bold?: boolean; opacity?: number; children: React.ReactNode}> = ({
  x, y, color = MUTED, size = 20, bold = true, opacity = 1, children,
}) => (
  <div style={{position: 'absolute', left: x, top: y, fontFamily: SANS, fontSize: size, fontWeight: bold ? 700 : 400, color, whiteSpace: 'nowrap', opacity}}>
    {children}
  </div>
);

// A dark panel of output lines in the mono font.
const Terminal: React.FC<{x: number; y: number; w: number; lines: {text: string; color?: string}[]; size?: number; line?: number; opacity?: number; on?: boolean}> = ({
  x, y, w, lines, size = 17, line = 27, opacity = 1, on = false,
}) => (
  <div
    style={{
      position: 'absolute', left: x, top: y, width: w, boxSizing: 'border-box', padding: '10px 16px', background: '#0b0d13', borderRadius: 10,
      border: '2px solid #2a2e40', ...(on ? glow(true, '#2a2e40') : {}), fontFamily: MONO, fontSize: size, lineHeight: `${line}px`, color: TEXT, opacity,
    }}
  >
    {lines.map((l, i) => (
      <div key={i} style={{whiteSpace: 'pre', color: l.color ?? TEXT, minHeight: line}}>
        {l.text}
      </div>
    ))}
  </div>
);

// A tile showing the value of one name over time: [second, value] keyframes; it pulses when the value is set.
const Tile: React.FC<{x: number; y: number; w: number; title: string; name: string; keys: [number, string][]; color: string; opacity?: number; note?: React.ReactNode}> = ({
  x, y, w, title, name, keys, color, opacity = 1, note,
}) => {
  const frame = useSceneFrame();
  const i = keys.reduce((found, [at], j) => (frame >= f(at) ? j : found), 0);
  const pulse = frame >= f(keys[i][0]) && frame < f(keys[i][0] + 0.6) && keys[i][0] > 0;
  return (
    <div
      style={{
        position: 'absolute', left: x, top: y, width: w, height: 104, boxSizing: 'border-box', borderRadius: 16, background: PANEL,
        border: `3px solid ${color}`, ...glow(pulse, color), opacity,
      }}
    >
      <div style={{position: 'absolute', left: 20, top: 10, fontFamily: SANS, fontSize: 17, color: MUTED}}>{title}</div>
      <div style={{position: 'absolute', left: 20, top: 40, fontFamily: MONO, fontSize: 32, fontWeight: 700, color: TEXT, whiteSpace: 'nowrap'}}>
        <span style={{color: MUTED, fontWeight: 400}}>{name} = </span>
        <span style={{color: pulse ? LOOK : TEXT}}>{keys[i][1]}</span>
      </div>
      {note && <div style={{position: 'absolute', right: 18, top: 10, fontFamily: SANS, fontSize: 17, fontWeight: 700}}>{note}</div>}
    </div>
  );
};

// Scene 1 — the matrix: the outer effect on each row, the effect performed inside it on each column.

const MATRIX_LINES: Lines = [
  [0, 'Effect combinations: what one effect does to the effects inside it.'],
  [8.7, 'Rows: the outer effect. Columns: the effect performed inside it.'],
  [15, 'Each cell is a rule. Under Local, Ask is scoped; Put passes through.'],
];
const MATRIX = f(21.5);
// The matrix of docs/18-effect-combinations.md, as the page writes it.
const COLS = ['Ask', 'Get', 'Put', 'Tell', 'Try', 'Local', 'Listen', 'WithObserve', 'Gather'];
const ROWS: [string, string[]][] = [
  ['Local', ['Scoped', 'Propagates', 'Propagates', 'Propagates', 'Propagates', 'Scoped', 'Propagates', 'Propagates', 'Propagates']],
  ['Listen', ['-', '-', '-', 'Captured*', '-', '-', 'Nested', '-', 'All captured*']],
  ['Try', ['-', '-', 'Persists', 'Persists', 'Wrapped', 'Restores', '-', '-', 'First error']],
  ['WithObserve', ['Observed', 'Observed', 'Observed', 'Observed', 'Observed', 'Observed', 'Observed', 'Observed', 'Observed']],
  ['Gather', ['Inherit', 'Shared**', 'Shared**', 'Merged', 'Isolated', 'Inherit', 'Shared', 'Propagates', 'Nested']],
];
const GRID = {x: 50, y: 112, label: 150, w: 1180, head: 46, row: 60};
const cellW = (GRID.w - GRID.label) / COLS.length;

const MatrixScene: React.FC = () => {
  const frame = useSceneFrame();
  // The axes light up in turn: the rows (outer), then the columns (inner); then the two cells of the Local row.
  const rowsOn = frame >= f(9.4) && frame < f(11.8);
  const colsOn = frame >= f(11.8) && frame < f(14.6);
  const localOn = frame >= f(15.4);
  const cellOn = (r: number, c: number) => localOn && r === 0 && ((c === 0 && frame >= f(16.0)) || (c === 2 && frame >= f(17.6)));
  return (
    <AbsoluteFill>
      <Headlines lines={MATRIX_LINES} />
      <div style={{position: 'absolute', left: GRID.x + 8, top: GRID.y + 4, fontFamily: SANS, fontSize: 15, lineHeight: '19px', color: MUTED, opacity: fadeAt(frame, 0.4)}}>
        <span style={{color: rowsOn ? LOOK : MUTED}}>outer ↓</span>
        <br />
        <span style={{color: colsOn ? LOOK : MUTED}}>inner →</span>
      </div>
      {COLS.map((name, c) => (
        <div
          key={name}
          style={{
            position: 'absolute', left: GRID.x + GRID.label + c * cellW, top: GRID.y, width: cellW - 4, height: GRID.head - 6, boxSizing: 'border-box',
            borderRadius: 8, background: PANEL, border: `2px solid ${colsOn ? LOOK : '#2a2e40'}`, fontFamily: MONO, fontSize: name.length > 8 ? 14 : 17,
            fontWeight: 700, color: ACCENT, textAlign: 'center', lineHeight: `${GRID.head - 10}px`, opacity: fadeAt(frame, 0.5 + c * 0.06),
          }}
        >
          {name}
        </div>
      ))}
      {ROWS.map(([outer, cells], r) => {
        const y = GRID.y + GRID.head + r * GRID.row;
        const appear = 1.3 + r * 0.55;
        const dim = localOn && r !== 0 ? 0.35 : 1;
        return (
          <React.Fragment key={outer}>
            <div
              style={{
                position: 'absolute', left: GRID.x, top: y, width: GRID.label - 8, height: GRID.row - 6, boxSizing: 'border-box', borderRadius: 8,
                background: PANEL, border: `2px solid ${rowsOn || (localOn && r === 0) ? LOOK : '#2a2e40'}`, fontFamily: MONO, fontSize: 18, fontWeight: 700,
                color: ACCENT, lineHeight: `${GRID.row - 10}px`, paddingLeft: 12, opacity: fadeAt(frame, appear) * dim,
              }}
            >
              {outer}
            </div>
            {cells.map((text, c) => {
              const on = cellOn(r, c);
              return (
                <div
                  key={c}
                  style={{
                    position: 'absolute', left: GRID.x + GRID.label + c * cellW, top: y, width: cellW - 4, height: GRID.row - 6, boxSizing: 'border-box',
                    borderRadius: 8, background: '#0b0d13', border: `2px solid ${on ? LOOK : '#22263a'}`, boxShadow: on ? `0 0 18px ${LOOK}aa` : 'none',
                    fontFamily: SANS, fontSize: 15, fontWeight: on ? 700 : 400, color: text === '-' ? '#4a4f66' : on ? LOOK : TEXT, display: 'flex',
                    alignItems: 'center', justifyContent: 'center', textAlign: 'center', lineHeight: '18px',
                    opacity: fadeAt(frame, appear + 0.1 + c * 0.05) * (localOn && !on && r === 0 ? 0.7 : dim),
                  }}
                >
                  {text}
                </div>
              );
            })}
          </React.Fragment>
        );
      })}
      <Label x={GRID.x} y={GRID.y + GRID.head + ROWS.length * GRID.row + 8} size={15} bold={false} opacity={fadeAt(frame, 4.4) * (localOn ? 0 : 1)}>
        * on success only     ** a read-modify-write is safe only if the child does not suspend in between
      </Label>
      {/* The Local row read as a rule, with what measure/scopes.py printed for it. */}
      <Terminal
        x={GRID.x} y={GRID.y + GRID.head + ROWS.length * GRID.row + 10} w={GRID.w} size={18} line={30}
        lines={[
          {text: 'yield Local({"key": "inner"}, changes())', color: MUTED},
          {text: frame >= f(16.0) ? '  Ask("key") inside -> "inner"     after Local -> "outer"     (Scoped)' : '', color: ACCENT},
          {text: frame >= f(17.6) ? '  Put("counter", 1) inside         after Local -> counter = 1  (Propagates)' : '', color: PROD},
        ]}
        opacity={fadeAt(frame, 15.4)}
      />
    </AbsoluteFill>
  );
};

// Scene 2 — Try and Listen nested both ways: the outer one decides what the caller gets.

const NEST_LINES: Lines = [
  [0, 'The same two effects, nested the other way, give a different answer.'],
  [5.5, 'Try outside Listen: the error leaves Listen, and its logs are lost.'],
  [12, 'Listen outside Try: Try makes the error an Err; Listen keeps both logs.'],
  [20.3, 'The outer effect decides what the caller gets.'],
];
const NEST = f(25.5);
const FAILS = ['@do', 'def tells_then_fails():', '    yield Tell("loaded config")', '    yield Tell("parsing rows")', '    raise ValueError("bad row")'];
const LOGS = ["'loaded config'", "'parsing rows'"];

// One nesting: its outer and inner effect, and the design seconds its raise starts at.
type Nesting = {x: number; outer: 'Try' | 'Listen'; raise: number; result: {text: string; color?: string}[]};
const NESTINGS: Nesting[] = [
  // measure/nesting_order.py: Try(Listen(p)) -> Err(ValueError('bad row'))
  {x: 40, outer: 'Try', raise: 6.2, result: [{text: "Err(ValueError('bad row'))", color: FAIL}, {text: 'the two logs are gone', color: MUTED}]},
  // measure/nesting_order.py: Listen(Try(p)) -> (Err(ValueError('bad row')), ['loaded config', 'parsing rows'])
  {
    x: 670, outer: 'Listen', raise: 12.6,
    result: [{text: "(Err(ValueError('bad row')),", color: FAIL}, {text: " ['loaded config', 'parsing rows'])", color: TEST}],
  },
];
const NEST_W = 570;
const ABOUT = {Try: 'catches a raise, gives Err', Listen: 'collects the Tells inside it'};

const NestPanel: React.FC<{n: Nesting}> = ({n}) => {
  const frame = useSceneFrame();
  const y = 104;
  const h = 400;
  const tryOuter = n.outer === 'Try';
  const inner = {x: n.x + 22, y: y + 52, w: NEST_W - 44, h: tryOuter ? 330 : 225};
  const code = {x: inner.x + 22, y: inner.y + 46, w: inner.w - 44, size: 16, line: 27};
  const codeBottom = code.y + PAD * 2 + FAILS.length * code.line;
  // Where Listen keeps the logs it collects: inside the inner frame (Listen inner) or below it (Listen outer).
  const chipY = tryOuter ? codeBottom + 14 : inner.y + inner.h + 14;
  const t = n.raise;
  const lit = frame >= f(t - 0.4) && frame < f(t + 6.4);
  const band = frame < f(1.2) ? null : frame < f(2.6) ? 2 : frame < f(t) ? 3 : 4;
  const dropped = tryOuter && frame >= f(t + 1.6);
  const raiseY = lineMid(code.y, code.line, 4);
  const innerName = tryOuter ? 'Listen' : 'Try';
  // The path of the raise: out of the code, through the inner frame, to the outer frame, then to the result.
  const leg1 = ramp(frame, t + 0.6, t + 1.4);
  const leg2 = ramp(frame, t + 1.8, t + 2.6);
  const leg3 = ramp(frame, t + 3.2, t + 4.0);
  const innerEdge = inner.y + inner.h;
  const caughtAt = tryOuter ? t + 2.6 : t + 1.4;
  const isErr = frame >= f(caughtAt);
  const startX = code.x + 330;
  const p1 = {x: mix(startX, n.x + NEST_W / 2, leg1), y: mix(raiseY, innerEdge, leg1)};
  const p2 = {x: p1.x, y: mix(p1.y, tryOuter ? y + h - 12 : chipY + 84, leg2)};
  const p3 = {x: mix(p2.x, n.x + NEST_W / 2, leg3), y: mix(p2.y, 548, leg3)};
  const tellAt = (i: number) => 1.4 + i * 1.4;
  const frameStyle = (box: {x: number; y: number; w: number; h: number}, on: boolean) => ({
    position: 'absolute' as const, left: box.x, top: box.y, width: box.w, height: box.h, boxSizing: 'border-box' as const, borderRadius: 18,
    border: `3px solid ${ACCENT}`, ...glow(on, ACCENT),
  });
  const outerOn = lit && (tryOuter ? frame >= f(t + 2.4) && frame < f(t + 3.2) : frame >= f(t + 2.4) && frame < f(t + 3.4));
  const innerOn = lit && (tryOuter ? frame >= f(t + 1.2) && frame < f(t + 1.9) : frame >= f(t + 1.2) && frame < f(t + 2.0));
  return (
    <>
      <div style={{position: 'absolute', left: n.x, top: 66, width: NEST_W, textAlign: 'center', fontFamily: MONO, fontSize: 22, fontWeight: 700, color: lit ? LOOK : TEXT, opacity: fadeAt(frame, 0.3)}}>
        {tryOuter ? 'Try(Listen(tells_then_fails()))' : 'Listen(Try(tells_then_fails()))'}
      </div>
      <div style={{...frameStyle({x: n.x, y, w: NEST_W, h}, outerOn), background: PANEL, opacity: fadeAt(frame, 0.3)}}>
        <div style={{position: 'absolute', left: 18, top: 10, fontFamily: MONO, fontSize: 20, fontWeight: 700, color: ACCENT}}>
          {n.outer}( <span style={{fontFamily: SANS, fontSize: 15, fontWeight: 400, color: MUTED, marginLeft: 8}}>{ABOUT[n.outer]}</span>
        </div>
      </div>
      <div style={{...frameStyle(inner, innerOn), background: '#12141e', opacity: fadeAt(frame, 0.5)}}>
        <div style={{position: 'absolute', left: 16, top: 8, fontFamily: MONO, fontSize: 18, fontWeight: 700, color: ACCENT}}>
          {innerName}( <span style={{fontFamily: SANS, fontSize: 15, fontWeight: 400, color: MUTED, marginLeft: 8}}>{ABOUT[innerName]}</span>
        </div>
      </div>
      <CodeBlock lines={FAILS} {...code} band={band} opacity={fadeAt(frame, 0.6)} />
      {/* The two Tells: each flies to Listen and stays there as a log. */}
      {LOGS.map((log, i) => {
        const at = tellAt(i);
        const fly = ramp(frame, at + 0.2, at + 0.9);
        const chipX = n.x + 44 + i * 250;
        return (
          <React.Fragment key={log}>
            {frame >= f(at + 0.2) && frame < f(at + 0.9) && (
              <Pill x={mix(startX - 40, chipX + 100, fly)} y={mix(lineMid(code.y, code.line, 2 + i), chipY + 16, fly)} text={`Tell(${log})`} color={TEST} />
            )}
            <div
              style={{
                position: 'absolute', left: chipX, top: chipY, width: 230, height: 32, boxSizing: 'border-box', borderRadius: 10, border: `2px solid ${dropped ? FAIL : TEST}`,
                fontFamily: MONO, fontSize: 16, lineHeight: '28px', textAlign: 'center', color: dropped ? MUTED : TEST, textDecoration: dropped ? 'line-through' : 'none',
                opacity: fadeAt(frame, at + 0.9, 0.2) * (dropped ? mix(1, 0.45, ramp(frame, t + 1.6, t + 2.2)) : 1),
              }}
            >
              {log}
            </div>
          </React.Fragment>
        );
      })}
      {dropped && (
        <Label x={n.x + 44} y={chipY + 38} color={FAIL} size={16} opacity={fadeAt(frame, t + 1.8)}>
          Listen only returns its logs on success: dropped
        </Label>
      )}
      {!tryOuter && frame >= f(t + 2.6) && (
        <Label x={n.x + 44} y={chipY + 38} color={TEST} size={16} opacity={fadeAt(frame, t + 2.6)}>
          Listen got a value (the Err), so it returns it with its logs
        </Label>
      )}
      {/* The raise on its way out. */}
      {frame >= f(t + 0.6) && frame < f(t + 4.1) && (
        <Pill
          x={frame < f(t + 1.8) ? p1.x : frame < f(t + 3.2) ? p2.x : p3.x} y={frame < f(t + 1.8) ? p1.y : frame < f(t + 3.2) ? p2.y : p3.y}
          text={isErr ? "Err(ValueError('bad row'))" : "ValueError('bad row')"} color={FAIL} opacity={fadeAt(frame, t + 0.6, 0.15) * (1 - ramp(frame, t + 3.9, t + 4.1))}
        />
      )}
      <Terminal x={n.x} y={524} w={NEST_W} size={17} line={28} lines={n.result} opacity={fadeAt(frame, t + 4.0)} on={frame >= f(20.6)} />
    </>
  );
};

const NestScene: React.FC = () => {
  const frame = useSceneFrame();
  return (
    <AbsoluteFill>
      <Headlines lines={NEST_LINES} />
      {NESTINGS.map((n) => (
        <NestPanel key={n.outer} n={n} />
      ))}
      {/* measure/nesting_order.py, the same Listen around a program that succeeds. */}
      <Label x={40} y={624} size={17} bold={false} color={MUTED} opacity={fadeAt(frame, 21.4)}>
        On success, Listen(p) returns <span style={{fontFamily: MONO, color: TEST}}>(42, ['loaded config', 'parsing rows'])</span>
      </Label>
    </AbsoluteFill>
  );
};

// Scene 3 — scopes restore the environment; the store keeps every write.

const SCOPE_LINES: Lines = [
  [0, 'Scopes restore the environment. They never roll back the store.'],
  [5.2, 'Local: key is "inner" inside, "outer" after. The Put to counter stays.'],
  [12.4, 'Try around a failing Local: key comes back; counter is still 1.'],
  [20.8, 'Ask reads the scope you are in. Get and Put read one shared store.'],
];
const SCOPE = f(28.5);
const LOCAL_CODE = ['@do', 'def changes():', '    inside = yield Ask("key")', '    yield Put("counter", 1)', '    return inside', ' ', 'yield Local({"key": "inner"}, changes())'];
const TRY_CODE = ['@do', 'def changes_then_fails():', '    _ = yield Ask("key")', '    yield Put("counter", 1)', '    raise ValueError("boom")', ' ', 'yield Try(Local({"key": "inner"}, changes_then_fails()))'];
// The steps of each run in design seconds: the scope opens, Ask, Put, the end of the sub-program, the scope closes.
const RUNS = [
  {open: 6.0, ask: 6.8, put: 7.8, end: 8.8, close: 9.6},
  {open: 13.4, ask: 14.2, put: 15.2, end: 16.2, close: 17.2},
];

const ScopeScene: React.FC = () => {
  const frame = useSceneFrame();
  const second = frame >= f(12.4);
  const run = second ? RUNS[1] : RUNS[0];
  const code = {x: 40, y: 110, w: 620, size: 16, line: 28};
  const band =
    frame < f(run.open - 0.4) ? null : frame < f(run.ask) ? 6 : frame < f(run.put) ? 2 : frame < f(run.end) ? 3 : frame < f(run.close + 0.6) ? 4 : 6;
  const scope = ramp(frame, run.open, run.open + 0.4) * (1 - ramp(frame, run.close, run.close + 0.4));
  const env = {x: 730, y: 140, w: 500};
  const store = {x: 730, y: 330, w: 500};
  const raiseOut = ramp(frame, RUNS[1].end + 0.2, RUNS[1].close + 0.6);
  return (
    <AbsoluteFill>
      <Headlines lines={SCOPE_LINES} />
      <CodeBlock lines={LOCAL_CODE} {...code} band={second ? null : band} opacity={fadeAt(frame, 0.2) * (1 - ramp(frame, 12.4, 12.8))} />
      <CodeBlock lines={TRY_CODE} {...code} band={second ? band : null} opacity={ramp(frame, 12.6, 13.0)} />
      {/* The scope: drawn around the environment only, opening when Local starts and closing when it ends. */}
      <div
        style={{
          position: 'absolute', left: env.x - 22, top: env.y - 46, width: env.w + 44, height: 168, boxSizing: 'border-box', borderRadius: 20,
          border: `3px dashed ${ACCENT}`, opacity: scope, transform: `scale(${mix(1.06, 1, scope)})`,
        }}
      >
        <div style={{position: 'absolute', left: 16, top: 6, fontFamily: MONO, fontSize: 17, fontWeight: 700, color: ACCENT}}>Local({'{'}"key": "inner"{'}'}, ...)</div>
      </div>
      <Tile
        x={env.x} y={env.y} w={env.w} title="environment — what Ask reads" name="key" color={ACCENT} opacity={fadeAt(frame, 0.6)}
        keys={[[0, '"outer"'], [RUNS[0].open + 0.3, '"inner"'], [RUNS[0].close + 0.3, '"outer"'], [RUNS[1].open + 0.3, '"inner"'], [RUNS[1].close + 0.3, '"outer"']]}
        note={frame >= f(RUNS[0].close + 0.4) && frame < f(RUNS[1].open) ? <span style={{color: ACCENT}}>restored</span> : frame >= f(RUNS[1].close + 0.4) ? <span style={{color: ACCENT}}>restored</span> : null}
      />
      <Tile
        x={store.x} y={store.y} w={store.w} title="store — what Get and Put use" name="counter" color={PROD} opacity={fadeAt(frame, 0.8)}
        keys={[[0, '0'], [RUNS[0].put + 0.3, '1'], [12.8, '0'], [RUNS[1].put + 0.3, '1']]}
        note={(frame >= f(RUNS[0].close + 0.4) && frame < f(12.8)) || frame >= f(RUNS[1].close + 0.4) ? <span style={{color: PROD}}>stays</span> : null}
      />
      {/* The second run fails: the raise leaves Local, Try catches it. */}
      {frame >= f(RUNS[1].end + 0.2) && frame < f(20.8) && (
        <Pill
          x={mix(code.x + 380, store.x + store.w / 2, raiseOut)} y={mix(lineMid(code.y, code.line, 4), 470, raiseOut)}
          text={raiseOut < 1 ? "ValueError('boom')" : "Try gives Err(ValueError('boom'))"} color={FAIL} opacity={fadeAt(frame, RUNS[1].end + 0.2, 0.15)}
        />
      )}
      {/* measure/scopes.log: (key, counter) before and after each scope. */}
      <Terminal
        x={40} y={526} w={1190} size={17} line={28}
        lines={[
          {text: frame >= f(10.2) ? 'Local, success:     before ("outer", 0)   inside key "inner"              after ("outer", 1)' : '', color: TEXT},
          {text: frame >= f(18.2) ? "Try(Local), raise:  before (\"outer\", 0)   caught Err(ValueError('boom'))  after (\"outer\", 1)" : '', color: TEXT},
        ]}
        opacity={fadeAt(frame, 10.0)}
      />
      <Label x={40} y={632} size={18} color={ACCENT} opacity={fadeAt(frame, 21.4)}>
        Ask: the scope you are in
      </Label>
      <Label x={360} y={632} size={18} color={PROD} opacity={fadeAt(frame, 22.4)}>
        Get / Put: one store, shared by every scope, never rolled back
      </Label>
    </AbsoluteFill>
  );
};

// Scene 4 — WithObserve sees every effect in its body and changes none of them.

const OBSERVE_LINES: Lines = [
  [0, 'WithObserve sees every effect in its body, spawned children included.'],
  [7, 'The observer only watches: what it returns goes nowhere.'],
  [15, 'Spawn, Ask, Spawn, Ask, Gather were seen. Both children got "value".'],
];
const OBSERVE = f(23);
const BODY = ['@do', 'def child():', '    return (yield Ask("key"))', ' ', '@do', 'def body():', '    t1 = yield Spawn(child())', '    t2 = yield Spawn(child())', '    return (yield Gather(t1, t2))'];
const OBSERVER = ['def observer(effect):', '    seen = (*seen, name_of(effect))', '    return "ignored"'];
// measure/observe.log: the effects the observer saw, in order, and the handler that answers each.
const SEEN = [
  {name: 'Spawn', line: 6, to: 'scheduled'},
  {name: "Ask('key')", line: 2, to: 'reader'},
  {name: 'Spawn', line: 7, to: 'scheduled'},
  {name: "Ask('key')", line: 2, to: 'reader'},
  {name: 'Gather', line: 8, to: 'scheduled'},
];

const ObserveScene: React.FC = () => {
  const frame = useSceneFrame();
  const code = {x: 56, y: 132, w: 450, size: 17, line: 28};
  const ring = {x: 32, y: 92, w: 500, h: PAD * 2 + BODY.length * code.line + 62};
  const handlers = {reader: {y: 104}, scheduled: {y: 186}};
  const list = {x: 760, y: 280};
  const watch = {x: 56, y: 450, w: 450, size: 16, line: 27};
  const at = (i: number) => 1.4 + i * 1.1;
  const current = SEEN.reduce((found, _, i) => (frame >= f(at(i)) && frame < f(at(i) + 0.8) ? i : found), -1);
  const ignoredRise = ramp(frame, 8.6, 10.2);
  return (
    <AbsoluteFill>
      <Headlines lines={OBSERVE_LINES} />
      <div
        style={{
          position: 'absolute', left: ring.x, top: ring.y, width: ring.w, height: ring.h, boxSizing: 'border-box', borderRadius: 20,
          border: `3px dashed ${ACCENT}`, ...(current >= 0 ? glow(true, ACCENT) : {}), opacity: fadeAt(frame, 0.3),
        }}
      >
        <div style={{position: 'absolute', left: 16, top: 6, fontFamily: MONO, fontSize: 18, fontWeight: 700, color: ACCENT}}>WithObserve(observer, body())</div>
      </div>
      <CodeBlock lines={BODY} {...code} band={current >= 0 ? SEEN[current].line : null} opacity={fadeAt(frame, 0.5)} />
      {(['reader', 'scheduled'] as const).map((name) => {
        const on = current >= 0 && SEEN[current].to === name && frame >= f(at(current) + 0.4);
        return (
          <div
            key={name}
            style={{
              position: 'absolute', left: 760, top: handlers[name].y, width: 470, height: 64, boxSizing: 'border-box', borderRadius: 14, background: PANEL,
              border: `3px solid ${TEST}`, ...glow(on, TEST), padding: '6px 18px', opacity: fadeAt(frame, 0.7),
            }}
          >
            <div style={{fontFamily: MONO, fontSize: 20, fontWeight: 700, color: on ? LOOK : TEST}}>{name === 'reader' ? 'reader({"key": "value"})' : 'scheduled(...)'}</div>
            <div style={{fontFamily: SANS, fontSize: 15, color: MUTED}}>{name === 'reader' ? 'answers Ask' : 'answers Spawn and Gather'}</div>
          </div>
        );
      })}
      <Label x={list.x} y={list.y} size={18} color={ACCENT} opacity={fadeAt(frame, 1.0)}>observer saw</Label>
      {SEEN.map((s, i) => {
        const t = at(i);
        const out = ramp(frame, t, t + 0.4);
        const on = ramp(frame, t + 0.4, t + 0.8);
        const fromY = lineMid(code.y, code.line, s.line);
        const ringEdge = ring.x + ring.w;
        return (
          <React.Fragment key={i}>
            {frame >= f(t) && frame < f(t + 0.85) && (
              <Pill
                x={frame < f(t + 0.4) ? mix(code.x + 300, ringEdge, out) : mix(ringEdge, 790, on)} y={frame < f(t + 0.4) ? mix(fromY, fromY, out) : mix(fromY, handlers[s.to].y + 32, on)}
                text={s.name} color={ACCENT} opacity={1 - ramp(frame, t + 0.75, t + 0.85)}
              />
            )}
            <div
              style={{
                position: 'absolute', left: list.x, top: list.y + 32 + i * 30, fontFamily: MONO, fontSize: 19, color: s.name.startsWith('Ask') && frame >= f(15.6) ? LOOK : TEXT,
                opacity: fadeAt(frame, t + 0.4, 0.2), transform: `translateX(${mix(-14, 0, ramp(frame, t + 0.4, t + 0.7))}px)`,
              }}
            >
              {i + 1}. {s.name}
            </div>
          </React.Fragment>
        );
      })}
      {/* The observer: called for each effect; what it returns goes nowhere. */}
      <CodeBlock lines={OBSERVER} {...watch} band={frame >= f(7.6) && frame < f(11) ? 2 : null} opacity={fadeAt(frame, 7.2)} />
      {frame >= f(8.6) && frame < f(10.4) && (
        <Pill x={mix(watch.x + 300, watch.x + 600, ignoredRise)} y={lineMid(watch.y, watch.line, 2)} text={'"ignored"'} color={MUTED} opacity={1 - ignoredRise} />
      )}
      <Label x={watch.x} y={watch.y + PAD * 2 + OBSERVER.length * watch.line + 10} size={17} color={MUTED} bold={false} opacity={fadeAt(frame, 9.6)}>
        its return value goes nowhere: it cannot answer an effect
      </Label>
      {[0, 1].map((i) =>
        frame >= f(11.0 + i * 0.5) && frame < f(12.0 + i * 0.5) ? (
          <Pill
            key={i} x={mix(780, code.x + 380, ramp(frame, 11.0 + i * 0.5, 11.9 + i * 0.5))} y={mix(handlers.reader.y + 32, lineMid(code.y, code.line, 2), ramp(frame, 11.0 + i * 0.5, 11.9 + i * 0.5))}
            text="'value'" color={TEST}
          />
        ) : null,
      )}
      <Terminal
        x={760} y={470} w={470} line={28}
        lines={[{text: "asks seen: [\"Ask('key')\", \"Ask('key')\"]", color: ACCENT}, {text: "body returned: ['value', 'value']", color: TEST}]} size={16}
        opacity={fadeAt(frame, 15.4)} on={frame >= f(15.4) && frame < f(17)}
      />
    </AbsoluteFill>
  );
};

// A timeline of tasks: one lane per task, one block per step in the order the scheduler ran them. The lane of the
// task running now is ringed; a block marked lost turns red at its time.
type Step = {at: number; lane: number; text: string; kind: 'read' | 'write' | 'wait' | 'raise'; lost?: number};
const KIND_COLOR = {read: ACCENT, write: PROD, wait: MUTED, raise: FAIL};

const Timeline: React.FC<{x: number; y: number; w: number; lanes: string[]; steps: Step[]; size: number; lane?: number; opacity?: number}> = ({
  x, y, w, lanes, steps, size, lane = 74, opacity = 1,
}) => {
  const frame = useSceneFrame();
  const shown = steps.filter((s) => frame >= f(s.at));
  const last = shown[shown.length - 1];
  const running = last && last.kind !== 'wait' && last.kind !== 'raise' && frame < f(last.at + 0.9) ? last.lane : last && last.kind === 'read' ? last.lane : -1;
  const col = (w - 56) / steps.length;
  return (
    <div style={{position: 'absolute', left: x, top: y, opacity}}>
      {lanes.map((name, i) => (
        <React.Fragment key={name}>
          <div
            style={{
              position: 'absolute', left: 0, top: i * lane, width: w, height: lane - 10, boxSizing: 'border-box', borderRadius: 12,
              border: `2px solid ${running === i ? LOOK : '#2a2e40'}`, boxShadow: running === i ? `0 0 16px ${LOOK}77` : 'none',
            }}
          />
          <div style={{position: 'absolute', left: 12, top: i * lane, fontFamily: MONO, fontSize: 20, fontWeight: 700, lineHeight: `${lane - 10}px`, color: running === i ? LOOK : TEXT}}>{name}</div>
        </React.Fragment>
      ))}
      {steps.map((s, i) => {
        const lost = s.lost !== undefined && frame >= f(s.lost);
        const color = lost ? FAIL : KIND_COLOR[s.kind];
        return (
          <div
            key={i}
            style={{
              position: 'absolute', left: 52 + i * col, top: s.lane * lane + 7, width: col - 6, height: lane - 24, boxSizing: 'border-box', borderRadius: 9,
              border: `2px ${s.kind === 'wait' ? 'dashed' : 'solid'} ${color}`, background: lost ? `${FAIL}22` : '#0b0d13', fontFamily: MONO, fontSize: size,
              fontWeight: 700, color, display: 'flex', alignItems: 'center', justifyContent: 'center', textAlign: 'center', lineHeight: `${size + 3}px`,
              opacity: fadeAt(frame, s.at, 0.2), transform: `translateY(${mix(10, 0, ramp(frame, s.at, s.at + 0.3))}px)`, textDecoration: lost ? 'line-through' : 'none',
            }}
          >
            {s.text}
          </div>
        );
      })}
    </div>
  );
};

// Scene 5 — Gather: one shared store, one task at a time; a read-modify-write across a suspension loses updates.

const GATHER_LINES: Lines = [
  [0, 'Children under Gather share one store; one task runs at a time.'],
  [6.5, 'A child that does not suspend runs to its end. Each sees the last write.'],
  [14.5, 'Gather gives [0, 1, 2], and counter is 3.'],
  [18.5, 'Now each child suspends on Await between its read and its write.'],
  [24, 'The others run in the gap and read the same 0: two updates are lost.'],
  [31.5, 'The Rust and the Python scheduler give the same results.'],
];
const GATHER = f(37.5);
const INCREMENT = ['@do', 'def increment():', '    n = yield Get("counter")', '    yield Await(asyncio.sleep(0.05))', '    yield Put("counter", n + 1)', '    return n'];
// measure/gather_store.log, the order of the reads and writes under each scheduler (the same in both).
const STRAIGHT: Step[] = [
  {at: 7.2, lane: 0, text: 'read 0', kind: 'read'},
  {at: 8.2, lane: 0, text: 'wrote 1', kind: 'write'},
  {at: 9.2, lane: 1, text: 'read 1', kind: 'read'},
  {at: 10.2, lane: 1, text: 'wrote 2', kind: 'write'},
  {at: 11.2, lane: 2, text: 'read 2', kind: 'read'},
  {at: 12.2, lane: 2, text: 'wrote 3', kind: 'write'},
];
const SUSPENDED: Step[] = [
  {at: 19.4, lane: 0, text: 'read 0', kind: 'read'},
  {at: 20.2, lane: 0, text: 'waits', kind: 'wait'},
  {at: 21.0, lane: 1, text: 'read 0', kind: 'read'},
  {at: 21.8, lane: 1, text: 'waits', kind: 'wait'},
  {at: 22.6, lane: 2, text: 'read 0', kind: 'read'},
  {at: 23.4, lane: 2, text: 'waits', kind: 'wait'},
  {at: 25.0, lane: 0, text: 'wrote 1', kind: 'write'},
  {at: 25.8, lane: 1, text: 'wrote 1', kind: 'write', lost: 27.2},
  {at: 26.6, lane: 2, text: 'wrote 1', kind: 'write', lost: 27.4},
];

const GatherScene: React.FC = () => {
  const frame = useSceneFrame();
  const second = frame >= f(18.5);
  const code = {x: 40, y: 104, w: 440, size: 16, line: 27};
  const steps = second ? SUSPENDED : STRAIGHT;
  const shown = steps.filter((s) => frame >= f(s.at));
  const last = shown[shown.length - 1];
  // The band follows the step being run: Get for a read, Await for a wait, Put for a write.
  const band = !last ? null : last.kind === 'read' ? 2 : last.kind === 'wait' ? 3 : 4;
  const counterKeys: [number, string][] = [[0, '0'], ...STRAIGHT.filter((s) => s.kind === 'write').map((s): [number, string] => [s.at, s.text.split(' ')[1]]), [18.5, '0'],
    ...SUSPENDED.filter((s) => s.kind === 'write').map((s): [number, string] => [s.at, s.text.split(' ')[1]])];
  const allWaiting = frame >= f(23.8) && frame < f(25.0);
  return (
    <AbsoluteFill>
      <Headlines lines={GATHER_LINES} />
      <CodeBlock
        lines={INCREMENT} {...code} band={band} opacity={fadeAt(frame, 0.2)}
        dim={(i) => (i === 3 ? ramp(frame, 18.7, 19.2) : 1)}
      />
      <Label x={code.x} y={code.y + PAD * 2 + INCREMENT.length * code.line + 8} size={16} bold={false} opacity={fadeAt(frame, 0.8)}>
        three children: t1, t2, t3 = Spawn(increment()) × 3
      </Label>
      <Tile x={code.x} y={340} w={code.w} title="store — shared by every child" name="counter" color={PROD} keys={counterKeys} opacity={fadeAt(frame, 1.0)} />
      <Timeline x={530} y={104} w={710} lanes={['t1', 't2', 't3']} steps={STRAIGHT} size={17} opacity={fadeAt(frame, 1.4) * (1 - ramp(frame, 18.3, 18.6))} />
      <Timeline x={530} y={104} w={710} lanes={['t1', 't2', 't3']} steps={SUSPENDED} size={13} opacity={ramp(frame, 18.6, 18.9)} />
      <Label x={530} y={330} size={17} color={LOOK} bold={false} opacity={fadeAt(frame, 1.6) * (allWaiting ? 0 : 1)}>
        the scheduler runs one task at a time, and switches only where a task suspends
      </Label>
      {allWaiting && (
        <Label x={530} y={330} size={17} color={MUTED} opacity={fadeAt(frame, 23.8)}>
          all three are waiting on Await, each holding n = 0
        </Label>
      )}
      {frame >= f(27.2) && (
        <Label x={1010} y={362} size={17} color={FAIL} opacity={fadeAt(frame, 27.2)}>
          two updates lost
        </Label>
      )}
      <Terminal
        x={40} y={470} w={1200} size={18} line={30}
        lines={[
          {text: frame >= f(13.4) ? 'does not suspend:     Gather -> [0, 1, 2]   counter -> 3' : '', color: TEST},
          {text: frame >= f(28.2) ? 'suspends on Await:    Gather -> [0, 0, 0]   counter -> 1' : '', color: FAIL},
        ]}
        opacity={fadeAt(frame, 13.2)} on={frame >= f(14.5) && frame < f(15.6)}
      />
      {(['rust', 'python'] as const).map((name, i) => (
        <div
          key={name}
          style={{
            position: 'absolute', left: 40 + i * 610, top: 584, width: 590, height: 76, boxSizing: 'border-box', borderRadius: 14, background: PANEL,
            border: `3px solid ${TEST}`, ...glow(frame >= f(32.2 + i * 0.8) && frame < f(33.4 + i * 0.8), TEST), padding: '8px 18px', opacity: fadeAt(frame, 32.0 + i * 0.8),
          }}
        >
          <div style={{fontFamily: MONO, fontSize: 18, fontWeight: 700, color: TEST}}>scheduled(..., implementation="{name}")</div>
          <div style={{fontFamily: MONO, fontSize: 17, color: TEXT, marginTop: 4, whiteSpace: 'pre'}}>([0, 1, 2], 3)    ([0, 0, 0], 1)</div>
        </div>
      ))}
    </AbsoluteFill>
  );
};

// Scene 6 — a failing child: Gather raises its error; the siblings are not cancelled and their writes stay.

const ERROR_LINES: Lines = [
  [0, 'When a child fails, Gather raises its error in the parent: Try gives Err.'],
  [7.5, 'Gather does not cancel the others: t3 still ran, and its write stays.'],
  [14.5, 'side_effects is 2. Cancel a task yourself when it must stop.'],
];
const ERROR = f(21);
const PARENT = [
  'yield Put("side_effects", 0)',
  't1 = yield Spawn(succeeds_with_side_effect())',
  't2 = yield Spawn(fails_immediately())',
  't3 = yield Spawn(succeeds_with_side_effect())',
  'result = yield Try(Gather(t1, t2, t3))',
];
// measure/gather_error.log: the order the children ran in.
const FAILING: Step[] = [
  {at: 1.4, lane: 0, text: 'wrote side_effects=1', kind: 'write'},
  {at: 2.4, lane: 1, text: "raises ValueError('boom')", kind: 'raise'},
  {at: 3.4, lane: 2, text: 'wrote side_effects=2', kind: 'write'},
];

const ErrorScene: React.FC = () => {
  const frame = useSceneFrame();
  const code = {x: 40, y: 110, w: 500, size: 15, line: 27};
  const tl = {x: 580, y: 110, lane: 74};
  const errBack = ramp(frame, 4.4, 5.4);
  const t3On = frame >= f(8.0) && frame < f(12.0);
  return (
    <AbsoluteFill>
      <Headlines lines={ERROR_LINES} />
      <CodeBlock lines={PARENT} {...code} band={frame < f(4.4) ? null : 4} opacity={fadeAt(frame, 0.2)} />
      <Tile
        x={code.x} y={330} w={code.w} title="store" name="side_effects" color={PROD} keys={[[0, '0'], [1.4, '1'], [3.4, '2']]} opacity={fadeAt(frame, 0.6)}
        note={frame >= f(8.4) ? <span style={{color: PROD}}>not rolled back</span> : null}
      />
      <Timeline x={tl.x} y={tl.y} w={650} lanes={['t1', 't2', 't3']} steps={FAILING} size={15} opacity={fadeAt(frame, 0.8)} />
      {t3On && (
        <div
          style={{
            position: 'absolute', left: tl.x - 6, top: tl.y + 2 * tl.lane - 6, width: 662, height: tl.lane + 2, boxSizing: 'border-box', borderRadius: 14,
            border: `3px solid ${LOOK}`, boxShadow: `0 0 18px ${LOOK}88`,
          }}
        />
      )}
      {frame >= f(4.4) && frame < f(5.6) && (
        <Pill
          x={mix(tl.x + 52 + (650 - 56) / 2, code.x + 360, errBack)} y={mix(tl.y + tl.lane + 26, lineMid(code.y, code.line, 4), errBack)} text={"ValueError('boom')"} color={FAIL}
          opacity={1 - ramp(frame, 5.4, 5.6)}
        />
      )}
      <Label x={code.x} y={code.y + PAD * 2 + PARENT.length * code.line + 12} size={17} color={FAIL} opacity={fadeAt(frame, 5.4)}>
        Gather raises t2's error in the parent; Try gives Err
      </Label>
      <Label x={tl.x} y={tl.y + 3 * tl.lane + 6} size={17} color={MUTED} bold={false} opacity={fadeAt(frame, 8.4)}>
        t1 and t3 are not cancelled: Gather does not stop the other children
      </Label>
      <Terminal
        x={40} y={470} w={1190} size={18} line={30}
        lines={[{text: "Try(Gather(t1, t2, t3)) -> Err(ValueError('boom'))", color: FAIL}, {text: 'side_effects -> 2', color: PROD}]}
        opacity={fadeAt(frame, 14.8)}
      />
      <Label x={40} y={584} size={19} color={TEXT} bold={false} opacity={fadeAt(frame, 16.0)}>
        A task that must stop: <span style={{fontFamily: MONO, color: ACCENT, fontWeight: 700}}>yield Cancel(task)</span>
      </Label>
    </AbsoluteFill>
  );
};

// Outro — the chapter in one sentence.

const OUTRO = f(10.5);

const Outro: React.FC = () => {
  const frame = useSceneFrame();
  const rise = interpolate(frame, [0, f(0.67)], [20, 0], {extrapolateRight: 'clamp', easing: Easing.out(Easing.cubic)});
  const rules = [
    {text: 'the outer effect decides', color: ACCENT},
    {text: 'scopes restore the environment', color: ACCENT},
    {text: 'the store is shared', color: PROD},
    {text: 'tasks switch only where they suspend', color: TEST},
  ];
  return (
    <AbsoluteFill style={{padding: 90, justifyContent: 'center', transform: `translateY(${rise}px)`}}>
      <div style={{fontFamily: SANS, fontSize: 56, fontWeight: 700, color: TEXT, opacity: fadeAt(frame, 0, 0.4)}}>Effect combinations</div>
      <div style={{fontFamily: SANS, fontSize: 30, color: ACCENT, marginTop: 6, opacity: fadeAt(frame, 0.2, 0.4)}}>What one effect does to the effects inside it</div>
      <div style={{display: 'flex', flexWrap: 'wrap', marginTop: 46, gap: 18}}>
        {rules.map((r, i) => (
          <div
            key={r.text}
            style={{
              fontFamily: MONO, fontSize: 23, fontWeight: 700, color: r.color, padding: '10px 18px', borderRadius: 12, border: `2px solid ${r.color}`,
              background: PANEL, whiteSpace: 'nowrap', opacity: fadeAt(frame, 1.6 + i * 1.6),
            }}
          >
            {r.text}
          </div>
        ))}
      </div>
      <div style={{fontFamily: MONO, fontSize: 26, color: TEST, marginTop: 46, opacity: fadeAt(frame, 8.4, 0.4)}}>pip install doeff</div>
    </AbsoluteFill>
  );
};

// The scenes in order; Root.tsx registers EFFECT_COMBINATIONS.length frames of EFFECT_COMBINATIONS.Video.
export const EFFECT_COMBINATIONS = narrate('effect-combinations', DURATIONS, [
  {length: MATRIX, starts: startsOf(MATRIX_LINES), ids: ['a1', 'a2', 'a3'], node: <MatrixScene />},
  {length: NEST, starts: startsOf(NEST_LINES), ids: ['b1', 'b2', 'b3', 'b4'], node: <NestScene />},
  {length: SCOPE, starts: startsOf(SCOPE_LINES), ids: ['c1', 'c2', 'c3', 'c4'], node: <ScopeScene />},
  {length: OBSERVE, starts: startsOf(OBSERVE_LINES), ids: ['d1', 'd2', 'd3'], node: <ObserveScene />},
  {length: GATHER, starts: startsOf(GATHER_LINES), ids: ['e1', 'e2', 'e3', 'e4', 'e5', 'e6'], node: <GatherScene />},
  {length: ERROR, starts: startsOf(ERROR_LINES), ids: ['f1', 'f2', 'f3'], node: <ErrorScene />},
  {length: OUTRO, starts: [0], ids: ['o1'], node: <Outro />},
]);
