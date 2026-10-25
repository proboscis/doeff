// The video on the Error Handling page (docs/05-error-handling.md): a failure is handled like every other effect.
// Five scenes and an outro, each with headlines a synthetic voice reads (narration.json):
//   1. run() returns the program's value as it is, or raises its exception: no wrapper.
//   2. Ok holds a value, Err holds an exception; they test as true / false and match by pattern.
//   3. Try is an effect: try_handler runs the sub-program, catches the raise, and resumes k with Err.
//   4. The three Try composition guarantees: no rollback, Local restores env, nested Try keeps both layers.
//   5. The exception inside Err keeps its traceback, down to the frame that raised.
// Every value on screen is printed by a program in measure/ (its output is in the .log next to it).
import React from 'react';
import {AbsoluteFill, Easing, interpolate} from 'remotion';
import {clamp, fadeAt, mix, ramp} from '../../kit/motion';
import {narrate} from '../../kit/narrated';
import {CodeBlock, glow, Headlines, lineMid, Lines, PAD, Pill, startsOf} from '../../kit/parts';
import {ACCENT, f, FAIL, LOOK, MONO, MUTED, PANEL, SANS, TEST, TEXT} from '../../kit/theme';
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
const Terminal: React.FC<{x: number; y: number; w: number; lines: {text: string; color?: string}[]; size?: number; line?: number; opacity?: number}> = ({
  x, y, w, lines, size = 17, line = 27, opacity = 1,
}) => (
  <div
    style={{
      position: 'absolute', left: x, top: y, width: w, boxSizing: 'border-box', padding: '10px 16px', background: '#0b0d13', borderRadius: 10,
      border: '2px solid #2a2e40', fontFamily: MONO, fontSize: size, lineHeight: `${line}px`, color: TEXT, opacity,
    }}
  >
    {lines.map((l, i) => (
      <div key={i} style={{whiteSpace: 'pre', color: l.color ?? TEXT, minHeight: line}}>
        {l.text}
      </div>
    ))}
  </div>
);

// Scene 1 — run() gives back the value as it is, or raises the exception as it is.

const RUN_LINES: Lines = [
  [0, 'Error handling in doeff: a failure is handled like everything else.'],
  [7, 'run(...) gives back what the program returns, as it is.'],
  [14.5, 'When the program raises, run(...) raises the same exception. No wrapper.'],
];
const RUN = f(22);
const GREET = ['@do', 'def greet_or_fail():', '    name = yield Ask("name")', '    if not name:', '        raise ValueError("name is empty")', '    return name.upper()'];
// The output of measure/run_raw_or_raise.py when run() raises: doeff's traceback, then the exception.
const RAISED = [
  {text: 'doeff Traceback (most recent call last):', color: MUTED},
  {text: '  greet_or_fail()  run_raw_or_raise.py:11', color: MUTED},
  {text: '    raise ValueError("name is empty")'},
  {text: 'ValueError: name is empty', color: FAIL},
];

