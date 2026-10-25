// The video on the Static Typing page (docs/23-static-typing.md): a type checker follows the effects out to the
// handler and back. Six scenes and an outro, each with headlines a synthetic voice reads (narration.json):
//   1. The three things a checker checks: what an effect answers, what a program yields, what a handler answers.
//   2. An effect declares its answer type (EffectBase[int]); the type is readable at run time too.
//   3. yield from gives the answer its type; a bare yield gives Any; yield from a sub-program gives its result type.
//   4. @do keeps the parameters (P), the effects (E) and the result (T): wrong arguments and stray effects are errors.
//   5. typed_resume checks a handler's answer against the effect's type; a handler no effect can reach is refused.
//   6. The runtime readers, and what is not typed: Ask / Get answer Any, handler coverage is checked at run time.
// Every value and checker message on screen is printed by a program in measure/ (its output is in the .log next to it).
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

// The type a checker gives a name, shown as a tag next to the code: yellow while it is new.
const TypeTag: React.FC<{x: number; y: number; name: string; type: string; color: string; at: number}> = ({x, y, name, type, color, at}) => {
  const frame = useSceneFrame();
  const fresh = frame >= f(at) && frame < f(at + 1.4);
  return (
    <div
      style={{
        position: 'absolute', left: x, top: y, transform: `translateY(-50%) translateX(${mix(-16, 0, ramp(frame, at, at + 0.4))}px)`, whiteSpace: 'nowrap',
        fontFamily: MONO, fontSize: 16, fontWeight: 700, padding: '2px 10px', borderRadius: 9, background: PANEL, border: `2px solid ${color}`,
        ...glow(fresh, color), color: TEXT, opacity: fadeAt(frame, at),
      }}
    >
      {name}: <span style={{color}}>{type}</span>
    </div>
  );
};

// A box for a program or a handler, with a name and a line under it.
const Box: React.FC<{x: number; y: number; w: number; h: number; name: string; note: string; color: string; on: boolean; opacity?: number}> = ({
  x, y, w, h, name, note, color, on, opacity = 1,
}) => (
  <div
    style={{
      position: 'absolute', left: x, top: y, width: w, height: h, boxSizing: 'border-box', borderRadius: 16, background: PANEL,
      border: `3px solid ${color}`, ...glow(on, color), opacity,
    }}
  >
    <div style={{position: 'absolute', left: 16, top: 10, fontFamily: MONO, fontSize: 22, fontWeight: 700, color: on ? LOOK : color, whiteSpace: 'nowrap'}}>{name}</div>
    <div style={{position: 'absolute', left: 16, top: 44, fontFamily: SANS, fontSize: 16, color: MUTED, whiteSpace: 'nowrap'}}>{note}</div>
  </div>
);

// Scene 1 — the three things a checker checks, on one round trip of an effect.

const INTRO_LINES: Lines = [
  [0, 'Static typing in doeff: the checker follows your effects out and back.'],
  [6.5, '1. The value each effect answers with.'],
  [12.5, '2. The effects a program may yield.'],
  [16, '3. The value a handler answers with.'],
];
const INTRO = f(20);
const CHECKS = [
  {n: '1', what: 'what ReadClock answers', type: 'int', at: 6.5, color: ACCENT},
  {n: '2', what: 'what elapsed may yield', type: 'ReadClock', at: 12.5, color: TEXT},
  {n: '3', what: 'what clock answers with', type: 'int', at: 16, color: TEST},
];

