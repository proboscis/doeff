// The video on the Patterns page (docs/12-patterns.md): every pattern is a program that yields effects and a handler
// stack that gives them their meaning. Five scenes and an outro, each with headlines a synthetic voice reads
// (narration.json):
//   1. Layered architecture: the layers' effects travel outward to the handler stack; Try keeps order 7's failure.
//   2. Retry with backoff: Try, Tell and Delay in one loop; the time handler decides what a second is.
//   3. State: the store lives in the state handler; the state machine steps; Try does not roll back, a snapshot does.
//   4. Testing: a test is the same program under the stack it chooses, reading what its handlers kept.
//   5. Anti-patterns: a blocking sleep behind the time handler's back; a side effect done in place.
// Every value on screen is printed by a program in measure/ (its output is in the .log next to it).
import React from 'react';
import {AbsoluteFill, Easing, interpolate} from 'remotion';
import {clamp, fadeAt, mix, ramp} from '../../kit/motion';
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
const Terminal: React.FC<{x: number; y: number; w: number; lines: {text: string; color?: string}[]; size?: number; line?: number; opacity?: number}> = ({
  x, y, w, lines, size = 16, line = 26, opacity = 1,
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

// An effect or an answer flying from one point to another between at and at + length seconds, then fading.
const Fly: React.FC<{from: [number, number]; to: [number, number]; at: number; length?: number; text: string; color: string}> = ({
  from, to, at, length = 1.0, text, color,
}) => {
  const frame = useSceneFrame();
  if (frame < f(at) || frame >= f(at + length + 0.25)) return null;
  const t = ramp(frame, at, at + length);
  return <Pill x={mix(from[0], to[0], t)} y={mix(from[1], to[1], t)} text={text} color={color} opacity={fadeAt(frame, at, 0.15) * (1 - ramp(frame, at + length, at + length + 0.25))} />;
};

// A handler in a stack: its name, a note, and a glow while it is at work.
const HandlerBox: React.FC<{x: number; y: number; w: number; h?: number; name: string; note?: string; color: string; on: boolean; opacity?: number}> = ({
  x, y, w, h = 56, name, note, color, on, opacity = 1,
}) => (
  <div
    style={{
      position: 'absolute', left: x, top: y, width: w, height: h, boxSizing: 'border-box', borderRadius: 12, background: PANEL,
      border: `2px solid ${color}`, ...glow(on, color), opacity, padding: h > 56 ? '12px 16px 0' : '0 16px', display: 'flex', alignItems: h > 56 ? 'flex-start' : 'center', gap: 14,
    }}
  >
    <span style={{fontFamily: MONO, fontSize: 19, fontWeight: 700, color: on ? LOOK : color, whiteSpace: 'nowrap'}}>{name}</span>
    {note && <span style={{fontFamily: SANS, fontSize: 15, color: MUTED, whiteSpace: 'nowrap'}}>{note}</span>}
  </div>
);

// Whether the frame is between two times in seconds.
const between = (frame: number, from: number, to: number) => frame >= f(from) && frame < f(to);

// Scene 1 — layered architecture: the layers yield, the handler stack answers.

const LAYER_LINES: Lines = [
  [0, 'Patterns: a program yields effects; a handler stack gives them meaning.'],
  [9.5, 'Layered architecture: the domain computes, the service asks, the API tries.'],
  [19.1, 'No layer reaches for its dependencies: the effects travel outward.'],
  [31.3, 'Order 7 fails in the service. Try keeps the failure as a value.'],
];
const LAYER = f(40);
const API = [
  'def get_order_handler(order_id):',
  '    yield Tell(f"Request: GET /orders/{order_id}")',
  '    result = yield Try(order_service(order_id))',
  '    yield Tell(f"Response: {order}")',
];
const SERVICE = [
  'def order_service(order_id):',
  '    db = yield Ask("database")',
  '    cache = yield Ask("cache")',
  '    order = yield db.get_order(order_id)',
];
const DOMAIN = ['def calculate_order_total(items):', '    subtotal = sum(i.price * i.quantity for i in items)', '    return subtotal + subtotal * 0.1'];
// The handler stack of measure/layers.py, outermost first.
const STACK = [
  {name: 'reader', note: 'env: database, cache', color: ACCENT},
  {name: 'state()', note: 'the store writer keeps its log in', color: TEST},
  {name: 'writer', note: 'answers Tell, keeps the log', color: TEST},
  {name: 'try_handler', note: 'answers Try with Ok or Err', color: TEST},
];
// The effects measure/layers.py saw, in order: [second, text, layer code (0 API, 1 service), line, handler index].
const YIELDS: [number, string, number, number, number][] = [
  [20.0, 'Tell', 0, 1, 2],
  [22.2, 'Try', 0, 2, 3],
  [24.4, 'Ask("database")', 1, 1, 0],
  [26.4, 'Ask("cache")', 1, 2, 0],
  [28.6, 'Tell', 0, 3, 2],
];

const LayerScene: React.FC = () => {
  const frame = useSceneFrame();
  const code = {x: 50, w: 600, size: 15, line: 24};
  const apiY = 124;
  const serviceY = 284;
  const domainY = 444;
  const codeYs = [apiY, serviceY];
  const stackX = 730;
  const stackW = 500;
  const stackY = (i: number) => 128 + i * 66;
  const failing = frame >= f(31.3);
  // Which layer is being named in the second headline: domain, then service, then API.
  const named = between(frame, 10.4, 13.2) ? 2 : between(frame, 13.2, 16.0) ? 1 : between(frame, 16.0, 19.1) ? 0 : -1;
  const roles = ['logs the request, tries the service', 'asks for its database and cache', 'a plain computation: yields nothing'];
  const layers = [
    {name: 'API layer', y: apiY, lines: API},
    {name: 'Service layer', y: serviceY, lines: SERVICE},
    {name: 'Domain layer', y: domainY, lines: DOMAIN},
  ];
  // The band of each layer: the line whose effect is on the move.
  const bandOf = (layer: number) => {
    const live = YIELDS.filter(([at, , l]) => l === layer && between(frame, at - 0.2, at + 1.4));
    if (failing && layer === 1 && between(frame, 33.0, 35.6)) return 3;
    return live.length ? live[0][3] : null;
  };
  const handlerOn = (i: number) => YIELDS.some(([at, , , , h]) => h === i && between(frame, at + 0.9, at + 1.9)) || (failing && i === 3 && between(frame, 34.0, 36.4));
  const logLines = failing
    ? [
        {text: 'get_order_handler(7)', color: MUTED},
        {text: frame >= f(32.0) ? 'Request: GET /orders/7' : ''},
        {text: frame >= f(36.6) ? "Response: {'error': 'no order 7'}" : '', color: FAIL},
      ]
    : [
        {text: 'get_order_handler(42)', color: MUTED},
        {text: frame >= f(21.0) ? 'Request: GET /orders/42' : ''},
        {text: frame >= f(29.6) ? 'Response: Order(id=42, ..., total=55.0)' : '', color: TEST},
      ];
  return (
    <AbsoluteFill>
      <Headlines lines={LAYER_LINES} />
      {layers.map((layer, i) => (
        <React.Fragment key={layer.name}>
          <Label x={code.x + 4} y={layer.y - 28} size={18} color={named === i ? LOOK : TEXT} opacity={fadeAt(frame, 0.6 + i * 0.4)}>
            {layer.name}
            <span style={{fontWeight: 400, fontSize: 16, color: MUTED, marginLeft: 14, opacity: frame >= f(10.4 + (2 - i) * 2.8) ? 1 : 0}}>{roles[i]}</span>
          </Label>
          <CodeBlock
            lines={layer.lines} x={code.x} y={layer.y} w={code.w} size={code.size} line={code.line} band={i < 2 ? bandOf(i) : null} opacity={fadeAt(frame, 0.6 + i * 0.4)}
          />
          <div
            style={{
              position: 'absolute', left: code.x - 4, top: layer.y - 4, width: code.w + 8, height: PAD * 2 + layer.lines.length * code.line + 8, boxSizing: 'border-box',
              borderRadius: 16, border: `3px solid ${failing && i === 1 && frame >= f(33.0) ? FAIL : LOOK}`,
              opacity: named === i || (failing && i === 1 && between(frame, 33.0, 36.0)) ? 1 : 0,
            }}
          />
        </React.Fragment>
      ))}
      <Label x={stackX + 4} y={96} size={18} color={TEXT} opacity={fadeAt(frame, 2.6)}>
        the handler stack <span style={{fontWeight: 400, fontSize: 16, color: MUTED}}>outermost first</span>
      </Label>
      {STACK.map((h, i) => (
        <HandlerBox key={h.name} x={stackX + i * 14} y={stackY(i)} w={stackW - i * 14} name={h.name} note={h.note} color={h.color} on={handlerOn(i)} opacity={fadeAt(frame, 3.0 + i * 0.5)} />
      ))}
      <div style={{position: 'absolute', left: stackX + 4, top: 402, fontFamily: MONO, fontSize: 14, color: MUTED, whiteSpace: 'nowrap', opacity: fadeAt(frame, 5.2) * (1 - ramp(frame, 19.0, 19.4))}}>
        run(reader(env)(state()(writer(try_handler(get_order_handler(42))))))
      </div>
      {/* The effects of order 42, each to the handler that answers it. */}
      {YIELDS.map(([at, text, layer, line, h], i) => (
        <Fly key={i} at={at} from={[code.x + code.w - 60, lineMid(codeYs[layer], code.line, line)]} to={[stackX + stackW / 2, stackY(h) + 28]} text={text} color={ACCENT} />
      ))}
      {/* Order 7: the failure inside the service is caught by try_handler and comes back to the API layer as Err. */}
      <Fly at={33.4} length={1.0} from={[code.x + code.w - 120, lineMid(serviceY, code.line, 3)]} to={[stackX + stackW / 2, stackY(3) + 28]} text="LookupError('no order 7')" color={FAIL} />
      <Fly at={34.8} length={1.2} from={[stackX + stackW / 2, stackY(3) + 28]} to={[code.x + code.w - 100, lineMid(apiY, code.line, 2)]} text="Err(LookupError(...))" color={FAIL} />
      <Terminal x={stackX} y={420} w={stackW} size={15} line={25} lines={[{text: 'log', color: MUTED}, ...logLines]} opacity={fadeAt(frame, 19.6)} />
      <Label x={code.x + 4} y={576} size={17} color={TEXT} opacity={fadeAt(frame, 19.6)}>
        effects yielded, in order <span style={{fontWeight: 400, color: MUTED}}>(measure/layers.log)</span>
      </Label>
      <div style={{position: 'absolute', left: code.x, top: 612, display: 'flex', gap: 10}}>
        {YIELDS.map(([at, text], i) => (
          <div
            key={i}
            style={{
              fontFamily: MONO, fontSize: 16, fontWeight: 700, color: ACCENT, padding: '5px 10px', borderRadius: 10, border: `2px solid ${ACCENT}`, background: PANEL,
              whiteSpace: 'nowrap', opacity: fadeAt(frame, at + 0.9),
            }}
          >
            {i + 1} {text}
          </div>
        ))}
      </div>
      <Label x={stackX + 4} y={600} size={17} color={TEXT} opacity={fadeAt(frame, 37.0)}>
        The API layer answers; nothing raised past Try.
      </Label>
    </AbsoluteFill>
  );
};

// Scene 2 — retry with backoff: Try, Tell and Delay compose; the time handler decides what a second is.

const RETRY_LINES: Lines = [
  [0, 'Effects compose: retry with backoff is Try, Tell and Delay in one loop.'],
  [7.4, 'It fails twice, waits 2 s, then 4 s, and the third attempt answers at 6 s.'],
  [15.8, 'Delay is an effect: the time handler decides what a second is.'],
  [26.2, 'Under sync_time_handler, the same program waits 6 real seconds.'],
];
const RETRY = f(32.6);
const LOOP = [
  '@do',
  'def retry_with_exponential_backoff(operation, max_attempts=5):',
  '    for attempt in range(1, max_attempts + 1):',
  '        safe_result = yield Try(operation())',
  '        if safe_result.is_ok():',
  '            return safe_result.value',
  '        if attempt < max_attempts:',
  '            delay = 2 ** attempt',
  '            yield Tell(f"Retry {attempt} failed, waiting {delay}s")',
  '            yield Delay(delay)',
  '        else:',
  '            raise safe_result.error',
];
// From measure/retry.log: the attempts on the handler's clock.
const ATTEMPTS = [
  {at: 8.0, t: 0, ok: false},
  {at: 10.6, t: 2, ok: false},
  {at: 13.8, t: 6, ok: true},
];

const RetryScene: React.FC = () => {
  const frame = useSceneFrame();
  const code = {x: 40, y: 96, w: 650, size: 15, line: 24};
  // The band walks the loop: Try at each attempt; Tell and Delay after each failure; return after the answer.
  const steps: [number, number][] = [[1.0, 3], [8.6, 6], [8.9, 8], [9.2, 9], [10.6, 3], [11.2, 6], [11.5, 8], [11.8, 9], [13.8, 3], [14.4, 4], [14.7, 5]];
  const step = steps.reduce((found, [at], i) => (frame >= f(at) ? i : found), -1);
  const band = step < 0 ? null : steps[step][1];
  // The timeline of the handler's clock, 0 to 6 seconds.
  const tl = {x: 730, y: 150, w: 480};
  const tx = (t: number) => tl.x + (t / 6) * tl.w;
  const cursor = interpolate(frame, [f(8.0), f(9.2), f(10.6), f(11.8), f(13.8)], [0, 0, 2, 2, 6], clamp);
  const lanes = [
    {name: 'sim_time_handler', note: 'moves a virtual clock', color: TEST, y: 330, from: 17.6, wall: '1.58 ms', share: 0.00158 / 6, fill: ramp(frame, 18.6, 18.7)},
    {name: 'sync_time_handler', note: 'waits on the wall clock', color: PROD, y: 490, from: 26.4, wall: '6.00 s', share: 1, fill: ramp(frame, 26.8, 31.8)},
  ];
  const log = [
    {text: 'log', color: MUTED},
    {text: frame >= f(9.0) ? 'Retry 1 failed, waiting 2s' : ''},
    {text: frame >= f(11.6) ? 'Retry 2 failed, waiting 4s' : ''},
    {text: frame >= f(14.8) ? "returns 'data after 6s'" : '', color: TEST},
  ];
  return (
    <AbsoluteFill>
      <Headlines lines={RETRY_LINES} />
      <CodeBlock lines={LOOP} {...code} band={band} opacity={fadeAt(frame, 0.2)} />
      <Terminal x={code.x} y={430} w={code.w} lines={log} opacity={fadeAt(frame, 7.6)} />
      {/* The three effects of the loop, named while the first headline is read. */}
      {[['Try', 3, 1.6], ['Tell', 8, 3.0], ['Delay', 9, 4.4]].map(([name, line, at]) => (
        <Pill key={name as string} x={code.x + code.w + 20} y={lineMid(code.y, code.line, line as number)} left text={name as string} color={ACCENT} opacity={fadeAt(frame, at as number) * (1 - ramp(frame, 7.0, 7.4))} />
      ))}
      <Label x={tl.x} y={100} size={18} color={TEXT} opacity={fadeAt(frame, 7.6)}>
        the handler's clock <span style={{fontWeight: 400, fontSize: 16, color: MUTED}}>(measure/retry.log)</span>
      </Label>
      <div style={{position: 'absolute', left: tl.x, top: tl.y + 40, width: tl.w, height: 3, background: '#3a3f55', opacity: fadeAt(frame, 7.6)}} />
      {[0, 1, 2, 3, 4, 5, 6].map((t) => (
        <React.Fragment key={t}>
          <div style={{position: 'absolute', left: tx(t) - 1, top: tl.y + 34, width: 2, height: 15, background: MUTED, opacity: fadeAt(frame, 7.6)}} />
          <div style={{position: 'absolute', left: tx(t) - 20, top: tl.y + 54, width: 40, textAlign: 'center', fontFamily: MONO, fontSize: 14, color: MUTED, opacity: fadeAt(frame, 7.6)}}>{t}s</div>
        </React.Fragment>
      ))}
      {/* The waits: Delay(2) from 0 to 2 s, Delay(4) from 2 to 6 s. */}
      {[{from: 0, to: 2, at: 9.2, text: 'Delay(2)'}, {from: 2, to: 6, at: 11.8, text: 'Delay(4)'}].map((d) => (
        <div
          key={d.text}
          style={{
            position: 'absolute', left: tx(d.from) + 4, top: tl.y + 84, width: (tx(d.to) - tx(d.from) - 8) * ramp(frame, d.at, d.at + (d.to - d.from) * 0.4), height: 30,
            boxSizing: 'border-box', borderRadius: 8, background: `${ACCENT}33`, border: `2px solid ${ACCENT}`, fontFamily: MONO, fontSize: 14, color: ACCENT,
            lineHeight: '26px', paddingLeft: 8, overflow: 'hidden', whiteSpace: 'nowrap', opacity: fadeAt(frame, d.at, 0.1),
          }}
        >
          {d.text}
        </div>
      ))}
      {ATTEMPTS.map((a, i) => (
        <div
          key={i}
          style={{
            position: 'absolute', left: tx(a.t) - 15, top: tl.y + 4, width: 30, height: 30, borderRadius: 15, background: a.ok ? TEST : FAIL, color: '#0f1117',
            fontFamily: SANS, fontSize: 18, fontWeight: 700, textAlign: 'center', lineHeight: '30px', boxShadow: `0 0 14px ${a.ok ? TEST : FAIL}`, opacity: fadeAt(frame, a.at + 0.3),
          }}
        >
          {a.ok ? '✓' : '✗'}
        </div>
      ))}
      <div style={{position: 'absolute', left: tx(cursor) - 2, top: tl.y + 26, width: 4, height: 30, background: LOOK, boxShadow: `0 0 10px ${LOOK}`, opacity: fadeAt(frame, 8.0) * (1 - ramp(frame, 15.2, 15.6))}} />
      <Label x={tl.x} y={tl.y + 126} size={16} bold={false} opacity={fadeAt(frame, 14.4)}>
        attempts at 0 s and 2 s raise ConnectionError; at 6 s it answers
      </Label>
      {/* The two time handlers: the same program, the same log; only the wall clock differs. */}
      {lanes.map((lane) => (
        <div
          key={lane.name}
          style={{
            position: 'absolute', left: tl.x - 10, top: lane.y, width: tl.w + 20, height: 140, boxSizing: 'border-box', borderRadius: 14, background: PANEL,
            border: `2px solid ${lane.color}`, ...glow(between(frame, lane.from + 0.6, lane.from + (lane.color === PROD ? 5.6 : 2.4)), lane.color), opacity: fadeAt(frame, lane.from),
          }}
        >
          <div style={{position: 'absolute', left: 16, top: 10, fontFamily: MONO, fontSize: 19, fontWeight: 700, color: lane.color}}>{lane.name}</div>
          <div style={{position: 'absolute', left: 16, top: 38, fontFamily: SANS, fontSize: 15, color: MUTED}}>{lane.note}</div>
          <div style={{position: 'absolute', left: 16, top: 66, fontFamily: MONO, fontSize: 15, color: TEXT}}>clock moves 0 s → 6 s · bar: wall clock, up to 6 s</div>
          <div style={{position: 'absolute', left: 16, top: 98, width: 300, height: 22, borderRadius: 6, background: '#0b0d13', border: '2px solid #2a2e40', overflow: 'hidden'}}>
            <div style={{width: `${lane.fill > 0 ? Math.max(lane.fill * lane.share * 100, 1) : 0}%`, height: '100%', background: lane.color}} />
          </div>
          <div style={{position: 'absolute', left: 330, top: 96, fontFamily: MONO, fontSize: 17, fontWeight: 700, color: lane.color, opacity: lane.fill >= 1 ? 1 : 0}}>wall {lane.wall}</div>
        </div>
      ))}
      <Pill x={tl.x + tl.w / 2} y={lanes[0].y - 16} text="Delay(2) · Delay(4)" color={ACCENT} opacity={fadeAt(frame, 16.6) * (1 - ramp(frame, 25.6, 26.0))} />
    </AbsoluteFill>
  );
};

// Scene 3 — state lives in the state handler.

const STATE_LINES: Lines = [
  [0, 'State lives in a handler, not in the program: Get and Put ask its store.'],
  [8.3, 'Each run of the state machine moves order 7 one step.'],
  [16.3, 'A fourth run finds no step left: Try gives back Err.'],
  [22.7, 'Try never rolls the store back. The snapshot pattern does.'],
];
const STATE = f(31.5);
const MACHINE = [
  '@do',
  'def order_state_machine(order_id):',
  '    state = yield Get(f"order_{order_id}_state")',
  '    if state == "pending":',
  '        yield Put(f"order_{order_id}_state", "paid")',
  '    elif state == "paid":',
  '        yield Put(f"order_{order_id}_state", "shipped")',
  '    elif state == "shipped":',
  '        yield Put(f"order_{order_id}_state", "completed")',
  '    else:',
  '        raise ValueError(f"Invalid state: {state}")',
];
// From measure/state.log: the store after each run, and the line each run's Put is on.
const STEPS = ['pending', 'paid', 'shipped', 'completed'];
const RUNS = [
  {at: 9.0, branch: 3, put: 4},
  {at: 11.4, branch: 5, put: 6},
  {at: 13.8, branch: 7, put: 8},
];

const StateScene: React.FC = () => {
  const frame = useSceneFrame();
  const code = {x: 40, y: 96, w: 640, size: 15, line: 24};
  const box = {x: 730, y: 100, w: 500, h: 150};
  const away = ramp(frame, 22.7, 23.3);
  // Which run is on: its Get at `at`, its Put a second later.
  const run = RUNS.reduce((found, r, i) => (frame >= f(r.at) ? i : found), -1);
  const fourth = frame >= f(16.6);
  const band =
    fourth ? (frame < f(18.0) ? 2 : frame < f(18.3) ? 9 : 10)
    : run < 0 ? (frame >= f(1.6) ? 2 : null)
    : frame < f(RUNS[run].at + 1.2) ? 2 : frame < f(RUNS[run].at + 1.35) ? RUNS[run].branch : RUNS[run].put;
  const stored = frame >= f(16.0) ? 3 : run < 0 ? 0 : frame >= f(RUNS[run].at + 1.75) ? run + 1 : run;
  const changed = run >= 0 && between(frame, RUNS[run].at + 1.75, RUNS[run].at + 2.35);
  const log = [
    {text: 'log', color: MUTED},
    ...[0, 1, 2].map((i) => ({text: stored > i ? `Order 7: ${STEPS[i]} -> ${STEPS[i + 1]}` : ''})),
    {text: frame >= f(18.6) ? "4th run: Try -> Err(ValueError('Invalid state: completed'))" : '', color: FAIL},
  ];
  // The comparison of the last headline: the same failing operation under Try alone and inside with_state_snapshot.
  const sides = [
    {name: 'Try(drain_then_fail())', after: '0', color: FAIL, note: 'the Put before the failure stays'},
    {name: 'Try(with_state_snapshot(drain_then_fail))', after: '100', color: TEST, note: 'the snapshot is put back, then the error goes on'},
  ];
  return (
    <AbsoluteFill>
      <Headlines lines={STATE_LINES} />
      <div style={{opacity: 1 - away}}>
        <CodeBlock lines={MACHINE} {...code} band={band} opacity={fadeAt(frame, 0.2)} />
        <div
          style={{
            position: 'absolute', left: box.x, top: box.y, width: box.w, height: box.h, boxSizing: 'border-box', borderRadius: 16, background: PANEL,
            border: `3px solid ${TEST}`, ...glow(changed, TEST), opacity: fadeAt(frame, 1.0),
          }}
        >
          <div style={{position: 'absolute', left: 18, top: 12, fontFamily: MONO, fontSize: 22, fontWeight: 700, color: TEST}}>state()</div>
          <div style={{position: 'absolute', left: 120, top: 18, fontFamily: SANS, fontSize: 15, color: MUTED}}>the handler that holds the store</div>
          <div
            style={{
              position: 'absolute', left: 18, top: 62, width: box.w - 36, height: 62, boxSizing: 'border-box', borderRadius: 10, border: `2px dashed ${TEST}`,
              fontFamily: MONO, fontSize: 21, lineHeight: '58px', paddingLeft: 16, color: TEXT,
            }}
          >
            <span style={{color: MUTED}}>order_7_state = </span>
            <span style={{color: changed ? LOOK : TEXT, fontWeight: 700}}>"{STEPS[stored]}"</span>
          </div>
        </div>
        {/* Get goes out and the value comes back; then Put goes out with the next state. */}
        {RUNS.map((r, i) => (
          <React.Fragment key={i}>
            <Fly at={r.at} length={0.5} from={[code.x + code.w - 40, lineMid(code.y, code.line, 2)]} to={[box.x + 40, box.y + 92]} text="Get" color={ACCENT} />
            <Fly at={r.at + 0.8} length={0.4} from={[box.x + 40, box.y + 92]} to={[code.x + code.w - 40, lineMid(code.y, code.line, 2)]} text={`"${STEPS[i]}"`} color={TEST} />
            <Fly at={r.at + 1.35} length={0.4} from={[code.x + code.w - 40, lineMid(code.y, code.line, r.put)]} to={[box.x + 40, box.y + 92]} text={`Put "${STEPS[i + 1]}"`} color={ACCENT} />
          </React.Fragment>
        ))}
        <Fly at={16.8} length={0.5} from={[code.x + code.w - 40, lineMid(code.y, code.line, 2)]} to={[box.x + 40, box.y + 92]} text="Get" color={ACCENT} />
        <Fly at={17.6} length={0.4} from={[box.x + 40, box.y + 92]} to={[code.x + code.w - 40, lineMid(code.y, code.line, 2)]} text='"completed"' color={TEST} />
        <div style={{position: 'absolute', left: box.x, top: 280, display: 'flex', alignItems: 'center', gap: 8, opacity: fadeAt(frame, 8.6)}}>
          {STEPS.map((s, i) => (
            <React.Fragment key={s}>
              {i > 0 && <div style={{fontFamily: SANS, fontSize: 20, color: MUTED}}>→</div>}
              <div
                style={{
                  fontFamily: MONO, fontSize: 16, fontWeight: 700, padding: '6px 10px', borderRadius: 10, background: PANEL, whiteSpace: 'nowrap',
                  color: stored === i ? LOOK : stored > i ? TEST : MUTED, border: `2px solid ${stored === i ? LOOK : stored > i ? TEST : '#3a3f55'}`,
                }}
              >
                {s}
              </div>
            </React.Fragment>
          ))}
        </div>
        <Terminal x={code.x} y={400} w={1190} size={16} line={26} lines={log} opacity={fadeAt(frame, 9.0)} />
        {fourth && frame < f(19.2) && (
          <Pill x={mix(code.x + 420, code.x + 560, ramp(frame, 18.4, 19.0))} y={lineMid(code.y, code.line, 10)} text="ValueError" color={FAIL} opacity={fadeAt(frame, 18.4, 0.15)} />
        )}
      </div>
      {/* Try alone against the snapshot pattern: the balance after the same failure. */}
      {sides.map((side, i) => {
        const x = 50 + i * 600;
        const at = 23.4 + i * 0.6;
        const drained = frame >= f(25.0);
        const after = frame >= f(i === 0 ? 26.6 : 28.4);
        const value = !drained ? '100' : i === 0 || !after ? '0' : side.after;
        return (
          <div
            key={side.name}
            style={{
              position: 'absolute', left: x, top: 110, width: 580, height: 420, boxSizing: 'border-box', borderRadius: 18, background: PANEL,
              border: `3px solid ${after ? side.color : '#3a3f55'}`, boxShadow: after ? `0 0 18px ${side.color}55` : 'none', opacity: fadeAt(frame, at, 0.4),
            }}
          >
            <div style={{position: 'absolute', left: 22, top: 18, fontFamily: MONO, fontSize: i === 0 ? 20 : 17, fontWeight: 700, color: TEXT, whiteSpace: 'nowrap'}}>{side.name}</div>
            <div style={{position: 'absolute', left: 22, top: 56, fontFamily: SANS, fontSize: 16, color: MUTED}}>drain_then_fail: Put("_state", {'{'}"balance": 0{'}'}), then raise</div>
            <div
              style={{
                position: 'absolute', left: 22, top: 120, width: 536, height: 90, boxSizing: 'border-box', borderRadius: 14, border: `2px dashed ${TEST}`,
                fontFamily: MONO, fontSize: 30, lineHeight: '86px', paddingLeft: 22, color: TEXT,
              }}
            >
              <span style={{color: MUTED}}>balance = </span>
              <span style={{fontWeight: 700, color: between(frame, 25.0, 25.6) || (after && frame < f(i === 0 ? 27.2 : 29.0)) ? LOOK : TEXT}}>{value}</span>
            </div>
            <div style={{position: 'absolute', left: 22, top: 236, fontFamily: MONO, fontSize: 18, color: FAIL, opacity: fadeAt(frame, 25.8)}}>Err(RuntimeError('card declined'))</div>
            <div style={{position: 'absolute', left: 22, top: 290, fontFamily: SANS, fontSize: 20, fontWeight: 700, color: side.color, opacity: after ? fadeAt(frame, i === 0 ? 26.6 : 28.4) : 0}}>
              _state after = {'{'}'balance': {side.after}{'}'}
            </div>
            <div style={{position: 'absolute', left: 22, top: 330, fontFamily: SANS, fontSize: 17, color: MUTED, opacity: after ? 1 : 0}}>{side.note}</div>
          </div>
        );
      })}
      <Label x={50} y={560} size={17} bold={false} opacity={fadeAt(frame, 29.4)}>
        from measure/state.log: the store starts as {'{'}'balance': 100{'}'} in both runs
      </Label>
    </AbsoluteFill>
  );
};

// Scene 4 — a test is the same program under the stack it chooses.

const TEST_LINES: Lines = [
  [0, 'A test is the same program under a handler stack the test chooses.'],
  [6.3, 'The test reads the log and the store from its handlers. Nothing is patched.'],
  [12.2, 'The retry path runs on the virtual clock: 6 s of backoff, in milliseconds.'],
];
const TESTING = f(21);
const PROGRAM = ['@do', 'def program():', '    yield Put("key", "value")', '    yield Tell("Logged message")', '    return "result"'];
// From measure/testing.log: each test, the stack it runs under, what it checks, and its time.
const TESTS = [
  {name: 'test_effects_executed', at: 1.4, stack: 'run(state()(writer(program())))', checks: "result == 'result'", time: '0.16 ms'},
  {name: 'test_effects_seen', at: 7.0, stack: 'run(state()(writer(program_and_what_its_handlers_kept())))', checks: "(value, log, key) == ('result', ['Logged message'], 'value')", time: '0.06 ms'},
  {name: 'test_retry_on_a_virtual_clock', at: 12.9, stack: 'run(scheduled(sim_time_handler(...)(state()(writer(try_handler(...))))))', checks: "(value, seconds the clock moved) == ('data', 6.0)", time: '3.48 ms'},
];

const TestScene: React.FC = () => {
  const frame = useSceneFrame();
  const code = {x: 40, y: 110, w: 420, size: 16, line: 27};
  return (
    <AbsoluteFill>
      <Headlines lines={TEST_LINES} />
      <Label x={code.x + 4} y={code.y - 30} size={18} color={TEXT} opacity={fadeAt(frame, 0.2)}>the program under test</Label>
      <CodeBlock lines={PROGRAM} {...code} band={between(frame, 7.6, 9.4) ? (frame < f(8.5) ? 2 : 3) : null} opacity={fadeAt(frame, 0.2)} />
      <Label x={code.x + 4} y={code.y + PAD * 2 + PROGRAM.length * code.line + 18} size={16} bold={false} opacity={fadeAt(frame, 2.4)}>
        the same code in every test:<br />only the stack around it is chosen
      </Label>
      {TESTS.map((t, i) => {
        const y = 100 + i * 186;
        const passed = frame >= f(t.at + 2.4);
        return (
          <div
            key={t.name}
            style={{
              position: 'absolute', left: 500, top: y, width: 730, height: 168, boxSizing: 'border-box', borderRadius: 16, background: PANEL,
              border: `3px solid ${passed ? TEST : '#3a3f55'}`, ...glow(between(frame, t.at + 2.4, t.at + 3.2), TEST), opacity: fadeAt(frame, t.at, 0.4),
            }}
          >
            <div style={{position: 'absolute', left: 20, top: 12, fontFamily: MONO, fontSize: 20, fontWeight: 700, color: TEXT}}>{t.name}</div>
            <div style={{position: 'absolute', left: 20, top: 50, fontFamily: MONO, fontSize: 14, color: ACCENT, whiteSpace: 'nowrap', opacity: fadeAt(frame, t.at + 0.6)}}>{t.stack}</div>
            <div style={{position: 'absolute', left: 20, top: 84, fontFamily: MONO, fontSize: 16, color: TEXT, whiteSpace: 'nowrap', opacity: fadeAt(frame, t.at + 1.4)}}>
              <span style={{color: MUTED}}>assert </span>{t.checks}
            </div>
            <div style={{position: 'absolute', left: 20, top: 122, display: 'flex', gap: 14, alignItems: 'center', opacity: passed ? 1 : 0}}>
              <span style={{fontFamily: MONO, fontSize: 16, fontWeight: 700, padding: '3px 12px', borderRadius: 10, background: TEST, color: '#0f1117'}}>PASSED</span>
              <span style={{fontFamily: MONO, fontSize: 16, color: TEST}}>in {t.time}</span>
            </div>
          </div>
        );
      })}
      {/* The log and the store the second test reads back from writer and state. */}
      <Fly at={8.0} length={0.8} from={[code.x + code.w - 40, lineMid(code.y, code.line, 2)]} to={[700, 100 + 186 + 94]} text="Put → state()" color={TEST} />
      <Fly at={8.9} length={0.8} from={[code.x + code.w - 40, lineMid(code.y, code.line, 3)]} to={[900, 100 + 186 + 94]} text="Tell → writer" color={TEST} />
      <Label x={code.x + 4} y={560} size={17} color={TEST} opacity={fadeAt(frame, 14.8)}>
        6 s of backoff on the virtual clock,<br />no sleep in the test
      </Label>
    </AbsoluteFill>
  );
};

// Scene 5 — anti-patterns: a blocking sleep, and a side effect done in place.

const AVOID_LINES: Lines = [
  [0, "Patterns to avoid: a blocking sleep waits behind the time handler's back."],
  [6.5, 'Under the virtual clock, time.sleep(5) still holds 5 real seconds.'],
  [15.4, "The repository's semgrep rule reports the sleep as an error."],
  [21.2, 'A side effect done in place is invisible to handlers: declare WriteFile.'],
  [28.3, 'A handler gives WriteFile its meaning: a file, or a record in a test.'],
];
const AVOID = f(36);
const BAD_SLEEP = ['@do', 'def bad_program():', '    time.sleep(5)  # blocks', '    return "done"'];
const GOOD_SLEEP = ['@do', 'def good_program():', '    yield Delay(5)', '    return "done"'];
const BAD_WRITE = ['@do', 'def bad_side_effects():', '    with open("file.txt", "w") as f:', '        f.write("data")', '    return "done"'];
const GOOD_WRITE = ['@dataclass(frozen=True)', 'class WriteFile(EffectBase):', '    path: str', '    data: str', ' ', '@do', 'def good_side_effects():', '    yield WriteFile(path="file.txt", data="data")', '    return "done"'];

const AvoidScene: React.FC = () => {
  const frame = useSceneFrame();
  const first = 1 - ramp(frame, 21.0, 21.5);
  const second = fadeAt(frame, 21.4, 0.4);
  const sides = [
    {code: BAD_SLEEP, title: 'BAD', color: FAIL, x: 40, wall: '5.006 s', clock: '+0 s', share: 1, fill: ramp(frame, 7.4, 12.4), at: 7.2},
    {code: GOOD_SLEEP, title: 'GOOD', color: TEST, x: 650, wall: '0.001 s', clock: '+5 s', share: 0.001 / 5.006, fill: ramp(frame, 12.8, 12.85), at: 12.6},
  ];
  const sub = {size: 17, line: 28, w: 590};
  const handlers = [
    {name: 'write_to_disk', note: 'the bytes go to a file', result: "files in the directory: ['file.txt']", color: PROD, y: 470, at: 29.4},
    {name: 'record_writes', note: 'what a test installs', result: "writes = {'file.txt': 'data'}; no file", color: TEST, y: 580, at: 32.0},
  ];
  return (
    <AbsoluteFill>
      <Headlines lines={AVOID_LINES} />
      <div style={{opacity: first}}>
        {sides.map((s) => (
          <React.Fragment key={s.title}>
            <Label x={s.x + 4} y={96} size={19} color={s.color} opacity={fadeAt(frame, s.title === 'BAD' ? 0.4 : 2.4)}>{s.title}</Label>
            <CodeBlock lines={s.code} x={s.x} y={126} w={sub.w} size={sub.size} line={sub.line} band={s.title === 'BAD' && between(frame, 7.4, 12.4) ? 2 : s.title === 'GOOD' && between(frame, 12.6, 13.4) ? 2 : null} opacity={fadeAt(frame, s.title === 'BAD' ? 0.4 : 2.4)} />
            <div
              style={{
                position: 'absolute', left: s.x, top: 290, width: sub.w, height: 104, boxSizing: 'border-box', borderRadius: 14, background: PANEL,
                border: `2px solid ${s.color}`, opacity: fadeAt(frame, s.at),
              }}
            >
              <div style={{position: 'absolute', left: 16, top: 10, fontFamily: MONO, fontSize: 15, color: MUTED}}>under sim_time_handler (measure/antipatterns.log)</div>
              <div style={{position: 'absolute', left: 16, top: 42, fontFamily: MONO, fontSize: 17, color: TEXT}}>virtual clock <span style={{color: s.color, fontWeight: 700}}>{s.fill >= 1 ? s.clock : '…'}</span></div>
              <div style={{position: 'absolute', left: 16, top: 70, width: 300, height: 18, borderRadius: 6, background: '#0b0d13', border: '2px solid #2a2e40', overflow: 'hidden'}}>
                <div style={{width: `${s.fill > 0 ? Math.max(s.fill * s.share * 100, 1) : 0}%`, height: '100%', background: s.color}} />
              </div>
              <div style={{position: 'absolute', left: 330, top: 66, fontFamily: MONO, fontSize: 17, fontWeight: 700, color: s.color, opacity: s.fill >= 1 ? 1 : 0}}>wall {s.wall}</div>
            </div>
          </React.Fragment>
        ))}
        <Terminal
          x={40} y={420} w={1200} size={16} line={27}
          lines={[
            {text: '$ semgrep --config .semgrep.yaml examples/bad_program.py', color: MUTED},
            {text: frame >= f(16.6) ? 'ERROR  doeff-no-sleep-in-programs  examples/bad_program.py:9   time.sleep(5)' : '', color: FAIL},
            {text: frame >= f(17.4) ? 'A doeff program waits by yielding a doeff-time effect, not by calling time.sleep() or' : ''},
            {text: frame >= f(17.8) ? 'asyncio.sleep() (Await(asyncio.sleep(...)) included). Use `yield Delay(seconds)` (or' : ''},
            {text: frame >= f(18.2) ? 'WaitUntil / WaitWithin) from doeff_time: the caller\'s time handler decides what a second …' : ''},
          ]}
          opacity={fadeAt(frame, 15.8)}
        />
      </div>
      <div style={{opacity: second}}>
        <Label x={44} y={96} size={19} color={FAIL}>BAD <span style={{fontWeight: 400, fontSize: 16, color: MUTED}}>no handler sees the write</span></Label>
        <CodeBlock lines={BAD_WRITE} x={40} y={126} w={560} size={16} line={26} band={between(frame, 22.4, 24.6) ? 2 : null} />
        <Label x={654} y={96} size={19} color={TEST} opacity={fadeAt(frame, 24.8)}>GOOD <span style={{fontWeight: 400, fontSize: 16, color: MUTED}}>the write is an effect</span></Label>
        <CodeBlock lines={GOOD_WRITE} x={650} y={126} w={590} size={16} line={26} band={frame >= f(28.6) ? 7 : null} opacity={fadeAt(frame, 24.8)} />
        {handlers.map((h) => (
          <React.Fragment key={h.name}>
            <HandlerBox x={650} y={h.y} w={590} h={90} name={h.name} note={h.note} color={h.color} on={between(frame, h.at + 0.8, h.at + 2.4)} opacity={fadeAt(frame, 28.6)} />
            <div style={{position: 'absolute', left: 668, top: h.y + 60, fontFamily: MONO, fontSize: 15, color: h.color, opacity: fadeAt(frame, h.at + 1.0)}}>{h.result}</div>
            <Fly at={h.at} length={0.8} from={[1000, lineMid(126, 26, 7)]} to={[1000, h.y + 30]} text='WriteFile("file.txt", "data")' color={ACCENT} />
          </React.Fragment>
        ))}
        <Label x={44} y={300} size={17} bold={false} opacity={fadeAt(frame, 25.4)}>
          open() and write() run in place:
          <br />
          the effect never leaves the program,
          <br />
          so no handler can give it a meaning.
        </Label>
        <Label x={44} y={480} size={18} color={TEXT} opacity={fadeAt(frame, 33.4)}>
          good_side_effects() returns 'done'
          <br />
          <span style={{fontWeight: 400, color: MUTED}}>under both handlers; the program is the same</span>
        </Label>
      </div>
    </AbsoluteFill>
  );
};

// Outro — the page in one sentence.

const OUTRO = f(10);

const Outro: React.FC = () => {
  const frame = useSceneFrame();
  const rise = interpolate(frame, [0, f(0.67)], [20, 0], {extrapolateRight: 'clamp', easing: Easing.out(Easing.cubic)});
  const steps = [
    {text: 'yield effects', color: ACCENT},
    {text: 'handler stack', color: TEST},
    {text: 'the meaning you choose', color: LOOK},
  ];
  const patterns = ['layers', 'retry with backoff', 'state machine', 'snapshot', 'tests on a virtual clock'];
  return (
    <AbsoluteFill style={{padding: 90, justifyContent: 'center', transform: `translateY(${rise}px)`}}>
      <div style={{fontFamily: SANS, fontSize: 56, fontWeight: 700, color: TEXT, opacity: fadeAt(frame, 0, 0.4)}}>Patterns</div>
      <div style={{fontFamily: SANS, fontSize: 30, color: ACCENT, marginTop: 6, opacity: fadeAt(frame, 0.2, 0.4)}}>Programs say what they need; handlers decide what it means</div>
      <div style={{display: 'flex', alignItems: 'center', marginTop: 46, gap: 18}}>
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
      <div style={{display: 'flex', gap: 12, marginTop: 34, opacity: fadeAt(frame, 3.8, 0.4)}}>
        {patterns.map((p) => (
          <div key={p} style={{fontFamily: SANS, fontSize: 18, color: MUTED, padding: '6px 12px', borderRadius: 10, border: '2px solid #3a3f55', whiteSpace: 'nowrap'}}>{p}</div>
        ))}
      </div>
      <div style={{fontFamily: MONO, fontSize: 26, color: TEST, marginTop: 44, opacity: fadeAt(frame, 4.8, 0.4)}}>pip install doeff</div>
    </AbsoluteFill>
  );
};

// The scenes in order; Root.tsx registers PATTERNS.length frames of PATTERNS.Video.
export const PATTERNS = narrate('patterns', DURATIONS, [
  {length: LAYER, starts: startsOf(LAYER_LINES), ids: ['a1', 'a2', 'a3', 'a4'], node: <LayerScene />},
  {length: RETRY, starts: startsOf(RETRY_LINES), ids: ['b1', 'b2', 'b3', 'b4'], node: <RetryScene />},
  {length: STATE, starts: startsOf(STATE_LINES), ids: ['c1', 'c2', 'c3', 'c4'], node: <StateScene />},
  {length: TESTING, starts: startsOf(TEST_LINES), ids: ['d1', 'd2', 'd3'], node: <TestScene />},
  {length: AVOID, starts: startsOf(AVOID_LINES), ids: ['e1', 'e2', 'e3', 'e4', 'e5'], node: <AvoidScene />},
  {length: OUTRO, starts: [0], ids: ['o1'], node: <Outro />},
]);
