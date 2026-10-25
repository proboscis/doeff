// The video on the Basic Effects page (docs/03-basic-effects.md): the effects that come with doeff, each one yielded
// by the code and answered by a handler that ships with doeff. Scenes, each with headlines a synthetic voice reads
// (narration.json):
//   1. Reader, State, Writer and slog: an effect your code yields, a handler doeff ships.
//   2. Ask reads the env the reader handler holds; a missing key names the close key.
//   3. Local runs a sub-program over a changed env, and the env is back after it.
//   4. Get and Put read and write the store the state handler holds.
//   5. Tell appends to a log, and writer, a handler, keeps that log in state with its own Get and Put.
//   6. slog emits a structured entry; slog_handler prints it, and Listen collects the entries as values.
//   7. One program under reader, state and writer: each effect goes to its layer.
// Every output, order and value on screen is the one the programs in measure/ print (the .log next to each).
import React from 'react';
import {AbsoluteFill, Easing, interpolate} from 'remotion';
import {fadeAt, mix, ramp} from '../../kit/motion';
import {narrate} from '../../kit/narrated';
import {CodeBlock, glow, Headlines, lineMid, Lines, Pill, startsOf} from '../../kit/parts';
import {ACCENT, f, LOOK, MONO, MUTED, PANEL, SANS, TEST, TEXT} from '../../kit/theme';
import {useSceneFrame} from '../../kit/warp';
import DURATIONS from './durations.json';

type Point = {x: number; y: number};

// The code line the program is on: [second, line] steps, each move taking 0.3 s; null before the first step.
const bandAt = (frame: number, steps: [number, number][]) => {
  const i = steps.reduce((found, [at], j) => (frame >= f(at) ? j : found), -1);
  if (i < 0) return null;
  const from = i > 0 ? steps[i - 1][1] : steps[0][1];
  return mix(from, steps[i][1], ramp(frame, steps[i][0], steps[i][0] + 0.3));
};

// An effect or an answer flying from a to b between two seconds, fading out just after it lands.
const Fly: React.FC<{a: Point; b: Point; leave: number; land: number; text: string; color: string}> = ({a, b, leave, land, text, color}) => {
  const frame = useSceneFrame();
  if (frame < f(leave) || frame >= f(land + 0.25)) return null;
  const t = ramp(frame, leave, land);
  return <Pill x={mix(a.x, b.x, t)} y={mix(a.y, b.y, t)} text={text} color={color} opacity={1 - ramp(frame, land + 0.05, land + 0.25)} />;
};

// A handler's panel: its name at the top in the handler colour, glowing while it works.
const Handler: React.FC<{x: number; y: number; w: number; h: number; name: string; note?: string; on: boolean; opacity: number; children?: React.ReactNode}> = ({
  x, y, w, h, name, note, on, opacity, children,
}) => (
  <div
    style={{
      position: 'absolute', left: x, top: y, width: w, height: h, boxSizing: 'border-box', borderRadius: 18, background: PANEL,
      border: `3px solid ${TEST}`, ...glow(on, TEST), opacity,
    }}
  >
    <div style={{position: 'absolute', left: 20, top: 12, fontFamily: MONO, fontSize: 24, fontWeight: 700, color: on ? LOOK : TEST}}>{name}</div>
    {note && <div style={{position: 'absolute', left: 20, top: 46, fontFamily: SANS, fontSize: 16, color: MUTED}}>{note}</div>}
    {children}
  </div>
);

// A small caption in a scene.
const Caption: React.FC<{x: number; y: number; w?: number; color?: string; opacity: number; children: React.ReactNode}> = ({x, y, w = 600, color = MUTED, opacity, children}) => (
  <div style={{position: 'absolute', left: x, top: y, width: w, fontFamily: SANS, fontSize: 21, lineHeight: '30px', color, opacity}}>{children}</div>
);

// A terminal-like box of output lines; lines given as [second it appears, text].
const Output: React.FC<{x: number; y: number; w: number; h: number; title: string; lines: [number, string][]; size?: number; opacity: number}> = ({
  x, y, w, h, title, lines, size = 18, opacity,
}) => {
  const frame = useSceneFrame();
  return (
    <div style={{position: 'absolute', left: x, top: y, width: w, height: h, boxSizing: 'border-box', padding: '34px 16px 8px', background: '#0b0d13', borderRadius: 10, opacity}}>
      <div style={{position: 'absolute', left: 16, top: 8, fontFamily: SANS, fontSize: 15, color: MUTED}}>{title}</div>
      {lines.map(([at, text], i) => (
        <div key={i} style={{fontFamily: MONO, fontSize: size, lineHeight: `${size + 11}px`, color: TEXT, whiteSpace: 'pre', opacity: fadeAt(frame, at, 0.25)}}>
          {text}
        </div>
      ))}
    </div>
  );
};

// Scene 1 — the effects that come with doeff, each with the handler that answers it.

const INTRO_LINES: Lines = [
  [0, 'Basic effects: the batteries that come with doeff.'],
  [4, 'Each is an effect your code yields, answered by a handler doeff ships.'],
];
const INTRO = f(10);
const FAMILIES = [
  {name: 'Reader', effects: ['Ask(key)', 'Local(env, prog)'], handler: 'reader(env=...)'},
  {name: 'State', effects: ['Get(key)', 'Put(key, value)'], handler: 'state()'},
  {name: 'Writer', effects: ['Tell(msg)'], handler: 'writer'},
  {name: 'slog', effects: ['slog(msg, **kw)'], handler: 'slog_handler'},
];