const IntroScene: React.FC = () => {
  const frame = useSceneFrame();
  const prog = {x: 90, y: 170, w: 330, h: 190};
  const hand = {x: 860, y: 170, w: 330, h: 190};
  // One round trip every 4 s: ReadClock out (0.6 to 1.8), the answer back (2.2 to 3.4).
  const t = (frame / f(1)) % 4;
  const out = interpolate(t, [0.6, 1.8], [0, 1], clamp);
  const back = interpolate(t, [2.2, 3.4], [0, 1], clamp);
  const which = CHECKS.reduce((found, c, i) => (frame >= f(c.at) ? i : found), -1);
  const fromX = prog.x + prog.w + 20;
  const toX = hand.x - 20;
  return (
    <AbsoluteFill>
      <Headlines lines={INTRO_LINES} />
      <Box {...prog} name="elapsed(400)" note="a program" color={ACCENT} on={which === 1} opacity={fadeAt(frame, 0.3)} />
      <div style={{position: 'absolute', left: prog.x + 16, top: prog.y + 80, fontFamily: MONO, fontSize: 17, lineHeight: '28px', color: TEXT, opacity: fadeAt(frame, 0.6)}}>
        <div>now = <span style={{color: ACCENT, fontWeight: 700}}>yield</span> from ReadClock()</div>
        <div>return now - since</div>
      </div>
      <Box {...hand} name="clock" note="a handler" color={TEST} on={which === 2} opacity={fadeAt(frame, 0.6)} />
      <div style={{position: 'absolute', left: hand.x + 16, top: hand.y + 80, fontFamily: MONO, fontSize: 17, lineHeight: '28px', color: TEXT, opacity: fadeAt(frame, 0.9)}}>
        <div style={{whiteSpace: 'pre'}}>typed_resume(effect, k,</div>
        <div style={{whiteSpace: 'pre'}}>             now_ms())</div>
      </div>
      {t >= 0.6 && t < 2.0 && frame >= f(1) && (
        <Pill x={mix(fromX, toX, out)} y={prog.y + 60} text="ReadClock()" color={ACCENT} opacity={1 - interpolate(t, [1.8, 2.0], [0, 1], clamp)} />
      )}
      {t >= 2.2 && t < 3.6 && frame >= f(1) && (
        <Pill x={mix(toX, fromX, back)} y={prog.y + 140} text="1000" color={TEST} opacity={1 - interpolate(t, [3.4, 3.6], [0, 1], clamp)} />
      )}
      <Label x={fromX + 40} y={prog.y - 4} size={17} bold={false} opacity={fadeAt(frame, 1.2) * (which === 0 ? 1 : 0.6)}>the effect travels out</Label>
      <Label x={fromX + 40} y={prog.y + 170} size={17} bold={false} opacity={fadeAt(frame, 1.6) * (which === 2 ? 1 : 0.6)}>the answer comes back</Label>
      <Label x={90} y={470} size={22} color={TEXT} opacity={fadeAt(frame, 2.4) * (1 - ramp(frame, 6.0, 6.4))}>
        doeff ships its types (py.typed): pyright and mypy read them.
      </Label>
      <Label x={90} y={508} size={22} bold={false} opacity={fadeAt(frame, 3.4) * (1 - ramp(frame, 6.0, 6.4))}>
        The types travel with the effect, and with the answer that comes back.
      </Label>
      {CHECKS.map((c, i) => {
        const x = 90 + i * 375;
        const on = which === i;
        return (
          <div
            key={c.n}
            style={{
              position: 'absolute', left: x, top: 430 + mix(20, 0, ramp(frame, c.at, c.at + 0.5)), width: 350, height: 170, boxSizing: 'border-box',
              borderRadius: 16, background: PANEL, border: `2px solid ${on ? LOOK : '#2a2e40'}`, boxShadow: on ? `0 0 18px ${LOOK}55` : 'none',
              padding: '16px 20px', opacity: fadeAt(frame, c.at, 0.5),
            }}
          >
            <div style={{fontFamily: SANS, fontSize: 40, fontWeight: 700, color: on ? LOOK : MUTED}}>{c.n}</div>
            <div style={{fontFamily: SANS, fontSize: 19, color: TEXT, marginTop: 6}}>{c.what}</div>
            <div style={{fontFamily: MONO, fontSize: 24, fontWeight: 700, color: c.color, marginTop: 10}}>{c.type}</div>
          </div>
        );
      })}
    </AbsoluteFill>
  );
};

// Scene 2 — an effect declares its answer type, and the type is readable at run time (measure/runtime.py).

const DECLARE_LINES: Lines = [
  [0, 'An effect declares its answer type: EffectBase[int] is answered with an int.'],
  [7.5, 'A generic parent works too, and the type can be read at run time.'],
  [14.5, 'An effect on plain EffectBase has an unknown answer type: Any.'],
];
const DECLARE = f(21);
const EFFECTS = [
  '@dataclass(frozen=True)',
  'class ReadClock(EffectBase[int]):',
  '    pass',
  ' ',
  'class WriteEffect(EffectBase[T]):',
  '    pass',
  ' ',
  '@dataclass(frozen=True)',
  'class WriteShared(WriteEffect[bool]):',
  '    key: str',
  ' ',
  'class Untyped(EffectBase):',
  '    pass',
];
// From measure/runtime.log.
const READS = [
  {ask: '>>> effect_result_type(ReadClock)', says: "<class 'int'>", at: 2.6, color: TEST},
  {ask: '>>> effect_result_type(WriteShared)', says: "<class 'bool'>   via WriteEffect[bool]", at: 9.4, color: TEST},
  {ask: '>>> effect_result_type(Untyped)', says: 'typing.Any', at: 15.4, color: PROD},
];

