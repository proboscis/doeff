// The video on the CLI Script Execution page (docs/15-cli-script-execution.md): doeff run runs a program with its
// handlers, then a script of yours with the run in hand. Eight scenes, each with headlines a synthetic voice reads
// (narration.json):
//   1. The idea: the program runs first, its effects answered by handlers; then the script runs.
//   2. The command, and auto-discovery: the closest marked interpreter, and the default envs merged in path order.
//   3. The run: each Ask travels out to the reader holding the merged env; the program returns its value.
//   4. The script's globals: program, value, interpreter, sys and json — nothing else.
//   5. program is a value: the script runs it again with another env, and with none.
//   6. The script wraps the program in handlers of its own: trace passes each Ask on, free answers one itself.
//   7. A script that raises: doeff prints the error and exits with status 1.
//   8. The outro.
// Every output, order and error on screen is printed by measure/run.sh (runs.log), from measure/project and
// measure/scripts.
import React from 'react';
import {AbsoluteFill} from 'remotion';
import {fadeAt, mix, ramp} from '../../kit/motion';
import {narrate} from '../../kit/narrated';
import {CodeBlock, glow, Headlines, lineMid, Lines, PAD, Pill, startsOf} from '../../kit/parts';
import {ACCENT, f, FAIL, LOOK, MONO, MUTED, PANEL, SANS, TEST, TEXT} from '../../kit/theme';
import {useSceneFrame} from '../../kit/warp';
import DURATIONS from './durations.json';

// Seen between two seconds of a scene: fades in at the first and out at the second.
const between = (frame: number, from: number, to: number, length = 0.3) => fadeAt(frame, from, length) * (1 - fadeAt(frame, to, length));

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

// One line a run printed: [the second it appears, the text, its colour, the stream it went to].
type Printed = [number, string, string, ('stdout' | 'stderr')?];

// The terminal the script prints to: each line appears at its time, as runs.log has it.
const Terminal: React.FC<{x: number; y: number; w: number; h: number; lines: Printed[]; size?: number; line?: number; opacity?: number; streams?: boolean}> = ({
  x, y, w, h, lines, size = 16, line = 28, opacity = 1, streams = false,
}) => {
  const frame = useSceneFrame();
  return (
    <div style={{position: 'absolute', left: x, top: y, width: w, height: h, boxSizing: 'border-box', borderRadius: 14, background: '#07080c', border: '2px solid #2a2e40', opacity}}>
      <div style={{position: 'absolute', left: 18, top: 8, fontFamily: SANS, fontSize: 14, color: MUTED}}>the terminal</div>
      {lines.map(([at, text, color, stream], i) => (
        <div key={i} style={{position: 'absolute', left: 18, right: 18, top: 34 + i * line, height: line, fontFamily: MONO, fontSize: size, lineHeight: `${line}px`, color, whiteSpace: 'pre', opacity: fadeAt(frame, at)}}>
          {text}
          {streams && stream && <span style={{position: 'absolute', right: 0, fontFamily: SANS, fontSize: 13, color: MUTED}}>{stream}</span>}
        </div>
      ))}
    </div>
  );
};

// The command every scene runs, and the program it runs (measure/project/shop/checkout/cart.py).
const COMMAND = "$ doeff run --program shop.checkout.cart.total_program - <<'PY'";
const TOTAL = ['@do', 'def total():', '    rate = yield Ask("tax_rate")', '    currency = yield Ask("currency")', '    return f"{42 * (1 + rate):.2f} {currency}"'];

// Scene 1 — the idea.

const TITLE_LINES: Lines = [
  [0, 'CLI Script Execution: run a program, then run your script against that run.'],
  [5.5, 'First the program runs. The effects it yields are answered by its handlers.'],
  [10.5, 'Then your script runs, with the program, its value and its interpreter in hand.'],
];
const TITLE = f(16);
const STAGES = [
  {x: 90, title: 'the program runs', color: ACCENT, at: 5.5},
  {x: 490, title: 'the run, in hand', color: TEST, at: 10.5},
  {x: 890, title: 'your script runs', color: LOOK, at: 11.3},
];
const STAGE = {y: 330, w: 300, h: 190};