const RunScene: React.FC = () => {
  const frame = useSceneFrame();
  const code = {x: 80, y: 190, w: 560, size: 20, line: 34};
  const box = {x: 56, y: 130, w: 608, h: 300};
  const empty = frame >= f(15.0);
  // The band walks the code: name, then return (first run); name, the if, the raise (second run).
  const band =
    frame < f(8.0) ? null
    : frame < f(15.6) ? (frame < f(9.0) ? 2 : mix(2, 5, ramp(frame, 9.0, 9.4)))
    : frame < f(16.6) ? 2 : frame < f(17.4) ? mix(2, 3, ramp(frame, 16.6, 16.9)) : mix(3, 4, ramp(frame, 17.4, 17.7));
  const valueOut = ramp(frame, 9.8, 10.8);
  const raiseOut = ramp(frame, 18.0, 19.0);
  const burst = frame >= f(18.0) && frame < f(19.4);
  const resultY = 180;
  return (
    <AbsoluteFill>
      <Headlines lines={RUN_LINES} />
      <div
        style={{
          position: 'absolute', left: box.x, top: box.y, width: box.w, height: box.h, boxSizing: 'border-box', borderRadius: 18,
          border: `3px solid ${ACCENT}`, ...glow(burst, ACCENT), opacity: fadeAt(frame, 0.4),
        }}
      >
        <div style={{position: 'absolute', left: 18, top: 12, fontFamily: MONO, fontSize: 18, fontWeight: 700, color: ACCENT, whiteSpace: 'nowrap'}}>
          run(reader(env={'{'}"name": <span style={{color: TEXT}}>{empty ? '""' : '"doeff"'}</span>{'}'})(greet_or_fail()))
        </div>
      </div>
      <CodeBlock lines={GREET} {...code} band={band} opacity={fadeAt(frame, 0.2)} />
      <Label x={80} y={460} size={22} color={TEXT} opacity={fadeAt(frame, 2.0) * (1 - ramp(frame, 6.6, 7.0))}>
        Raising an exception is how a Python program fails.
      </Label>
      <Label x={80} y={496} size={22} color={MUTED} bold={false} opacity={fadeAt(frame, 3.0) * (1 - ramp(frame, 6.6, 7.0))}>
        What happens to it is decided by what is around the program.
      </Label>
      {/* First run: the value leaves run() unchanged. */}
      <Label x={760} y={resultY - 40} opacity={fadeAt(frame, 7.2)}>run(...) returns</Label>
      {frame >= f(9.8) && frame < f(11.0) && (
        <Pill x={mix(box.x + box.w - 40, 840, valueOut)} y={mix(lineMid(code.y, code.line, 5), resultY + 30, valueOut)} text="'DOEFF'" color={TEST} opacity={1 - ramp(frame, 10.8, 11.0)} />
      )}
      <div
        style={{
          position: 'absolute', left: 760, top: resultY, width: 440, height: 60, boxSizing: 'border-box', borderRadius: 12, background: PANEL,
          border: `3px solid ${TEST}`, ...glow(frame >= f(10.8) && frame < f(12.4), TEST), fontFamily: MONO, fontSize: 26, fontWeight: 700,
          color: TEXT, lineHeight: '54px', paddingLeft: 20, opacity: fadeAt(frame, 7.2),
        }}
      >
        <span style={{opacity: fadeAt(frame, 10.8, 0.2)}}>'DOEFF'</span>
        <span style={{fontFamily: SANS, fontSize: 18, fontWeight: 400, color: MUTED, marginLeft: 18, opacity: fadeAt(frame, 11.2)}}>a str, not a wrapper</span>
      </div>
      {/* Second run: the exception leaves run() unchanged. */}
      <Label x={760} y={300} color={FAIL} opacity={fadeAt(frame, 15.0)}>run(...) raises</Label>
      {frame >= f(18.0) && frame < f(19.2) && (
        <Pill
          x={mix(box.x + box.w - 120, 980, raiseOut)} y={mix(lineMid(code.y, code.line, 4), 360, raiseOut)} text="ValueError('name is empty')" color={FAIL}
          opacity={1 - ramp(frame, 19.0, 19.2)}
        />
      )}
      <Terminal x={760} y={340} w={470} lines={RAISED} opacity={fadeAt(frame, 19.0)} />
      <Label x={80} y={470} size={22} color={TEXT} opacity={fadeAt(frame, 19.6)}>
        The caller gets the same ValueError the program raised.
      </Label>
    </AbsoluteFill>
  );
};

// Scene 2 — Ok and Err: a result kept as a value.

const VALUE_LINES: Lines = [
  [0, 'To keep a failure as a value: Ok holds a value, Err holds an exception.'],
  [8.5, 'Ok is true, Err is false, and both match by pattern.'],
];
const VALUE = f(15);
// From measure/ok_err_values.py.
const RESULTS = [
  {name: 'Ok', shown: 'Ok(5)', field: '.value', inside: '5', truth: 'True', pattern: 'case Ok(value=v):', bound: 'v = 5', color: TEST},
  {name: 'Err', shown: "Err(ValueError('boom'))", field: '.error', inside: "ValueError('boom')", truth: 'False', pattern: 'case Err(error=e):', bound: "e = ValueError('boom')", color: FAIL},
];