const DeclareScene: React.FC = () => {
  const frame = useSceneFrame();
  const code = {x: 50, y: 110, w: 600, size: 18, line: 30};
  const band = frame < f(0.8) ? null : frame < f(7.5) ? 1 : frame < f(8.5) ? mix(1, 4, ramp(frame, 7.5, 7.9)) : frame < f(14.5) ? mix(4, 8, ramp(frame, 8.5, 8.9)) : mix(8, 11, ramp(frame, 14.5, 14.9));
  const travel = ramp(frame, 1.2, 2.2);
  return (
    <AbsoluteFill>
      <Headlines lines={DECLARE_LINES} />
      <CodeBlock lines={EFFECTS} {...code} band={band} opacity={fadeAt(frame, 0.2)} />
      {/* The [int] of ReadClock travels to the right: the type its handler answers with. */}
      <Pill x={mix(code.x + 400, 830, travel)} y={mix(lineMid(code.y, code.line, 1), 160, travel)} text="answers int" color={ACCENT} opacity={fadeAt(frame, 1.2, 0.2)} />
      <Label x={940} y={146} size={17} bold={false} opacity={fadeAt(frame, 2.2)}>for the checker</Label>
      <Terminal
        x={720} y={230} w={510} size={16} line={27}
        lines={READS.flatMap((r) => [
          {text: frame >= f(r.at) ? r.ask : '', color: MUTED},
          {text: frame >= f(r.at + 0.6) ? r.says : '', color: r.color},
        ])}
        opacity={fadeAt(frame, 2.4)}
      />
      <Label x={720} y={440} size={17} bold={false} opacity={fadeAt(frame, 10.4)}>
        EffectBase[T] is a real generic at run time,
      </Label>
      <Label x={720} y={466} size={17} bold={false} opacity={fadeAt(frame, 10.4)}>
        so the answer type follows the parent.
      </Label>
      <Label x={720} y={520} size={19} color={PROD} opacity={fadeAt(frame, 16.2)}>
        no [T]: the checker cannot help
      </Label>
    </AbsoluteFill>
  );
};

// Scene 3 — yield from gives the answer its type (measure/check_types.log: reveal_type in pyright and mypy).

const YIELD_LINES: Lines = [
  [0, 'yield from an effect: the program pauses, a handler sends back the answer.'],
  [7, 'The checker knows the answer\'s type: now is an int.'],
  [13.5, 'A bare yield runs the same, but the checker sees Any.'],
  [20, 'yield from a sub-program gives its result type: spent is an int.'],
];
const YIELD = f(26);
const ELAPSED = [
  '@do',
  'def elapsed(since: int)',
  '        -> Generator[ReadClock, Any, int]:',
  '    now = yield from ReadClock()',
  '    loose = yield ReadClock()',
  '    return now - since',
  ' ',
  '@do',
  'def label(since: int)',
  '        -> Generator[ReadClock, Any, str]:',
  '    spent = yield from elapsed(since)',
  '    return f"{spent} ms"',
];