const IntroScene: React.FC = () => {
  const frame = useSceneFrame();
  return (
    <AbsoluteFill>
      <Headlines lines={INTRO_LINES} />
      <div style={{position: 'absolute', left: 0, right: 0, top: 110 + mix(16, 0, ramp(frame, 0.2, 0.8)), textAlign: 'center', opacity: fadeAt(frame, 0.2, 0.5)}}>
        <div style={{fontFamily: SANS, fontSize: 72, fontWeight: 700, color: TEXT}}>Basic Effects</div>
        <div style={{fontFamily: SANS, fontSize: 28, color: ACCENT, marginTop: 4}}>Reader · State · Writer · slog</div>
      </div>
      {FAMILIES.map((family, i) => {
        const appear = 1.2 + i * 0.5;
        const answered = 4.6 + i * 1.1;
        const flying = frame >= f(answered) && frame < f(answered + 0.9);
        const on = frame >= f(answered + 0.7) && frame < f(answered + 1.4);
        const x = 70 + i * 290;
        return (
          <div
            key={family.name}
            style={{
              position: 'absolute', left: x, top: 330, width: 260, height: 300, boxSizing: 'border-box', borderRadius: 18, background: PANEL,
              border: '2px solid #2a2e40', opacity: fadeAt(frame, appear, 0.5), transform: `translateY(${mix(14, 0, ramp(frame, appear, appear + 0.5))}px)`,
            }}
          >
            <div style={{position: 'absolute', left: 20, top: 14, fontFamily: SANS, fontSize: 30, fontWeight: 700, color: TEXT}}>{family.name}</div>
            <div style={{position: 'absolute', left: 20, top: 66, fontFamily: SANS, fontSize: 16, color: MUTED}}>your code yields</div>
            {family.effects.map((effect, k) => (
              <div key={effect} style={{position: 'absolute', left: 20, top: 92 + k * 30, fontFamily: MONO, fontSize: 19, fontWeight: 700, color: ACCENT}}>
                {effect}
              </div>
            ))}
            <div style={{position: 'absolute', left: 20, top: 190, fontFamily: SANS, fontSize: 16, color: MUTED}}>a handler answers</div>
            <div
              style={{
                position: 'absolute', left: 14, top: 216, width: 232, height: 44, boxSizing: 'border-box', borderRadius: 10, border: `3px solid ${TEST}`,
                ...glow(on, TEST), fontFamily: MONO, fontSize: 18, fontWeight: 700, color: on ? LOOK : TEST, lineHeight: '38px', paddingLeft: 10,
              }}
            >
              {family.handler}
            </div>
            {flying && (
              <Pill x={130} y={mix(108, 238, ramp(frame, answered, answered + 0.7))} text={family.effects[0].split('(')[0]} color={ACCENT} opacity={1 - ramp(frame, answered + 0.7, answered + 0.9)} />
            )}
          </div>
        );
      })}
    </AbsoluteFill>
  );
};

// Scene 2 — Reader: Ask reads the env the reader handler holds (measure/reader.py).

const ASK_LINES: Lines = [
  [0, 'Reader: Ask(key) reads a value from the environment.'],
  [5, 'The env lives in the reader handler. Your code only names the key.'],
  [10.5, 'A key that is not there raises MissingEnvKeyError, naming the close key.'],
];
const ASK = f(17);
const CONNECT = ['@do', 'def connect():', '    url = yield Ask("database_url")', '    timeout = yield Ask("timeout")', '    return f"{url} ({timeout} s)"'];
const ENV = [
  ['"database_url"', '"postgresql://localhost/mydb"'],
  ['"timeout"', '30'],
];
// The error a misspelled key raises, as measure/reader.py prints it.
const MISSING = [
  "MissingEnvKeyError: Ask: key not found: 'timout'",
  "  Did you mean 'timeout'?",
  "  Keys in the env: 'database_url', 'timeout'",
];