const ValueScene: React.FC = () => {
  const frame = useSceneFrame();
  return (
    <AbsoluteFill>
      <Headlines lines={VALUE_LINES} />
      <div style={{position: 'absolute', left: 0, right: 0, top: 96, textAlign: 'center', fontFamily: MONO, fontSize: 20, color: MUTED, opacity: fadeAt(frame, 0.3)}}>
        from doeff import Ok, Err
      </div>
      {RESULTS.map((r, i) => {
        const x = 90 + i * 570;
        const appear = 1.2 + i * 2.6;
        const rise = mix(24, 0, ramp(frame, appear, appear + 0.5));
        return (
          <React.Fragment key={r.name}>
            <div
              style={{
                position: 'absolute', left: x, top: 150 + rise, width: 530, height: 210, boxSizing: 'border-box', borderRadius: 18, background: PANEL,
                border: `3px solid ${r.color}`, boxShadow: `0 0 20px ${r.color}55`, opacity: fadeAt(frame, appear, 0.5),
              }}
            >
              <div style={{position: 'absolute', left: 24, top: 16, fontFamily: MONO, fontSize: 30, fontWeight: 700, color: r.color}}>{r.shown}</div>
              <div style={{position: 'absolute', left: 24, top: 64, fontFamily: SANS, fontSize: 19, color: MUTED}}>{i === 0 ? 'a result that worked' : 'a result that failed'}</div>
              <div
                style={{
                  position: 'absolute', left: 24, top: 110, width: 482, height: 70, boxSizing: 'border-box', borderRadius: 12, border: `2px dashed ${r.color}`,
                  padding: '0 18px', fontFamily: MONO, fontSize: 22, lineHeight: '66px', color: TEXT, opacity: fadeAt(frame, appear + 0.8),
                }}
              >
                <span style={{color: r.color, fontWeight: 700}}>{r.field}</span>
                <span style={{color: MUTED}}>{'  →  '}</span>
                {r.inside}
              </div>
            </div>
            <Terminal
              x={x} y={400} w={530} size={20} line={34}
              lines={[
                {text: `bool(${r.name === 'Ok' ? 'Ok(5)' : 'Err(...)'})  →  ${r.truth}`, color: r.truth === 'True' ? TEST : FAIL},
                {text: ''},
                {text: 'match result:', color: MUTED},
                {text: `    ${r.pattern}`, color: r.color},
                {text: `        ${r.bound}`},
              ]}
              opacity={fadeAt(frame, 9.0 + i * 1.2)}
            />
          </React.Fragment>
        );
      })}
    </AbsoluteFill>
  );
};

// Scene 3 — Try is an effect: try_handler runs the sub-program and resumes k with Ok or Err.

const TRY_LINES: Lines = [
  [0, 'Try is an effect. app yields Try, with the sub-program it wants to attempt.'],
  [5.5, 'try_handler catches the effect and runs parse_count inside itself.'],
  [11, 'int raises on "not-a-number". The handler catches it and wraps it in Err.'],
  [18.2, 'It resumes k with that Err. app goes on: it logs the failure and returns 0.'],
  [25, 'Without try_handler, Try is unhandled. It is a handler like any other.'],
];
const TRY = f(32);
const APP = [
  '@do',
  'def app(raw):',
  '    parsed = yield Try(parse_count(raw))',
  '    match parsed:',
  '        case Ok(value=count):',
  '            yield Tell(f"parsed count={count}")',
  '            return count',
  '        case Err(error=exc):',
  '            yield Tell(f"parse failed: {exc}")',
  '            return 0',
];
const PARSE = ['@do', 'def parse_count(raw):', '    return int(raw)'];