const YieldScene: React.FC = () => {
  const frame = useSceneFrame();
  const code = {x: 50, y: 110, w: 560, size: 18, line: 30};
  const hand = {x: 900, y: 140, w: 320, h: 120};
  // Two round trips: ReadClock from line 3 (yield from) and from line 4 (bare yield).
  const trips = [
    {line: 3, out: 1.4, back: 3.0},
    {line: 4, out: 14.4, back: 16.0},
  ];
  const band = frame < f(1.0) ? null : frame < f(13.5) ? 3 : frame < f(20) ? mix(3, 4, ramp(frame, 13.5, 13.8)) : mix(4, 10, ramp(frame, 20, 20.4));
  const handling = trips.some((trip) => frame >= f(trip.out + 1.0) && frame < f(trip.back + 0.4));
  const tagX = code.x + code.w + 24;
  return (
    <AbsoluteFill>
      <Headlines lines={YIELD_LINES} />
      <CodeBlock lines={ELAPSED} {...code} band={band} opacity={fadeAt(frame, 0.2)} />
      <Box {...hand} name="clock" note="answers ReadClock with now_ms()" color={TEST} on={handling} opacity={fadeAt(frame, 0.5)} />
      {trips.map((trip) => {
        const y = lineMid(code.y, code.line, trip.line);
        const out = ramp(frame, trip.out, trip.out + 1.0);
        const back = ramp(frame, trip.back, trip.back + 1.0);
        return (
          <React.Fragment key={trip.line}>
            {frame >= f(trip.out) && frame < f(trip.out + 1.2) && (
              <Pill x={mix(code.x + 430, hand.x + hand.w / 2, out)} y={mix(y, hand.y + 30, out)} text="ReadClock()" color={ACCENT} opacity={1 - ramp(frame, trip.out + 1.0, trip.out + 1.2)} />
            )}
            {frame >= f(trip.back) && frame < f(trip.back + 1.2) && (
              <Pill x={mix(hand.x + hand.w / 2, code.x + 200, back)} y={mix(hand.y + hand.h - 20, y, back)} text="1000" color={TEST} opacity={1 - ramp(frame, trip.back + 1.0, trip.back + 1.2)} />
            )}
          </React.Fragment>
        );
      })}
      <Label x={hand.x} y={hand.y + hand.h + 16} size={16} bold={false} opacity={fadeAt(frame, 4.2)}>the same value comes back both times</Label>
      <TypeTag x={tagX} y={lineMid(code.y, code.line, 3)} name="now" type="int" color={TEST} at={7.6} />
      <TypeTag x={tagX} y={lineMid(code.y, code.line, 4)} name="loose" type="Any" color={PROD} at={17.2} />
      <TypeTag x={tagX} y={lineMid(code.y, code.line, 10)} name="spent" type="int" color={TEST} at={20.6} />
      {/* What reveal_type printed in measure/check_types.log. */}
      <Terminal
        x={50} y={520} w={1180} size={17} line={27}
        lines={[
          {text: frame >= f(8.2) ? 'reveal_type(now)     pyright: "int"    mypy: "int"' : '', color: TEST},
          {text: frame >= f(17.8) ? 'reveal_type(loose)   pyright: "Any"    mypy: "Any"     (yield gives Any; yield from gives the type)' : '', color: PROD},
          {text: frame >= f(21.2) ? 'reveal_type(spent)   pyright: "int"    mypy: "int"' : '', color: TEST},
        ]}
        opacity={fadeAt(frame, 7.8)}
      />
    </AbsoluteFill>
  );
};

// Scene 4 — @do keeps the parameters, the effects and the result (measure/check_types.log).

const DO_LINES: Lines = [
  [0, '@do keeps the parameters, the effects and the result of what it wraps.'],
  [8, 'P, the parameters: calling elapsed with a str is an error.'],
  [13.5, 'E, the effects: yielding one outside the declared set is an error.'],
  [19.5, 'T, the result: run of the program under its handler is an int.'],
];
const DO = f(26);
const COL_W = 370;
const colX = (i: number) => 45 + i * (COL_W + 30);
const LETTER = {P: PROD, E: ACCENT, T: TEST};
// The code of each column and what pyright and mypy said about it in measure/check_types.log.
const KEPT = [
  {
    letter: 'P' as const, title: 'parameters', start: 8, bad: true,
    code: ['elapsed("400")'],
    said: ['pyright: Argument of type', '  "Literal[\'400\']" cannot be', '  assigned to parameter "since"', '  of type "int"', 'mypy: Argument 1 to "elapsed"', '  has incompatible type "str";', '  expected "int"'],
  },
  {
    letter: 'E' as const, title: 'effects', start: 13.5, bad: true,
    code: ['@do', 'def strays(since: int)', '    -> Generator[ReadClock, ...]:', '    yield from SleepSeconds(1.0)'],
    said: ['pyright: "SleepSeconds" is not', '  assignable to "ReadClock"', 'mypy: Incompatible types in', '  "yield from" (actual type', '  "SleepSeconds", expected', '  type "ReadClock")'],
  },
  {
    letter: 'T' as const, title: 'result', start: 19.5, bad: false,
    code: ['reveal_type(', '    run(clock(elapsed(400))))'],
    said: ['pyright: "int"', 'mypy: "int"', '', 'running it prints:', '  600'],
  },
];