const AskScene: React.FC = () => {
  const frame = useSceneFrame();
  const code = {x: 60, y: 120, w: 600, size: 21, line: 34};
  const box = {x: 720, y: 120, w: 500, h: 210};
  const rowY = (i: number) => box.y + 96 + i * 46;
  const from = (i: number) => ({x: code.x + code.w + 40, y: lineMid(code.y, code.line, 2 + i)});
  const to = (i: number) => ({x: box.x + 30, y: rowY(i) + 16});
  const asks = [
    {key: 'Ask("database_url")', answer: '"postgresql://…/mydb"', leave: 1.4, land: 2.3, back: 3.2},
    {key: 'Ask("timeout")', answer: '30', leave: 5.8, land: 6.7, back: 7.6},
  ];
  const lit = (i: number) => frame >= f(asks[i].land) && frame < f(asks[i].back + 0.8);
  const band = bandAt(frame, [[1.0, 2], [5.4, 3], [8.6, 4]]);
  const top = 1 - ramp(frame, 10.2, 10.6);
  return (
    <AbsoluteFill>
      <Headlines lines={ASK_LINES} />
      <CodeBlock lines={CONNECT} {...code} band={band} opacity={fadeAt(frame, 0)} />
      <Handler {...box} name="reader(env=...)" note="holds the env" on={lit(0) || lit(1)} opacity={fadeAt(frame, 0.4)}>
        {ENV.map(([key, value], i) => (
          <div
            key={key}
            style={{
              position: 'absolute', left: 20, top: 96 + i * 46 - 18 + 16, height: 36, width: box.w - 40, boxSizing: 'border-box', borderRadius: 8,
              background: lit(i) ? `${LOOK}22` : 'transparent', fontFamily: MONO, fontSize: 16, lineHeight: '36px', paddingLeft: 10, whiteSpace: 'nowrap', color: TEXT,
            }}
          >
            <span style={{color: lit(i) ? LOOK : TEXT}}>{key}</span>
            <span style={{color: MUTED}}>{': '}</span>
            {value}
          </div>
        ))}
      </Handler>
      {asks.map((ask, i) => (
        <React.Fragment key={ask.key}>
          <Fly a={from(i)} b={to(i)} leave={ask.leave} land={ask.land} text={ask.key} color={ACCENT} />
          <Fly a={to(i)} b={from(i)} leave={ask.back} land={ask.back + 0.9} text={ask.answer} color={TEST} />
        </React.Fragment>
      ))}
      <div
        style={{
          position: 'absolute', left: code.x, top: code.y + 200, fontFamily: MONO, fontSize: 20, fontWeight: 700, color: ACCENT, whiteSpace: 'nowrap',
          opacity: fadeAt(frame, 9.0) * top,
        }}
      >
        → "postgresql://localhost/mydb (30 s)"
      </div>
      <Caption x={720} y={350} w={500} opacity={fadeAt(frame, 5.2) * top}>
        The code never sees the dict. It only says which key it wants.
      </Caption>
      <CodeBlock lines={['@do', 'def connect_typo():', '    return (yield Ask("timout"))']} x={60} y={400} w={560} size={19} line={30} band={2} opacity={fadeAt(frame, 10.8)} />
      <Output
        x={640} y={400} w={590} h={150} title="what it raises (a KeyError)" size={15} opacity={fadeAt(frame, 11.4)}
        lines={MISSING.map((line, i) => [11.8 + i * 0.6, line] as [number, string])}
      />
      <Caption x={60} y={590} w={1160} color={TEST} opacity={fadeAt(frame, 14.0)}>
        The message names the keys the env holds, and the close name for a typo.
      </Caption>
    </AbsoluteFill>
  );
};

// Scene 3 — Local: a sub-program over a changed env; the env is back after it (measure/reader.py).

const LOCAL_LINES: Lines = [
  [0, 'Local runs a sub-program over a changed environment.'],
  [5, 'Inside, Ask sees the override. The code inside is the same as ever.'],
  [10, 'When the sub-program returns, the env is back as it was.'],
];
const LOCAL = f(15.5);
const OUTER = [
  '@do',
  'def with_custom_config():',
  '    url1 = yield Ask("api_url")',
  '    yield Tell(f"Default URL: {url1}")',
  '    result = yield Local(',
  '        {"api_url": "https://staging.example.com"},',
  '        fetch_data())',
  '    url2 = yield Ask("api_url")',
  '    yield Tell(f"Back to: {url2}")',
];
const INNER = ['@do', 'def fetch_data():', '    url = yield Ask("api_url")', '    yield Tell(f"Fetching from {url}")'];

const LocalScene: React.FC = () => {
  const frame = useSceneFrame();
  const outer = {x: 40, y: 110, w: 640, size: 17, line: 27};
  const inner = {x: 40, y: 400, w: 640, size: 17, line: 27};
  const env = {x: 740, y: 110, w: 490, h: 250};
  const outerBand = bandAt(frame, [[0.8, 2], [1.8, 3], [3.0, 4], [10.4, 7], [11.6, 8]]);
  const insideBand = frame >= f(4.4) && frame < f(9.4) ? bandAt(frame, [[4.4, 2], [6.6, 3]]) : null;
  const overlayIn = ramp(frame, 3.4, 4.2);
  const overlayOut = ramp(frame, 9.4, 10.1);
  const overlay = overlayIn * (1 - overlayOut);
  const baseLit = (frame >= f(0.9) && frame < f(2.0)) || (frame >= f(10.5) && frame < f(11.7));
  const overLit = frame >= f(5.0) && frame < f(6.8);
  const card = (y: number, text: string, lit: boolean, extra: React.CSSProperties) => (
    <div
      style={{
        position: 'absolute', left: 20, top: y, width: env.w - 40, height: 52, boxSizing: 'border-box', borderRadius: 10, background: '#0b0d13',
        border: `2px solid ${lit ? LOOK : '#2a2e40'}`, boxShadow: lit ? `0 0 16px ${LOOK}88` : 'none', fontFamily: MONO, fontSize: 16,
        lineHeight: '48px', paddingLeft: 12, whiteSpace: 'nowrap', color: TEXT, ...extra,
      }}
    >
      <span style={{color: MUTED}}>api_url = </span>
      {text}
    </div>
  );
  return (
    <AbsoluteFill>
      <Headlines lines={LOCAL_LINES} />
      <CodeBlock lines={OUTER} {...outer} band={outerBand} dim={(i) => (frame >= f(4.4) && frame < f(9.4) && i !== 4 ? 0.45 : 1)} opacity={fadeAt(frame, 0)} />
      <CodeBlock lines={INNER} {...inner} band={insideBand} opacity={fadeAt(frame, 0.3) * (frame >= f(4.2) && frame < f(9.6) ? 1 : 0.55)} />
      <Handler {...env} name="the env Ask sees" on={baseLit || overLit} opacity={fadeAt(frame, 0.4)}>
        {card(180, 'https://api.example.com', baseLit, {opacity: 1 - 0.6 * overlay})}
        {card(mix(40, 112, overlayIn), 'https://staging.example.com', overLit, {
          opacity: overlay, borderColor: overLit ? LOOK : ACCENT, transform: `translateY(${mix(0, -60, overlayOut)}px)`,
        })}
        <div style={{position: 'absolute', left: 20, top: 60, fontFamily: SANS, fontSize: 16, color: ACCENT, opacity: overlay}}>Local: inside fetch_data() only</div>
      </Handler>
      <Output
        x={740} y={400} w={490} h={150} title="writer log" size={16} opacity={fadeAt(frame, 0.4)}
        lines={[
          [2.2, 'Default URL: https://api.example.com'],
          [7.0, 'Fetching from https://staging.example.com'],
          [12.0, 'Back to: https://api.example.com'],
        ]}
      />
      <Caption x={40} y={540} w={640} color={TEST} opacity={fadeAt(frame, 12.6)}>
        Local returned "data from https://staging.example.com", and the env is as it was.
      </Caption>
    </AbsoluteFill>
  );
};