const TryScene: React.FC = () => {
  const frame = useSceneFrame();
  const code = {x: 50, y: 100, w: 590, size: 18, line: 29};
  const box = {x: 720, y: 100, w: 510, h: 330};
  const sub = {x: box.x + 24, y: box.y + 96, w: box.w - 48, size: 17, line: 28};
  const away = frame >= f(3.4) && frame < f(19.4);
  const restDim = interpolate(frame, [f(3.4), f(3.9), f(18.9), f(19.4)], [1, 0.3, 0.3, 1], clamp);
  // app's band: the Try line, then (once resumed with Err) the match, the Err case, its Tell and its return.
  const steps: [number, number][] = [[1.5, 2], [20.0, 3], [20.6, 7], [21.2, 8], [22.4, 9]];
  const step = steps.reduce((found, [at], i) => (frame >= f(at) ? i : found), -1);
  const band = step < 0 ? null : step === 0 ? 2 : mix(steps[step - 1][1], steps[step][1], ramp(frame, steps[step][0], steps[step][0] + 0.3));
  const tryOut = ramp(frame, 2.4, 3.6);
  const handling = frame >= f(3.6) && frame < f(19.0);
  const subOn = frame >= f(7.0) && frame < f(16.4);
  const raised = frame >= f(12.4);
  const caught = ramp(frame, 13.6, 14.6);
  const back = ramp(frame, 18.4, 19.4);
  const gone = ramp(frame, 25.6, 26.4);
  const startX = code.x + code.w + 60;
  const tryY = lineMid(code.y, code.line, 2);
  return (
    <AbsoluteFill>
      <Headlines lines={TRY_LINES} />
      <CodeBlock lines={APP} {...code} band={band} dim={(i) => (i >= 3 ? restDim : 1)} opacity={fadeAt(frame, 0)} />
      <Label x={code.x} y={code.y + PAD * 2 + APP.length * code.line + 10} color={ACCENT} size={19} opacity={fadeAt(frame, 3.6) * (away ? 1 : 0)}>
        k = the rest of app()  <span style={{color: MUTED, fontWeight: 400}}>paused, waiting for the answer</span>
      </Label>
      <div
        style={{
          position: 'absolute', left: box.x, top: box.y, width: box.w, height: box.h, boxSizing: 'border-box', borderRadius: 18, background: PANEL,
          border: `3px solid ${TEST}`, ...glow(handling, TEST), opacity: fadeAt(frame, 0.6) * mix(1, 0.25, gone),
        }}
      >
        <div style={{position: 'absolute', left: 20, top: 14, fontFamily: MONO, fontSize: 24, fontWeight: 700, color: handling ? LOOK : TEST}}>try_handler</div>
        <div style={{position: 'absolute', left: 20, top: 50, fontFamily: SANS, fontSize: 17, color: MUTED}}>a handler that answers Try with Ok or Err</div>
      </div>
      <CodeBlock lines={PARSE} {...sub} band={frame >= f(8.0) ? 2 : null} opacity={fadeAt(frame, 6.4) * (1 - ramp(frame, 16.4, 17.0)) * mix(1, 0, gone)} />
      <div
        style={{
          position: 'absolute', left: sub.x - 6, top: sub.y - 6, width: sub.w + 12, height: PAD * 2 + PARSE.length * sub.line + 12, boxSizing: 'border-box',
          borderRadius: 18, border: `3px solid ${raised ? FAIL : LOOK}`, boxShadow: `0 0 18px ${raised ? FAIL : LOOK}88`,
          opacity: (subOn ? 1 : 0) * fadeAt(frame, 7.0),
        }}
      />
      <Label x={sub.x} y={sub.y + PAD * 2 + PARSE.length * sub.line + 14} color={MUTED} size={17} bold={false} opacity={fadeAt(frame, 7.4) * (1 - ramp(frame, 16.4, 17.0))}>
        the sub-program, run inside the handler<br />with raw = "not-a-number"
      </Label>
      {frame >= f(2.4) && frame < f(4.0) && (
        <Pill x={mix(startX, box.x + box.w / 2, tryOut)} y={mix(tryY, box.y + 90, tryOut)} text="Try(parse_count(raw))" color={ACCENT} opacity={1 - ramp(frame, 3.6, 4.0)} />
      )}
      {/* The raise: out of int(raw), caught by the handler and wrapped in Err. */}
      {raised && frame < f(18.4) && (
        <Pill
          x={mix(sub.x + 300, box.x + box.w / 2, caught)} y={mix(lineMid(sub.y, sub.line, 2), box.y + box.h - 40, caught)}
          text={caught < 1 ? 'ValueError(...)' : 'Err(ValueError(...))'} color={FAIL}
          opacity={fadeAt(frame, 12.4, 0.15)}
        />
      )}
      {frame >= f(14.8) && frame < f(18.4) && (
        <div style={{position: 'absolute', left: box.x, top: box.y + box.h + 8, width: box.w, textAlign: 'center', fontFamily: SANS, fontSize: 18, color: FAIL, opacity: fadeAt(frame, 14.8)}}>
          caught: the exception becomes a value
        </div>
      )}
      {frame >= f(18.4) && frame < f(19.8) && (
        <Pill x={mix(box.x + box.w / 2, startX, back)} y={mix(box.y + box.h - 40, tryY, back)} text="Err(ValueError(...))" color={FAIL} opacity={1 - ramp(frame, 19.5, 19.8)} />
      )}
      <Label x={code.x} y={code.y + PAD * 2 + APP.length * code.line + 10} color={FAIL} size={17} bold={false} opacity={fadeAt(frame, 19.4)}>
        parsed = Err(ValueError("invalid literal for int() with base 10: 'not-a-number'"))
      </Label>
      {/* The log and the return, from measure/try_effect.log. */}
      <Terminal
        x={50} y={480} w={1180} size={17} line={28}
        lines={[
          {text: frame >= f(22.0) ? "app('not-a-number')  log: parse failed: invalid literal for int() with base 10: 'not-a-number'" : 'app(\'not-a-number\')', color: TEXT},
          {text: frame >= f(22.6) ? '                     returns 0' : '', color: FAIL},
          {text: frame >= f(23.6) ? "app('42')            Try gives Ok(42)  log: parsed count=42  returns 42" : '', color: TEST},
        ]}
        opacity={fadeAt(frame, 19.6)}
      />
      {frame >= f(25.6) && (
        <div
          style={{
            position: 'absolute', left: box.x + 20, top: box.y + 120, width: box.w - 40, boxSizing: 'border-box', padding: '12px 16px', borderRadius: 12,
            background: '#0b0d13', border: `2px solid ${FAIL}`, fontFamily: MONO, fontSize: 18, lineHeight: '28px', color: FAIL, opacity: fadeAt(frame, 26.2),
          }}
        >
          <div>run(app("42"))</div>
          <div style={{color: TEXT}}>UnhandledEffect: Try</div>
          <div style={{color: MUTED, fontSize: 15}}>hint: Try is handled by try_handler</div>
        </div>
      )}
    </AbsoluteFill>
  );
};