// A type expression with its P, E and T coloured; the letter at work glows.
const Signature: React.FC<{parts: string[]; on: string | null; size: number}> = ({parts, on, size}) => (
  <span style={{fontFamily: MONO, fontSize: size, fontWeight: 700, color: TEXT, whiteSpace: 'pre'}}>
    {parts.map((part, i) =>
      part in LETTER ? (
        <span key={i} style={{color: on === part ? LOOK : LETTER[part as keyof typeof LETTER], textShadow: on === part ? `0 0 12px ${LOOK}` : 'none'}}>{part}</span>
      ) : (
        <span key={i}>{part}</span>
      ),
    )}
  </span>
);

const DoScene: React.FC = () => {
  const frame = useSceneFrame();
  const on = KEPT.reduce<string | null>((found, k) => (frame >= f(k.start) ? k.letter : found), null);
  const arrow = ramp(frame, 1.6, 2.4);
  return (
    <AbsoluteFill>
      <Headlines lines={DO_LINES} />
      <div style={{position: 'absolute', left: 0, right: 0, top: 104, display: 'flex', justifyContent: 'center', alignItems: 'center', gap: 22}}>
        <span style={{fontFamily: MONO, fontSize: 22, fontWeight: 700, color: ACCENT, opacity: fadeAt(frame, 0.3)}}>@do</span>
        <span style={{opacity: fadeAt(frame, 0.6)}}>
          <Signature parts={['Callable[', 'P', ', Generator[', 'E', ', Any, ', 'T', ']]']} on={on} size={22} />
        </span>
        <span style={{fontFamily: SANS, fontSize: 26, color: MUTED, opacity: arrow}}>→</span>
        <span style={{opacity: fadeAt(frame, 2.2)}}>
          <Signature parts={['Callable[', 'P', ', Expand[', 'T', ', ', 'E', ']]']} on={on} size={22} />
        </span>
      </div>
      <div style={{position: 'absolute', left: 0, right: 0, top: 150, textAlign: 'center', fontFamily: MONO, fontSize: 17, color: MUTED, opacity: fadeAt(frame, 3.6)}}>
        reveal_type(elapsed)  <span style={{color: TEXT}}>(since: int) -&gt; Expand[int, ReadClock]</span>   pyright and mypy
      </div>
      {KEPT.map((k, i) => {
        const x = colX(i);
        const active = on === k.letter;
        const codeY = 252;
        const codeLine = 25;
        const codeH = PAD * 2 + k.code.length * codeLine;
        return (
          <React.Fragment key={k.letter}>
            <div
              style={{
                position: 'absolute', left: x - 12, top: 190, width: COL_W + 24, height: 515, boxSizing: 'border-box', borderRadius: 18,
                border: `2px solid ${active ? LOOK : '#2a2e40'}`, boxShadow: active ? `0 0 18px ${LOOK}55` : 'none', opacity: fadeAt(frame, k.start, 0.4),
              }}
            />
            <Label x={x + 6} y={204} color={active ? LOOK : LETTER[k.letter]} size={22} opacity={fadeAt(frame, k.start)}>
              {k.letter}  <span style={{color: TEXT}}>{k.title}</span>
            </Label>
            <CodeBlock lines={k.code} x={x} y={codeY} w={COL_W} size={15} line={codeLine} opacity={fadeAt(frame, k.start + 0.3)} />
            <Terminal
              x={x} y={codeY + codeH + 18} w={COL_W} size={14} line={22}
              lines={k.said.map((text) => ({text, color: k.bad ? FAIL : text.startsWith('  6') ? TEXT : TEST}))}
              opacity={fadeAt(frame, k.start + 1.4)}
            />
            {k.letter === 'E' && (
              <div style={{position: 'absolute', left: x + 6, top: 578, width: COL_W - 12, fontFamily: SANS, fontSize: 16, lineHeight: '23px', color: MUTED, opacity: fadeAt(frame, k.start + 3.2)}}>
                <span style={{fontFamily: MONO, color: TEXT}}>yield from sub()</span> adds sub's effects to the caller's: the declaration closes over calls.
              </div>
            )}
            <Label x={x + 6} y={668} size={17} color={k.bad ? FAIL : TEST} opacity={fadeAt(frame, k.start + 2.2)}>
              {k.bad ? 'error, in pyright and mypy' : 'the result type is kept'}
            </Label>
          </React.Fragment>
        );
      })}
    </AbsoluteFill>
  );
};