const TitleScene: React.FC = () => {
  const frame = useSceneFrame();
  const askOut = ramp(frame, 6.6, 7.4);
  const answerBack = ramp(frame, 7.8, 8.6);
  return (
    <AbsoluteFill>
      <Headlines lines={TITLE_LINES} />
      <div style={{position: 'absolute', left: 0, right: 0, top: 110 + mix(16, 0, ramp(frame, 0.2, 0.8)), textAlign: 'center', opacity: fadeAt(frame, 0.2, 0.5)}}>
        <div style={{fontFamily: SANS, fontSize: 64, fontWeight: 700, color: TEXT}}>CLI Script Execution</div>
        <div style={{fontFamily: MONO, fontSize: 22, color: ACCENT, marginTop: 10}}>{COMMAND}</div>
      </div>
      {STAGES.map((stage, i) => {
        const on = i === 0 ? frame >= f(5.6) && frame < f(10.4) : i === 1 ? frame >= f(10.6) && frame < f(11.3) : frame >= f(11.4);
        return (
          <Box key={stage.title} x={stage.x} y={STAGE.y} w={STAGE.w} h={STAGE.h} color={stage.color} on={on} opacity={fadeAt(frame, i === 0 ? 1.2 : stage.at, 0.5)}>
            <div style={{position: 'absolute', left: 0, right: 0, top: 16, textAlign: 'center', fontFamily: SANS, fontSize: 24, fontWeight: 700, color: on ? LOOK : stage.color}}>{stage.title}</div>
          </Box>
        );
      })}
      {/* Inside the program's box: an Ask goes out to a handler and its answer comes back. */}
      <div style={{position: 'absolute', left: 110, top: STAGE.y + 70, width: 260, height: 100, opacity: fadeAt(frame, 5.8)}}>
        <div style={{position: 'absolute', left: 0, top: 30, fontFamily: MONO, fontSize: 18, color: TEXT}}>total()</div>
        <div style={{position: 'absolute', right: 0, top: 18, width: 100, height: 52, boxSizing: 'border-box', border: `2px solid ${TEST}`, borderRadius: 10, fontFamily: MONO, fontSize: 17, color: TEST, textAlign: 'center', lineHeight: '48px'}}>reader</div>
      </div>
      {frame >= f(6.6) && frame < f(7.8) && <Pill x={mix(190, 236, askOut)} y={STAGE.y + 114} text="Ask" color={ACCENT} opacity={1 - ramp(frame, 7.5, 7.8)} />}
      {frame >= f(7.8) && frame < f(8.9) && <Pill x={mix(236, 190, answerBack)} y={STAGE.y + 114} text="0.1" color={TEST} opacity={1 - ramp(frame, 8.6, 8.9)} />}
      {[0, 1].map((i) => (
        <div key={i} style={{position: 'absolute', left: 390 + i * 400 + 18, top: STAGE.y + STAGE.h / 2 - 2, width: 64, height: 4, background: MUTED, opacity: fadeAt(frame, i === 0 ? 10.5 : 11.3)}} />
      ))}
      <div style={{position: 'absolute', left: 490 + 30, top: STAGE.y + 70, fontFamily: MONO, fontSize: 19, lineHeight: '34px', color: TEXT}}>
        {['program', 'value', 'interpreter'].map((name, i) => (
          <div key={name} style={{opacity: fadeAt(frame, 10.8 + i * 0.3)}}>{name}</div>
        ))}
      </div>
      <div style={{position: 'absolute', left: 890 + 30, top: STAGE.y + 70, fontFamily: MONO, fontSize: 18, lineHeight: '34px', color: TEXT, opacity: fadeAt(frame, 11.8)}}>
        <div>print(value)</div>
        <div style={{color: MUTED}}>interpreter(program)</div>
      </div>
      <Label x={640} y={580} size={22} color={MUTED} center opacity={fadeAt(frame, 12.6)}>
        one command: <span style={{fontFamily: MONO, color: TEXT}}>doeff run --program …</span> and a script after <span style={{fontFamily: MONO, color: TEXT}}>-</span>
      </Label>
    </AbsoluteFill>
  );
};

// Scene 2 — the command, and what auto-discovery finds on the module path (runs.log, --format json).

const DISCOVER_LINES: Lines = [
  [0, 'The command: a program path, then "-": the script is read from stdin.'],
  [6, 'No interpreter given: doeff takes the closest marked one on the module path.'],
  [12.5, 'The default envs along the path are merged in order. The closer one wins.'],
];
const DISCOVER = f(19);
const CMD = [COMMAND, 'print(value)', 'PY'];
const CMD_BOX = {x: 60, y: 108, w: 1160, size: 19, line: 30};
// The advance of one character of the command at 19 px, as the rendered frames measure it (the mono font the
// renderer finds is narrower than DejaVu's 0.602 em).
const CMD_CHAR = 11.0;
// The example project's files, top of the module path first (measure/project/shop).
const FILES = [
  {path: 'shop/__init__.py', interp: 'shop_interpreter', env: 'shop_env = {"currency": "EUR", "tax_rate": 0.2}'},
  {path: 'shop/checkout/__init__.py', interp: 'checkout_interpreter', env: 'checkout_env = {"tax_rate": 0.1}'},
  {path: 'shop/checkout/cart.py', interp: null, env: 'total_program = total()'},
];
const FILE = {x: 60, y: 268, w: 700, h: 104, gap: 12, indent: 30};
const fileY = (i: number) => FILE.y + i * (FILE.h + FILE.gap);
const INTERP = {x: 800, y: 268, w: 420, h: 104};
const ENV = {x: 800, y: 384, w: 420, h: 220};