// Scene 4 — the three promises of Try when it is combined with other effects.

const PROMISE_LINES: Lines = [
  [0, 'Try keeps three promises when it meets other effects.'],
  [4.2, 'One: no rollback. The Put before the failure stays: counter is 1.'],
  [9.8, 'Two: Local still restores the environment the failure passed through.'],
  [17.2, 'Three: Try inside Try keeps both layers: Ok of Ok, or Ok of Err.'],
];
const PROMISE = f(26);
const COL_W = 370;
const colX = (i: number) => 45 + i * (COL_W + 30);
// The code of each promise and what measure/composition.py printed for it.
const PROMISES = [
  {
    title: '1  No rollback', start: 4.2,
    code: ['yield Put("counter", 0)', 'r = yield Try(mutate_then_fail())', 'yield Get("counter")'],
    note: 'mutate_then_fail: Put("counter", 1), then raise',
    result: ["(Err(ValueError('boom')), 1)"],
  },
  {
    title: '2  Local restores env', start: 9.8,
    code: ['yield Ask("key")', 'yield Try(Local({"key": "inner"},', '              failing_inner()))', 'yield Ask("key")'],
    note: 'failing_inner: Ask("key"), then raise',
    result: ["('outer', True, 'outer')"],
  },
  {
    title: '3  Nested Try', start: 17.2,
    code: ['a = yield Try(Try(succeeds()))', 'b = yield Try(Try(fails()))'],
    note: 'succeeds returns 5; fails raises',
    result: ['a = Ok(Ok(5))', 'b = Ok(Err(', "      ValueError('inner failure')))"],
  },
];