// Scene 5 — a handler's answer is checked against the effect's type (measure/check_types.log, measure/runtime.log).

const HANDLER_LINES: Lines = [
  [0, 'A handler answers an effect by resuming k, the rest of the program.'],
  [6.5, 'typed_resume checks the value against the effect\'s type: 1000 is an int.'],
  [15, 'Answering ReadClock with a str is refused by the checker.'],
  [20.5, 'A handler no effect can reach is refused twice: checker and @handler.'],
];
const HANDLER = f(29);
const HANDLERS = [
  {lines: ['@handler', 'def clock(effect: ReadClock, k: K):', '    return (yield typed_resume(effect, k, now_ms()))'], at: 0.2, bad: false},
  {lines: ['@handler', 'def noon(effect: ReadClock, k: K):', '    return (yield typed_resume(effect, k, "noon"))'], at: 15.2, bad: true},
  {lines: ['@handler', 'def wrong(effect: str, k: K):', '    return (yield Resume(k, None))'], at: 20.7, bad: true},
];

const HandlerScene: React.FC = () => {
  const frame = useSceneFrame();
  const codeX = 50;
  const codeW = 620;
  const codeLine = 26;
  const codeY = (i: number) => 104 + i * 128;
  const prog = {x: 720, y: 110, w: 230, h: 100};
  const hand = {x: 1000, y: 110, w: 230, h: 100};
  const gate = {x: 1000, y: 250};
  const out = ramp(frame, 1.2, 2.2);
  const back = ramp(frame, 7.6, 8.8);
  const bounce = interpolate(frame, [f(16.4), f(17.2), f(17.8)], [0, 1, 0.75], clamp);
  const handling = frame >= f(2.2) && frame < f(8.8);
  const wrongOn = frame >= f(20.7);
  return (
    <AbsoluteFill>
      <Headlines lines={HANDLER_LINES} />
      {HANDLERS.map((h, i) => (
        <React.Fragment key={i}>
          <CodeBlock lines={h.lines} x={codeX} y={codeY(i)} w={codeW} size={16} line={codeLine} band={i === 0 && frame >= f(6.6) && frame < f(15) ? 2 : null} opacity={fadeAt(frame, h.at)} />
          {h.bad && (
            <div
              style={{
                position: 'absolute', left: codeX - 4, top: codeY(i) - 4, width: codeW + 8, height: PAD * 2 + h.lines.length * codeLine + 8, boxSizing: 'border-box',
                borderRadius: 16, border: `3px solid ${FAIL}`, boxShadow: `0 0 16px ${FAIL}66`, opacity: fadeAt(frame, h.at + 1.2),
              }}
            />
          )}
        </React.Fragment>
      ))}
      <Box {...prog} name="elapsed(400)" note="k waits for the answer" color={ACCENT} on={handling} opacity={fadeAt(frame, 0.4)} />
      <Box {...hand} name="clock" note="handles ReadClock" color={TEST} on={handling} opacity={fadeAt(frame, 0.6)} />
      {frame >= f(1.2) && frame < f(2.4) && (
        <Pill x={mix(prog.x + prog.w, hand.x, out)} y={prog.y + 30} text="ReadClock()" color={ACCENT} opacity={1 - ramp(frame, 2.2, 2.4)} />
      )}
      {/* typed_resume: the answer passes the check of the effect's type on its way back to k. */}
      <div
        style={{
          position: 'absolute', left: gate.x, top: gate.y, width: 230, height: 54, boxSizing: 'border-box', borderRadius: 12, border: `2px dashed ${MUTED}`,
          fontFamily: MONO, fontSize: 17, lineHeight: '50px', textAlign: 'center', color: MUTED, opacity: fadeAt(frame, 6.6),
        }}
      >
        answer must be <span style={{color: TEST, fontWeight: 700}}>int</span>
      </div>
      {frame >= f(6.8) && frame < f(7.6) && <Pill x={hand.x + 115} y={mix(hand.y + hand.h, gate.y, ramp(frame, 6.8, 7.6))} text="1000" color={TEST} />}
      {frame >= f(7.6) && frame < f(9.0) && (
        <Pill x={mix(gate.x + 115, prog.x + prog.w / 2, back)} y={mix(gate.y + 27, prog.y + prog.h + 4, back)} text="1000 ✓" color={TEST} opacity={1 - ramp(frame, 8.8, 9.0)} />
      )}
      {/* "noon" does not pass: it bounces off the check. */}
      {frame >= f(16.4) && frame < f(19.6) && (
        <Pill x={gate.x + 115} y={mix(gate.y + 120, gate.y + 60, bounce)} text={'"noon" ✗'} color={FAIL} opacity={1 - ramp(frame, 19.2, 19.6)} />
      )}
      {/* wrong: no effect is a str, so nothing ever reaches it. */}
      <Box x={1000} y={400} w={230} h={86} name="wrong" note="effect: str" color={FAIL} on={false} opacity={fadeAt(frame, 21.0) * (wrongOn ? 1 : 0)} />
      <Label x={720} y={420} size={17} color={FAIL} opacity={fadeAt(frame, 21.6)}>no effect is a str</Label>
      {/* From measure/runtime.log and measure/check_types.log. */}
      <Terminal
        x={50} y={500} w={1180} size={15} line={24}
        lines={[
          {text: frame >= f(9.0) ? 'run(clock(elapsed(400)))            600' : '', color: TEST},
          {text: frame >= f(10.4) ? "run(scheduled(clock(both(400))))    [600, '600 ms', 600]" : '', color: TEST},
          {text: frame >= f(17.2) ? 'pyright: "Literal[\'noon\']" is not assignable to "int"     mypy: Cannot infer value of type parameter "_Answer"' : '', color: FAIL},
          {text: frame >= f(22.4) ? 'pyright: Parameter 1: type "_HandledEffect@handler" is incompatible with type "str"' : '', color: FAIL},
          {text: frame >= f(23.4) ? 'mypy:    Value of type variable "_HandledEffect" of "handler" cannot be "str"' : '', color: FAIL},
          {text: frame >= f(24.6) ? "run time: TypeError: ... names str, which no effect can be an instance of, so the handler would never be called." : '', color: FAIL},
        ]}
        opacity={fadeAt(frame, 8.8)}
      />
    </AbsoluteFill>
  );
};

