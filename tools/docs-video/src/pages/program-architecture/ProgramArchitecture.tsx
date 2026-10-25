// The video on the Program Architecture page (docs/program-architecture-overview.md): how a doeff program is built
// and run, in the points of the page that hold for doeff today. Seven scenes, each with headlines a synthetic voice
// reads (narration.json):
//   1. A program is a value of control instructions the Rust VM evaluates; an effect is data, dispatched at Perform.
//   2. Calling a @do function builds a program; yield Ask is lowered to yield Perform(Ask); run returns the value.
//   3. Handler installers nest, h0(h1(h2(program()))), and dispatch goes from the innermost outward.
//   4. A program that yields a program calls it; the VM keeps the calls as frames, which the doeff traceback lists.
//   5. WithObserve(observer, body) sees each effect dispatched in its body; run has no trace flag.
//   6. run(scheduled(program)): the scheduler is a handler and interleaves tasks in the VM's one step loop.
//   7. The outro.
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
// True between two seconds of a scene.
const during = (frame: number, from: number, to: number) => frame >= f(from) && frame < f(to);

// A label in the scenes' sans font.
const Label: React.FC<{x: number; y: number; size?: number; color?: string; bold?: boolean; opacity?: number; children: React.ReactNode; center?: boolean; w?: number; mono?: boolean}> = ({
  x, y, size = 20, color = MUTED, bold = false, opacity = 1, children, center = false, w, mono = false,
}) => (
  <div
    style={{
      position: 'absolute', left: x, top: y, width: w, fontFamily: mono ? MONO : SANS, fontSize: size, fontWeight: bold ? 700 : 400, color, opacity,
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

// The height of a code box of n lines.
const codeH = (n: number, line: number) => PAD * 2 + n * line;

// Scene 1 — a program is a value of control instructions; an effect is data, dispatched at Perform.

const TITLE_LINES: Lines = [
  [0, 'Program Architecture: how a doeff program is built, and how it runs.'],
  [5.0, 'A program is a value: a DoExpr of control instructions the Rust VM evaluates.'],
  [14.6, 'An effect is plain data. It is dispatched at one place only: Perform.'],
];
const TITLE = f(21);
const NODES = ['Pure', 'Call', 'Resume', 'Pass', 'Transfer', 'WithHandler', 'Perform', 'WithObserve'];
const PROG = {x: 80, y: 290, w: 700, h: 290};
const chip = (i: number) => ({x: PROG.x + 26 + (i % 4) * 164, y: PROG.y + 104 + Math.floor(i / 4) * 64, w: 150, h: 46});
const EFF = {x: 840, y: 290, w: 360, h: 290};
const PERFORM = chip(6);

const TitleScene: React.FC = () => {
  const frame = useSceneFrame();
  const fly = ramp(frame, 16.4, 17.4);
  const performOn = frame >= f(17.4);
  return (
    <AbsoluteFill>
      <Headlines lines={TITLE_LINES} />
      <div style={{position: 'absolute', left: 0, right: 0, top: 110 + mix(16, 0, ramp(frame, 0.2, 0.8)), textAlign: 'center', opacity: fadeAt(frame, 0.2, 0.5)}}>
        <div style={{fontFamily: SANS, fontSize: 68, fontWeight: 700, color: TEXT}}>Program Architecture</div>
        <div style={{fontFamily: SANS, fontSize: 26, color: ACCENT, marginTop: 4}}>programs, effects, handlers and the VM</div>
      </div>
      <Box x={PROG.x} y={PROG.y} w={PROG.w} h={PROG.h} color={ACCENT} opacity={fadeAt(frame, 5.4, 0.5)}>
        <div style={{position: 'absolute', left: 24, top: 14, fontFamily: MONO, fontSize: 25, fontWeight: 700, color: ACCENT}}>Program[T] = DoExpr[T]</div>
        <div style={{position: 'absolute', left: 24, top: 54, fontFamily: SANS, fontSize: 18, color: MUTED}}>control instructions (DoCtrl), and only data until run</div>
      </Box>
      {NODES.map((name, i) => {
        const c = chip(i);
        const on = i === 6 && performOn;
        return (
          <div
            key={name}
            style={{
              position: 'absolute', left: c.x, top: c.y, width: c.w, height: c.h, boxSizing: 'border-box', borderRadius: 10, background: '#0b0d13',
              border: `2px solid ${on ? LOOK : '#3a3f58'}`, boxShadow: on ? `0 0 22px ${LOOK}aa` : 'none', fontFamily: MONO, fontSize: 17, fontWeight: 700,
              color: on ? LOOK : TEXT, lineHeight: `${c.h - 4}px`, textAlign: 'center', opacity: fadeAt(frame, 6.2 + i * 0.25),
            }}
          >
            {name}
          </div>
        );
      })}
      <Box x={PROG.x} y={600} w={PROG.w} h={60} color={TEST} on={during(frame, 10.6, 11.6)} opacity={fadeAt(frame, 10.0, 0.5)}>
        <div style={{position: 'absolute', left: 0, right: 0, top: 15, textAlign: 'center', fontFamily: SANS, fontSize: 20, fontWeight: 700, color: TEST}}>
          the Rust VM evaluates them, one step at a time
        </div>
      </Box>
      <Box x={EFF.x} y={EFF.y} w={EFF.w} h={EFF.h} color={ACCENT} dashed opacity={fadeAt(frame, 14.8, 0.5)}>
        <div style={{position: 'absolute', left: 22, top: 14, fontFamily: SANS, fontSize: 25, fontWeight: 700, color: ACCENT}}>EffectValue</div>
        <div style={{position: 'absolute', left: 22, top: 56, fontFamily: SANS, fontSize: 18, lineHeight: '26px', color: MUTED}}>
          <div>plain data: it names an</div>
          <div>operation, and does nothing</div>
          <div>by itself</div>
        </div>
      </Box>
      <Pill
        x={mix(EFF.x + EFF.w / 2, PERFORM.x + PERFORM.w / 2, fly)}
        y={mix(EFF.y + 220, PERFORM.y + PERFORM.h + 30, fly)}
        text={'Ask("key")'}
        color={ACCENT}
        opacity={fadeAt(frame, 15.2)}
      />
      <Label x={EFF.x} y={600} w={EFF.w} size={19} bold color={LOOK} center opacity={fadeAt(frame, 17.8)}>
        Perform(effect): the one place
      </Label>
      <Label x={EFF.x} y={628} w={EFF.w} size={19} bold color={LOOK} center opacity={fadeAt(frame, 17.8)}>
        an effect is dispatched
      </Label>
    </AbsoluteFill>
  );
};

// Scene 2 — a program and an effect are values; yield Ask is lowered to yield Perform(Ask) (measure/data.py).

const DATA_LINES: Lines = [
  [0, 'Calling a @do function runs nothing. It returns a program value.'],
  [6.7, 'Ask("key") is a value too. It names an operation and does nothing by itself.'],
  [14.7, 'yield Ask(...) is lowered to yield Perform(Ask(...)). Both get "ada".'],
  [22.7, 'run takes that one program and returns its raw value.'],
];
const DATA = f(28.5);
const SHORT = ['@do', 'def short():', '    RAN.append("short")', '    value = yield Ask("key")', '    return value'];
const LOWERED = ['@do', 'def lowered():', '    RAN.append("lowered")', '    value = yield Perform(Ask("key"))', '    return value'];
const D = {y: 120, w: 560, size: 19, line: 30, left: 60, right: 660};
const READER = {x: 60, y: 512, w: 1160, h: 64};

const DataScene: React.FC = () => {
  const frame = useSceneFrame();
  const band = frame >= f(15.6) && frame < f(19.0) ? 3 : null;
  const yLine = lineMid(D.y, D.line, 3);
  const down = ramp(frame, 16.0, 17.0);
  const up = ramp(frame, 17.6, 18.6);
  const toReader = READER.y + READER.h / 2;
  return (
    <AbsoluteFill>
      <Headlines lines={DATA_LINES} />
      <CodeBlock lines={SHORT} x={D.left} y={D.y} w={D.w} size={D.size} line={D.line} band={band} opacity={fadeAt(frame, 0.2)} />
      <Box x={D.left} y={318} w={D.w} h={150} color={ACCENT} opacity={between(frame, 1.0, 14.4)}>
        <div style={{position: 'absolute', left: 22, top: 16, fontFamily: MONO, fontSize: 20, lineHeight: '40px', color: TEXT}}>
          <div style={{opacity: fadeAt(frame, 1.2)}}>program = short()</div>
          <div style={{opacity: fadeAt(frame, 2.4)}}>
            isinstance(program, DoExpr) <span style={{color: MUTED}}>→</span> <span style={{color: ACCENT}}>True</span>
          </div>
          <div style={{opacity: fadeAt(frame, 3.6)}}>
            RAN <span style={{color: MUTED}}>→</span> <span style={{color: LOOK}}>[]</span>
            <span style={{fontFamily: SANS, fontSize: 18, color: MUTED, marginLeft: 16}}>the body has not run</span>
          </div>
        </div>
      </Box>
      <Box x={D.right} y={D.y} w={D.w} h={200} color={ACCENT} dashed opacity={between(frame, 7.0, 14.4)}>
        <div style={{position: 'absolute', left: 22, top: 16, fontFamily: MONO, fontSize: 21, color: TEXT}}>effect = Ask("key")</div>
        <div style={{position: 'absolute', left: 22, top: 56, fontFamily: MONO, fontSize: 21, color: TEXT, opacity: fadeAt(frame, 8.2)}}>
          effect <span style={{color: MUTED}}>→</span> <span style={{color: ACCENT}}>Ask('key')</span>
        </div>
        <div style={{position: 'absolute', left: 22, top: 104, fontFamily: SANS, fontSize: 18, lineHeight: '26px', color: MUTED, opacity: fadeAt(frame, 9.4)}}>
          <div>a value that names an operation;</div>
          <div>nothing happens until a program yields it</div>
        </div>
      </Box>
      <Pill x={D.right + D.w / 2} y={390} text={'Ask("key")'} color={ACCENT} opacity={between(frame, 8.0, 14.4)} />
      <CodeBlock lines={LOWERED} x={D.right} y={D.y} w={D.w} size={D.size} line={D.line} band={band} opacity={fadeAt(frame, 14.8)} />
      <Box x={READER.x} y={READER.y} w={READER.w} h={READER.h} color={TEST} on={during(frame, 17.0, 17.6)} opacity={fadeAt(frame, 15.2)}>
        <div style={{position: 'absolute', left: 0, right: 0, top: 16, textAlign: 'center', fontFamily: MONO, fontSize: 21, fontWeight: 700, color: TEST}}>
          {'reader(env={"key": "ada"})'}
          <span style={{fontFamily: SANS, fontWeight: 400, color: MUTED, marginLeft: 18}}>answers Ask</span>
        </div>
      </Box>
      {during(frame, 16.0, 17.3) && (
        <>
          <Pill x={mix(D.left + D.w - 70, 340, down)} y={mix(yLine, toReader, down)} text={'Ask("key")'} color={ACCENT} opacity={1 - ramp(frame, 17.0, 17.3)} />
          <Pill x={mix(D.right + D.w - 70, 940, down)} y={mix(yLine, toReader, down)} text={'Perform(Ask("key"))'} color={ACCENT} opacity={1 - ramp(frame, 17.0, 17.3)} />
        </>
      )}
      {during(frame, 17.6, 18.9) && (
        <>
          <Pill x={mix(340, D.left + D.w - 70, up)} y={mix(toReader, yLine, up)} text={'"ada"'} color={TEST} opacity={1 - ramp(frame, 18.6, 18.9)} />
          <Pill x={mix(940, D.right + D.w - 70, up)} y={mix(toReader, yLine, up)} text={'"ada"'} color={TEST} opacity={1 - ramp(frame, 18.6, 18.9)} />
        </>
      )}
      <Label x={D.left} y={D.y + codeH(SHORT.length, D.line) + 8} mono size={18} color={TEXT} opacity={fadeAt(frame, 19.0)}>
        run(env(short())) <span style={{color: MUTED}}>→</span> <span style={{color: TEST}}>'ada'</span>
      </Label>
      <Label x={D.right} y={D.y + codeH(LOWERED.length, D.line) + 8} mono size={18} color={TEXT} opacity={fadeAt(frame, 19.0)}>
        run(env(lowered())) <span style={{color: MUTED}}>→</span> <span style={{color: TEST}}>'ada'</span>
      </Label>
      <div style={{position: 'absolute', left: 0, right: 0, top: 372, textAlign: 'center', fontFamily: MONO, fontSize: 24, color: TEXT, opacity: fadeAt(frame, 19.6)}}>
        yield Ask("key") <span style={{color: MUTED}}>≡</span> yield <span style={{color: LOOK}}>Perform</span>(Ask("key"))
      </div>
      <Label x={640} y={418} size={19} center opacity={fadeAt(frame, 20.2)}>
        the VM wraps a bare effect in Perform before it dispatches it
      </Label>
      <div style={{position: 'absolute', left: 0, right: 0, top: 612, textAlign: 'center', fontFamily: MONO, fontSize: 23, fontWeight: 700, color: ACCENT, opacity: fadeAt(frame, 23.0)}}>
        {'run(reader(env={"key": "ada"})(short()))  →  \'ada\''}
      </div>
      <Label x={640} y={656} size={19} center opacity={fadeAt(frame, 23.8)}>
        one program in, its raw value out
      </Label>
    </AbsoluteFill>
  );
};

// Scene 3 — handler installers nest, and dispatch goes from the innermost outward (measure/stack.py).

const STACK_LINES: Lines = [
  [0, 'Handler installers map a program to a program. Calling them nests handlers.'],
  [7, 'h2 is innermost and sees every effect first. It answers Greet.'],
  [12.8, 'Lookup is not for h2. It passes outward, and h1 answers it.'],
  [18.4, 'Save passes h2 and h1 to reach h0, the outermost. Dispatch goes inside out.'],
];
const STACK = f(28.5);
const PROGRAM = ['@do', 'def program():', '    a = yield Greet("bob")', '    b = yield Lookup("user")', '    c = yield Save("x")', '    return (a, b, c)'];
const ST = {cx: 470, cy: 430, w: 420, size: 19, line: 30, margin: 48};
const stH = codeH(PROGRAM.length, ST.line);
const stCode = {x: ST.cx - ST.w / 2, y: ST.cy - stH / 2};
const layer = (i: number) => {
  const lw = ST.w + 2 * ST.margin * (i + 1);
  const lh = stH + 2 * ST.margin * (i + 1);
  return {x: ST.cx - lw / 2, y: ST.cy - lh / 2, w: lw, h: lh};
};
const LAYERS = [
  {name: 'h2', answers: 'answers Greet'},
  {name: 'h1', answers: 'answers Lookup'},
  {name: 'h0', answers: 'answers Save'},
];
// The run's log (measure/stack.log) and the second each line appears.
const STACK_LOG: [number, string][] = [
  [7.6, 'program yields Greet'],
  [8.4, '  h2 answers Greet'],
  [13.3, 'program yields Lookup'],
  [14.0, '  h2 passes Lookup'],
  [14.9, '  h1 answers Lookup'],
  [18.9, 'program yields Save'],
  [19.5, '  h2 passes Save'],
  [20.3, '  h1 passes Save'],
  [21.1, '  h0 answers Save'],
  [23.6, "result: ('hi bob', 'ada', 'saved')"],
];
// Each effect: its line, the times it reaches each layer edge (as far as it goes), and its answer.
const TRIPS = [
  {line: 2, text: 'Greet("bob")', reach: [[7.6, 8.4]], answer: '"hi bob"', back: [9.2, 10.0]},
  {line: 3, text: 'Lookup("user")', reach: [[13.3, 14.0], [14.2, 14.9]], answer: '"ada"', back: [15.6, 16.6]},
  {line: 4, text: 'Save("x")', reach: [[18.9, 19.5], [19.7, 20.3], [20.5, 21.1]], answer: '"saved"', back: [21.8, 23.0]},
];

const StackScene: React.FC = () => {
  const frame = useSceneFrame();
  const startX = stCode.x + ST.w;
  const edge = (i: number) => ST.cx + layer(i).w / 2;
  const band = frame < f(7) ? null : frame < f(12.8) ? 2 : frame < f(18.4) ? 3 : frame < f(23.3) ? 4 : 5;
  const answering = [during(frame, 8.4, 9.2), during(frame, 14.9, 15.6), during(frame, 21.1, 21.8)];
  return (
    <AbsoluteFill>
      <Headlines lines={STACK_LINES} />
      <div style={{position: 'absolute', left: layer(2).x, width: layer(2).w, top: 108, textAlign: 'center', fontFamily: MONO, fontSize: 24, fontWeight: 700, color: TEXT, opacity: fadeAt(frame, 0.4)}}>
        run(<span style={{color: TEST}}>h0</span>(<span style={{color: TEST}}>h1</span>(<span style={{color: TEST}}>h2</span>(<span style={{color: ACCENT}}>program()</span>))))
      </div>
      {LAYERS.map((l, i) => {
        const r = layer(i);
        const on = answering[i];
        return (
          <div
            key={l.name}
            style={{
              position: 'absolute', left: r.x, top: r.y, width: r.w, height: r.h, boxSizing: 'border-box', borderRadius: 22,
              border: `3px solid ${TEST}`, ...glow(on, TEST), opacity: fadeAt(frame, 1.2 + i * 0.7, 0.5),
            }}
          >
            <div style={{position: 'absolute', left: 16, top: 9, fontFamily: MONO, fontSize: 18, fontWeight: 700, color: on ? LOOK : TEST, whiteSpace: 'nowrap'}}>
              {l.name}
              <span style={{fontFamily: SANS, fontWeight: 400, fontSize: 16, marginLeft: 12, color: MUTED}}>{l.answers}</span>
            </div>
          </div>
        );
      })}
      <Label x={layer(0).x + 16} y={layer(0).y + layer(0).h - 30} size={15} opacity={between(frame, 3.6, 7.0)}>innermost: sees effects first</Label>
      <Label x={layer(2).x + 16} y={layer(2).y + layer(2).h - 30} size={15} opacity={between(frame, 4.2, 7.0)}>outermost: sees what was passed out</Label>
      <CodeBlock lines={PROGRAM} x={stCode.x} y={stCode.y} w={ST.w} size={ST.size} line={ST.line} band={band} opacity={fadeAt(frame, 0.2)} />
      {TRIPS.map((trip) => {
        const y = lineMid(stCode.y, ST.line, trip.line);
        const first = trip.reach[0][0];
        const last = trip.reach[trip.reach.length - 1][1];
        let x = startX;
        trip.reach.forEach(([from, to], i) => {
          if (frame >= f(from)) x = mix(i === 0 ? startX : edge(i - 1), edge(i), ramp(frame, from, to));
        });
        const far = edge(trip.reach.length - 1);
        return (
          <React.Fragment key={trip.text}>
            {during(frame, first, last + 0.3) && <Pill x={x} y={y} text={trip.text} color={ACCENT} opacity={1 - ramp(frame, last, last + 0.3)} />}
            {during(frame, trip.back[0], trip.back[1] + 0.3) && (
              <Pill x={mix(far, startX, ramp(frame, trip.back[0], trip.back[1]))} y={y} text={trip.answer} color={TEST} opacity={1 - ramp(frame, trip.back[1], trip.back[1] + 0.3)} />
            )}
          </React.Fragment>
        );
      })}
      <Box x={layer(2).x + layer(2).w + 24} y={layer(2).y} w={1220 - (layer(2).x + layer(2).w + 24)} h={layer(2).h} color={'#3a3f58'} opacity={fadeAt(frame, 7.2, 0.5)}>
        <div style={{position: 'absolute', left: 16, top: 12, fontFamily: SANS, fontSize: 16, fontWeight: 700, color: MUTED}}>the run prints</div>
        <div style={{position: 'absolute', left: 16, top: 44, fontFamily: MONO, fontSize: 14, lineHeight: '30px', color: TEXT}}>
          {STACK_LOG.map(([at, text]) => (
            <div key={text} style={{whiteSpace: 'pre', opacity: fadeAt(frame, at), color: text.includes('answers') ? TEST : text.startsWith('result') ? ACCENT : text.includes('passes') ? MUTED : TEXT}}>
              {text}
            </div>
          ))}
        </div>
      </Box>
    </AbsoluteFill>
  );
};

// Scene 4 — a program that yields a program calls it; the doeff traceback lists the calls (measure/callstack.py).

const CALLS_LINES: Lines = [
  [0, 'The VM steps the program. A program that yields a program calls it.'],
  [6.8, 'Each call becomes a frame in the VM, with its function, file and line.'],
  [12.6, 'When charge raises, the doeff traceback lists each call and its line.'],
];
const CALLS = f(23);
const CK = {x: 60, w: 540, size: 17, line: 26};
const FUNCS = [
  {name: 'checkout', y: 112, code: ['@do', 'def checkout():', '    paid = yield pay(120)', '    return paid'], at: 2, frame: 'checkout()  callstack.py:13'},
  {name: 'pay', y: 262, code: ['@do', 'def pay(amount):', '    return (yield charge(amount))'], at: 2, frame: 'pay()  callstack.py:19'},
  {
    name: 'charge', y: 386,
    code: ['@do', 'def charge(amount):', '    limit = yield Ask("limit")', '    if amount > limit:', '        raise ValueError(f"{amount} > {limit}")', '    return amount'],
    at: 2, frame: 'charge()  callstack.py:26',
  },
];
// When each function starts running, and the second its frame is shown on the VM's stack.
const STARTS = [1.0, 3.6, 5.2];
const PUSHES = [7.4, 8.2, 9.0];
const VMBOX = {x: 640, y: 112, w: 580, h: 270};
// The doeff traceback the run prints (measure/callstack.log), less its handlers lines.
const TRACE = [
  'doeff Traceback (most recent call last):',
  '  checkout()  callstack.py:13',
  '    paid = yield pay(120)',
  '  pay()  callstack.py:19',
  '    return (yield charge(amount))',
  '  charge()  callstack.py:26',
  '    raise ValueError(f"{amount} > {limit}")',
  'ValueError: 120 > 100',
];

const CallsScene: React.FC = () => {
  const frame = useSceneFrame();
  const raising = frame >= f(13.2);
  const bandOf = (i: number) => {
    if (i === 2 && frame >= f(13.0)) return 4;
    return frame >= f(STARTS[i] + 0.6) ? FUNCS[i].at : null;
  };
  return (
    <AbsoluteFill>
      <Headlines lines={CALLS_LINES} />
      {FUNCS.map((fn, i) => (
        <React.Fragment key={fn.name}>
          <CodeBlock lines={fn.code} x={CK.x} y={fn.y} w={CK.w} size={CK.size} line={CK.line} band={bandOf(i)} opacity={fadeAt(frame, STARTS[i] - 0.6)} />
          {i === 2 && (
            <div
              style={{
                position: 'absolute', left: CK.x, top: fn.y, width: CK.w, height: codeH(fn.code.length, CK.line), boxSizing: 'border-box', borderRadius: 14,
                border: `3px solid ${FAIL}`, boxShadow: `0 0 22px ${FAIL}aa`, opacity: fadeAt(frame, 13.2),
              }}
            />
          )}
        </React.Fragment>
      ))}
      {[0, 1].map((i) => {
        const from = STARTS[i] + 0.8;
        const to = STARTS[i + 1] - 0.2;
        const y0 = lineMid(FUNCS[i].y, CK.line, FUNCS[i].at);
        const y1 = FUNCS[i + 1].y + 14;
        return during(frame, from, to + 0.3) ? (
          <Pill key={i} x={CK.x + CK.w - 110} y={mix(y0, y1, ramp(frame, from, to))} text={i === 0 ? 'pay(120)' : 'charge(120)'} color={ACCENT} opacity={1 - ramp(frame, to, to + 0.3)} />
        ) : null;
      })}
      <Box x={VMBOX.x} y={VMBOX.y} w={VMBOX.w} h={VMBOX.h} color={TEST} opacity={fadeAt(frame, 6.9, 0.5)}>
        <div style={{position: 'absolute', left: 20, top: 12, fontFamily: SANS, fontSize: 20, fontWeight: 700, color: TEST}}>the VM: one frame per call</div>
      </Box>
      {FUNCS.map((fn, i) => {
        const on = (during(frame, PUSHES[i], PUSHES[i] + 0.8)) || (i === 2 && raising);
        return (
          <div
            key={fn.name}
            style={{
              position: 'absolute', left: VMBOX.x + 24 + i * 22, top: VMBOX.y + 56 + i * 66 + mix(-20, 0, ramp(frame, PUSHES[i], PUSHES[i] + 0.4)), width: VMBOX.w - 48 - i * 22,
              height: 54, boxSizing: 'border-box', borderRadius: 10, background: '#0b0d13', border: `2px solid ${i === 2 && raising ? FAIL : on ? LOOK : '#3a3f58'}`,
              boxShadow: on ? `0 0 18px ${i === 2 && raising ? FAIL : LOOK}aa` : 'none', fontFamily: MONO, fontSize: 18, lineHeight: '50px', paddingLeft: 18,
              color: TEXT, opacity: fadeAt(frame, PUSHES[i], 0.4), whiteSpace: 'nowrap',
            }}
          >
            {fn.frame}
          </div>
        );
      })}
      <Pill x={CK.x + CK.w - 130} y={FUNCS[2].y + codeH(6, CK.line) + 26} text={'ValueError("120 > 100")'} color={FAIL} opacity={fadeAt(frame, 13.4)} />
      <Box x={VMBOX.x} y={404} w={VMBOX.w} h={266} color={FAIL} opacity={fadeAt(frame, 13.8, 0.4)}>
        <div style={{position: 'absolute', left: 20, top: 14, fontFamily: MONO, fontSize: 16, lineHeight: '29px', color: TEXT}}>
          {TRACE.map((line, i) => (
            <div
              key={line}
              style={{
                whiteSpace: 'pre', opacity: fadeAt(frame, 14.2 + i * 0.45),
                color: i === 0 ? MUTED : line.startsWith('ValueError') ? FAIL : line.startsWith('    ') ? MUTED : TEXT, fontWeight: line.startsWith('  ') && !line.startsWith('    ') ? 700 : 400,
              }}
            >
              {line}
            </div>
          ))}
        </div>
      </Box>
    </AbsoluteFill>
  );
};

// Scene 5 — WithObserve(observer, body) sees each effect dispatched in its body (measure/observe.py).

const OBSERVE_LINES: Lines = [
  [0, 'To watch the effects, wrap a program in WithObserve(observer, body).'],
  [6, 'The observer gets each effect dispatched in the body: Ask, Get, Put, Tell.'],
  [13.6, 'The answer is still 42. Tell("start") is outside the body, so it is not seen.'],
  [20.2, 'run has no trace flag. Observation composes into the program, like a handler.'],
];
const OBSERVE = f(27);
const BUMP = ['@do', 'def bump():', '    key = yield Ask("key")', '    current = yield Get(key)', '    yield Put(key, current + 1)', '    yield Tell(f"{key} is {current + 1}")', '    return current + 1'];
const WATCHED = ['@do', 'def watched():', '    yield Tell("start")', '    return (yield WithObserve(seen.append, bump()))'];
const OB = {x: 60, w: 620, size: 18, line: 28, bump: 118, watched: 362};
const OBS = {x: 720, y: 118, w: 500, h: 330};
const SEEN = [
  {line: 2, text: "Ask('key')", at: 6.4},
  {line: 3, text: "Get('count')", at: 7.8},
  {line: 4, text: "Put('count', 42)", at: 9.2},
  {line: 5, text: "Tell('count is 42')", at: 10.6},
];

const ObserveScene: React.FC = () => {
  const frame = useSceneFrame();
  const current = SEEN.reduce((found, s, i) => (frame >= f(s.at) ? i : found), -1);
  const bumpBand = frame >= f(6.4) && frame < f(12.6) ? SEEN[current].line : null;
  const watchedBand = frame >= f(15.4) && frame < f(19.6) ? 2 : frame >= f(1.6) && frame < f(6.4) ? 3 : null;
  const rowY = (i: number) => OBS.y + 78 + i * 56;
  return (
    <AbsoluteFill>
      <Headlines lines={OBSERVE_LINES} />
      <CodeBlock lines={BUMP} x={OB.x} y={OB.bump} w={OB.w} size={OB.size} line={OB.line} band={bumpBand} opacity={fadeAt(frame, 0.2)} />
      <div
        style={{
          position: 'absolute', left: OB.x - 8, top: OB.bump - 8, width: OB.w + 16, height: codeH(BUMP.length, OB.line) + 16, boxSizing: 'border-box', borderRadius: 18,
          border: `3px dashed ${LOOK}`, opacity: fadeAt(frame, 2.6),
        }}
      />
      <Label x={OB.x + OB.w - 200} y={OB.bump + 10} size={16} bold color={LOOK} opacity={fadeAt(frame, 2.6)}>
        the body: observed
      </Label>
      <CodeBlock lines={WATCHED} x={OB.x} y={OB.watched} w={OB.w} size={OB.size} line={OB.line} band={watchedBand} opacity={fadeAt(frame, 0.8)} />
      <Label x={OB.x} y={OB.watched + codeH(WATCHED.length, OB.line) + 8} size={17} bold color={MUTED} opacity={between(frame, 15.6, 20.0)}>
        ↑ Tell("start") is before the body: not observed
      </Label>
      <Box x={OBS.x} y={OBS.y} w={OBS.w} h={OBS.h} color={ACCENT} dashed opacity={fadeAt(frame, 3.4, 0.5)}>
        <div style={{position: 'absolute', left: 20, top: 14, fontFamily: SANS, fontSize: 20, fontWeight: 700, color: ACCENT}}>
          the observer: <span style={{fontFamily: MONO}}>seen.append</span>
        </div>
        <div style={{position: 'absolute', left: 20, top: 44, fontFamily: SANS, fontSize: 16, color: MUTED}}>called with each effect dispatched in the body</div>
      </Box>
      {SEEN.map((s, i) => {
        const fly = ramp(frame, s.at + 0.1, s.at + 0.8);
        const from = {x: OB.x + OB.w - 60, y: lineMid(OB.bump, OB.line, s.line)};
        const to = {x: OBS.x + 30, y: rowY(i) + 18};
        return (
          <React.Fragment key={s.text}>
            {during(frame, s.at + 0.1, s.at + 0.9) && <Pill x={mix(from.x, to.x, fly)} y={mix(from.y, to.y, fly)} text={s.text} color={ACCENT} left={fly > 0.5} opacity={1 - ramp(frame, s.at + 0.8, s.at + 0.9)} />}
            <div
              style={{
                position: 'absolute', left: OBS.x + 24, top: rowY(i), width: OBS.w - 48, height: 40, boxSizing: 'border-box', borderRadius: 10, background: '#0b0d13',
                border: `2px solid ${i === current && frame < f(12.6) ? LOOK : '#3a3f58'}`, fontFamily: MONO, fontSize: 18, lineHeight: '36px', paddingLeft: 14,
                color: TEXT, opacity: fadeAt(frame, s.at + 0.8),
              }}
            >
              {s.text}
            </div>
          </React.Fragment>
        );
      })}
      <Box x={OBS.x} y={470} w={OBS.w} h={110} color={TEST} opacity={fadeAt(frame, 13.9, 0.4)}>
        <div style={{position: 'absolute', left: 22, top: 14, fontFamily: MONO, fontSize: 21, lineHeight: '40px', color: TEXT}}>
          <div style={{opacity: fadeAt(frame, 14.0)}}>
            run(... plain())   <span style={{color: MUTED}}>→</span> <span style={{color: TEST}}>42</span>
          </div>
          <div style={{opacity: fadeAt(frame, 14.6)}}>
            run(... watched()) <span style={{color: MUTED}}>→</span> <span style={{color: TEST}}>42</span>
          </div>
        </div>
      </Box>
      <div style={{position: 'absolute', left: 0, right: 0, top: 606, textAlign: 'center', fontFamily: MONO, fontSize: 23, color: TEXT, opacity: fadeAt(frame, 20.6)}}>
        <span style={{color: MUTED, textDecoration: 'line-through'}}>run(program, trace=True)</span>
        <span style={{color: MUTED, margin: '0 22px'}}>→</span>
        <span style={{color: ACCENT, fontWeight: 700}}>run(WithObserve(observer, program))</span>
      </div>
      <Label x={640} y={650} size={19} center opacity={fadeAt(frame, 21.6)}>
        observation is part of the program, wrapped around it like a handler
      </Label>
    </AbsoluteFill>
  );
};

// Scene 6 — run(scheduled(program)): the scheduler is a handler that interleaves tasks (measure/async_boundary.py).

const ASYNC_LINES: Lines = [
  [0, 'Async programs run under the scheduler: run(scheduled(program)).'],
  [6.2, 'The scheduler is a handler: it answers Spawn and Gather, switching at waits.'],
  [14.1, "Two tasks interleave, a1 b1 a2 b2, in the VM's one step loop."],
  [24.3, 'Without scheduled, Spawn finds no handler: the run raises UnhandledEffect.'],
];
const ASYNC = f(31.5);
const WORKER = ['@do', 'def worker(name):', '    for n in (1, 2):', '        yield Step(f"{name}{n}")', '        yield Delay(0)', '    return name'];
const TWO = ['@do', 'def two():', '    a = yield Spawn(worker("a"))', '    b = yield Spawn(worker("b"))', '    return (yield Gather(a, b))'];
const AS = {x: 60, w: 520, size: 18, line: 28, worker: 116, two: 330};
const SCHED = {x: 620, y: 116, w: 600, h: 380};
const LANES = [
  {name: 'task a', y: SCHED.y + 120, spawn: 7.0},
  {name: 'task b', y: SCHED.y + 210, spawn: 8.0},
];
// The order the steps ran in (measure/async_boundary.log) and the second each one runs.
const RUNS = [
  {text: 'a1', lane: 0, at: 14.6},
  {text: 'b1', lane: 1, at: 15.8},
  {text: 'a2', lane: 0, at: 17.0},
  {text: 'b2', lane: 1, at: 18.2},
];
const runX = (i: number) => SCHED.x + 150 + i * 108;

const AsyncScene: React.FC = () => {
  const frame = useSceneFrame();
  const twoBand = frame < f(6.6) ? null : frame < f(7.8) ? 2 : frame < f(9.4) ? 3 : frame < f(24.3) ? 4 : 2;
  const now = RUNS.reduce((found, r, i) => (frame >= f(r.at) ? i : found), -1);
  const workerBand = now >= 0 && frame < f(19.4) ? 3 + ((frame - f(RUNS[now].at)) > f(0.6) ? 1 : 0) : null;
  const failed = frame >= f(24.6);
  return (
    <AbsoluteFill>
      <Headlines lines={ASYNC_LINES} />
      <CodeBlock lines={WORKER} x={AS.x} y={AS.worker} w={AS.w} size={AS.size} line={AS.line} band={workerBand} opacity={fadeAt(frame, 0.2)} />
      <CodeBlock lines={TWO} x={AS.x} y={AS.two} w={AS.w} size={AS.size} line={AS.line} band={twoBand} opacity={fadeAt(frame, 0.6)} />
      <div style={{position: 'absolute', left: AS.x, top: 520, fontFamily: MONO, fontSize: 19, fontWeight: 700, color: TEXT, opacity: between(frame, 1.4, 24.3)}}>
        run(<span style={{color: TEST}}>scheduled</span>(sim_time_handler()(recorded(<span style={{color: ACCENT}}>two()</span>))))
      </div>
      <Box x={SCHED.x} y={SCHED.y} w={SCHED.w} h={SCHED.h} color={TEST} on={during(frame, 9.6, 10.4)} opacity={fadeAt(frame, 2.0, 0.5) * (failed ? 0.25 : 1)}>
        <div style={{position: 'absolute', left: 20, top: 12, fontFamily: MONO, fontSize: 21, fontWeight: 700, color: TEST}}>scheduled(...)</div>
        <div style={{position: 'absolute', left: 20, top: 46, fontFamily: SANS, fontSize: 17, color: MUTED, opacity: fadeAt(frame, 6.6)}}>
          a handler: answers Spawn and Gather, switches at each wait
        </div>
      </Box>
      {LANES.map((lane, i) => (
        <React.Fragment key={lane.name}>
          <Label x={SCHED.x + 24} y={lane.y + 8} size={19} bold color={ACCENT} opacity={fadeAt(frame, lane.spawn + 0.8) * (failed ? 0.25 : 1)}>
            {lane.name}
          </Label>
          <div style={{position: 'absolute', left: SCHED.x + 120, top: lane.y + 20, width: SCHED.w - 150, height: 2, background: '#3a3f58', opacity: fadeAt(frame, lane.spawn + 0.8) * (failed ? 0.25 : 1)}} />
          {during(frame, lane.spawn, lane.spawn + 1.0) && (
            <Pill
              x={mix(AS.x + AS.w - 40, SCHED.x + 70, ramp(frame, lane.spawn, lane.spawn + 0.8))}
              y={mix(lineMid(AS.two, AS.line, 2 + i), lane.y + 20, ramp(frame, lane.spawn, lane.spawn + 0.8))}
              text={`Spawn(worker("${i === 0 ? 'a' : 'b'}"))`}
              color={ACCENT}
              opacity={1 - ramp(frame, lane.spawn + 0.8, lane.spawn + 1.0)}
            />
          )}
        </React.Fragment>
      ))}
      {during(frame, 9.0, 9.9) && (
        <Pill x={mix(AS.x + AS.w - 40, SCHED.x + SCHED.w / 2, ramp(frame, 9.0, 9.7))} y={mix(lineMid(AS.two, AS.line, 4), SCHED.y + 90, ramp(frame, 9.0, 9.7))} text="Gather(a, b)" color={ACCENT} opacity={1 - ramp(frame, 9.7, 9.9)} />
      )}
      <Label x={SCHED.x + 24} y={SCHED.y + 290} size={16} opacity={fadeAt(frame, 11.0) * (failed ? 0.25 : 1)}>
        one thread, one step loop: one step runs at a time
      </Label>
      {RUNS.map((r, i) => {
        const on = i === now && frame < f(19.4);
        return (
          <div
            key={r.text}
            style={{
              position: 'absolute', left: runX(i), top: LANES[r.lane].y, width: 90, height: 42, boxSizing: 'border-box', borderRadius: 10, background: '#0b0d13',
              border: `2px solid ${on ? LOOK : ACCENT}`, boxShadow: on ? `0 0 18px ${LOOK}aa` : 'none', fontFamily: MONO, fontSize: 19, fontWeight: 700, lineHeight: '38px',
              textAlign: 'center', color: on ? LOOK : TEXT, opacity: fadeAt(frame, r.at, 0.25) * (failed ? 0.25 : 1),
            }}
          >
            {r.text}
          </div>
        );
      })}
      {now >= 0 && frame < f(19.4) && (
        <div style={{position: 'absolute', left: runX(now) + 44, top: SCHED.y + 104, width: 2, height: 176, background: LOOK, opacity: 0.8}} />
      )}
      <div style={{position: 'absolute', left: SCHED.x + 24, top: SCHED.y + 320, fontFamily: MONO, fontSize: 20, color: TEXT, opacity: fadeAt(frame, 19.8) * (failed ? 0.25 : 1)}}>
        run(...) <span style={{color: MUTED}}>→</span> <span style={{color: TEST}}>['a', 'b']</span>
        <span style={{color: MUTED, marginLeft: 22}}>order a1 b1 a2 b2</span>
      </div>
      <Box x={60} y={560} w={1160} h={110} color={FAIL} opacity={fadeAt(frame, 24.6, 0.4)}>
        <div style={{position: 'absolute', left: 22, top: 12, fontFamily: MONO, fontSize: 18, lineHeight: '29px', color: TEXT}}>
          <div>
            run(sim_time_handler()(recorded(two())))<span style={{fontFamily: SANS, color: MUTED, marginLeft: 16}}>without scheduled</span>
          </div>
          <div style={{color: FAIL, fontWeight: 700, opacity: fadeAt(frame, 25.4)}}>UnhandledEffect: no handler found for effect: Spawn</div>
          <div style={{color: MUTED, opacity: fadeAt(frame, 26.2)}}>hint: Spawn is handled by scheduled: run(scheduled(prog))</div>
        </div>
      </Box>
      {failed && frame < f(26.0) && <Pill x={mix(AS.x + AS.w - 40, AS.x + AS.w + 40, ramp(frame, 24.6, 25.6))} y={lineMid(AS.two, AS.line, 2)} text={'Spawn(worker("a"))'} color={ACCENT} opacity={1 - ramp(frame, 25.6, 26.0)} />}
    </AbsoluteFill>
  );
};

// The outro.

const OUTRO_LINES: Lines = [[0, 'Programs are values. Effects are data, dispatched at Perform. Handlers nest. The VM steps it all.']];
const OUTRO = f(13);
const POINTS = [
  {head: 'a program', body: 'a value: DoExpr', color: ACCENT},
  {head: 'an effect', body: 'data, sent by Perform', color: ACCENT},
  {head: 'handlers', body: 'nest, inside out', color: TEST},
  {head: 'the VM', body: 'steps it all', color: TEST},
];

const OutroScene: React.FC = () => {
  const frame = useSceneFrame();
  return (
    <AbsoluteFill>
      <Headlines lines={OUTRO_LINES} />
      <div style={{position: 'absolute', left: 0, right: 0, top: 170 + mix(16, 0, ramp(frame, 0.2, 0.8)), textAlign: 'center', opacity: fadeAt(frame, 0.2, 0.5)}}>
        <div style={{fontFamily: SANS, fontSize: 64, fontWeight: 700, color: TEXT}}>doeff</div>
        <div style={{fontFamily: SANS, fontSize: 30, color: ACCENT, marginTop: 4}}>Algebraic effects for Python</div>
      </div>
      {POINTS.map((p, i) => (
        <div
          key={p.head}
          style={{
            position: 'absolute', left: 640 - (POINTS.length * 266 - 20) / 2 + i * 266, top: 390, width: 246, height: 96, boxSizing: 'border-box', borderRadius: 14,
            border: `2px solid ${p.color}`, background: PANEL, textAlign: 'center', opacity: fadeAt(frame, 1.2 + i * 0.5),
          }}
        >
          <div style={{fontFamily: SANS, fontSize: 23, fontWeight: 700, color: p.color, marginTop: 14}}>{p.head}</div>
          <div style={{fontFamily: SANS, fontSize: 18, color: MUTED, marginTop: 8}}>{p.body}</div>
        </div>
      ))}
      <div style={{position: 'absolute', left: 0, right: 0, top: 548, textAlign: 'center', fontFamily: MONO, fontSize: 24, color: TEXT, opacity: fadeAt(frame, 3.8)}}>
        run(<span style={{color: TEST}}>handlers</span>(<span style={{color: ACCENT}}>program</span>)) <span style={{color: MUTED}}>→</span> its value
      </div>
    </AbsoluteFill>
  );
};

// The scenes in order; Root.tsx registers PROGRAM_ARCHITECTURE.length frames of PROGRAM_ARCHITECTURE.Video.
export const PROGRAM_ARCHITECTURE = narrate('program-architecture', DURATIONS, [
  {length: TITLE, starts: startsOf(TITLE_LINES), ids: ['t1', 't2', 't3'], node: <TitleScene />},
  {length: DATA, starts: startsOf(DATA_LINES), ids: ['p1', 'p2', 'p3', 'p4'], node: <DataScene />},
  {length: STACK, starts: startsOf(STACK_LINES), ids: ['s1', 's2', 's3', 's4'], node: <StackScene />},
  {length: CALLS, starts: startsOf(CALLS_LINES), ids: ['c1', 'c2', 'c3'], node: <CallsScene />},
  {length: OBSERVE, starts: startsOf(OBSERVE_LINES), ids: ['o1', 'o2', 'o3', 'o4'], node: <ObserveScene />},
  {length: ASYNC, starts: startsOf(ASYNC_LINES), ids: ['a1', 'a2', 'a3', 'a4'], node: <AsyncScene />},
  {length: OUTRO, starts: [0], ids: ['z1'], node: <OutroScene />},
]);