// A tile showing the value of one key over time: [second, value] keyframes; it pulses when the value changes.
const Tile: React.FC<{x: number; y: number; name: string; keys: [number, string][]; color: string; opacity: number}> = ({x, y, name, keys, color, opacity}) => {
  const frame = useSceneFrame();
  const i = keys.reduce((found, [at], j) => (frame >= f(at) ? j : found), 0);
  const pulse = i > 0 && frame < f(keys[i][0] + 0.6);
  return (
    <div
      style={{
        position: 'absolute', left: x, top: y, width: COL_W - 40, height: 52, boxSizing: 'border-box', borderRadius: 12, background: PANEL,
        border: `2px solid ${color}`, ...glow(pulse, color), fontFamily: MONO, fontSize: 22, lineHeight: '48px', paddingLeft: 16, color: TEXT, opacity,
      }}
    >
      <span style={{color: MUTED}}>{name} = </span>
      <span style={{color: pulse ? LOOK : TEXT, fontWeight: 700}}>{keys[i][1]}</span>
    </div>
  );
};

// Nested result boxes for Ok(Ok(5)) and Ok(Err(...)): each layer a frame of its own colour.
const Nested: React.FC<{x: number; y: number; layers: {name: string; color: string}[]; inner: string; opacity: number; grow: number}> = ({x, y, layers, inner, opacity, grow}) => (
  <div style={{position: 'absolute', left: x, top: y, opacity}}>
    {layers.map((layer, i) => (
      <div
        key={i}
        style={{
          position: 'absolute', left: i * 22 * grow, top: i * 30 * grow, width: COL_W - 40 - i * 44 * grow, height: 128 - i * 60 * grow, boxSizing: 'border-box',
          borderRadius: 12, border: `2px solid ${layer.color}`, background: i === 0 ? PANEL : 'transparent',
        }}
      >
        <div style={{position: 'absolute', left: 10, top: 2, fontFamily: MONO, fontSize: 16, fontWeight: 700, color: layer.color}}>{layer.name}</div>
      </div>
    ))}
    <div style={{position: 'absolute', left: layers.length * 22 + 12, top: layers.length * 30 + 4, fontFamily: MONO, fontSize: 16, fontWeight: 700, color: TEXT, opacity: grow}}>{inner}</div>
  </div>
);

const PromiseScene: React.FC = () => {
  const frame = useSceneFrame();
  return (
    <AbsoluteFill>
      <Headlines lines={PROMISE_LINES} />
      {PROMISES.map((p, i) => {
        const x = colX(i);
        const on = frame >= f(p.start) && (i + 1 < PROMISES.length ? frame < f(PROMISES[i + 1].start) : true);
        const codeY = 160;
        const codeLine = 26;
        const codeH = PAD * 2 + p.code.length * codeLine;
        return (
          <React.Fragment key={p.title}>
            <div
              style={{
                position: 'absolute', left: x - 12, top: 100, width: COL_W + 24, height: 600, boxSizing: 'border-box', borderRadius: 18,
                border: `2px solid ${on ? LOOK : '#2a2e40'}`, boxShadow: on ? `0 0 18px ${LOOK}55` : 'none', opacity: fadeAt(frame, 0.4 + i * 0.4),
              }}
            />
            <Label x={x + 6} y={116} color={on ? LOOK : TEXT} size={21} opacity={fadeAt(frame, 0.4 + i * 0.4)}>{p.title}</Label>
            <CodeBlock lines={p.code} x={x} y={codeY} w={COL_W} size={15} line={codeLine} opacity={fadeAt(frame, p.start)} />
            <Label x={x + 6} y={codeY + codeH + 10} size={15} bold={false} opacity={fadeAt(frame, p.start + 0.4)}>{p.note}</Label>
            <Terminal x={x} y={590} w={COL_W} size={15} line={24} lines={p.result.map((text) => ({text, color: TEST}))} opacity={fadeAt(frame, p.start + 3.4)} />
          </React.Fragment>
        );
      })}
      {/* 1: counter goes 0 → 1, the failure comes back as Err, and counter stays 1. */}
      <Tile x={colX(0) + 20} y={330} name="counter" color={TEST} keys={[[0, '—'], [4.8, '0'], [5.8, '1']]} opacity={fadeAt(frame, 4.4)} />
      {frame >= f(6.6) && (
        <Pill x={colX(0) + COL_W / 2} y={mix(404, 424, ramp(frame, 6.6, 7.4))} text="Err(ValueError('boom'))" color={FAIL} opacity={fadeAt(frame, 6.6, 0.2)} />
      )}
      <Label x={colX(0) + 20} y={460} color={TEST} size={17} opacity={fadeAt(frame, 7.6)}>Err comes back; counter stays 1</Label>
      {/* 2: key is outer, inner inside Local, and outer again after the failure leaves Local. */}
      <Tile x={colX(1) + 20} y={330} name="key" color={ACCENT} keys={[[0, '—'], [10.4, '"outer"'], [11.6, '"inner"'], [13.6, '"outer"']]} opacity={fadeAt(frame, 10.0)} />
      {frame >= f(12.6) && (
        <Pill x={colX(1) + COL_W / 2} y={mix(404, 424, ramp(frame, 12.6, 13.4))} text="ValueError → Err" color={FAIL} opacity={fadeAt(frame, 12.6, 0.2)} />
      )}
      <Label x={colX(1) + 20} y={460} color={ACCENT} size={17} opacity={fadeAt(frame, 14.0)}>Local leaves; key is "outer" again</Label>
      {/* 3: two layers each. */}
      <Nested x={colX(2) + 20} y={300} layers={[{name: 'Ok', color: TEST}, {name: 'Ok', color: TEST}]} inner="5" opacity={fadeAt(frame, 17.6)} grow={ramp(frame, 17.8, 18.6)} />
      <Nested
        x={colX(2) + 20} y={440} layers={[{name: 'Ok', color: TEST}, {name: 'Err', color: FAIL}]} inner="ValueError(...)" opacity={fadeAt(frame, 19.0)}
        grow={ramp(frame, 19.2, 20.0)}
      />
    </AbsoluteFill>
  );
};