// Scene 6 — the runtime readers, and what is not typed (measure/runtime.log, measure/check_types.log).

const LIMIT_LINES: Lines = [
  [0, 'The same types are readable at run time, for tools such as the Hy checker.'],
  [9, 'Not typed: Ask and Get answer Any. The environment and state are dynamic.'],
  [16.5, 'Handler coverage is not a type error: run without clock raises at run time.'],
];
const LIMIT = f(26);

const LimitScene: React.FC = () => {
  const frame = useSceneFrame();
  const coverGone = ramp(frame, 19.6, 20.4);
  return (
    <AbsoluteFill>
      <Headlines lines={LIMIT_LINES} />
      <Label x={50} y={104} size={20} color={TEXT} opacity={fadeAt(frame, 0.3)}>Runtime readers</Label>
      <Terminal
        x={50} y={140} w={600} size={15} line={24}
        lines={[
          {text: frame >= f(1.0) ? '>>> effect_result_type(ReadClock)' : '', color: MUTED},
          {text: frame >= f(1.6) ? "<class 'int'>" : '', color: TEST},
          {text: frame >= f(2.8) ? '>>> program_signature(elapsed)' : '', color: MUTED},
          {text: frame >= f(3.4) ? 'ProgramSignature(' : ''},
          {text: frame >= f(3.4) ? "  effects=(<class '__main__.ReadClock'>,)," : '', color: ACCENT},
          {text: frame >= f(3.4) ? "  result=<class 'int'>)" : '', color: TEST},
        ]}
        opacity={fadeAt(frame, 0.6)}
      />
      <Label x={50} y={326} size={17} bold={false} opacity={fadeAt(frame, 5.2)}>
        one source of truth: the annotations pyright reads
      </Label>
      <Label x={720} y={104} size={20} color={PROD} opacity={fadeAt(frame, 9.2)}>What is not typed</Label>
      <CodeBlock
        lines={['name = yield from Ask("name")', 'count = yield from Get("count")']} x={720} y={140} w={510} size={16} line={34}
        opacity={fadeAt(frame, 9.4)}
      />
      <TypeTag x={1086} y={lineMid(140, 34, 0)} name="name" type="Any" color={PROD} at={10.8} />
      <TypeTag x={1086} y={lineMid(140, 34, 1)} name="count" type="Any" color={PROD} at={11.6} />
      <Label x={720} y={250} size={16} bold={false} opacity={fadeAt(frame, 10.8)}>pyright and mypy</Label>
      {/* Coverage: the checker says int, the run says UnhandledEffect. */}
      <div style={{position: 'absolute', left: 50, top: 380, width: 1180, height: 250, boxSizing: 'border-box', borderRadius: 18, border: `2px solid ${frame >= f(16.5) ? LOOK : '#2a2e40'}`, opacity: fadeAt(frame, 16.5)}} />
      <CodeBlock lines={['run(elapsed(400))  # no clock handler around it']} x={80} y={404} w={620} size={17} line={30} opacity={fadeAt(frame, 16.7)} />
      <Box x={80} y={480} w={260} h={86} name="elapsed(400)" note="yields ReadClock" color={ACCENT} on={frame >= f(18.4) && frame < f(20.4)} opacity={fadeAt(frame, 17.0)} />
      {frame >= f(18.6) && (
        <Pill x={mix(380, 620, ramp(frame, 18.6, 19.6))} y={510} text="ReadClock()" color={ACCENT} opacity={1 - coverGone * 0.7} />
      )}
      <div style={{position: 'absolute', left: 380, top: 560, fontFamily: SANS, fontSize: 18, color: MUTED, opacity: fadeAt(frame, 19.4)}}>no handler in scope</div>
      <Terminal
        x={760} y={404} w={440} size={16} line={28}
        lines={[
          {text: frame >= f(17.4) ? 'pyright: "int"   mypy: "int"' : '', color: TEST},
          {text: frame >= f(17.4) ? '  no type error' : '', color: MUTED},
        ]}
        opacity={fadeAt(frame, 17.2)}
      />
      <Terminal
        x={760} y={500} w={440} size={16} line={28}
        lines={[{text: frame >= f(20.4) ? 'run: UnhandledEffect' : '', color: FAIL}, {text: frame >= f(21.2) ? 'program_signature + handler_effect_types' : '', color: MUTED}, {text: frame >= f(21.2) ? '  give the data to check coverage' : '', color: MUTED}]}
        opacity={fadeAt(frame, 20.2)}
      />
    </AbsoluteFill>
  );
};