const DiscoverScene: React.FC = () => {
  const frame = useSceneFrame();
  const closest = frame >= f(7.4);
  const toInterp = ramp(frame, 8.0, 8.8);
  const firstEnv = ramp(frame, 13.0, 13.8);
  const secondEnv = ramp(frame, 14.6, 15.4);
  const overridden = ramp(frame, 15.4, 15.9);
  const ringX = CMD_BOX.x + 26 + 55 * CMD_CHAR;
  return (
    <AbsoluteFill>
      <Headlines lines={DISCOVER_LINES} />
      <CodeBlock lines={CMD} {...CMD_BOX} band={frame >= f(1.0) && frame < f(5.8) ? 0 : null} opacity={fadeAt(frame, 0)} />
      <div
        style={{
          position: 'absolute', left: ringX - 5, top: CMD_BOX.y + PAD - 1, width: CMD_CHAR + 10, height: CMD_BOX.line + 2, boxSizing: 'border-box',
          border: `3px solid ${LOOK}`, borderRadius: 8, opacity: between(frame, 1.6, 5.8),
        }}
      />
      <Label x={ringX + 8} y={CMD_BOX.y + PAD + 3 * CMD_BOX.line + 14} size={17} bold color={LOOK} opacity={between(frame, 1.8, 5.8)} center>
        - : the script comes from stdin, here a heredoc
      </Label>
      {FILES.map((file, i) => {
        const indent = i * FILE.indent;
        const interpOn = file.interp !== null && frame >= f(6.6 + i * 0.6) && frame < f(12.4);
        const chosen = i === 1 && closest;
        const envOn = (i === 0 && frame >= f(13.0) && frame < f(13.8)) || (i === 1 && frame >= f(14.6) && frame < f(15.4));
        return (
          <Box key={file.path} x={FILE.x + indent} y={fileY(i)} w={FILE.w - indent} h={FILE.h} color={i === 2 ? ACCENT : TEST} on={envOn || (interpOn && !closest) || (chosen && frame < f(12.4))} opacity={fadeAt(frame, 2.4 + i * 0.4, 0.5)}>
            <div style={{position: 'absolute', left: 18, top: 8, fontFamily: MONO, fontSize: 16, fontWeight: 700, color: MUTED}}>{file.path}</div>
            <div style={{position: 'absolute', left: 18, top: 36, fontFamily: MONO, fontSize: 17, color: file.interp === null ? MUTED : interpOn ? (i === 0 && closest ? MUTED : LOOK) : TEXT}}>
              {file.interp ?? 'the program'}
              {file.interp !== null && <span style={{fontFamily: SANS, fontSize: 14, color: MUTED, marginLeft: 12}}># doeff: interpreter, default</span>}
              {chosen && <span style={{fontFamily: SANS, fontSize: 16, fontWeight: 700, color: LOOK, marginLeft: 12, opacity: fadeAt(frame, 7.4)}}>← closest</span>}
            </div>
            <div style={{position: 'absolute', left: 18, top: 66, fontFamily: MONO, fontSize: 17, color: envOn ? LOOK : TEXT}}>
              {file.env}
              {i < 2 && <span style={{fontFamily: SANS, fontSize: 14, color: MUTED, marginLeft: 12}}># doeff: default</span>}
            </div>
          </Box>
        );
      })}
      <Box x={INTERP.x} y={INTERP.y} w={INTERP.w} h={INTERP.h} color={TEST} on={frame >= f(8.8) && frame < f(9.6)} opacity={fadeAt(frame, 6.2, 0.5)}>
        <div style={{position: 'absolute', left: 18, top: 10, fontFamily: SANS, fontSize: 18, fontWeight: 700, color: TEST}}>interpreter</div>
        <div style={{position: 'absolute', left: 18, top: 50, fontFamily: MONO, fontSize: 19, color: TEXT, opacity: fadeAt(frame, 8.8)}}>shop.checkout.checkout_interpreter</div>
      </Box>
      {frame >= f(8.0) && frame < f(9.1) && <Pill x={mix(FILE.x + FILE.w, INTERP.x + 200, toInterp)} y={mix(fileY(1) + 46, INTERP.y + 64, toInterp)} text="checkout_interpreter" color={TEST} opacity={1 - ramp(frame, 8.8, 9.1)} />}
      <Box x={ENV.x} y={ENV.y} w={ENV.w} h={ENV.h} color={TEST} on={(frame >= f(13.8) && frame < f(14.4)) || (frame >= f(15.4) && frame < f(16.0))} opacity={fadeAt(frame, 12.6, 0.5)}>
        <div style={{position: 'absolute', left: 18, top: 10, fontFamily: SANS, fontSize: 18, fontWeight: 700, color: TEST}}>env, merged in path order</div>
        <div style={{position: 'absolute', left: 18, top: 50, fontFamily: MONO, fontSize: 19, lineHeight: '34px', color: TEXT}}>
          <div style={{opacity: fadeAt(frame, 13.8)}}>{'"currency": "EUR"'}</div>
          <div style={{position: 'relative', opacity: fadeAt(frame, 13.8)}}>
            {'"tax_rate": '}
            <span style={{color: MUTED, textDecoration: overridden > 0.5 ? 'line-through' : 'none', opacity: 1 - 0.5 * overridden}}>0.2</span>
            <span style={{color: LOOK, fontWeight: 700, marginLeft: 12, opacity: overridden}}>0.1</span>
            <span style={{fontFamily: SANS, fontSize: 15, color: MUTED, marginLeft: 12, opacity: overridden}}>from checkout_env</span>
          </div>
        </div>
        <div style={{position: 'absolute', left: 18, top: 136, fontFamily: SANS, fontSize: 15, lineHeight: '22px', color: MUTED, opacity: fadeAt(frame, 16.2)}}>
          <div>passed to the interpreter as env=…</div>
        </div>
      </Box>
      {frame >= f(13.0) && frame < f(14.1) && <Pill x={mix(FILE.x + FILE.w - 60, ENV.x + 200, firstEnv)} y={mix(fileY(0) + 76, ENV.y + 80, firstEnv)} text="shop_env" color={TEST} opacity={1 - ramp(frame, 13.8, 14.1)} />}
      {frame >= f(14.6) && frame < f(15.7) && <Pill x={mix(FILE.x + FILE.w - 60, ENV.x + 200, secondEnv)} y={mix(fileY(1) + 76, ENV.y + 114, secondEnv)} text="checkout_env" color={TEST} opacity={1 - ramp(frame, 15.4, 15.7)} />}
      <div style={{position: 'absolute', left: 60, right: 60, top: 642, textAlign: 'center', fontFamily: MONO, fontSize: 17, color: MUTED, opacity: fadeAt(frame, 16.6)}}>
        --format json: <span style={{color: TEXT}}>{'"envs": ["shop.shop_env", "shop.checkout.checkout_env"]'}</span>
      </div>
    </AbsoluteFill>
  );
};

// Scene 3 — the run: each Ask travels out to the reader holding the merged env (runs.log, the first run).

const RUN_LINES: Lines = [
  [0, 'checkout_interpreter runs the program under a reader holding the merged env.'],
  [5, 'Each Ask travels out to the reader, and its answer comes back to the yield.'],
  [11.5, 'The program returns "46.20 EUR". That is value.'],
];
const RUN = f(17);
const T = {cx: 640, cy: 402, w: 560, size: 18, line: 30, margin: 80};
const tH = PAD * 2 + TOTAL.length * T.line;
const tCode = {x: T.cx - T.w / 2, y: T.cy - tH / 2};
const tLayer = (i: number) => {
  const lw = T.w + 2 * T.margin * (i + 1);
  const lh = tH + 2 * 44 * (i + 1);
  return {x: T.cx - lw / 2, y: T.cy - lh / 2, w: lw, h: lh};
};
const RUN_LAYERS = ['reader(env={"currency": "EUR", "tax_rate": 0.1})', 'scheduled'];

const RunScene: React.FC = () => {
  const frame = useSceneFrame();
  const startX = T.cx + T.w / 2;
  const edge = T.cx + tLayer(0).w / 2;
  const band = frame < f(5) ? null : frame < f(8.4) ? 2 : frame < f(10.9) ? mix(2, 3, ramp(frame, 8.4, 8.7)) : mix(3, 4, ramp(frame, 10.9, 11.2));
  const trips = [
    {y: lineMid(tCode.y, T.line, 2), ask: 'Ask("tax_rate")', answer: '0.1', leave: 5.6, land: 6.4},
    {y: lineMid(tCode.y, T.line, 3), ask: 'Ask("currency")', answer: '"EUR"', leave: 8.8, land: 9.6},
  ];
  const working = trips.some((trip) => frame >= f(trip.land) && frame < f(trip.land + 0.6));
  return (
    <AbsoluteFill>
      <Headlines lines={RUN_LINES} />
      <div style={{position: 'absolute', left: 0, right: 0, top: 112, textAlign: 'center', fontFamily: MONO, fontSize: 19, color: TEXT, opacity: fadeAt(frame, 0.4)}}>
        <span style={{color: TEST}}>checkout_interpreter</span>: run(scheduled(reader(env=run(env))(prog)))
      </div>
      {RUN_LAYERS.map((name, i) => {
        const r = tLayer(i);
        const on = i === 0 && working;
        return (
          <div
            key={name}
            style={{
              position: 'absolute', left: r.x, top: r.y, width: r.w, height: r.h, boxSizing: 'border-box', borderRadius: 22,
              border: `3px solid ${TEST}`, ...glow(on, TEST), opacity: fadeAt(frame, 0.8 + (1 - i) * 0.6, 0.5),
            }}
          >
            <div style={{position: 'absolute', left: 16, top: 10, fontFamily: MONO, fontSize: 17, fontWeight: 700, color: on ? LOOK : TEST, whiteSpace: 'nowrap'}}>{name}</div>
          </div>
        );
      })}
      <CodeBlock lines={TOTAL} x={tCode.x} y={tCode.y} w={T.w} size={T.size} line={T.line} band={band} opacity={fadeAt(frame, 0)} />
      {trips.map((trip) => (
        <React.Fragment key={trip.ask}>
          {frame >= f(trip.leave) && frame < f(trip.land + 0.5) && (
            <Pill x={mix(startX, edge, ramp(frame, trip.leave, trip.land))} y={trip.y} text={trip.ask} color={ACCENT} opacity={1 - ramp(frame, trip.land + 0.2, trip.land + 0.5)} />
          )}
          {frame >= f(trip.land + 0.6) && frame < f(trip.land + 1.5) && (
            <Pill x={mix(edge, startX, ramp(frame, trip.land + 0.6, trip.land + 1.2))} y={trip.y} text={trip.answer} color={TEST} opacity={1 - ramp(frame, trip.land + 1.2, trip.land + 1.5)} />
          )}
        </React.Fragment>
      ))}
      <div style={{position: 'absolute', left: 0, right: 0, top: 638, textAlign: 'center', fontFamily: MONO, fontSize: 26, fontWeight: 700, color: ACCENT, opacity: fadeAt(frame, 11.8)}}>
        value = '46.20 EUR'
      </div>
    </AbsoluteFill>
  );
};

// Scene 4 — the script's globals (runs.log, scripts/inspect.py).

const INJECT_LINES: Lines = [
  [0, 'Now the script runs. doeff has put five names in its globals.'],
  [5, 'value is what the program returned.'],
  [8.5, 'program is the program as imported, still a value you can run. interpreter is the one that ran it.'],
  [15, 'sys and json come along too. Anything else, the script imports itself.'],
];
const INJECT = f(21);
const INSPECT = [
  COMMAND,
  'print("value:", repr(value))',
  'print("program:", type(program).__name__)',
  'print("interpreter:", interpreter.__module__ + "." + interpreter.__name__)',
  'print("injected:", sorted(k for k in globals() if not k.startswith("__")))',
  'PY',
];
const NAMES = [
  {name: 'value', is: "'46.20 EUR'", about: 'what the program returned', at: 5.0},
  {name: 'program', is: 'Call', about: 'the program as imported', at: 8.6},
  {name: 'interpreter', is: 'checkout_interpreter', about: 'the one that ran it', at: 11.4},
  {name: 'sys', is: 'module', about: '', at: 15.0},
  {name: 'json', is: 'module', about: '', at: 15.3},
];
const NAME = {x: 60, y: 324, w: 560, h: 58, gap: 8};
const INSPECT_OUT: Printed[] = [
  [6.0, "value: '46.20 EUR'", TEXT],
  [9.6, 'program: Call', TEXT],
  [12.2, 'interpreter: shop.checkout.checkout_interpreter', TEXT],
  [16.4, "injected: ['interpreter', 'json', 'program', 'sys', 'value']", TEXT],
];

const InjectScene: React.FC = () => {
  const frame = useSceneFrame();
  const band = frame < f(5.0) ? null : frame < f(8.5) ? 1 : frame < f(11.3) ? 2 : frame < f(15.8) ? 3 : 4;
  return (
    <AbsoluteFill>
      <Headlines lines={INJECT_LINES} />
      <CodeBlock lines={INSPECT} x={60} y={110} w={1160} size={16} line={28} band={band} opacity={fadeAt(frame, 0)} />
      {NAMES.map((n, i) => {
        const on = frame >= f(n.at) && frame < f(n.at + 2.4);
        return (
          <Box key={n.name} x={NAME.x} y={NAME.y + i * (NAME.h + NAME.gap)} w={NAME.w} h={NAME.h} color={i < 3 ? TEST : MUTED} on={on} opacity={fadeAt(frame, 1.0 + i * 0.3, 0.4)}>
            <div style={{position: 'absolute', left: 16, top: 14, fontFamily: MONO, fontSize: 21, fontWeight: 700, color: on ? LOOK : i < 3 ? TEST : TEXT}}>{n.name}</div>
            <div style={{position: 'absolute', left: 170, top: 6, fontFamily: MONO, fontSize: 18, color: TEXT, opacity: fadeAt(frame, n.at + 0.4)}}>{n.is}</div>
            <div style={{position: 'absolute', left: 170, top: 30, fontFamily: SANS, fontSize: 15, color: MUTED, opacity: fadeAt(frame, n.at + 0.4)}}>{n.about}</div>
          </Box>
        );
      })}
      <Terminal x={650} y={324} w={570} h={170} lines={INSPECT_OUT} size={14} line={30} opacity={fadeAt(frame, 4.6)} />
      <Label x={650 + 285} y={530} size={17} color={MUTED} center opacity={fadeAt(frame, 17.4)}>
        nothing else is injected: import run, handlers, effects
      </Label>
    </AbsoluteFill>
  );
};

// Scene 5 — program is a value: run it again with another env, and with none (runs.log, scripts/rerun.py).

const AGAIN_LINES: Lines = [
  [0, 'Because program is a value, the script can run it again.'],
  [5, 'Give the interpreter another env, and the same Asks get new answers.'],
  [11.5, 'With no env, nothing holds tax_rate: the Ask fails with MissingEnvKeyError.'],
];
const AGAIN = f(18);
const RERUN = [
  COMMAND,
  'from doeff import Pure',
  'print("first run:", value)',
  'print("again:", interpreter(program, env=Pure({"tax_rate": 0.25, "currency": "USD"})))',
  'try:',
  '    interpreter(program)',
  'except Exception as e:',
  '    print("no env:", type(e).__name__, e)',
  'PY',
];
const PROG = {x: 60, y: 386, w: 200, h: 120};
const READER = {x: 500, y: 386, w: 720, h: 120};
const AGAIN_OUT: Printed[] = [
  [2.4, 'first run: 46.20 EUR', TEXT],
  [9.6, 'again: 52.50 USD', TEXT],
  [15.2, "no env: MissingEnvKeyError Ask: key not found: 'tax_rate'", TEXT],
];

const AgainScene: React.FC = () => {
  const frame = useSceneFrame();
  const band = frame < f(1.8) ? null : frame < f(5.0) ? 2 : frame < f(11.5) ? 3 : frame < f(14.4) ? 5 : 7;
  const empty = frame >= f(11.5);
  const failed = frame >= f(13.0) && frame < f(15.6);
  const out = (from: number) => ramp(frame, from, from + 0.7);
  const back = (from: number) => ramp(frame, from, from + 0.6);
  const fromX = PROG.x + PROG.w + 20;
  const toX = READER.x + 120;
  const askY = READER.y + 74;
  return (
    <AbsoluteFill>
      <Headlines lines={AGAIN_LINES} />
      <CodeBlock lines={RERUN} x={60} y={108} w={1160} size={16} line={27} band={band} opacity={fadeAt(frame, 0)} />
      <Box x={PROG.x} y={PROG.y} w={PROG.w} h={PROG.h} color={ACCENT} dashed opacity={fadeAt(frame, 0.6)}>
        <div style={{position: 'absolute', left: 0, right: 0, top: 18, textAlign: 'center', fontFamily: MONO, fontSize: 21, fontWeight: 700, color: ACCENT}}>program</div>
        <div style={{position: 'absolute', left: 0, right: 0, top: 58, textAlign: 'center', fontFamily: SANS, fontSize: 16, color: MUTED}}>a value: run it again</div>
      </Box>
      <Box x={READER.x} y={READER.y} w={READER.w} h={READER.h} color={failed ? FAIL : TEST} on={(frame >= f(6.3) && frame < f(6.9)) || (frame >= f(7.9) && frame < f(8.5))} opacity={fadeAt(frame, 5.0)}>
        <div style={{position: 'absolute', left: 18, top: 12, fontFamily: MONO, fontSize: 18, fontWeight: 700, color: failed ? FAIL : TEST}}>
          <span style={{opacity: 1 - fadeAt(frame, 11.5)}}>{'reader(env={"tax_rate": 0.25, "currency": "USD"})'}</span>
        </div>
        <div style={{position: 'absolute', left: 18, top: 12, fontFamily: MONO, fontSize: 18, fontWeight: 700, color: failed ? FAIL : TEST, opacity: fadeAt(frame, 11.5)}}>
          {'reader(env={})'}
          <span style={{fontFamily: SANS, fontWeight: 400, fontSize: 15, color: MUTED, marginLeft: 14}}>the interpreter&apos;s default env, {'Pure({})'}</span>
        </div>
        <div style={{position: 'absolute', left: 300, top: 60, fontFamily: SANS, fontSize: 17, color: FAIL, fontWeight: 700, opacity: between(frame, 13.0, 15.6)}}>key not found: &apos;tax_rate&apos;</div>
      </Box>
      {!empty && frame >= f(5.6) && frame < f(6.6) && <Pill x={mix(fromX, toX, out(5.6))} y={askY} text={'Ask("tax_rate")'} color={ACCENT} opacity={1 - ramp(frame, 6.3, 6.6)} />}
      {!empty && frame >= f(6.9) && frame < f(7.6) && <Pill x={mix(toX, fromX, back(6.9))} y={askY} text="0.25" color={TEST} opacity={1 - ramp(frame, 7.4, 7.6)} />}
      {!empty && frame >= f(7.2) && frame < f(8.2) && <Pill x={mix(fromX, toX, out(7.2))} y={askY} text={'Ask("currency")'} color={ACCENT} opacity={1 - ramp(frame, 7.9, 8.2)} />}
      {!empty && frame >= f(8.5) && frame < f(9.2) && <Pill x={mix(toX, fromX, back(8.5))} y={askY} text={'"USD"'} color={TEST} opacity={1 - ramp(frame, 9.0, 9.2)} />}
      {empty && frame >= f(12.2) && frame < f(13.2) && <Pill x={mix(fromX, toX, out(12.2))} y={askY} text={'Ask("tax_rate")'} color={ACCENT} opacity={1 - ramp(frame, 12.9, 13.2)} />}
      {empty && frame >= f(13.4) && frame < f(14.6) && <Pill x={mix(toX, PROG.x + PROG.w + 120, back(13.4))} y={askY} text="MissingEnvKeyError" color={FAIL} opacity={1 - ramp(frame, 14.3, 14.6)} />}
      <Terminal x={60} y={530} w={1160} h={160} lines={AGAIN_OUT} size={17} line={36} opacity={fadeAt(frame, 1.8)} />
    </AbsoluteFill>
  );
};

// Scene 6 — the script's own handlers around the program (runs.log, scripts/handlers.py).

const HANDLE_LINES: Lines = [
  [0, 'The script can also wrap the program in handlers of its own.'],
  [4.5, 'trace receives each Ask, prints its key, and passes it on to reader.'],
  [11.5, 'free answers tax_rate itself, with 0.0, and passes currency on.'],
  [17.5, 'Same program, other handlers, another meaning: 42.00 EUR.'],
];
const HANDLE = f(23);
const HANDLERS = [
  '@handler',
  'def trace(effect: Ask, k):',
  '    print("asked:", effect.key)',
  '    yield Pass(effect, k)',
  '',
  '@handler',
  'def free(effect: Ask, k):',
  '    if effect.key == "tax_rate":',
  '        return (yield Resume(k, 0.0))',
  '    yield Pass(effect, k)',
];
const RUNS = ['env = {"tax_rate": 0.1, "currency": "EUR"}', 'print("traced:", run(reader(env=env)(trace(program))))', 'print("tax-free:", run(reader(env=env)(free(program))))'];
const O = {x: 640, y: 196, w: 410, size: 13, line: 22, right: 80, side: 30, top: 36};
const oH = PAD * 2 + TOTAL.length * O.line;
// The layers around the program, innermost first: wider on the right, where the effects travel out.
const oLayer = (i: number) => ({x: O.x - O.side * (i + 1), y: O.y - O.top * (i + 1), w: O.w + (O.side + O.right) * (i + 1), h: oH + 2 * O.top * (i + 1)});
const HANDLE_OUT: Printed[] = [
  [6.0, 'asked: tax_rate', TEXT],
  [8.4, 'asked: currency', TEXT],
  [10.2, 'traced: 46.20 EUR', TEXT],
  [16.6, 'tax-free: 42.00 EUR', LOOK],
];

const HandleScene: React.FC = () => {
  const frame = useSceneFrame();
  const freeNow = frame >= f(11.5);
  const startX = O.x + O.w;
  const edge = (i: number) => oLayer(i).x + oLayer(i).w;
  const y2 = lineMid(O.y, O.line, 2);
  const y3 = lineMid(O.y, O.line, 3);
  // Each trip: the effect leaves the program, may stop at the inner handler, and goes on to reader or comes back.
  type Trip = {y: number; text: string; leave: number; inner: number; outer: number | null; back: string};
  const trips: Trip[] = [
    {y: y2, text: 'Ask', leave: 5.0, inner: 5.6, outer: 6.4, back: '0.1'},
    {y: y3, text: 'Ask', leave: 7.4, inner: 8.0, outer: 8.8, back: '"EUR"'},
    {y: y2, text: 'Ask', leave: 12.2, inner: 12.8, outer: null, back: '0.0'},
    {y: y3, text: 'Ask', leave: 14.2, inner: 14.8, outer: 15.6, back: '"EUR"'},
  ];
  const at = (trip: Trip) => {
    const end = trip.outer ?? trip.inner;
    if (frame < f(trip.inner)) return mix(startX, edge(0), ramp(frame, trip.leave, trip.inner));
    if (trip.outer !== null && frame < f(trip.outer)) return mix(edge(0), edge(1), ramp(frame, trip.inner + 0.2, trip.outer));
    return trip.outer === null ? edge(0) : end === trip.outer ? edge(1) : edge(0);
  };
  const innerOn = trips.some((trip) => frame >= f(trip.inner) && frame < f(trip.inner + 0.5));
  const outerOn = trips.some((trip) => trip.outer !== null && frame >= f(trip.outer) && frame < f(trip.outer + 0.5));
  const band = frame < f(4.5) ? null : frame < f(7.2) ? 2 : frame < f(10.0) ? 3 : frame < f(11.5) ? 4 : frame < f(14.0) ? 2 : frame < f(16.4) ? 3 : 4;
  const dimTrace = ramp(frame, 11.3, 11.7);
  return (
    <AbsoluteFill>
      <Headlines lines={HANDLE_LINES} />
      <CodeBlock lines={HANDLERS} x={60} y={110} w={540} size={16} line={25} dim={(i) => (i < 4 ? 1 - 0.6 * dimTrace : i > 4 ? 0.4 + 0.6 * dimTrace : 1)} opacity={fadeAt(frame, 0.4)} />
      <div style={{position: 'absolute', left: 60, top: 408, fontFamily: MONO, fontSize: 15, lineHeight: '27px', color: TEXT, whiteSpace: 'pre', opacity: fadeAt(frame, 1.2)}}>
        {RUNS.map((line, i) => (
          <div key={line} style={{color: (i === 1 && frame >= f(4.5) && !freeNow) || (i === 2 && freeNow) ? LOOK : TEXT}}>{line}</div>
        ))}
      </div>
      {['inner', 'reader(env=env)'].map((name, i) => {
        const r = oLayer(i);
        const on = i === 0 ? innerOn : outerOn;
        return (
          <div
            key={name}
            style={{
              position: 'absolute', left: r.x, top: r.y, width: r.w, height: r.h, boxSizing: 'border-box', borderRadius: 18,
              border: `3px solid ${TEST}`, ...glow(on, TEST), opacity: fadeAt(frame, 0.6 + (1 - i) * 0.5, 0.5),
            }}
          >
            <div style={{position: 'absolute', left: 14, top: 7, fontFamily: MONO, fontSize: 15, fontWeight: 700, color: on ? LOOK : TEST, whiteSpace: 'nowrap'}}>
              {i === 0 ? (
                <>
                  <span style={{opacity: 1 - dimTrace}}>trace</span>
                  <span style={{position: 'absolute', left: 0, opacity: dimTrace}}>free</span>
                </>
              ) : (
                name
              )}
            </div>
          </div>
        );
      })}
      <CodeBlock lines={TOTAL} x={O.x} y={O.y} w={O.w} size={O.size} line={O.line} band={band} />
      {trips.map((trip, i) => {
        const end = trip.outer ?? trip.inner;
        const backFrom = trip.outer === null ? edge(0) : edge(1);
        return (
          <React.Fragment key={i}>
            {frame >= f(trip.leave) && frame < f(end + 0.4) && <Pill x={at(trip)} y={trip.y} text={trip.text} color={ACCENT} opacity={1 - ramp(frame, end + 0.1, end + 0.4)} />}
            {frame >= f(end + 0.5) && frame < f(end + 1.3) && <Pill x={mix(backFrom, startX, ramp(frame, end + 0.5, end + 1.1))} y={trip.y} text={trip.back} color={TEST} opacity={1 - ramp(frame, end + 1.1, end + 1.3)} />}
          </React.Fragment>
        );
      })}
      <Terminal x={620} y={486} w={600} h={190} lines={HANDLE_OUT} size={16} line={34} opacity={fadeAt(frame, 4.6)} />
      <Label x={920} y={448} size={17} bold color={LOOK} center opacity={fadeAt(frame, 17.8)}>
        same program: 46.20 EUR under trace, 42.00 EUR under free
      </Label>
    </AbsoluteFill>
  );
};

// Scene 7 — a script that raises (runs.log, scripts/fails.py).

const FAIL_LINES: Lines = [
  [0, 'If the script raises, doeff prints the error and exits with status 1.'],
  [6, 'What the script printed before the error stays on stdout.'],
];
const FAILS = f(12);
const FAILS_SCRIPT = [COMMAND, 'print("value:", value)', 'print(value["amount"])', 'PY'];
const FAILS_OUT: Printed[] = [
  [1.6, 'value: 46.20 EUR', TEXT, 'stdout'],
  [3.0, "Error executing script: string indices must be integers, not 'str'", FAIL, 'stderr'],
  [4.2, 'exit status 1', FAIL],
];

const FailScene: React.FC = () => {
  const frame = useSceneFrame();
  const band = frame < f(1.0) ? null : frame < f(2.4) ? 1 : 2;
  return (
    <AbsoluteFill>
      <Headlines lines={FAIL_LINES} />
      <CodeBlock lines={FAILS_SCRIPT} x={60} y={130} w={1160} size={19} line={32} band={band} opacity={fadeAt(frame, 0)} />
      <Terminal x={60} y={330} w={1160} h={180} lines={FAILS_OUT} size={19} line={40} streams opacity={fadeAt(frame, 0.8)} />
      <div
        style={{
          position: 'absolute', left: 64, top: 330 + 34 - 2, width: 1152, height: 44, boxSizing: 'border-box', border: `3px solid ${LOOK}`, borderRadius: 10,
          opacity: between(frame, 6.4, 11.0),
        }}
      />
      <Label x={640} y={560} size={21} color={MUTED} center opacity={fadeAt(frame, 7.0)}>
        so a script can print a report, or fail a check, after the run
      </Label>
    </AbsoluteFill>
  );
};

// The outro.

const OUTRO_LINES: Lines = [[0, 'Run the program with its handlers, then script against the run.']];
const OUTRO = f(10);
const CHIPS = ['doeff run --program …', 'program · value · interpreter', 'your script'];

const OutroScene: React.FC = () => {
  const frame = useSceneFrame();
  return (
    <AbsoluteFill>
      <Headlines lines={OUTRO_LINES} />
      <div style={{position: 'absolute', left: 0, right: 0, top: 160 + mix(16, 0, ramp(frame, 0.2, 0.8)), textAlign: 'center', opacity: fadeAt(frame, 0.2, 0.5)}}>
        <div style={{fontFamily: SANS, fontSize: 64, fontWeight: 700, color: TEXT}}>doeff</div>
        <div style={{fontFamily: SANS, fontSize: 30, color: ACCENT, marginTop: 4}}>Algebraic effects for Python</div>
      </div>
      {CHIPS.map((chip, i) => (
        <React.Fragment key={chip}>
          <div
            style={{
              position: 'absolute', left: 90 + i * 380, top: 400, width: 340, height: 60, boxSizing: 'border-box', borderRadius: 12, border: `2px solid ${[ACCENT, TEST, LOOK][i]}`,
              background: PANEL, fontFamily: MONO, fontSize: 19, fontWeight: 700, color: [ACCENT, TEST, LOOK][i], lineHeight: '56px', textAlign: 'center', opacity: fadeAt(frame, 1.0 + i * 0.5),
            }}
          >
            {chip}
          </div>
          {i < 2 && <div style={{position: 'absolute', left: 90 + i * 380 + 346, top: 428, width: 28, height: 4, background: MUTED, opacity: fadeAt(frame, 1.3 + i * 0.5)}} />}
        </React.Fragment>
      ))}
      <Label x={640} y={510} size={22} color={TEXT} center opacity={fadeAt(frame, 3.6)}>
        There, the program is a value you can run again, under any handlers.
      </Label>
    </AbsoluteFill>
  );
};

// The scenes in order; Root.tsx registers CLI_SCRIPT_EXECUTION.length frames of CLI_SCRIPT_EXECUTION.Video.
export const CLI_SCRIPT_EXECUTION = narrate('cli-script-execution', DURATIONS, [
  {length: TITLE, starts: startsOf(TITLE_LINES), ids: ['t1', 't2', 't3'], node: <TitleScene />},
  {length: DISCOVER, starts: startsOf(DISCOVER_LINES), ids: ['d1', 'd2', 'd3'], node: <DiscoverScene />},
  {length: RUN, starts: startsOf(RUN_LINES), ids: ['r1', 'r2', 'r3'], node: <RunScene />},
  {length: INJECT, starts: startsOf(INJECT_LINES), ids: ['i1', 'i2', 'i3', 'i4'], node: <InjectScene />},
  {length: AGAIN, starts: startsOf(AGAIN_LINES), ids: ['a1', 'a2', 'a3'], node: <AgainScene />},
  {length: HANDLE, starts: startsOf(HANDLE_LINES), ids: ['h1', 'h2', 'h3', 'h4'], node: <HandleScene />},
  {length: FAILS, starts: startsOf(FAIL_LINES), ids: ['e1', 'e2'], node: <FailScene />},
  {length: OUTRO, starts: [0], ids: ['z1'], node: <OutroScene />},
]);