// Scene 5 — the exception inside Err keeps its traceback.

const TRACE_LINES: Lines = [
  [0, 'Err keeps the exception, and the exception keeps its traceback.'],
  [5, 'Its frames lead from inside try_handler, through boom, down to load.'],
];
const TRACE = f(13);
const BOOM = ['@do', 'def load(path):', '    raise FileNotFoundError(path)', ' ', '@do', 'def boom():', '    return (yield load("config.toml"))', ' ', 'result = run(try_handler(app()))  # app: Try(boom())'];
// traceback.extract_tb(result.error.__traceback__) in measure/traceback_on_err.py: the frame names, outermost first.
const FRAMES = [
  {name: 'attempt', where: "inside try_handler, where Try's sub-program runs"},
  {name: 'boom', where: 'yield load("config.toml")'},
  {name: 'load', where: 'raise FileNotFoundError(path)  ← raised here'},
];

const TraceScene: React.FC = () => {
  const frame = useSceneFrame();
  const code = {x: 50, y: 110, w: 620, size: 16, line: 27};
  const band = frame < f(9.4) ? null : 2;
  return (
    <AbsoluteFill>
      <Headlines lines={TRACE_LINES} />
      <CodeBlock lines={BOOM} {...code} band={band} opacity={fadeAt(frame, 0.2)} />
      <div
        style={{
          position: 'absolute', left: 720, top: 110, width: 510, height: 96, boxSizing: 'border-box', borderRadius: 16, background: PANEL,
          border: `3px solid ${FAIL}`, boxShadow: `0 0 18px ${FAIL}55`, padding: '12px 18px', opacity: fadeAt(frame, 0.8),
        }}
      >
        <div style={{fontFamily: MONO, fontSize: 19, fontWeight: 700, color: FAIL}}>Err(FileNotFoundError('config.toml'))</div>
        <div style={{fontFamily: MONO, fontSize: 16, color: MUTED, marginTop: 10}}>
          .error.__traceback__ <span style={{color: TEXT}}>→ the frames below</span>
        </div>
      </div>
      {FRAMES.map((frameOf, i) => {
        const at = 5.6 + i * 1.6;
        const last = i === FRAMES.length - 1;
        return (
          <React.Fragment key={frameOf.name}>
            {i > 0 && <div style={{position: 'absolute', left: 760, top: 240 + i * 92 - 30, width: 3, height: 26, background: MUTED, opacity: fadeAt(frame, at)}} />}
            <div
              style={{
                position: 'absolute', left: 740 + i * 16, top: 240 + i * 92, width: 490 - i * 16, height: 60, boxSizing: 'border-box', borderRadius: 12,
                background: '#0b0d13', border: `2px solid ${last ? FAIL : '#3a3f55'}`, ...(last ? glow(frame >= f(at) && frame < f(at + 1.6), FAIL) : {}),
                padding: '6px 14px', opacity: fadeAt(frame, at), transform: `translateY(${mix(14, 0, ramp(frame, at, at + 0.4))}px)`,
              }}
            >
              <div style={{fontFamily: MONO, fontSize: 18, fontWeight: 700, color: last ? FAIL : TEXT}}>{frameOf.name}</div>
              <div style={{fontFamily: MONO, fontSize: 13, color: MUTED, whiteSpace: 'nowrap'}}>{frameOf.where}</div>
            </div>
          </React.Fragment>
        );
      })}
      <Terminal
        x={50} y={480} w={620} size={16} line={26}
        lines={[
          {text: 'traceback.extract_tb(result.error.__traceback__)', color: MUTED},
          {text: "  names: ['attempt', 'boom', 'load']"},
          {text: 'result.captured_traceback: None', color: MUTED},
        ]}
        opacity={fadeAt(frame, 10.4)}
      />
    </AbsoluteFill>
  );
};