// Scene 4 — State: Get and Put read and write the store the state handler holds (measure/state.py).

const STATE_LINES: Lines = [
  [0, 'State: Get and Put read and write a store.'],
  [5, 'The store lives in the state handler, not in your code.'],
  [10.5, 'Get, add one, Put, three times: the program returns 3.'],
];
const STATE = f(16);
const COUNTER = [
  '@do',
  'def counter_operations():',
  '    yield Put("count", 0)',
  '    for _ in range(3):',
  '        val = yield Get("count")',
  '        yield Put("count", val + 1)',
  '    return (yield Get("count"))',
];
// The Puts measure/state.py records on their way to state, with the second each one leaves.
const PUTS = [
  {at: 1.2, value: 0},
  {at: 3.8, value: 1},
  {at: 6.2, value: 2},
  {at: 8.6, value: 3},
];

const StateScene: React.FC = () => {
  const frame = useSceneFrame();
  const code = {x: 60, y: 130, w: 600, size: 21, line: 36};
  const box = {x: 740, y: 130, w: 480, h: 300};
  const slot = {x: box.x + 170, y: box.y + 279};
  const gets = PUTS.slice(0, 3).map((put, i) => ({at: put.at + 1.2, value: put.value, line: 4, i}));
  const finalGet = 11.0;
  const shown = PUTS.reduce((v, put) => (frame >= f(put.at + 0.9) ? put.value : v), -1);
  const changedAt = PUTS.reduce((t, put) => (frame >= f(put.at + 0.9) ? put.at + 0.9 : t), 0);
  const working = PUTS.some((put) => frame >= f(put.at + 0.8) && frame < f(put.at + 1.2)) || gets.some((g) => frame >= f(g.at + 0.8) && frame < f(g.at + 1.2));
  const steps: [number, number][] = [[1.0, 2]];
  PUTS.slice(1).forEach((put, i) => steps.push([gets[i].at - 0.2, 4], [put.at - 0.2, 5]));
  steps.push([finalGet - 0.2, 6]);
  const band = bandAt(frame, steps);
  const out = (line: number) => ({x: code.x + code.w + 40, y: lineMid(code.y, code.line, line)});
  return (
    <AbsoluteFill>
      <Headlines lines={STATE_LINES} />
      <CodeBlock lines={COUNTER} {...code} band={band} opacity={fadeAt(frame, 0)} />
      <Handler {...box} name="state()" note="holds the store" on={working} opacity={fadeAt(frame, 0.4)}>
        <div style={{position: 'absolute', left: 20, top: 96, fontFamily: SANS, fontSize: 18, color: MUTED}}>store</div>
        <div
          style={{
            position: 'absolute', left: 20, top: 128, width: box.w - 40, height: 130, boxSizing: 'border-box', borderRadius: 12, background: '#0b0d13',
            border: '2px solid #2a2e40', fontFamily: MONO, color: TEXT,
          }}
        >
          <div style={{position: 'absolute', left: 20, top: 44, fontSize: 26, color: MUTED}}>"count":</div>
          {shown >= 0 && (
            <div
              key={shown}
              style={{
                position: 'absolute', left: 190, top: 22, fontSize: 64, fontWeight: 700, color: frame < f(changedAt + 0.5) ? LOOK : TEXT,
                transform: `scale(${mix(1.25, 1, ramp(frame, changedAt, changedAt + 0.4))})`, transformOrigin: 'left center',
              }}
            >
              {shown}
            </div>
          )}
        </div>
      </Handler>
      {PUTS.map((put, i) => (
        <Fly key={`p${i}`} a={out(i === 0 ? 2 : 5)} b={slot} leave={put.at} land={put.at + 0.8} text={`Put("count", ${put.value})`} color={ACCENT} />
      ))}
      {gets.map((get) => (
        <React.Fragment key={`g${get.i}`}>
          <Fly a={out(4)} b={slot} leave={get.at} land={get.at + 0.6} text={'Get("count")'} color={ACCENT} />
          <Fly a={slot} b={out(4)} leave={get.at + 0.7} land={get.at + 1.2} text={String(get.value)} color={TEST} />
        </React.Fragment>
      ))}
      <Fly a={out(6)} b={slot} leave={finalGet} land={finalGet + 0.6} text={'Get("count")'} color={ACCENT} />
      <Fly a={slot} b={out(6)} leave={finalGet + 0.7} land={finalGet + 1.3} text="3" color={TEST} />
      <Caption x={740} y={450} w={480} opacity={fadeAt(frame, 5.2)}>
        Your code keeps no variable for it. Each run of state() starts its own store.
      </Caption>
      <div style={{position: 'absolute', left: code.x, top: code.y + 300, fontFamily: MONO, fontSize: 26, fontWeight: 700, color: ACCENT, opacity: fadeAt(frame, 12.6)}}>
        → 3
      </div>
      <Caption x={code.x} y={code.y + 350} w={600} color={TEST} opacity={fadeAt(frame, 13.2)}>
        A Get of a key that is not in the store returns None.
      </Caption>
    </AbsoluteFill>
  );
};

