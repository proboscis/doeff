// The video on the Kleisli Arrows page (docs/11-kleisli-arrows.md): what @do makes of a function, in motion. Six
// scenes, each with headlines a synthetic voice reads (narration.json):
//   1. A @do function takes plain values and returns a program: calling add_one(41) builds a Call node, run runs it.
//   2. Two phases: the call is built in Python with no handler consulted; the VM runs it, and its effects need handlers.
//   3. Arguments are passed as given: an effect or a program stays a value until something yields it.
//   4. Programs compose by yielding programs inside @do; an effect deep inside travels out to the handler of the run.
//   5. A handler is a @do function too: @handler refuses one without (effect, k) or with a non-effect annotation.
//   6. The outro.
// Every output, order and error on screen is printed by a program in measure/ (its .log is next to it).
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

// The line a code box's band is on: [the second it moves there, the line or null for no band]; each move takes 0.3 s.
const bandAt = (frame: number, steps: [number, number | null][]): number | null => {
  let line: number | null = null;
  for (const [from, to] of steps) {
    if (frame < f(from)) break;
    line = to === null || line === null ? to : mix(line, to, ramp(frame, from, from + 0.3));
  }
  return line;
};

// A label in the scenes' sans font.
const Label: React.FC<{x: number; y: number; size?: number; color?: string; bold?: boolean; opacity?: number; children: React.ReactNode; center?: boolean}> = ({
  x, y, size = 20, color = MUTED, bold = false, opacity = 1, children, center = false,
}) => (
  <div
    style={{
      position: 'absolute', left: x, top: y, fontFamily: SANS, fontSize: size, fontWeight: bold ? 700 : 400, color, opacity,
      whiteSpace: 'pre', transform: center ? 'translateX(-50%)' : undefined,
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

type Point = [number, number];

// An effect, a call or an answer flying from one point to another between two seconds, fading out after it lands.
const Fly: React.FC<{from: Point; to: Point; at: number; until: number; text: string; color: string}> = ({from, to, at, until, text, color}) => {
  const frame = useSceneFrame();
  if (frame < f(at) || frame >= f(until + 0.3)) return null;
  const t = ramp(frame, at, until);
  return <Pill x={mix(from[0], to[0], t)} y={mix(from[1], to[1], t)} text={text} color={color} opacity={fadeAt(frame, at, 0.15) * (1 - ramp(frame, until, until + 0.3))} />;
};

// Scene 1 — a @do function returns a program; run evaluates it (measure/returns.py).

const RETURNS_LINES: Lines = [
  [0, 'Kleisli Arrows: a @do function takes plain values and returns a program.'],
  [6.6, 'Calling add_one(41) runs nothing. It returns a Call node, an Expand.'],
  [14.2, 'run() evaluates the node. Only now does the body run, and 42 comes back.'],
  [21.5, 'The node is a value: run it again, and the body runs again.'],
];
const RETURNS = f(27.5);
const ADD_ONE = ['@do', 'def add_one(x: int):', '    print("  add_one\'s body runs")', '    return x + 1'];
const A = {x: 60, y: 120, w: 540, size: 21, line: 38};
const NODE = {x: 660, y: 120, w: 560, h: 236};
// What the console prints, at the second it prints it (returns.log).
const CONSOLE: [number, string, string][] = [
  [15.0, 'run(call_expr):', TEXT],
  [16.0, "  add_one's body runs", MUTED],
  [17.0, '  -> 42', ACCENT],
  [22.0, 'run(call_expr) again:', TEXT],
  [22.9, "  add_one's body runs", MUTED],
  [23.8, '  -> 42', ACCENT],
];
const CHIPS = [
  {x: 300, text: '41', note: 'a plain value', color: TEXT},
  {x: 640, text: '@do add_one', note: 'a Kleisli arrow', color: ACCENT},
  {x: 980, text: 'Program[int]', note: 'a program', color: TEST},
];

const ReturnsScene: React.FC = () => {
  const frame = useSceneFrame();
  const intro = 1 - fadeAt(frame, 6.2, 0.4);
  const body = fadeAt(frame, 6.6, 0.4);
  const band = bandAt(frame, [[15.2, 2], [16.4, 3], [17.4, null], [22.2, 2], [23.2, 3], [24.2, null]]);
  const running = (frame >= f(15.0) && frame < f(17.4)) || (frame >= f(22.0) && frame < f(24.2));
  const nodeRow = (i: number) => NODE.y + 70 + i * 38;
  return (
    <AbsoluteFill>
      <Headlines lines={RETURNS_LINES} />
      <AbsoluteFill style={{opacity: intro}}>
        <div style={{position: 'absolute', left: 0, right: 0, top: 150 + mix(16, 0, ramp(frame, 0.2, 0.8)), textAlign: 'center', opacity: fadeAt(frame, 0.2, 0.5)}}>
          <div style={{fontFamily: SANS, fontSize: 72, fontWeight: 700, color: TEXT}}>Kleisli Arrows</div>
          <div style={{fontFamily: SANS, fontSize: 28, color: ACCENT, marginTop: 6}}>what @do makes of a function</div>
        </div>
        {CHIPS.map((chip, i) => (
          <React.Fragment key={chip.text}>
            <div
              style={{
                position: 'absolute', left: chip.x, top: 420, transform: 'translateX(-50%)', padding: '10px 22px', borderRadius: 14, background: PANEL,
                border: `3px solid ${chip.color}`, fontFamily: MONO, fontSize: 26, fontWeight: 700, color: chip.color, whiteSpace: 'nowrap', opacity: fadeAt(frame, 1.4 + i * 0.9, 0.4),
              }}
            >
              {chip.text}
            </div>
            <Label x={chip.x} y={490} size={20} center opacity={fadeAt(frame, 1.6 + i * 0.9, 0.4)}>
              {chip.note}
            </Label>
            {i < 2 && (
              <div style={{position: 'absolute', left: [434, 806][i], top: 424, transform: 'translateX(-50%)', fontFamily: SANS, fontSize: 34, color: MUTED, opacity: fadeAt(frame, 1.9 + i * 0.9, 0.4)}}>→</div>
            )}
          </React.Fragment>
        ))}
        <Label x={640} y={570} size={22} color={TEXT} center opacity={fadeAt(frame, 4.0)}>
          a → Program[b]: calling it builds the program; running it is a separate step
        </Label>
      </AbsoluteFill>
      <AbsoluteFill style={{opacity: body}}>
        <CodeBlock lines={ADD_ONE} {...A} band={band} />
        <div style={{position: 'absolute', left: A.x, top: 316, fontFamily: MONO, fontSize: 22, color: TEXT, opacity: fadeAt(frame, 7.0)}}>
          call_expr = add_one(41)
        </div>
        <Box x={NODE.x} y={NODE.y} w={NODE.w} h={NODE.h} color={ACCENT} on={running} opacity={fadeAt(frame, 8.2, 0.4)}>
          <div style={{position: 'absolute', left: 20, top: 12, fontFamily: MONO, fontSize: 26, fontWeight: 700, color: running ? LOOK : ACCENT}}>
            Call<span style={{fontFamily: SANS, fontSize: 19, fontWeight: 400, color: MUTED, marginLeft: 14}}>a node of the program, not its value</span>
          </div>
          {[
            ['type(call_expr).__name__', "'Call'", 8.6],
            ['isinstance(call_expr, Expand)', 'True', 9.2],
            ['call_expr.args', '(41,)', 9.8],
            ['hasattr(call_expr, "map")', 'False', 10.4],
          ].map(([code, value, at], i) => (
            <div key={code as string} style={{position: 'absolute', left: 20, top: nodeRow(i) - NODE.y, fontFamily: MONO, fontSize: 19, color: TEXT, whiteSpace: 'pre', opacity: fadeAt(frame, at as number)}}>
              {code}
              <span style={{color: ACCENT, marginLeft: 14}}>{`# ${value}`}</span>
            </div>
          ))}
        </Box>
        <Fly from={[A.x + 300, 330]} to={[NODE.x + 100, NODE.y + 30]} at={7.4} until={8.2} text="add_one(41)" color={ACCENT} />
        <div style={{position: 'absolute', left: 60, top: 400, width: 1160, height: 286, boxSizing: 'border-box', padding: '12px 22px', background: '#0b0d13', borderRadius: 14, border: '2px solid #2a2e40', opacity: fadeAt(frame, 7.4)}}>
          <div style={{fontFamily: SANS, fontSize: 16, color: MUTED, marginBottom: 6}}>the console</div>
          <div style={{position: 'absolute', left: 22, top: 44, fontFamily: SANS, fontSize: 19, color: MUTED, opacity: between(frame, 7.8, 14.8)}}>nothing printed yet</div>
          {CONSOLE.map(([at, text, color]) => (
            <div key={`${at}`} style={{fontFamily: MONO, fontSize: 21, lineHeight: '38px', whiteSpace: 'pre', color, opacity: fadeAt(frame, at)}}>
              {text}
            </div>
          ))}
        </div>
        <Fly from={[A.x + A.w, lineMid(A.y, A.line, 3)]} to={[150, 400 + 12 + 22 + 2 * 38 + 19]} at={16.5} until={17.1} text="42" color={TEST} />
        <Fly from={[A.x + A.w, lineMid(A.y, A.line, 3)]} to={[150, 400 + 12 + 22 + 5 * 38 + 19]} at={23.3} until={23.9} text="42" color={TEST} />
      </AbsoluteFill>
    </AbsoluteFill>
  );
};

// Scene 2 — building consults no handler; running needs them (measure/expansion.py).

const EXPAND_LINES: Lines = [
  [0, 'Two phases: Python builds the program when you call, and the VM runs it later.'],
  [5.6, 'Building consults no handler. greeting("Hello") is built with no handler anywhere.'],
  [11.3, 'Running it needs one. With no handler, its Ask is an unhandled effect.'],
  [17.9, 'Under reader, the same program runs, and returns "Hello, Ada".'],
  [23.2, 'So a call is a macro, not an effect: an effect would need handlers before the program exists.'],
];
const EXPAND = f(30.5);
const GREETING = ['@do', 'def greeting(greet: str):', '    print("  greeting\'s body runs")', '    name = yield Ask("name")', '    return f"{greet}, {name}"'];
const BUILD = {x: 60, y: 120, w: 560, h: 450};
const RUN = {x: 660, y: 120, w: 560, h: 450};
const GC = {x: 80, y: 172, w: 520, size: 17, line: 28};
const BUILT = {x: 80, y: 410, w: 520, h: 140};
// The program while it runs, in the run panel, and the reader around it.
const RUNNING = {x: 740, y: 260, w: 400, h: 90};
const READER = {x: 700, y: 200, w: 480, h: 190};

const ExpandScene: React.FC = () => {
  const frame = useSceneFrame();
  const band = bandAt(frame, [[12.4, 3], [14.0, null], [18.6, 3], [20.6, 4], [21.2, null]]);
  const into = ramp(frame, 11.6, 12.4);
  const failed = frame >= f(13.6) && frame < f(18.0);
  const runOn = (frame >= f(12.4) && frame < f(13.6)) || (frame >= f(18.6) && frame < f(21.6));
  const reader = fadeAt(frame, 18.0, 0.4);
  const askY = RUNNING.y + RUNNING.h / 2;
  return (
    <AbsoluteFill>
      <Headlines lines={EXPAND_LINES} />
      {[
        {r: BUILD, title: 'call time', about: 'Python builds the program', color: ACCENT, on: frame >= f(6.2) && frame < f(11.3), at: 0.4},
        {r: RUN, title: 'run time', about: 'the VM evaluates it', color: TEST, on: runOn, at: 1.2},
      ].map(({r, title, about, color, on, at}) => (
        <div
          key={title}
          style={{
            position: 'absolute', left: r.x, top: r.y, width: r.w, height: r.h, boxSizing: 'border-box', borderRadius: 18, border: `2px solid ${on ? LOOK : '#2a2e40'}`,
            boxShadow: on ? `0 0 18px ${LOOK}66` : 'none', opacity: fadeAt(frame, at, 0.5),
          }}
        >
          <div style={{position: 'absolute', left: 20, top: 12, fontFamily: SANS, fontSize: 22, fontWeight: 700, color}}>
            {title}
            <span style={{fontWeight: 400, fontSize: 18, color: MUTED, marginLeft: 14}}>{about}</span>
          </div>
        </div>
      ))}
      <CodeBlock lines={GREETING} {...GC} band={band} opacity={fadeAt(frame, 0.8)} />
      <div style={{position: 'absolute', left: GC.x, top: 350, fontFamily: MONO, fontSize: 19, color: TEXT, opacity: fadeAt(frame, 6.0)}}>
        program = greeting("Hello")
      </div>
      <Box x={BUILT.x} y={BUILT.y} w={BUILT.w} h={BUILT.h} color={ACCENT} on={frame >= f(6.6) && frame < f(8.0)} opacity={fadeAt(frame, 6.6, 0.4) * (1 - 0.6 * into)}>
        <div style={{position: 'absolute', left: 18, top: 12, fontFamily: MONO, fontSize: 20, color: TEXT}}>
          Call<span style={{color: MUTED, marginLeft: 12}}>args = ('Hello',)</span>
        </div>
        <div style={{position: 'absolute', left: 18, top: 52, fontFamily: SANS, fontSize: 19, color: TEST, opacity: fadeAt(frame, 7.6)}}>handlers consulted: none</div>
        <div style={{position: 'absolute', left: 18, top: 88, fontFamily: SANS, fontSize: 19, color: MUTED, opacity: fadeAt(frame, 8.6)}}>nothing printed: the body has not run</div>
      </Box>
      {frame >= f(11.6) && (
        <Box
          x={mix(BUILT.x, RUNNING.x, into)}
          y={mix(BUILT.y, RUNNING.y, into)}
          w={mix(BUILT.w, RUNNING.w, into)}
          h={mix(BUILT.h, RUNNING.h, into)}
          color={ACCENT}
          on={runOn}
        >
          <div style={{position: 'absolute', left: 18, top: 14, fontFamily: MONO, fontSize: 19, color: runOn ? LOOK : TEXT, whiteSpace: 'nowrap'}}>greeting("Hello")</div>
          <div style={{position: 'absolute', left: 18, top: 48, fontFamily: MONO, fontSize: 16, color: MUTED, whiteSpace: 'nowrap', opacity: fadeAt(frame, 12.4)}}>
            {"greeting's body runs"}
          </div>
        </Box>
      )}
      <div
        style={{
          position: 'absolute', left: READER.x, top: READER.y, width: READER.w, height: READER.h, boxSizing: 'border-box', borderRadius: 22,
          border: `3px solid ${TEST}`, ...glow(frame >= f(19.4) && frame < f(20.2), TEST), opacity: reader,
        }}
      >
        <div style={{position: 'absolute', left: 16, top: 10, fontFamily: MONO, fontSize: 16, fontWeight: 700, color: TEST, whiteSpace: 'nowrap'}}>
          {'reader(env={"name": "Ada"})'}
          <span style={{fontFamily: SANS, fontWeight: 400, color: MUTED, marginLeft: 12}}>answers Ask</span>
        </div>
      </div>
      {frame >= f(12.8) && frame < f(18.0) && (
        <Pill
          x={mix(RUNNING.x + RUNNING.w, 1130, ramp(frame, 12.8, 13.6))}
          y={askY}
          text={'Ask("name")'}
          color={frame < f(13.6) ? ACCENT : FAIL}
          opacity={1 - ramp(frame, 17.6, 18.0)}
        />
      )}
      <Label x={RUN.x + RUN.w / 2} y={420} size={19} bold color={FAIL} center opacity={failed ? fadeAt(frame, 13.6) * (1 - fadeAt(frame, 17.7)) : 0}>
        {'raises UnhandledEffect:\nunhandled effect: Ask (no handlers in scope)'}
      </Label>
      <Fly from={[RUNNING.x + RUNNING.w, askY]} to={[READER.x + READER.w, askY]} at={18.8} until={19.4} text={'Ask("name")'} color={ACCENT} />
      <Fly from={[READER.x + READER.w, askY]} to={[RUNNING.x + RUNNING.w, askY]} at={20.0} until={20.6} text={'"Ada"'} color={TEST} />
      <div style={{position: 'absolute', left: RUN.x, width: RUN.w, top: 430, textAlign: 'center', fontFamily: MONO, fontSize: 21, fontWeight: 700, color: ACCENT, opacity: fadeAt(frame, 21.0)}}>
        {"→ 'Hello, Ada'"}
      </div>
      <div style={{position: 'absolute', left: RUN.x, width: RUN.w, top: 480, textAlign: 'center', fontFamily: MONO, fontSize: 16, color: TEXT, opacity: fadeAt(frame, 21.4)}}>
        {'run(reader(env={"name": "Ada"})(program))'}
      </div>
      <Label x={640} y={600} size={21} bold color={TEXT} center opacity={fadeAt(frame, 23.6)}>
        calling builds a Call node at once, with no handler asked
      </Label>
      <Label x={640} y={640} size={19} center opacity={fadeAt(frame, 25.0)}>
        an effect is dispatched to handlers, so building a call as one would need them before the call tree exists
      </Label>
    </AbsoluteFill>
  );
};

// Scene 3 — arguments are passed as given (measure/arguments.py).

const ARGS_LINES: Lines = [
  [0, 'Arguments are passed as given. An effect or a program you pass in stays a value.'],
  [6.7, 'echo(Ask("x")) returns the Ask itself, though x is annotated int.'],
  [13.6, 'To use a result, yield it. use_values yields both arguments: it gets 5 and 10.'],
  [21.0, 'The usual style: yield in the caller, then pass the plain value on.'],
];
const ARGS = f(28);
const COL = {y: 290, w: 370, size: 16, line: 26};
const colX = (i: number) => 60 + i * (COL.w + 25);
const ENV = {x: 60, y: 610, w: 1160, h: 60};
const COLUMNS = [
  {code: ['@do', 'def echo(x: int):', '    return x'], run: 'echo(Ask("x"))', answer: "Ask('x')", note: 'the effect itself, not 5', at: 6.7},
  {code: ['@do', 'def use_values(x, raw_program):', '    x_value = yield x', '    y = yield raw_program', '    return f"{x_value}:{y}"'], run: 'call_expr', answer: "'5:10'", note: 'each yielded in the body', at: 13.6},
  {code: ['@do', 'def caller():', '    x = yield Ask("x")', '    return (yield add_one(x))'], run: 'caller()', answer: '6', note: 'add_one gets the int 5', at: 21.0},
];

const ArgsScene: React.FC = () => {
  const frame = useSceneFrame();
  const argsIn = ramp(frame, 1.4, 2.4);
  const envY = ENV.y + ENV.h / 2;
  const right = (i: number) => colX(i) + COL.w;
  const at = (line: number) => lineMid(COL.y, COL.line, line);
  const bands = [
    bandAt(frame, [[7.0, 2], [9.4, null]]),
    bandAt(frame, [[14.0, 2], [16.2, 3], [18.0, 4], [18.8, null]]),
    bandAt(frame, [[21.3, 2], [23.4, 3], [25.4, null]]),
  ];
  const envOn = [[15.0, 15.8], [16.8, 17.0], [22.2, 23.0]].some(([a, b]) => frame >= f(a) && frame < f(b));
  return (
    <AbsoluteFill>
      <Headlines lines={ARGS_LINES} />
      <div style={{position: 'absolute', left: 0, right: 0, top: 120, textAlign: 'center', fontFamily: MONO, fontSize: 24, color: TEXT, opacity: fadeAt(frame, 0.4)}}>
        call_expr = use_values(Ask("x"), Pure(10))
      </div>
      <div style={{position: 'absolute', left: 0, right: 0, top: 172, textAlign: 'center', fontFamily: MONO, fontSize: 24, color: TEXT, opacity: fadeAt(frame, 2.4)}}>
        call_expr.args<span style={{color: ACCENT, marginLeft: 14}}>{"# (Ask('x'), Pure(10))"}</span>
      </div>
      <Pill x={mix(717, 560, argsIn)} y={mix(136, 236, argsIn)} text={'Ask("x")'} color={ACCENT} opacity={between(frame, 1.2, 5.6)} />
      <Pill x={mix(866, 720, argsIn)} y={mix(136, 236, argsIn)} text="Pure(10)" color={ACCENT} opacity={between(frame, 1.2, 5.6)} />
      <Label x={640} y={250} size={19} center opacity={between(frame, 3.0, 6.4)}>
        kept as they were passed: not run, not resolved, not converted
      </Label>
      {COLUMNS.map((col, i) => {
        const now = frame >= f(col.at) && (i === 2 || frame < f(COLUMNS[i + 1].at));
        const shown = fadeAt(frame, col.at, 0.4);
        const bottom = COL.y + PAD * 2 + col.code.length * COL.line;
        return (
          <React.Fragment key={col.run}>
            <div style={{position: 'absolute', left: colX(i) - 6, top: COL.y - 6, width: COL.w + 12, height: bottom - COL.y + 12, borderRadius: 18, ...glow(now, '#0000'), opacity: shown}} />
            <CodeBlock lines={col.code} x={colX(i)} y={COL.y} w={COL.w} size={COL.size} line={COL.line} band={bands[i]} opacity={shown} />
            <div style={{position: 'absolute', left: colX(i) + 4, top: bottom + 12, fontFamily: MONO, fontSize: 15, color: MUTED, whiteSpace: 'nowrap', opacity: shown}}>{`run(with_env(${col.run}))`}</div>
            <div style={{position: 'absolute', left: colX(i) + 4, top: bottom + 38, fontFamily: MONO, fontSize: 22, fontWeight: 700, color: ACCENT, whiteSpace: 'nowrap', opacity: fadeAt(frame, col.at + [2.2, 5.6, 4.4][i])}}>
              {`→ ${col.answer}`}
            </div>
            <Label x={colX(i) + 4} y={bottom + 72} size={17} color={TEXT} opacity={fadeAt(frame, col.at + [3.0, 6.0, 5.0][i])}>
              {col.note}
            </Label>
          </React.Fragment>
        );
      })}
      <Fly from={[right(0) - 60, at(1) - 6]} to={[right(0) - 60, at(2)]} at={7.4} until={8.2} text={'x = Ask("x")'} color={ACCENT} />
      <Fly from={[right(0) - 60, at(2)]} to={[colX(0) + 120, COL.y + PAD * 2 + 3 * COL.line + 50]} at={8.4} until={8.9} text={"Ask('x')"} color={ACCENT} />
      <Fly from={[right(1) - 60, at(2)]} to={[right(1) - 60, envY]} at={14.4} until={15.0} text={'Ask("x")'} color={ACCENT} />
      <Fly from={[right(1) - 60, envY]} to={[right(1) - 60, at(2)]} at={15.4} until={16.0} text="5" color={TEST} />
      <Fly from={[right(1) - 60, at(3)]} to={[right(1) + 90, at(3)]} at={16.4} until={16.9} text="Pure(10)" color={ACCENT} />
      <Fly from={[right(1) + 90, at(3)]} to={[right(1) - 60, at(3)]} at={17.0} until={17.6} text="10" color={TEST} />
      <Label x={right(1) + 90} y={at(3) + 22} size={14} center opacity={between(frame, 16.6, 17.8)}>
        the VM evaluates it
      </Label>
      <Fly from={[right(2) - 60, at(2)]} to={[right(2) - 60, envY]} at={21.6} until={22.2} text={'Ask("x")'} color={ACCENT} />
      <Fly from={[right(2) - 60, envY]} to={[right(2) - 60, at(2)]} at={22.6} until={23.2} text="5" color={TEST} />
      <Fly from={[right(2) - 60, at(3)]} to={[right(2) - 60, at(3) + 60]} at={23.6} until={24.1} text="add_one(5)" color={ACCENT} />
      <Fly from={[right(2) - 60, at(3) + 60]} to={[right(2) - 60, at(3)]} at={24.3} until={24.9} text="6" color={TEST} />
      <Box x={ENV.x} y={ENV.y} w={ENV.w} h={ENV.h} color={TEST} on={envOn} opacity={fadeAt(frame, 6.0)}>
        <div style={{position: 'absolute', left: 20, top: 14, fontFamily: MONO, fontSize: 20, color: envOn ? LOOK : TEST}}>
          {'with_env = reader(env={"x": 5})'}
          <span style={{fontFamily: SANS, color: MUTED, marginLeft: 16}}>answers Ask around each run</span>
        </div>
      </Box>
    </AbsoluteFill>
  );
};

// Scene 4 — programs compose by yielding programs inside @do (measure/nested.py).

const NESTED_LINES: Lines = [
  [0, 'No map, no flat_map, no >>. Programs compose by yielding programs inside @do.'],
  [7.8, 'fetch_user_posts yields fetch_user(7). Its value comes back at that yield.'],
  [14.6, 'The Ask inside fetch_user travels out past both functions, to the reader around the run.'],
  [22.5, 'Then fetch_posts(user) runs the same way, and the outer function returns the posts.'],
];
const NESTED = f(29);
const OUTER = ['@do', 'def fetch_user_posts(user_id: int):', '    user = yield fetch_user(user_id)', '    posts = yield fetch_posts(user)', '    return posts'];
const FETCH_USER = ['@do', 'def fetch_user(user_id: int):', '    prefix = yield Ask("prefix")', '    name = f"{prefix}-{user_id}"', '    user = {"id": user_id, "name": name}', '    return user'];
const FETCH_POSTS = ['@do', 'def fetch_posts(user: dict):', '    return [f"post-for-{user[\'name\']}"]'];
const RING = {x: 40, y: 112, w: 1200, h: 498};
const N = {size: 16, line: 26};
const OB = {x: 70, y: 176, w: 520};
const FU = {x: 640, y: 176, w: 570};
const FP = {x: 640, y: 390, w: 570};
// What the run prints, at the second it prints it (nested.log).
const TRACE: [number, string, string][] = [
  [8.3, 'fetch_user_posts(7) starts', TEXT],
  [9.3, '  fetch_user(7) starts', TEXT],
  [15.2, "  the program yields Ask('prefix')", ACCENT],
  [19.2, "  fetch_user returns {'id': 7, 'name': 'user-7'}", TEXT],
  [23.8, '  fetch_posts(user) starts', TEXT],
  [24.6, "  fetch_posts returns ['post-for-user-7']", TEXT],
  [26.0, "fetch_user_posts returns ['post-for-user-7']", TEXT],
  [26.8, 'effects that got past reader: []', TEST],
];

const NestedScene: React.FC = () => {
  const frame = useSceneFrame();
  const outerBand = bandAt(frame, [[1.0, null], [8.2, 2], [20.4, 3], [25.8, 4], [26.6, null]]);
  const userBand = bandAt(frame, [[9.4, 2], [17.8, 3], [18.2, 4], [18.6, 5], [19.4, null]]);
  const postsBand = bandAt(frame, [[24.0, 2], [24.8, null]]);
  const ringOn = frame >= f(16.2) && frame < f(17.0);
  const userOn = frame >= f(9.2) && frame < f(19.4);
  const postsOn = frame >= f(23.8) && frame < f(25.0);
  const outerRight = OB.x + OB.w;
  return (
    <AbsoluteFill>
      <Headlines lines={NESTED_LINES} />
      <div
        style={{
          position: 'absolute', left: RING.x, top: RING.y, width: RING.w, height: RING.h, boxSizing: 'border-box', borderRadius: 24,
          border: `3px solid ${TEST}`, ...glow(ringOn, TEST), opacity: fadeAt(frame, 0.4, 0.5),
        }}
      >
        <div style={{position: 'absolute', left: 18, top: 12, fontFamily: MONO, fontSize: 17, fontWeight: 700, color: ringOn ? LOOK : TEST, whiteSpace: 'nowrap'}}>
          {'reader(env={"prefix": "user"})'}
          <span style={{fontFamily: SANS, fontWeight: 400, color: MUTED, marginLeft: 12}}>installed once, around the whole run</span>
        </div>
      </div>
      <CodeBlock lines={OUTER} x={OB.x} y={OB.y} w={OB.w} size={N.size} line={N.line} band={outerBand} opacity={fadeAt(frame, 0.8)} />
      <div style={{position: 'absolute', left: FU.x - 6, top: FU.y - 6, width: FU.w + 12, height: PAD * 2 + FETCH_USER.length * N.line + 12, borderRadius: 18, ...glow(userOn, '#0000')}} />
      <CodeBlock lines={FETCH_USER} x={FU.x} y={FU.y} w={FU.w} size={N.size} line={N.line} band={userBand} opacity={fadeAt(frame, 1.4)} />
      <div style={{position: 'absolute', left: FP.x - 6, top: FP.y - 6, width: FP.w + 12, height: PAD * 2 + FETCH_POSTS.length * N.line + 12, borderRadius: 18, ...glow(postsOn, '#0000')}} />
      <CodeBlock lines={FETCH_POSTS} x={FP.x} y={FP.y} w={FP.w} size={N.size} line={N.line} band={postsBand} opacity={fadeAt(frame, 1.8)} />
      <div style={{position: 'absolute', left: OB.x, top: 360, opacity: between(frame, 2.4, 8.0)}}>
        <div style={{display: 'flex', gap: 18}}>
        {['.map()', '.flat_map()', '>>'].map((op, i) => (
          <div
            key={op}
            style={{
              padding: '4px 14px', borderRadius: 10, border: `2px solid ${MUTED}`, fontFamily: MONO, fontSize: 19,
              color: MUTED, textDecoration: 'line-through', whiteSpace: 'nowrap', opacity: fadeAt(frame, 2.6 + i * 0.4),
            }}
          >
            {op}
          </div>
        ))}
        </div>
        <div style={{position: 'absolute', left: 0, top: 52, fontFamily: MONO, fontSize: 17, color: TEXT, whiteSpace: 'nowrap', opacity: fadeAt(frame, 4.0)}}>
          hasattr(fetch_user_posts(7), "map")<span style={{color: ACCENT, marginLeft: 12}}># False</span>
        </div>
        <div style={{position: 'absolute', left: 0, top: 90, fontFamily: SANS, fontSize: 19, fontWeight: 700, color: ACCENT, whiteSpace: 'nowrap', opacity: fadeAt(frame, 5.0)}}>
          compose with yield, inside @do
        </div>
      </div>
      <div style={{position: 'absolute', left: OB.x, top: 360, width: OB.w, height: 230, boxSizing: 'border-box', padding: '10px 18px', background: '#0b0d13', borderRadius: 12, opacity: fadeAt(frame, 8.0)}}>
        <div style={{fontFamily: SANS, fontSize: 15, color: MUTED, marginBottom: 4}}>what the run prints, in order</div>
        {TRACE.map(([at, text, color]) => (
          <div key={text} style={{fontFamily: MONO, fontSize: 15, lineHeight: '23px', whiteSpace: 'pre', color, opacity: fadeAt(frame, at)}}>
            {text}
          </div>
        ))}
      </div>
      <Fly from={[outerRight, lineMid(OB.y, N.line, 2)]} to={[FU.x + 110, FU.y + 8]} at={8.4} until={9.2} text="fetch_user(7)" color={ACCENT} />
      <Fly from={[FU.x + 280, lineMid(FU.y, N.line, 2)]} to={[FU.x + 380, RING.y + 4]} at={15.2} until={16.2} text={'Ask("prefix")'} color={ACCENT} />
      <Fly from={[FU.x + 380, RING.y + 4]} to={[FU.x + 400, lineMid(FU.y, N.line, 2)]} at={16.8} until={17.6} text={'"user"'} color={TEST} />
      <Label x={FU.x + 300} y={RING.y + 46} size={16} bold color={LOOK} center opacity={between(frame, 15.4, 18.0)}>
        {'past fetch_user, past fetch_user_posts'}
      </Label>
      <Fly from={[FU.x, lineMid(FU.y, N.line, 5)]} to={[outerRight - 40, lineMid(OB.y, N.line, 2)]} at={19.0} until={20.2} text={"{'id': 7, 'name': 'user-7'}"} color={TEST} />
      <Fly from={[outerRight, lineMid(OB.y, N.line, 3)]} to={[FP.x + 110, FP.y + 8]} at={22.9} until={23.8} text="fetch_posts(user)" color={ACCENT} />
      <Fly from={[FP.x, lineMid(FP.y, N.line, 2)]} to={[outerRight - 40, lineMid(OB.y, N.line, 3)]} at={24.6} until={25.6} text={"['post-for-user-7']"} color={TEST} />
      <div style={{position: 'absolute', left: 0, right: 0, top: 628, textAlign: 'center', fontFamily: MONO, fontSize: 18, color: TEXT, opacity: fadeAt(frame, 1.2)}}>
        {'run(reader(env={"prefix": "user"})(fetch_user_posts(7)))'}
      </div>
      <div style={{position: 'absolute', left: 0, right: 0, top: 664, textAlign: 'center', fontFamily: MONO, fontSize: 22, fontWeight: 700, color: ACCENT, opacity: fadeAt(frame, 26.6)}}>
        {"→ ['post-for-user-7']"}
      </div>
    </AbsoluteFill>
  );
};

// Scene 5 — a handler is a @do function too, held to its contract when it is built (measure/contract.py).

const CONTRACT_LINES: Lines = [
  [0, 'A handler is a @do function too. @handler makes a def of (effect, k) into one.'],
  [8.0, 'It must take the effect and k. With one parameter, it is refused with a TypeError.'],
  [14.6, 'Its annotation must name an effect class. No effect can be an int, so that one is refused.'],
  [22.2, 'Both are refused when the handler is built, before any program runs. fetcher keeps the contract.'],
];
const CONTRACT = f(31.5);
const H = {y: 200, w: 370, size: 15, line: 26};
const hX = (i: number) => 60 + i * (H.w + 25);
const HANDLERS = [
  {code: ['@handler', 'def no_k(effect: Fetch):', '    return "no k"'], at: 8.0, ok: false, verdict: ['TypeError: called with (effect, k);', 'this one takes 1 positional argument']},
  {code: ['@handler', 'def wrong_type(effect: int, k):', '    return (yield Resume(k, 0))'], at: 14.6, ok: false, verdict: ["TypeError: … names int, which no", 'effect can be an instance of']},
  {code: ['@handler', 'def fetcher(effect: Fetch, k):', '    reply = f"got {effect.url}"', '    return (yield Resume(k, reply))'], at: 22.2, ok: true, verdict: ['built: it keeps the contract', '']},
];
const PHASE = {y: 470, h: 150};
const BUILD_PHASE = {x: 60, w: 540};
const RUN_PHASE = {x: 660, w: 560};
const TYPE_MARK = {x: hX(1) + 24 + 23 * 9.03, w: 3 * 9.03 + 8};

const ContractScene: React.FC = () => {
  const frame = useSceneFrame();
  const current = HANDLERS.reduce((found, h, i) => (frame >= f(h.at) ? i : found), -1);
  const buildOn = [[9.2, 10.4], [15.8, 17.0], [23.0, 23.8]].some(([a, b]) => frame >= f(a) && frame < f(b));
  const runOn = frame >= f(24.8) && frame < f(26.6);
  const stamp = (i: number, at: number) => (
    <div
      key={i}
      style={{
        position: 'absolute', left: BUILD_PHASE.x + 40 + i * 215, top: PHASE.y + 70, padding: '6px 14px', borderRadius: 12, background: FAIL, color: '#0f1117',
        fontFamily: MONO, fontSize: 18, fontWeight: 700, boxShadow: `0 0 14px ${FAIL}`, whiteSpace: 'nowrap', opacity: fadeAt(frame, at),
        transform: `scale(${mix(1.4, 1, ramp(frame, at, at + 0.3))})`,
      }}
    >
      {['no_k', 'wrong_type'][i]}: TypeError
    </div>
  );
  return (
    <AbsoluteFill>
      <Headlines lines={CONTRACT_LINES} />
      <div style={{position: 'absolute', left: 0, right: 0, top: 124, textAlign: 'center', fontFamily: MONO, fontSize: 21, color: TEXT, opacity: fadeAt(frame, 0.6)}}>
        <span style={{color: ACCENT}}>@handler</span> def h(effect, k)
        <span style={{fontFamily: SANS, color: MUTED}}>{'  →  a @do function;  '}</span>h(program)
        <span style={{fontFamily: SANS, color: MUTED}}>{'  installs it'}</span>
      </div>
      {HANDLERS.map((h, i) => {
        const shown = fadeAt(frame, i === 2 ? 1.6 : 1.0 + i * 0.3, 0.4);
        const bottom = H.y + PAD * 2 + h.code.length * H.line;
        const now = current === i;
        return (
          <React.Fragment key={h.code[1]}>
            <div style={{position: 'absolute', left: hX(i) - 6, top: H.y - 6, width: H.w + 12, height: bottom - H.y + 12, borderRadius: 18, ...glow(now, '#0000'), opacity: shown}} />
            <CodeBlock lines={h.code} x={hX(i)} y={H.y} w={H.w} size={H.size} line={H.line} opacity={shown} />
            <div style={{position: 'absolute', left: hX(i) + 4, top: 348, fontFamily: MONO, fontSize: 15, lineHeight: '22px', color: h.ok ? TEST : FAIL, whiteSpace: 'pre', opacity: fadeAt(frame, h.at + (h.ok ? 1.6 : 1.4))}}>
              {h.verdict.join('\n')}
            </div>
          </React.Fragment>
        );
      })}
      <div
        style={{
          position: 'absolute', left: hX(0) + 24 + 8 * 9.03, top: H.y + PAD + H.line - 2, width: 15 * 9.03 + 4, height: H.line + 4, boxSizing: 'border-box',
          border: `3px solid ${LOOK}`, borderRadius: 8, opacity: between(frame, 8.6, 13.8),
        }}
      />
      <Label x={hX(0) + 24 + 25 * 9.03 + 4} y={H.y + PAD + H.line + 2} size={14} bold color={LOOK} opacity={between(frame, 8.8, 13.8)}>
        no k
      </Label>
      <div
        style={{
          position: 'absolute', left: TYPE_MARK.x - 4, top: H.y + PAD + H.line - 2, width: TYPE_MARK.w, height: H.line + 4, boxSizing: 'border-box',
          border: `3px solid ${LOOK}`, borderRadius: 8, opacity: between(frame, 15.0, 21.6),
        }}
      />
      <Box x={BUILD_PHASE.x} y={PHASE.y} w={BUILD_PHASE.w} h={PHASE.h} color={ACCENT} on={buildOn} opacity={fadeAt(frame, 2.0)}>
        <div style={{position: 'absolute', left: 18, top: 12, fontFamily: SANS, fontSize: 20, fontWeight: 700, color: buildOn ? LOOK : ACCENT}}>
          1. @handler builds the handler
        </div>
      </Box>
      <Box x={RUN_PHASE.x} y={PHASE.y} w={RUN_PHASE.w} h={PHASE.h} color={TEST} on={runOn} opacity={fadeAt(frame, 2.4)}>
        <div style={{position: 'absolute', left: 18, top: 12, fontFamily: SANS, fontSize: 20, fontWeight: 700, color: runOn ? LOOK : TEST}}>2. a program runs under it</div>
        <div style={{position: 'absolute', left: 18, top: 48, fontFamily: MONO, fontSize: 16, color: MUTED, whiteSpace: 'nowrap'}}>{'page():  body = yield Fetch("https://example.com")'}</div>
      </Box>
      {stamp(0, 9.6)}
      {stamp(1, 16.2)}
      <Label x={BUILD_PHASE.x + 40} y={PHASE.y + 116} size={16} color={TEXT} opacity={fadeAt(frame, 23.0)}>
        refused here: no program ever starts
      </Label>
      <Fly from={[hX(2) + H.w / 2, H.y + PAD * 2 + 4 * H.line]} to={[BUILD_PHASE.x + BUILD_PHASE.w - 80, PHASE.y + 90]} at={22.8} until={23.4} text="fetcher" color={TEST} />
      <Fly from={[BUILD_PHASE.x + BUILD_PHASE.w - 80, PHASE.y + 90]} to={[RUN_PHASE.x + 110, PHASE.y + 102]} at={23.8} until={24.6} text="fetcher" color={TEST} />
      <Fly from={[RUN_PHASE.x + RUN_PHASE.w - 60, PHASE.y + 58]} to={[RUN_PHASE.x + 240, PHASE.y + 102]} at={24.8} until={25.4} text={'Fetch(url)'} color={ACCENT} />
      <Fly from={[RUN_PHASE.x + 240, PHASE.y + 102]} to={[RUN_PHASE.x + RUN_PHASE.w - 60, PHASE.y + 58]} at={25.6} until={26.2} text={'"got https://example.com"'} color={TEST} />
      <div style={{position: 'absolute', left: 0, right: 0, top: 650, textAlign: 'center', fontFamily: MONO, fontSize: 20, fontWeight: 700, color: ACCENT, opacity: fadeAt(frame, 26.6)}}>
        {"run(fetcher(page())) → 'body=got https://example.com'"}
      </div>
    </AbsoluteFill>
  );
};

// The outro.

const OUTRO_LINES: Lines = [[0, 'Plain values in, a program out. Handlers give its effects their meaning.']];
const OUTRO = f(10);
const FLOW = [
  {text: 'plain values', color: TEXT},
  {text: '@do function', color: ACCENT},
  {text: 'a program', color: ACCENT},
  {text: 'handlers + run', color: TEST},
  {text: 'a value', color: TEXT},
];

const OutroScene: React.FC = () => {
  const frame = useSceneFrame();
  const step = 236;
  return (
    <AbsoluteFill>
      <Headlines lines={OUTRO_LINES} />
      <div style={{position: 'absolute', left: 0, right: 0, top: 170 + mix(16, 0, ramp(frame, 0.2, 0.8)), textAlign: 'center', opacity: fadeAt(frame, 0.2, 0.5)}}>
        <div style={{fontFamily: SANS, fontSize: 64, fontWeight: 700, color: TEXT}}>doeff</div>
        <div style={{fontFamily: SANS, fontSize: 30, color: ACCENT, marginTop: 4}}>Algebraic effects for Python</div>
      </div>
      {FLOW.map((node, i) => (
        <React.Fragment key={node.text}>
          <div
            style={{
              position: 'absolute', left: 640 + (i - 2) * step, top: 420, transform: 'translateX(-50%)', width: 190, height: 56, boxSizing: 'border-box', borderRadius: 12,
              border: `2px solid ${node.color}`, background: PANEL, fontFamily: SANS, fontSize: 20, fontWeight: 700, color: node.color, lineHeight: '52px', textAlign: 'center',
              opacity: fadeAt(frame, 1.2 + i * 0.4),
            }}
          >
            {node.text}
          </div>
          {i < FLOW.length - 1 && (
            <div style={{position: 'absolute', left: 640 + (i - 2) * step + 98, top: 426, width: step - 196, textAlign: 'center', fontFamily: SANS, fontSize: 28, color: MUTED, opacity: fadeAt(frame, 1.4 + i * 0.4)}}>→</div>
          )}
        </React.Fragment>
      ))}
      <Label x={640} y={530} size={22} color={TEXT} center opacity={fadeAt(frame, 3.6)}>
        {'a → Program[b] is a Kleisli arrow; @do is how doeff writes one.'}
      </Label>
    </AbsoluteFill>
  );
};

// The scenes in order; Root.tsx registers KLEISLI_ARROWS.length frames of KLEISLI_ARROWS.Video.
export const KLEISLI_ARROWS = narrate('kleisli-arrows', DURATIONS, [
  {length: RETURNS, starts: startsOf(RETURNS_LINES), ids: ['t1', 't2', 't3', 't4'], node: <ReturnsScene />},
  {length: EXPAND, starts: startsOf(EXPAND_LINES), ids: ['e1', 'e2', 'e3', 'e4', 'e5'], node: <ExpandScene />},
  {length: ARGS, starts: startsOf(ARGS_LINES), ids: ['g1', 'g2', 'g3', 'g4'], node: <ArgsScene />},
  {length: NESTED, starts: startsOf(NESTED_LINES), ids: ['n1', 'n2', 'n3', 'n4'], node: <NestedScene />},
  {length: CONTRACT, starts: startsOf(CONTRACT_LINES), ids: ['h1', 'h2', 'h3', 'h4'], node: <ContractScene />},
  {length: OUTRO, starts: [0], ids: ['z1'], node: <OutroScene />},
]);