// Outro — the chapter in one sentence.

const OUTRO = f(10);

const Outro: React.FC = () => {
  const frame = useSceneFrame();
  const rise = interpolate(frame, [0, f(0.67)], [20, 0], {extrapolateRight: 'clamp', easing: Easing.out(Easing.cubic)});
  const steps = [
    {text: 'yield Try(...)', color: ACCENT},
    {text: 'try_handler', color: TEST},
    {text: 'Ok or Err', color: TEXT},
    {text: 'your code decides', color: LOOK},
  ];
  return (
    <AbsoluteFill style={{padding: 90, justifyContent: 'center', transform: `translateY(${rise}px)`}}>
      <div style={{fontFamily: SANS, fontSize: 56, fontWeight: 700, color: TEXT, opacity: fadeAt(frame, 0, 0.4)}}>Error handling</div>
      <div style={{fontFamily: SANS, fontSize: 30, color: ACCENT, marginTop: 6, opacity: fadeAt(frame, 0.2, 0.4)}}>A failure is an effect a handler answers</div>
      <div style={{display: 'flex', alignItems: 'center', marginTop: 50, gap: 18}}>
        {steps.map((s, i) => (
          <React.Fragment key={s.text}>
            {i > 0 && <div style={{fontFamily: SANS, fontSize: 30, color: MUTED, opacity: fadeAt(frame, 0.8 + i * 0.9)}}>→</div>}
            <div
              style={{
                fontFamily: MONO, fontSize: 24, fontWeight: 700, color: s.color, padding: '10px 18px', borderRadius: 12, border: `2px solid ${s.color}`,
                background: PANEL, whiteSpace: 'nowrap', opacity: fadeAt(frame, 0.8 + i * 0.9),
              }}
            >
              {s.text}
            </div>
          </React.Fragment>
        ))}
      </div>
      <div style={{fontFamily: MONO, fontSize: 26, color: TEST, marginTop: 50, opacity: fadeAt(frame, 4.6, 0.4)}}>pip install doeff</div>
    </AbsoluteFill>
  );
};

// The scenes in order; Root.tsx registers ERROR_HANDLING.length frames of ERROR_HANDLING.Video.
export const ERROR_HANDLING = narrate('error-handling', DURATIONS, [
  {length: RUN, starts: startsOf(RUN_LINES), ids: ['a1', 'a2', 'a3'], node: <RunScene />},
  {length: VALUE, starts: startsOf(VALUE_LINES), ids: ['b1', 'b2'], node: <ValueScene />},
  {length: TRY, starts: startsOf(TRY_LINES), ids: ['c1', 'c2', 'c3', 'c4', 'c5'], node: <TryScene />},
  {length: PROMISE, starts: startsOf(PROMISE_LINES), ids: ['d1', 'd2', 'd3', 'd4'], node: <PromiseScene />},
  {length: TRACE, starts: startsOf(TRACE_LINES), ids: ['e1', 'e2'], node: <TraceScene />},
  {length: OUTRO, starts: [0], ids: ['o1'], node: <Outro />},
]);