// Scene 5 — Writer: Tell appends to a log that writer keeps in state (measure/writer.py).

const WRITER_LINES: Lines = [
  [0, 'Writer: Tell appends a message to a log.'],
  [5, 'A handler can perform effects too: writer keeps its log in state.'],
  [11.5, 'writer_log() reads the log back: four lines, in the order told.'],
];
const WRITER = f(17.5);
const WITH_LOGGING = [
  '@do',
  'def with_logging():',
  '    yield Tell("Starting operation")',
  '    yield Tell("Processing data")',
  '    count = yield Get("count")',
  '    yield Tell(f"Count: {count}")',
  '    yield Tell("Operation complete")',
  '    return "done"',
];
const TOLD = ['Starting operation', 'Processing data', 'Count: 0', 'Operation complete'];
// The program's effects, with the second each one leaves. What writer performs for each Tell is the order
// measure/writer.py records reaching state: Get(log) and Put(log) for the first, Get(log) for the others.
const WRITES = [
  {line: 2, at: 1.0, tell: 0, hops: ['Get(log)', 'Put(log)']},
  {line: 3, at: 3.4, tell: 1, hops: ['Get(log)']},
  {line: 5, at: 7.4, tell: 2, hops: ['Get(log)']},
  {line: 6, at: 9.4, tell: 3, hops: ['Get(log)']},
];
const COUNT_GET = 5.6;

const WriterScene: React.FC = () => {
  const frame = useSceneFrame();
  const code = {x: 40, y: 120, w: 560, size: 19, line: 32};
  const writerBox = {x: 660, y: 120, w: 300, h: 110};
  const stateBox = {x: 660, y: 290, w: 570, h: 290};
  const out = (line: number) => ({x: code.x + code.w + 30, y: lineMid(code.y, code.line, line)});
  const atWriter = {x: writerBox.x + 40, y: writerBox.y + 80};
  const atState = {x: stateBox.x + 320, y: stateBox.y + 97};
  const hopTo = {x: stateBox.x + 420, y: stateBox.y + 132};
  const writerOn = WRITES.some((w) => frame >= f(w.at + 0.8) && frame < f(w.at + 0.8 + 0.6 * w.hops.length + 0.2));
  const stateOn =
    WRITES.some((w) => w.hops.some((_, h) => frame >= f(w.at + 1.3 + 0.6 * h) && frame < f(w.at + 1.5 + 0.6 * h))) ||
    (frame >= f(COUNT_GET + 0.8) && frame < f(COUNT_GET + 1.2)) ||
    (frame >= f(12.4) && frame < f(13.2));
  const appended = WRITES.filter((w) => frame >= f(w.at + 1.3 + 0.6 * (w.hops.length - 1))).length;
  const band = bandAt(frame, [[0.8, 2], [3.2, 3], [COUNT_GET - 0.2, 4], [7.2, 5], [9.2, 6], [11.0, 7]]);
  return (
    <AbsoluteFill>
      <Headlines lines={WRITER_LINES} />
      <CodeBlock lines={WITH_LOGGING} {...code} band={band} opacity={fadeAt(frame, 0)} />
      <Handler {...writerBox} name="writer" note="handles Tell" on={writerOn} opacity={fadeAt(frame, 0.3)} />
      <Handler {...stateBox} name="state()" note="the store, with writer's log in it" on={stateOn} opacity={fadeAt(frame, 0.5)}>
        <div style={{position: 'absolute', left: 20, top: 84, fontFamily: MONO, fontSize: 18, color: TEXT}}>
          <span style={{color: MUTED}}>"count": </span>0
        </div>
        <div style={{position: 'absolute', left: 20, top: 120, fontFamily: MONO, fontSize: 18, color: MUTED}}>writer's log:</div>
        {TOLD.slice(0, appended).map((line, i) => (
          <div key={line} style={{position: 'absolute', left: 40, top: 152 + i * 30, fontFamily: MONO, fontSize: 18, color: TEXT, opacity: fadeAt(frame, WRITES[i].at + 1.3 + 0.6 * (WRITES[i].hops.length - 1), 0.25)}}>
            {`${i + 1}. ${JSON.stringify(line)}`}
          </div>
        ))}
      </Handler>
      {WRITES.map((w) => (
        <React.Fragment key={w.line}>
          <Fly a={out(w.line)} b={atWriter} leave={w.at} land={w.at + 0.8} text={`Tell(${JSON.stringify(TOLD[w.tell]).slice(0, 14)}…)`} color={ACCENT} />
          {w.hops.map((hop, h) => (
            <Fly key={hop} a={{x: writerBox.x + 150, y: writerBox.y + 110}} b={hopTo} leave={w.at + 0.9 + 0.6 * h} land={w.at + 1.3 + 0.6 * h} text={hop} color={ACCENT} />
          ))}
        </React.Fragment>
      ))}
      <Fly a={out(4)} b={atState} leave={COUNT_GET} land={COUNT_GET + 0.8} text={'Get("count")'} color={ACCENT} />
      <Fly a={atState} b={out(4)} leave={COUNT_GET + 0.9} land={COUNT_GET + 1.5} text="0" color={TEST} />
      <Caption x={980} y={130} w={250} opacity={fadeAt(frame, 5.3)}>
        To handle a Tell, writer yields Get and Put of its own.
      </Caption>
      <CodeBlock lines={['log = yield writer_log()']} x={code.x} y={420} w={code.w} size={19} line={32} band={frame >= f(11.8) ? 0 : null} opacity={fadeAt(frame, 11.6)} />
      <Fly a={{x: code.x + code.w + 30, y: 450}} b={hopTo} leave={12.0} land={12.6} text="Get(log)" color={ACCENT} />
      <Output
        x={code.x} y={500} w={code.w} h={200} title="with_logging() returns, and the log" size={16} opacity={fadeAt(frame, 13.0)}
        lines={[[13.2, '"done"'], ...TOLD.map((line, i) => [13.6 + i * 0.4, `${i + 1}. ${JSON.stringify(line)}`] as [number, string])]}
      />
    </AbsoluteFill>
  );
};

