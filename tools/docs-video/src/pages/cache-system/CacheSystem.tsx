// The video on the Cache System page (docs/07-cache-system.md): caching is two effects and the handler that answers
// them. Five scenes and an outro, each with headlines a synthetic voice reads (narration.json):
//   1. CacheGet asks for the value under a key, CachePut stores one; which cache answers is the handler's choice.
//   2. Miss, then hit: the handler throws KeyError into the program at the yield, the program computes and stores;
//      the second call is answered from the cache (408.2 ms, then 0.5 ms).
//   3. The policy (ttl, lifecycle, storage, metadata) travels inside CachePut; the handler decides what it means.
//   4. The page's API example: two calls to fetch_user(1), one GetJson for the handler fake_api.
//   5. The same program under in_memory_cache_handler, sqlite_cache_handler, and no cache handler.
// Every output, order and time on screen is printed by a program in measure/ (its .log is next to it).
import React from 'react';
import {AbsoluteFill, Easing, interpolate} from 'remotion';
import {fadeAt, mix, ramp} from '../../kit/motion';
import {narrate} from '../../kit/narrated';
import {CodeBlock, glow, Headlines, lineMid, Lines, Pill, startsOf} from '../../kit/parts';
import {ACCENT, f, FAIL, LOOK, MONO, MUTED, PANEL, PROD, SANS, TEST, TEXT} from '../../kit/theme';
import {useSceneFrame} from '../../kit/warp';
import DURATIONS from './durations.json';

// Seen between two seconds of a scene: fades in at the first and out at the second.
const between = (frame: number, from: number, to: number, length = 0.3) => fadeAt(frame, from, length) * (1 - fadeAt(frame, to, length));