// Outro — the chapter in one sentence.

const OUTRO = f(9);

const Outro: React.FC = () => {
  const frame = useSceneFrame();
  const rise = interpolate(frame, [0, f(0.67)], [20, 0], {extrapolateRight: 'clamp', easing: Easing.out(Easing.cubic)});
  const steps = [
    {text: 'EffectBase[int]', color: ACCENT},
    {text: 'yield from', color: ACCENT},
    {text: '@do', color: TEXT},
    {text: 'typed_resume', color: TEST},
  ];
  return (
    <AbsoluteFill style={{padding: 90, justifyContent: 'center', transform: `translateY(${rise}px)`}}>
      <div style={{fontFamily: SANS, fontSize: 56, fontWeight: 700, color: TEXT, opacity: fadeAt(frame, 0, 0.4)}}>Static typing</div>
      <div style={{fontFamily: SANS, fontSize: 30, color: ACCENT, marginTop: 6, opacity: fadeAt(frame, 0.2, 0.4)}}>Effects carry their types, out to the handler and back</div>
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

// The scenes in order; Root.tsx registers STATIC_TYPING.length frames of STATIC_TYPING.Video.
export const STATIC_TYPING = narrate('static-typing', DURATIONS, [
  {length: INTRO, starts: startsOf(INTRO_LINES), ids: ['a1', 'a2', 'a3', 'a4'], node: <IntroScene />},
  {length: DECLARE, starts: startsOf(DECLARE_LINES), ids: ['b1', 'b2', 'b3'], node: <DeclareScene />},
  {length: YIELD, starts: startsOf(YIELD_LINES), ids: ['c1', 'c2', 'c3', 'c4'], node: <YieldScene />},
  {length: DO, starts: startsOf(DO_LINES), ids: ['d1', 'd2', 'd3', 'd4'], node: <DoScene />},
  {length: HANDLER, starts: startsOf(HANDLER_LINES), ids: ['e1', 'e2', 'e3', 'e4'], node: <HandlerScene />},
  {length: LIMIT, starts: startsOf(LIMIT_LINES), ids: ['f1', 'f2', 'f3'], node: <LimitScene />},
  {length: OUTRO, starts: [0], ids: ['o1'], node: <Outro />},
]);