// Scene 6 — slog: a structured entry; slog_handler prints it, Listen collects it (measure/slog.py).

const SLOG_LINES: Lines = [
  [0, 'slog emits a structured entry: a message and keyword data.'],
  [5, 'slog_handler prints each entry on stderr. It is not a Tell.'],
  [10.5, 'Listen collects the entries as values, so a test can check them.'],
];
const SLOG = f(18);
const STRUCTURED = [
  '@do',
  'def structured_logging():',
  '    yield slog("User logged in", level="info",',
  '               user_id=12345, ip="192.168.1.1")',
  '    yield slog("High memory usage", level="warn",',
  '               memory_mb=512, threshold_mb=400)',
  '    return "logged"',
];
const AUDITED = [
  '@do',
  'def import_rows(row_ids):',
  '    yield Tell("import started")',
  '    for row_id in row_ids:',
  '        yield slog("row imported", row_id=row_id)',
  ' ',
  'count, entries = yield Listen(',
  '    import_rows([7, 8]), types=(SlogEffect,))',
];
// The lines slog_handler prints on stderr in measure/slog.py.
const STDERR = ['    INFO User logged in user_id=12345 ip=192.168.1.1', '    WARN High memory usage memory_mb=512 threshold_mb=400'];

const SlogScene: React.FC = () => {
  const frame = useSceneFrame();
  const code = {x: 40, y: 120, w: 640, size: 17, line: 28};
  const box = {x: 730, y: 120, w: 500, h: 120};
  const first = 1 - ramp(frame, 10.0, 10.5);
  const second = fadeAt(frame, 10.6, 0.4);
  const entries = [
    {line: 2, at: 1.4, msg: '"User logged in"', data: ['level="info"', 'user_id=12345', 'ip="192.168.1.1"']},
    {line: 4, at: 6.0, msg: '"High memory usage"', data: ['level="warn"', 'memory_mb=512', 'threshold_mb=400']},
  ];
  const on = entries.some((e) => frame >= f(e.at + 2.2) && frame < f(e.at + 2.8)) || (frame >= f(12.6) && frame < f(14.4));
  const band = frame < f(10.0) ? bandAt(frame, [[1.0, 2], [5.8, 4], [9.0, 6]]) : bandAt(frame, [[11.0, 4], [12.4, 6]]);
  const rows = [{at: 12.0, id: 7}, {at: 13.4, id: 8}];
  return (
    <AbsoluteFill>
      <Headlines lines={SLOG_LINES} />
      <CodeBlock lines={STRUCTURED} {...code} band={frame < f(10.0) ? band : null} opacity={fadeAt(frame, 0) * first} />
      <CodeBlock lines={AUDITED} {...code} band={frame >= f(10.6) ? band : null} opacity={second} />
      <Handler {...box} name="slog_handler" note="prints each entry on stderr, keeps nothing" on={on} opacity={fadeAt(frame, 0.4)} />
      {entries.map((entry) => {
        if (frame < f(entry.at) || frame >= f(entry.at + 2.6)) return null;
        // The entry first opens up under slog_handler, a message and its keyword data, then travels to slog_handler.
        const t = ramp(frame, entry.at + 1.6, entry.at + 2.3);
        const x = mix(760, box.x + 40, t);
        const y = mix(box.y + 128, box.y + 70, t);
        return (
          <div
            key={entry.msg}
            style={{
              position: 'absolute', left: x, top: y, transform: `scale(${mix(1, 0.4, t)})`, transformOrigin: 'top left', width: 400, boxSizing: 'border-box',
              padding: '8px 14px', borderRadius: 12, background: '#1d1a33', border: `3px solid ${ACCENT}`, boxShadow: `0 0 18px ${ACCENT}88`,
              fontFamily: MONO, fontSize: 18, lineHeight: '28px', color: TEXT, opacity: fadeAt(frame, entry.at, 0.3) * (1 - ramp(frame, entry.at + 2.3, entry.at + 2.6)),
            }}
          >
            <div><span style={{color: MUTED}}>msg </span>{entry.msg}</div>
            {entry.data.map((d, k) => (
              <div key={d} style={{opacity: fadeAt(frame, entry.at + 0.3 + k * 0.25, 0.2)}}><span style={{color: MUTED}}>kw  </span>{d}</div>
            ))}
          </div>
        );
      })}
      <Output
        x={code.x} y={code.y + 260} w={1190} h={100} title="stderr" size={18} opacity={fadeAt(frame, 0.4) * first}
        lines={STDERR.map((line, i) => [entries[i].at + 2.5, line] as [number, string])}
      />
      <Caption x={code.x} y={code.y + 380} w={1190} opacity={fadeAt(frame, 5.3) * first}>
        writer does not handle it: slog is its own effect, SlogEffect, with its own handlers.
      </Caption>
      <Output
        x={code.x} y={code.y + 260} w={1190} h={100} title="stderr" size={18} opacity={second}
        lines={rows.map((row) => [row.at + 0.8, `    INFO row imported row_id=${row.id}`] as [number, string])}
      />
      {rows.map((row) => (
        <Fly key={row.id} a={{x: code.x + code.w + 10, y: lineMid(code.y, code.line, 4)}} b={{x: box.x + 60, y: box.y + 90}} leave={row.at} land={row.at + 0.7} text={`slog(row_id=${row.id})`} color={ACCENT} />
      ))}
      <Output
        x={code.x} y={code.y + 380} w={1190} h={100} title="Listen gives back the count and the entries (the Tell is not collected: types names only SlogEffect)" size={18}
        opacity={fadeAt(frame, 14.8)}
        lines={[[15.2, "(2, [('row imported', {'row_id': 7}), ('row imported', {'row_id': 8})])"]]}
      />
    </AbsoluteFill>
  );
};