// A label in the scenes' sans font.
const Label: React.FC<{x: number; y: number; size?: number; color?: string; bold?: boolean; opacity?: number; children: React.ReactNode}> = ({
  x, y, size = 20, color = MUTED, bold = false, opacity = 1, children,
}) => (
  <div style={{position: 'absolute', left: x, top: y, fontFamily: SANS, fontSize: size, fontWeight: bold ? 700 : 400, color, opacity, whiteSpace: 'nowrap'}}>
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

// A cache handler's box: its name, and its storage as rows of key → value. A row appears at its time and glows
// while the handler reads or writes it; with no rows the storage reads "(empty)".
type Row = {key: string; value: string; at: number; lit?: [number, number][]};
const Cache: React.FC<{x: number; y: number; w: number; h: number; name: string; color: string; on: boolean; rows: Row[]; missAt?: [number, number]; opacity?: number}> = ({
  x, y, w, h, name, color, on, rows, missAt, opacity = 1,
}) => {
  const frame = useSceneFrame();
  const shown = rows.filter((row) => frame >= f(row.at));
  const missing = missAt !== undefined && frame >= f(missAt[0]) && frame < f(missAt[1]);
  return (
    <Box x={x} y={y} w={w} h={h} color={color} on={on} opacity={opacity}>
      <div style={{position: 'absolute', left: 20, top: 12, fontFamily: MONO, fontSize: 21, fontWeight: 700, color: on ? LOOK : color, whiteSpace: 'nowrap'}}>{name}</div>
      <div style={{position: 'absolute', left: 20, top: 46, fontFamily: SANS, fontSize: 16, color: MUTED}}>its storage</div>
      <div
        style={{
          position: 'absolute', left: 20, right: 20, top: 72, bottom: 16, borderRadius: 10, background: '#0b0d13',
          border: `2px solid ${missing ? FAIL : '#2a2e40'}`, boxShadow: missing ? `0 0 16px ${FAIL}88` : 'none', padding: '8px 14px',
          fontFamily: MONO, fontSize: 18, lineHeight: '30px',
        }}
      >
        {shown.length === 0 && <div style={{color: missing ? FAIL : MUTED}}>{missing ? '(empty: no "my_key")' : '(empty)'}</div>}
        {shown.map((row) => {
          const lit = (row.lit ?? []).some(([from, to]) => frame >= f(from) && frame < f(to));
          return (
            <div key={row.key} style={{whiteSpace: 'nowrap', opacity: fadeAt(frame, row.at), color: lit ? LOOK : TEXT, fontWeight: lit ? 700 : 400}}>
              <span style={{color: lit ? LOOK : ACCENT}}>{row.key}</span>
              <span style={{color: MUTED}}>{'  →  '}</span>
              {row.value}
            </div>
          );
        })}
      </div>
    </Box>
  );
};

// A pill that travels from one point to another between two seconds, and is gone after it arrives.
const Travel: React.FC<{from: [number, number]; to: [number, number]; at: number; length?: number; text: string; color: string}> = ({from, to, at, length = 1.1, text, color}) => {
  const frame = useSceneFrame();
  if (frame < f(at) || frame >= f(at + length + 0.2)) return null;
  const t = ramp(frame, at, at + length);
  return <Pill x={mix(from[0], to[0], t)} y={mix(from[1], to[1], t)} text={text} color={color} opacity={1 - ramp(frame, at + length, at + length + 0.2)} />;
};

// The line a code box's band is on: [second, line] steps, moving smoothly between lines in 0.3 s.
const bandOf = (frame: number, steps: [number, number][]) => {
  const step = steps.reduce((found, [at], i) => (frame >= f(at) ? i : found), -1);
  if (step < 0) return null;
  if (step === 0) return steps[0][1];
  return mix(steps[step - 1][1], steps[step][1], ramp(frame, steps[step][0], steps[step][0] + 0.3));
};

// Scene 1 — two effects ask and store; the handler around the program answers them.

const INTRO_LINES: Lines = [
  [0, 'The cache system: the program asks for a value it has worked out before.'],
  [8, 'CacheGet asks for the value under a key. CachePut stores one.'],
  [16, 'Which cache answers them is up to the handler around the program.'],
];
const INTRO = f(23);
const REPORT = ['@do', 'def report():', '    try:', '        return (yield CacheGet("report"))', '    except KeyError:', '        ...'];
const EFFECTS = [
  {name: 'CacheGet(key)', says: 'asks for the value stored under key', then: 'a miss raises KeyError at the yield'},
  {name: 'CachePut(key, value, **policy)', says: 'stores the value under key', then: 'with policy hints: ttl, lifecycle, storage'},
];
const HANDLERS = [
  {name: 'in_memory_cache_handler()', color: TEST},
  {name: 'sqlite_cache_handler(path)', color: PROD},
  {name: 'your own handler', color: MUTED},
];

const IntroScene: React.FC = () => {
  const frame = useSceneFrame();
  const code = {x: 50, y: 110, w: 600, size: 19, line: 32};
  const cache = {x: 720, y: 110, w: 510, h: 220};
  const ask = lineMid(code.y, code.line, 3);
  const handling = frame >= f(3.0) && frame < f(5.4);
  // From 16 s the handler box stands for any cache handler: the name cycles through the choices.
  const which = frame < f(16.6) ? -1 : Math.floor((frame - f(16.6)) / f(1.6)) % HANDLERS.length;
  const chosen = which < 0 ? {name: 'a cache handler', color: TEST} : HANDLERS[which];
  return (
    <AbsoluteFill>
      <Headlines lines={INTRO_LINES} />
      <CodeBlock lines={REPORT} {...code} band={bandOf(frame, [[1.4, 3]])} opacity={fadeAt(frame, 0.2)} />
      <Cache
        {...cache} name={chosen.name} color={chosen.color} on={handling} opacity={fadeAt(frame, 0.6)}
        rows={[{key: '"report"', value: '"Q3 summary"', at: 0.6, lit: [[3.4, 4.4]]}]}
      />
      <Travel from={[code.x + code.w - 40, ask]} to={[cache.x + 120, cache.y + 120]} at={2.0} text='CacheGet("report")' color={ACCENT} />
      <Travel from={[cache.x + 120, cache.y + 150]} to={[code.x + code.w - 60, ask]} at={4.4} text='"Q3 summary"' color={TEST} />
      <Label x={code.x} y={code.y + 24 + REPORT.length * code.line + 14} size={19} color={TEXT} opacity={between(frame, 5.6, 15.6)}>
        report() gets the stored value back at its yield, and returns it.
      </Label>
      {EFFECTS.map((e, i) => (
        <Box key={e.name} x={50 + i * 600} y={420} w={580} h={140} color={ACCENT} on={frame >= f(9.0 + i * 2.6) && frame < f(10.6 + i * 2.6)} opacity={fadeAt(frame, 8.4 + i * 2.6, 0.4) * mix(1, 0.35, ramp(frame, 16.0, 16.5))}>
          <div style={{position: 'absolute', left: 20, top: 14, fontFamily: MONO, fontSize: 23, fontWeight: 700, color: ACCENT, whiteSpace: 'nowrap'}}>{e.name}</div>
          <div style={{position: 'absolute', left: 20, top: 56, fontFamily: SANS, fontSize: 20, color: TEXT}}>{e.says}</div>
          <div style={{position: 'absolute', left: 20, top: 90, fontFamily: SANS, fontSize: 18, color: MUTED}}>{e.then}</div>
        </Box>
      ))}
      <Label x={50} y={600} size={20} color={TEXT} opacity={fadeAt(frame, 17.0)}>
        The same program can run under
      </Label>
      {HANDLERS.map((h, i) => (
        <div
          key={h.name}
          style={{
            position: 'absolute', left: 50 + i * 400, top: 640, width: 380, height: 46, boxSizing: 'border-box', borderRadius: 12, background: PANEL,
            border: `2px ${h.color === MUTED ? 'dashed' : 'solid'} ${h.color}`, ...glow(which === i, h.color), fontFamily: MONO, fontSize: 18, fontWeight: 700,
            color: h.color, lineHeight: '42px', textAlign: 'center', opacity: fadeAt(frame, 17.4 + i * 0.4),
          }}
        >
          {h.name}
        </div>
      ))}
    </AbsoluteFill>
  );
};

// Scene 2 — miss, then hit: the page's with_manual_cache(), called twice (measure/miss_then_hit.log).

const MISS_LINES: Lines = [
  [0, 'First call: CacheGet goes out, and the cache has nothing under "my_key".'],
  [6.5, 'The handler throws KeyError into the program, right at the yield.'],
  [13, 'The program catches it, does the work (408.2 ms), and yields CachePut.'],
  [21, 'Second call: the handler finds "my_key" and resumes the program with 42.'],
  [28.5, '0.5 ms instead of 408.2 ms. The log: a miss, then a hit.'],
];
const MISS = f(35);
const MANUAL = [
  '@do',
  'def with_manual_cache():',
  '    try:',
  '        value = yield CacheGet("my_key")',
  '        yield Tell("Cache hit!")',
  '    except KeyError:',
  '        yield Tell("Cache miss, computing...")',
  '        value = yield expensive_operation()',
  '        yield CachePut("my_key", value, ttl=300, ...)',
  '    return value',
];
// The times measure/miss_then_hit.py printed for its two calls.
const CALLS = [
  {name: 'call 1', how: 'miss', ms: 408.2, grow: [14.0, 17.0], at: 13.6, color: FAIL},
  {name: 'call 2', how: 'hit', ms: 0.5, grow: [28.8, 29.0], at: 28.8, color: TEST},
];
const BAR_MAX = 640;

const MissScene: React.FC = () => {
  const frame = useSceneFrame();
  const code = {x: 40, y: 96, w: 640, size: 17, line: 28};
  const cache = {x: 730, y: 96, w: 500, h: 200};
  const at = (i: number) => lineMid(code.y, code.line, i);
  const out: [number, number] = [code.x + code.w - 40, at(3)];
  const into: [number, number] = [cache.x + 110, cache.y + 110];
  const band = bandOf(frame, [[0.8, 3], [9.0, 5], [10.0, 6], [13.6, 7], [17.4, 8], [19.6, 9], [21.6, 3], [25.8, 4], [26.6, 9]]);
  const handling = (frame >= f(2.8) && frame < f(8.6)) || (frame >= f(18.8) && frame < f(19.6)) || (frame >= f(23.4) && frame < f(25.6));
  const thrown = frame >= f(8.6) && frame < f(10.0);
  return (
    <AbsoluteFill>
      <Headlines lines={MISS_LINES} />
      <CodeBlock lines={MANUAL} {...code} band={band} opacity={fadeAt(frame, 0.2)} />
      <div
        style={{
          position: 'absolute', left: code.x - 4, top: at(3) - code.line / 2 - 4, width: code.w + 8, height: code.line + 8, boxSizing: 'border-box', borderRadius: 8,
          border: `3px solid ${FAIL}`, boxShadow: `0 0 18px ${FAIL}aa`, opacity: thrown ? 1 : 0,
        }}
      />
      <Cache
        {...cache} name="in_memory_cache_handler()" color={TEST} on={handling} missAt={[3.4, 8.6]} opacity={fadeAt(frame, 0.4)}
        rows={[{key: '"my_key"', value: '42', at: 18.8, lit: [[18.8, 19.8], [23.4, 24.6]]}]}
      />
      <Label x={cache.x} y={cache.y + cache.h + 12} size={17} color={FAIL} opacity={between(frame, 7.4, 12.6)}>
        ResumeThrow(k, KeyError("my_key"))
      </Label>
      <Label x={cache.x} y={cache.y + cache.h + 12} size={17} color={TEST} opacity={between(frame, 24.4, 28.2)}>
        Resume(k, 42)
      </Label>
      {/* First call: the ask, the miss thrown back in, the work, and the store. */}
      <Travel from={out} to={into} at={1.6} text='CacheGet("my_key")' color={ACCENT} />
      <Travel from={[cache.x + 110, cache.y + 150]} to={out} at={7.4} text="KeyError('my_key')" color={FAIL} />
      <Travel from={[code.x + code.w - 40, at(8)]} to={into} at={17.6} length={1.2} text='CachePut("my_key", 42)' color={ACCENT} />
      {/* Second call: the ask and the stored value coming back. */}
      <Travel from={out} to={into} at={22.2} text='CacheGet("my_key")' color={ACCENT} />
      <Travel from={[cache.x + 110, cache.y + 150]} to={out} at={24.4} text="42" color={TEST} />
      {/* The two calls' wall times, as bars to one scale. */}
      {CALLS.map((c, i) => {
        const y = 440 + i * 62;
        const width = Math.max(4, (c.ms / CALLS[0].ms) * BAR_MAX * ramp(frame, c.grow[0], c.grow[1]));
        return (
          <React.Fragment key={c.name}>
            <Label x={40} y={y + 8} size={20} color={TEXT} bold opacity={fadeAt(frame, c.at)}>{c.name}</Label>
            <Label x={130} y={y + 10} size={18} color={c.color} opacity={fadeAt(frame, c.at)}>{c.how}</Label>
            <div style={{position: 'absolute', left: 200, top: y + 6, width, height: 32, borderRadius: 8, background: c.color, boxShadow: `0 0 14px ${c.color}88`, opacity: fadeAt(frame, c.at)}} />
            <Label x={200 + Math.max(4, (c.ms / CALLS[0].ms) * BAR_MAX) + 18} y={y + 8} size={21} color={TEXT} bold opacity={fadeAt(frame, c.grow[1])}>
              {c.ms} ms
            </Label>
          </React.Fragment>
        );
      })}
      <Label x={200} y={418} size={15} color={MUTED} opacity={between(frame, 14.0, 17.2)}>expensive_operation() at work</Label>
      <Terminal
        x={40} y={590} w={1190} size={18} line={28}
        lines={[{text: "log: ['Cache miss, computing...', 'Cache hit!']", color: TEXT}]}
        opacity={fadeAt(frame, 30.4)}
      />
    </AbsoluteFill>
  );
};

// Scene 3 — the policy travels inside CachePut (measure/policy.log).

const POLICY_LINES: Lines = [
  [0, 'CachePut can carry a policy: ttl, lifecycle, storage and metadata.'],
  [8, 'The policy travels inside the effect: the handler gets it with the value.'],
  [14.5, 'A PERSISTENT lifecycle resolves to DISK. With no hints, MEMORY.'],
  [22, 'They are hints, and the handler decides what they mean.'],
];
const POLICY = f(34);
const PUT = ['yield CachePut(', '    "my_key",', '    value,', '    ttl=300,', '    lifecycle=CacheLifecycle.PERSISTENT,', '    storage=CacheStorage.DISK,', ')'];
// The page's table of policy fields.
const FIELDS = [
  {name: 'ttl', type: 'float', purpose: 'time-to-live in seconds', line: 3},
  {name: 'lifecycle', type: 'CacheLifecycle', purpose: 'TRANSIENT, SESSION, PERSISTENT', line: 4},
  {name: 'storage', type: 'CacheStorage', purpose: 'MEMORY, DISK', line: 5},
  {name: 'metadata', type: 'dict', purpose: 'custom metadata', line: -1},
];
// What the handler received for each CachePut in measure/policy.py, and the storage each policy resolves to.
const RESOLVED = [
  {put: "CachePut('my_key', ...)", ttl: '300', lifecycle: 'PERSISTENT', storage: 'DISK', resolved: 'DISK'},
  {put: "CachePut('user_1', ...)", ttl: '60', lifecycle: 'TRANSIENT', storage: 'None', resolved: 'MEMORY'},
  {put: "CachePut('report', ...)", ttl: 'None', lifecycle: 'PERSISTENT', storage: 'None', resolved: 'DISK'},
  {put: "CachePut('tmp', ...)", ttl: 'None', lifecycle: 'TRANSIENT', storage: 'None', resolved: 'MEMORY'},
];
const COLS = [{key: 'put', x: 0}, {key: 'ttl', x: 300}, {key: 'lifecycle', x: 400}, {key: 'storage', x: 570}, {key: 'resolved', x: 700}] as const;

const PolicyScene: React.FC = () => {
  const frame = useSceneFrame();
  const code = {x: 40, y: 100, w: 560, size: 18, line: 30};
  const handler = {x: 700, y: 100, w: 530, h: 234};
  const fieldOn = (i: number) => frame >= f(1.4 + i * 1.4) && frame < f(2.8 + i * 1.4);
  const lit = Math.floor((frame - f(1.4)) / f(1.4));
  const band = frame < f(1.4) || lit >= 3 ? null : FIELDS[lit].line;
  const fieldsOut = ramp(frame, 7.6, 8.2);
  const carried = ramp(frame, 9.0, 10.6);
  const received = frame >= f(10.6);
  const tableIn = fadeAt(frame, 14.8);
  const tableOut = ramp(frame, 22.2, 22.8);
  return (
    <AbsoluteFill>
      <Headlines lines={POLICY_LINES} />
      <CodeBlock lines={PUT} {...code} band={band} opacity={fadeAt(frame, 0.2) * mix(1, 0.5, ramp(frame, 22.2, 22.8))} />
      {/* The page's table of fields, one lit at a time with its line of the call. */}
      {FIELDS.map((field, i) => (
        <div
          key={field.name}
          style={{
            position: 'absolute', left: 660, top: 100 + i * 58, width: 570, height: 48, boxSizing: 'border-box', borderRadius: 10, background: PANEL,
            border: `2px solid ${fieldOn(i) ? LOOK : '#2a2e40'}`, boxShadow: fieldOn(i) ? `0 0 14px ${LOOK}66` : 'none', padding: '0 16px', lineHeight: '44px',
            fontFamily: SANS, fontSize: 16, color: TEXT, whiteSpace: 'nowrap', opacity: fadeAt(frame, 0.6 + i * 0.3) * (1 - fieldsOut),
          }}
        >
          <span style={{fontFamily: MONO, fontWeight: 700, color: ACCENT, display: 'inline-block', width: 100}}>{field.name}</span>
          <span style={{fontFamily: MONO, color: MUTED, display: 'inline-block', width: 160}}>{field.type}</span>
          {field.purpose}
        </div>
      ))}
      {/* The effect with its policy inside, on its way to the handler. */}
      <Box x={handler.x} y={handler.y} w={handler.w} h={handler.h} color={TEST} on={received && frame < f(13.6)} opacity={fadeAt(frame, 8.2) * mix(1, 0.5, ramp(frame, 22.2, 22.8))}>
        <div style={{position: 'absolute', left: 20, top: 12, fontFamily: MONO, fontSize: 21, fontWeight: 700, color: TEST}}>the cache handler</div>
        <div style={{position: 'absolute', left: 20, top: 46, fontFamily: SANS, fontSize: 16, color: MUTED}}>receives the effect, and in it, effect.policy</div>
      </Box>
      {frame >= f(8.6) && (
        <div
          style={{
            position: 'absolute', left: mix(code.x + 40, handler.x + 20, carried), top: mix(code.y + 30, handler.y + 84, carried), width: 490, boxSizing: 'border-box',
            borderRadius: 12, background: ACCENT, boxShadow: `0 0 16px ${ACCENT}`, padding: '8px 14px', opacity: fadeAt(frame, 8.6) * mix(1, 0.5, ramp(frame, 22.2, 22.8)),
          }}
        >
          <div style={{fontFamily: MONO, fontSize: 19, fontWeight: 700, color: '#0f1117'}}>{"CachePut('my_key', ...)"}</div>
          <div style={{marginTop: 6, borderRadius: 8, background: '#0b0d13', padding: '6px 12px', fontFamily: MONO, fontSize: 16, lineHeight: '24px', color: TEXT}}>
            <span style={{color: MUTED}}>policy: </span>ttl=300 lifecycle=PERSISTENT<br />
            <span style={{color: MUTED}}>{'        '}</span>storage=DISK metadata={'{}'}
          </div>
        </div>
      )}
      {/* What each policy resolves to. */}
      <div style={{position: 'absolute', left: 40, top: 380, width: 1190, opacity: tableIn * (1 - tableOut)}}>
        <div style={{position: 'relative', height: 32, fontFamily: SANS, fontSize: 16, fontWeight: 700, color: MUTED}}>
          {COLS.map((col) => (
            <div key={col.key} style={{position: 'absolute', left: col.x + 16, top: 4}}>{col.key === 'resolved' ? 'resolved_storage()' : col.key === 'put' ? 'the handler got' : col.key}</div>
          ))}
        </div>
        {RESOLVED.map((row, i) => {
          const at = 15.4 + i * 1.2;
          const disk = row.resolved === 'DISK';
          return (
            <div
              key={row.put}
              style={{
                position: 'relative', height: 44, marginTop: 6, borderRadius: 10, background: '#0b0d13', border: '2px solid #2a2e40', fontFamily: MONO, fontSize: 18,
                color: TEXT, opacity: fadeAt(frame, at), transform: `translateY(${mix(10, 0, ramp(frame, at, at + 0.4))}px)`,
              }}
            >
              {COLS.map((col) => (
                <div key={col.key} style={{position: 'absolute', left: col.x + 16, top: 9, whiteSpace: 'nowrap', color: col.key === 'resolved' ? (disk ? PROD : TEST) : col.key === 'put' ? ACCENT : TEXT, fontWeight: col.key === 'resolved' ? 700 : 400}}>
                  {col.key === 'resolved' ? `→ ${row.resolved}` : row[col.key]}
                </div>
              ))}
            </div>
          );
        })}
      </div>
      {/* The hint: in_memory_cache_handler keeps an entry with ttl=0.1 the same way as any other. */}
      <div style={{position: 'absolute', left: 40, top: 390, width: 1190, opacity: fadeAt(frame, 23.0)}}>
        <div style={{fontFamily: SANS, fontSize: 20, color: TEXT}}>
          Under <span style={{fontFamily: MONO, color: TEST, fontWeight: 700}}>in_memory_cache_handler()</span>, the policy is kept as a hint: every entry is stored the same way.
        </div>
        <div style={{position: 'relative', height: 150, marginTop: 20}}>
          <div style={{position: 'absolute', left: 60, right: 60, top: 70, height: 4, background: '#2a2e40'}} />
          <div style={{position: 'absolute', left: 60, top: 70, height: 4, width: 1070 * ramp(frame, 24.4, 27.4), background: TEXT}} />
          {[
            {at: 0, x: 60, text: 'CachePut("short", "value", ttl=0.1)', color: ACCENT, show: 23.8},
            {at: 0.1, x: 60 + 1070 / 3, text: 'ttl = 0.1 s', color: MUTED, show: 25.4},
            {at: 0.3, x: 1130, text: 'CacheGet("short")', color: ACCENT, show: 27.4},
          ].map((mark, i) => (
            <React.Fragment key={mark.text}>
              <div style={{position: 'absolute', left: mark.x - 2, top: 58, width: 4, height: 28, background: mark.color, opacity: fadeAt(frame, mark.show)}} />
              <div style={{position: 'absolute', left: mark.x, top: i === 1 ? 96 : 18, transform: i === 0 ? 'none' : i === 2 ? 'translateX(-100%)' : 'translateX(-50%)', fontFamily: MONO, fontSize: 18, fontWeight: 700, color: mark.color, whiteSpace: 'nowrap', opacity: fadeAt(frame, mark.show)}}>
                {mark.text}
              </div>
              <div style={{position: 'absolute', left: mark.x, top: 96, transform: i === 2 ? 'translateX(-100%)' : 'none', fontFamily: SANS, fontSize: 16, color: MUTED, opacity: i === 1 ? 0 : fadeAt(frame, mark.show)}}>
                {mark.at} s
              </div>
            </React.Fragment>
          ))}
        </div>
        <Terminal
          x={0} y={190} w={1190} size={18} line={28}
          lines={[{text: "in_memory_cache_handler, ttl=0.1, read 0.3 s later: 'value'", color: TEST}]}
          opacity={fadeAt(frame, 28.0)}
        />
      </div>
    </AbsoluteFill>
  );
};

// Scene 4 — the page's API example: two calls, one request (measure/api_response.log).

const API_LINES: Lines = [
  [0, 'The page\'s API example: fetch_user(1), called twice.'],
  [6, 'First call: a miss, one request, then CachePut. Second call: from the cache.'],
  [15, 'fake_api got one request: /users/1.'],
];
const API = f(21);
const FETCH = [
  '@do',
  'def fetch_user(user_id):',
  '    try:',
  '        return (yield CacheGet(f"user_{user_id}"))',
  '    except KeyError:',
  '        data = yield GetJson(f"/users/{user_id}")',
  '        yield CachePut(f"user_{user_id}", data, ttl=60)',
  '        return data',
];
const ADA = "{'id': 1, 'name': 'Ada'}";

const ApiScene: React.FC = () => {
  const frame = useSceneFrame();
  const code = {x: 40, y: 100, w: 640, size: 16, line: 28};
  const cache = {x: 730, y: 100, w: 500, h: 160};
  const server = {x: 730, y: 290, w: 500, h: 130};
  const at = (i: number) => lineMid(code.y, code.line, i);
  const out: [number, number] = [code.x + code.w - 40, at(3)];
  const into: [number, number] = [cache.x + 120, cache.y + 100];
  const band = bandOf(frame, [[0.8, 3], [7.8, 5], [9.8, 6], [10.8, 7], [11.6, 3]]);
  const requested = frame >= f(8.8);
  const handling = (frame >= f(7.0) && frame < f(7.6)) || (frame >= f(10.4) && frame < f(11.0)) || (frame >= f(12.4) && frame < f(13.2));
  return (
    <AbsoluteFill>
      <Headlines lines={API_LINES} />
      <CodeBlock lines={FETCH} {...code} band={band} opacity={fadeAt(frame, 0.2)} />
      <Cache
        {...cache} name="in_memory_cache_handler()" color={TEST} on={handling} opacity={fadeAt(frame, 0.4)}
        rows={[{key: '"user_1"', value: ADA, at: 10.4, lit: [[10.4, 11.0], [12.4, 13.2]]}]}
      />
      <Box x={server.x} y={server.y} w={server.w} h={server.h} color={PROD} on={frame >= f(8.8) && frame < f(9.6)} opacity={fadeAt(frame, 0.6)}>
        <div style={{position: 'absolute', left: 20, top: 12, fontFamily: MONO, fontSize: 19, fontWeight: 700, color: PROD}}>fake_api</div>
        <div style={{position: 'absolute', left: 20, top: 42, fontFamily: SANS, fontSize: 16, color: MUTED}}>the handler that answers GetJson in place of the server</div>
        <div style={{position: 'absolute', left: 20, top: 76, fontFamily: MONO, fontSize: 18, color: TEXT}}>
          requested = <span style={{color: frame >= f(15.4) ? LOOK : TEXT, fontWeight: 700}}>{requested ? "['/users/1']" : '[]'}</span>
        </div>
      </Box>
      {/* program() calls fetch_user(1) twice; the call at work is lit. */}
      {['first = yield fetch_user(1)', 'second = yield fetch_user(1)  # served from the cache'].map((text, i) => {
        const on = i === 0 ? frame >= f(6.2) && frame < f(11.6) : frame >= f(11.6);
        return (
          <div
            key={text}
            style={{
              position: 'absolute', left: 40, top: code.y + 24 + FETCH.length * code.line + 12 + i * 32, fontFamily: MONO, fontSize: 17, whiteSpace: 'pre',
              color: on ? LOOK : MUTED, fontWeight: on ? 700 : 400, opacity: fadeAt(frame, 0.8),
            }}
          >
            {i === 0 ? 'program():  ' : '            '}
            {i === 1 && frame < f(12.4) ? text.split('  #')[0] : text}
          </div>
        );
      })}
      {/* First call: miss, request, store. Second call: hit. */}
      <Travel from={out} to={into} at={6.4} length={0.6} text='CacheGet("user_1")' color={ACCENT} />
      <Travel from={[cache.x + 120, cache.y + 120]} to={out} at={7.0} length={0.7} text="KeyError" color={FAIL} />
      <Travel from={[code.x + code.w - 40, at(5)]} to={[server.x + 140, server.y + 60]} at={8.0} length={0.8} text='GetJson("/users/1")' color={PROD} />
      <Travel from={[server.x + 140, server.y + 60]} to={[code.x + code.w - 40, at(5)]} at={9.0} length={0.8} text={ADA} color={PROD} />
      <Travel from={[code.x + code.w - 40, at(6)]} to={into} at={9.8} length={0.6} text='CachePut("user_1", ...)' color={ACCENT} />
      <Travel from={out} to={into} at={11.8} length={0.6} text='CacheGet("user_1")' color={ACCENT} />
      <Travel from={[cache.x + 120, cache.y + 120]} to={out} at={13.2} length={0.7} text={ADA} color={TEST} />
      <Terminal
        x={40} y={520} w={1190} size={18} line={28}
        lines={[
          {text: `print(first)          ${ADA}`},
          {text: frame >= f(14.0) ? 'print(second == first) True' : '', color: TEST},
          {text: frame >= f(15.4) ? "print(requested)      ['/users/1']" : '', color: LOOK},
        ]}
        opacity={fadeAt(frame, 13.6)}
      />
    </AbsoluteFill>
  );
};

// Scene 5 — the same program under different cache handlers (measure/handlers.log).

const HANDLER_LINES: Lines = [
  [0, 'The same program, run twice, under different cache handlers.'],
  [5, 'in_memory_cache_handler starts empty in every run: both runs do the work.'],
  [12, 'sqlite_cache_handler keeps the cache in a file: run 2 is a hit, in 2.3 ms.'],
  [20, 'No cache handler: CacheGet is unhandled. The handler picks the cache.'],
];
const HANDLER = f(28);
const ANSWER = [
  '@do',
  'def cached_answer():',
  '    try:',
  '        return "hit", (yield CacheGet("answer"))',
  '    except KeyError:',
  '        value = yield expensive_operation()  # 0.4 s',
  '        yield CachePut("answer", value)',
  '        return "miss", value',
];
// What each run printed in measure/handlers.py: [how, ms], and how many times the work ran.
const LANES = [
  {name: 'in_memory_cache_handler()', color: TEST, runs: [['miss', 426.8], ['miss', 403.7]] as [string, number][], work: 'the work ran 2 times', start: 5.6},
  {name: "sqlite_cache_handler(db)", color: PROD, runs: [['miss', 499.7], ['hit', 2.3]] as [string, number][], work: 'the work ran 1 time', start: 12.6},
];
const RUN_MAX = 499.7;
const RUN_W = 300;

const HandlerScene: React.FC = () => {
  const frame = useSceneFrame();
  const code = {x: 40, y: 96, w: 590, size: 15, line: 25};
  // Which lane is running: its handler goes in the slot of the run(...) line.
  const lane = frame < f(5.0) ? -1 : frame < f(12.0) ? 0 : frame < f(20.0) ? 1 : 2;
  const slot = lane < 0 ? {name: '<cache handler>', color: MUTED} : lane < 2 ? LANES[lane] : {name: '(no cache handler)', color: FAIL};
  return (
    <AbsoluteFill>
      <Headlines lines={HANDLER_LINES} />
      <CodeBlock lines={ANSWER} {...code} opacity={fadeAt(frame, 0.2)} />
      <Box x={670} y={96} w={560} h={224} color={slot.color} on={lane >= 0 && frame < f(lane * 7.5 + 6.0)} opacity={fadeAt(frame, 0.6)}>
        <div style={{position: 'absolute', left: 20, top: 14, fontFamily: SANS, fontSize: 17, color: MUTED}}>each run is a separate run(...):</div>
        <div style={{position: 'absolute', left: 20, top: 44, fontFamily: MONO, fontSize: 18, lineHeight: '34px', color: TEXT, whiteSpace: 'pre'}}>
          {'run(scheduled(sync_time_handler()(\n    await_handler()(\n        '}
          <span style={{color: slot.color, fontWeight: 700}}>{slot.name}</span>
          {'(\n            cached_answer())))))'}
        </div>
        <div style={{position: 'absolute', left: 20, top: 190, fontFamily: SANS, fontSize: 17, color: MUTED}}>the program is the same in every run</div>
      </Box>
      {LANES.map((l, i) => {
        const y = 352 + i * 110;
        return (
          <React.Fragment key={l.name}>
            <Box x={40} y={y} w={1190} h={96} color={l.color} on={lane === i} opacity={fadeAt(frame, l.start - 0.4)}>
              <div style={{position: 'absolute', left: 20, top: 14, fontFamily: MONO, fontSize: 18, fontWeight: 700, color: l.color, whiteSpace: 'nowrap'}}>{l.name}</div>
              <div style={{position: 'absolute', left: 20, top: 52, fontFamily: SANS, fontSize: 18, color: TEXT, opacity: fadeAt(frame, l.start + 4.4)}}>{l.work}</div>
            </Box>
            {l.runs.map(([how, ms], r) => {
              const x = 360 + r * 450;
              const grow = l.start + r * 2.2;
              const width = Math.max(4, (ms / RUN_MAX) * RUN_W * ramp(frame, grow, grow + (ms > 100 ? 1.4 : 0.1)));
              const color = how === 'hit' ? TEST : FAIL;
              return (
                <React.Fragment key={r}>
                  <Label x={x} y={y + 12} size={16} color={MUTED} opacity={fadeAt(frame, grow)}>
                    run {r + 1}: <span style={{color, fontWeight: 700}}>{how}</span>
                  </Label>
                  <div style={{position: 'absolute', left: x, top: y + 46, width, height: 26, borderRadius: 7, background: color, boxShadow: `0 0 12px ${color}88`, opacity: fadeAt(frame, grow)}} />
                  <Label x={x + Math.max(4, (ms / RUN_MAX) * RUN_W) + 12} y={y + 47} size={18} color={TEXT} bold opacity={fadeAt(frame, grow + (ms > 100 ? 1.4 : 0.1))}>
                    {ms} ms
                  </Label>
                </React.Fragment>
              );
            })}
          </React.Fragment>
        );
      })}
      {/* Between the sqlite runs the entry waits in the file. */}
      <Label x={63} y={352 + 110 + 52} size={18} color={PROD} opacity={between(frame, 14.4, 16.8)}>
        "answer" waits in the file
      </Label>
      <Box x={40} y={572} w={1190} h={96} color={FAIL} on={lane === 2 && frame < f(23.0)} opacity={fadeAt(frame, 20.4)}>
        <div style={{position: 'absolute', left: 20, top: 14, fontFamily: MONO, fontSize: 18, fontWeight: 700, color: FAIL}}>no cache handler</div>
        <div style={{position: 'absolute', left: 20, top: 52, fontFamily: MONO, fontSize: 17, color: TEXT, whiteSpace: 'nowrap', opacity: fadeAt(frame, 21.2)}}>
          UnhandledEffect: no handler found for effect: CacheGetEffect (CacheGet('answer'))
        </div>
      </Box>
    </AbsoluteFill>
  );
};

// Outro — the chapter in one sentence.

const OUTRO = f(10);

const Outro: React.FC = () => {
  const frame = useSceneFrame();
  const rise = interpolate(frame, [0, f(0.67)], [20, 0], {extrapolateRight: 'clamp', easing: Easing.out(Easing.cubic)});
  const steps = [
    {text: 'CacheGet(key)', color: ACCENT},
    {text: 'cache handler', color: TEST},
    {text: 'value or KeyError', color: TEXT},
    {text: 'CachePut(...)', color: ACCENT},
  ];
  return (
    <AbsoluteFill style={{padding: 90, justifyContent: 'center', transform: `translateY(${rise}px)`}}>
      <div style={{fontFamily: SANS, fontSize: 56, fontWeight: 700, color: TEXT, opacity: fadeAt(frame, 0, 0.4)}}>Cache System</div>
      <div style={{fontFamily: SANS, fontSize: 30, color: ACCENT, marginTop: 6, opacity: fadeAt(frame, 0.2, 0.4)}}>The program asks; the handler you install keeps the answers</div>
      <div style={{display: 'flex', alignItems: 'center', marginTop: 50, gap: 18}}>
        {steps.map((s, i) => (
          <React.Fragment key={s.text}>
            {i > 0 && <div style={{fontFamily: SANS, fontSize: 30, color: MUTED, opacity: fadeAt(frame, 0.8 + i * 0.9)}}>→</div>}
            <div
              style={{
                fontFamily: MONO, fontSize: 22, fontWeight: 700, color: s.color, padding: '10px 16px', borderRadius: 12, border: `2px solid ${s.color}`,
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

// The scenes in order; Root.tsx registers CACHE_SYSTEM.length frames of CACHE_SYSTEM.Video.
export const CACHE_SYSTEM = narrate('cache-system', DURATIONS, [
  {length: INTRO, starts: startsOf(INTRO_LINES), ids: ['a1', 'a2', 'a3'], node: <IntroScene />},
  {length: MISS, starts: startsOf(MISS_LINES), ids: ['b1', 'b2', 'b3', 'b4', 'b5'], node: <MissScene />},
  {length: POLICY, starts: startsOf(POLICY_LINES), ids: ['c1', 'c2', 'c3', 'c4'], node: <PolicyScene />},
  {length: API, starts: startsOf(API_LINES), ids: ['e1', 'e2', 'e3'], node: <ApiScene />},
  {length: HANDLER, starts: startsOf(HANDLER_LINES), ids: ['d1', 'd2', 'd3', 'd4'], node: <HandlerScene />},
  {length: OUTRO, starts: [0], ids: ['o1'], node: <Outro />},
]);