// Scene 7 — one program under reader, state and writer: each effect reaches its layer (measure/combined.py).

const COMBINED_LINES: Lines = [
  [0, 'Put them together: one program, three handlers stacked around it.'],
  [5, 'Each effect travels out to the layer that handles it.'],
  [17.5, 'Attempt 2 succeeds. The log holds every step; the result is success.'],
];
const WORKFLOW = [
  '@do',
  'def application_workflow():',
  '    max_retries = yield Ask("max_retries")',
  '    yield Tell(f"Config: max_retries = {max_retries}")',
  '    yield Put("attempt", 0)',
  '    for _ in range(max_retries):',
  '        attempt = yield Get("attempt")',
  '        yield Put("attempt", attempt + 1)',
  '        yield Tell(f"Attempt {attempt + 1}/{max_retries}")',
  '        if (yield try_operation()):',
  '            yield Tell("Operation succeeded")',
  '            return "success"',
  '        yield Tell(f"Attempt {attempt + 1} failed")',
];
// The layers from the inside out: reader(env={"max_retries": 3})(state()(writer(prog))).
const COLUMNS = [
  {name: 'writer', handles: 'Tell'},
  {name: 'state()', handles: 'Get · Put'},
  {name: 'reader', handles: 'Ask'},
];
const LAYER_OF: Record<string, number> = {Tell: 0, Get: 1, Put: 1, Ask: 2};
// The effects the program yields, in the order measure/combined.py records them, with the code line each comes
// from (try_operation's Get comes from its call on line 9) and the log line each Tell writes.
const TRIPS: {effect: string; line: number; told?: string}[] = [
  {effect: 'Ask', line: 2},
  {effect: 'Tell', line: 3, told: 'Config: max_retries = 3'},
  {effect: 'Put', line: 4},
  {effect: 'Get', line: 6},
  {effect: 'Put', line: 7},
  {effect: 'Tell', line: 8, told: 'Attempt 1/3'},
  {effect: 'Get', line: 9},
  {effect: 'Tell', line: 12, told: 'Attempt 1 failed'},
  {effect: 'Get', line: 6},
  {effect: 'Put', line: 7},
  {effect: 'Tell', line: 8, told: 'Attempt 2/3'},
  {effect: 'Get', line: 9},
  {effect: 'Tell', line: 10, told: 'Operation succeeded'},
];
const TRIP_FROM = 5.4;
const TRIP_EVERY = 0.9;
const tripAt = (i: number) => TRIP_FROM + i * TRIP_EVERY;
const COMBINED = f(tripAt(TRIPS.length) + 8);

const CombinedScene: React.FC = () => {
  const frame = useSceneFrame();
  const code = {x: 30, y: 150, w: 650, size: 15, line: 25};
  const colX = (i: number) => 720 + i * 175;
  const col = {y: 150, w: 160, h: 340};
  const done = tripAt(TRIPS.length);
  const at = TRIPS.reduce((found, _, i) => (frame >= f(tripAt(i)) ? i : found), -1);
  const band = at < 0 ? null : frame >= f(done + 0.2) ? mix(TRIPS[TRIPS.length - 1].line, 11, ramp(frame, done + 0.2, done + 0.5)) : TRIPS[at].line;
  const on = (layer: number) =>
    TRIPS.some((trip, i) => LAYER_OF[trip.effect] === layer && frame >= f(tripAt(i) + 0.55) && frame < f(tripAt(i) + 0.85)) ||
    (layer === 1 && TRIPS.some((trip, i) => trip.effect === 'Tell' && frame >= f(tripAt(i) + 0.8) && frame < f(tripAt(i) + 0.95)));
  const told = TRIPS.flatMap((trip, i) => (trip.told ? [[tripAt(i) + 0.6, trip.told] as [number, string]] : []));
  return (
    <AbsoluteFill>
      <Headlines lines={COMBINED_LINES} />
      <div style={{position: 'absolute', left: code.x, top: 100, fontFamily: MONO, fontSize: 17, color: TEXT, whiteSpace: 'nowrap', opacity: fadeAt(frame, 1.0)}}>
        <span style={{color: TEST}}>reader(env={'{"max_retries": 3}'})</span>(<span style={{color: TEST}}>state()</span>(<span style={{color: TEST}}>writer</span>(application_workflow())))
      </div>
      <CodeBlock lines={WORKFLOW} {...code} band={band} opacity={fadeAt(frame, 0)} />
      {COLUMNS.map((column, i) => (
        <div
          key={column.name}
          style={{
            position: 'absolute', left: colX(i), top: col.y, width: col.w, height: col.h, boxSizing: 'border-box', borderRadius: 16, background: PANEL,
            border: `3px solid ${TEST}`, ...glow(on(i), TEST), opacity: fadeAt(frame, 1.4 + i * 0.6, 0.5),
          }}
        >
          <div style={{position: 'absolute', left: 14, top: 10, fontFamily: MONO, fontSize: 20, fontWeight: 700, color: on(i) ? LOOK : TEST}}>{column.name}</div>
          <div style={{position: 'absolute', left: 14, top: 40, fontFamily: SANS, fontSize: 15, color: MUTED}}>{`handles ${column.handles}`}</div>
          <div style={{position: 'absolute', left: 14, bottom: 10, fontFamily: SANS, fontSize: 14, color: MUTED}}>{['innermost', 'middle', 'outermost'][i]}</div>
        </div>
      ))}
      {TRIPS.map((trip, i) => {
        const leave = tripAt(i);
        const y = lineMid(code.y, code.line, trip.line);
        const layer = LAYER_OF[trip.effect];
        if (frame < f(leave) || frame >= f(leave + 1.05)) return null;
        const t = interpolate(frame, [f(leave), f(leave + 0.6)], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp', easing: Easing.out(Easing.cubic)});
        const x = mix(code.x + code.w + 20, colX(layer) + col.w / 2, t);
        const fade = 1 - ramp(frame, leave + 0.8, leave + 1.0);
        return (
          <React.Fragment key={i}>
            <Pill x={x} y={mix(y, col.y + 100 + layer * 20, t)} text={trip.effect} color={ACCENT} opacity={fade} />
            {trip.effect === 'Tell' && frame >= f(leave + 0.62) && (
              <Pill x={mix(colX(0) + col.w / 2, colX(1) + col.w / 2, ramp(frame, leave + 0.62, leave + 0.85))} y={col.y + 200} text={i === 1 ? 'Get · Put' : 'Get'} color={ACCENT} opacity={fade} />
            )}
          </React.Fragment>
        );
      })}
      <Caption x={code.x} y={515} w={650} opacity={fadeAt(frame, 7.0) * (1 - ramp(frame, done, done + 0.3))}>
        For each Tell, writer sends its own Get on to state (and a Put the first time).
      </Caption>
      <Output x={720} y={510} w={510} h={190} title="writer log" size={17} opacity={fadeAt(frame, 2.0)} lines={told} />
      <div style={{position: 'absolute', left: code.x, top: code.y + 13 * code.line + 50, fontFamily: MONO, fontSize: 26, fontWeight: 700, color: ACCENT, opacity: fadeAt(frame, done + 0.6)}}>
        → "success"
      </div>
      <Caption x={code.x} y={code.y + 13 * code.line + 100} w={650} color={TEST} opacity={fadeAt(frame, done + 1.4)}>
        13 effects, each to the layer that handles it. The code names none of the handlers.
      </Caption>
    </AbsoluteFill>
  );
};

// Outro.

const OUTRO = f(7);
const Outro: React.FC = () => {
  const frame = useSceneFrame();
  const rise = interpolate(frame, [0, f(0.67)], [20, 0], {extrapolateRight: 'clamp', easing: Easing.out(Easing.cubic)});
  return (
    <AbsoluteFill style={{padding: 90, justifyContent: 'center', transform: `translateY(${rise}px)`}}>
      <div style={{fontFamily: SANS, fontSize: 56, fontWeight: 700, color: TEXT, opacity: fadeAt(frame, 0, 0.4)}}>Basic Effects</div>
      <div style={{fontFamily: SANS, fontSize: 30, color: ACCENT, marginTop: 6, opacity: fadeAt(frame, 0.2, 0.4)}}>Reader · State · Writer · slog</div>
      <div style={{fontFamily: SANS, fontSize: 28, lineHeight: '42px', color: TEXT, marginTop: 34, width: 1050, opacity: fadeAt(frame, 0.6, 0.4)}}>
        Effects your code yields, and handlers that come with doeff, stacked the way your program needs.
      </div>
      <div style={{fontFamily: MONO, fontSize: 26, color: TEST, marginTop: 40, opacity: fadeAt(frame, 1.1, 0.4)}}>Next: Async Effects</div>
    </AbsoluteFill>
  );
};

// The scenes in order; Root.tsx registers BASIC_EFFECTS.length frames of BASIC_EFFECTS.Video.
export const BASIC_EFFECTS = narrate('basic-effects', DURATIONS, [
  {length: INTRO, starts: startsOf(INTRO_LINES), ids: ['i1', 'i2'], node: <IntroScene />},
  {length: ASK, starts: startsOf(ASK_LINES), ids: ['r1', 'r2', 'r3'], node: <AskScene />},
  {length: LOCAL, starts: startsOf(LOCAL_LINES), ids: ['l1', 'l2', 'l3'], node: <LocalScene />},
  {length: STATE, starts: startsOf(STATE_LINES), ids: ['s1', 's2', 's3'], node: <StateScene />},
  {length: WRITER, starts: startsOf(WRITER_LINES), ids: ['w1', 'w2', 'w3'], node: <WriterScene />},
  {length: SLOG, starts: startsOf(SLOG_LINES), ids: ['g1', 'g2', 'g3'], node: <SlogScene />},
  {length: COMBINED, starts: startsOf(COMBINED_LINES), ids: ['c1', 'c2', 'c3'], node: <CombinedScene />},
  {length: OUTRO, starts: [0], ids: ['o1'], node: <Outro />},
]);
