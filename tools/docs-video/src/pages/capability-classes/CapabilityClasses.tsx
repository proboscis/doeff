// The video on the Capability Classes page (docs/22-capability-classes.md): the classification the chapter gives, in
// motion. Seven scenes, each with headlines a synthetic voice reads (narration.json):
//   1. The axis: what does the handler demand of k? Four classes, each containing the one before.
//   2. Class 0, dispatch: resume k once, right away; one program under two handlers; the power of DI.
//   3. Class 1, data middleware: memo elides a repeated call, retry re-yields the effect and resumes k once.
//   4. Class 2, scheduling: k escapes; in-flight dedup parks a k on a virtual clock until the one call returns.
//   5. Class 3, multi-shot, is absent: a second resume raises; re-running per branch with a memo is the approximation.
//   6. The evidence rule: (a) boundaries changed at run time, (b) calls that cost far more than an effect.
//   7. The outro.
// Every output, order, time and cost on screen is printed by a program in measure/ (its .log is next to it).
import React from 'react';
import {AbsoluteFill, interpolate} from 'remotion';
import {clamp, fadeAt, mix, ramp} from '../../kit/motion';
import {narrate} from '../../kit/narrated';
import {CodeBlock, glow, Headlines, highlightYield, lineMid, Lines, Pill, startsOf} from '../../kit/parts';
import {ACCENT, f, FAIL, LOOK, MONO, MUTED, PANEL, PROD, SANS, TEST, TEXT} from '../../kit/theme';
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

// Lines of code in a box without the code box's band: a handler's body inside a diagram box.
const Code: React.FC<{lines: string[]; x: number; y: number; size?: number; line?: number; opacity?: number}> = ({lines, x, y, size = 16, line = 25, opacity = 1}) => (
  <div style={{position: 'absolute', left: x, top: y, fontFamily: MONO, fontSize: size, lineHeight: `${line}px`, color: TEXT, whiteSpace: 'pre', opacity}}>
    {lines.map((text, i) => (
      <div key={i} style={{color: text.startsWith('@') ? ACCENT : TEXT}}>{text.includes('yield') ? highlightYield(text) : text}</div>
    ))}
  </div>
);

type Point = {x: number; y: number};

// An effect or an answer travelling from a to b between two seconds, fading as it lands.
const Trip: React.FC<{a: Point; b: Point; from: number; to: number; text: string; color: string}> = ({a, b, from, to, text, color}) => {
  const frame = useSceneFrame();
  if (frame < f(from) || frame >= f(to + 0.25)) return null;
  const t = ramp(frame, from, to);
  return <Pill x={mix(a.x, b.x, t)} y={mix(a.y, b.y, t)} text={text} color={color} opacity={1 - ramp(frame, to, to + 0.25)} />;
};

// The four classes: what each demands of k, and an example of its members.
const CLASSES = [
  {n: 0, name: 'Dispatch', k: ['k resumed once,', 'right away'], members: 'routing · test handlers', color: TEST},
  {n: 1, name: 'Data middleware', k: ['k resumed once, with', 'work around it'], members: 'memo · retry · budget', color: TEST},
  {n: 2, name: 'Scheduling', k: ['k escapes: parked,', 'resumed later'], members: 'Spawn/Wait · dedup', color: ACCENT},
  {n: 3, name: 'Multi-shot', k: ['k resumed more', 'than once'], members: 'not in doeff', color: FAIL},
];

// Scene 1 — the axis, and the four classes it gives.

const TITLE_LINES: Lines = [
  [0, 'Capability classes predict where effects pay off, before you write code.'],
  [6.3, 'Every handler is classified by one question: what does it demand of k?'],
  [13.2, 'Four classes, each containing the one before: more power, at more cost.'],
];
const TITLE = f(20.3);
const STEP = {w: 270, gap: 20, base: 650};
const stepX = (i: number) => 640 - (4 * STEP.w + 3 * STEP.gap) / 2 + i * (STEP.w + STEP.gap);
const stepH = (i: number) => 190 + i * 30;

const TitleScene: React.FC = () => {
  const frame = useSceneFrame();
  const lift = ramp(frame, 13.0, 13.8);
  return (
    <AbsoluteFill>
      <Headlines lines={TITLE_LINES} />
      <div style={{position: 'absolute', left: 0, right: 0, top: mix(150, 104, lift) + mix(16, 0, ramp(frame, 0.2, 0.8)), textAlign: 'center', opacity: fadeAt(frame, 0.2, 0.5)}}>
        <div style={{fontFamily: SANS, fontSize: mix(72, 52, lift), fontWeight: 700, color: TEXT}}>Capability Classes</div>
        <div style={{fontFamily: SANS, fontSize: mix(28, 24, lift), color: ACCENT, marginTop: 6}}>predicting where effects pay off</div>
      </div>
      <div
        style={{
          position: 'absolute', left: 640, top: mix(360, 236, lift), transform: 'translateX(-50%)', padding: '12px 28px', borderRadius: 14, background: PANEL,
          border: `3px solid ${LOOK}`, boxShadow: `0 0 22px ${LOOK}55`, fontFamily: SANS, fontSize: 30, fontWeight: 700, color: TEXT, whiteSpace: 'nowrap',
          opacity: fadeAt(frame, 6.6, 0.5),
        }}
      >
        What does the handler demand of <span style={{fontFamily: MONO, color: ACCENT}}>k</span>?
      </div>
      <Label x={640} y={mix(436, 312, lift)} size={19} center opacity={fadeAt(frame, 8.5) * (1 - lift)}>
        <span style={{fontFamily: MONO, color: ACCENT}}>k</span> is the continuation: the rest of the program, from the yield on
      </Label>
      {CLASSES.map((c, i) => {
        const at = 13.8 + i * 0.6;
        const on = frame >= f(at) && frame < f(at + 0.6);
        return (
          <Box key={c.n} x={stepX(i)} y={STEP.base - stepH(i)} w={STEP.w} h={stepH(i)} color={c.color} dashed={c.n === 3} on={on} opacity={fadeAt(frame, at, 0.4)}>
            <div style={{position: 'absolute', left: 18, top: 12, fontFamily: SANS, fontSize: 17, fontWeight: 700, color: MUTED}}>Class {c.n}</div>
            <div style={{position: 'absolute', left: 18, top: 36, fontFamily: SANS, fontSize: 24, fontWeight: 700, color: on ? LOOK : c.color}}>{c.name}</div>
            <div style={{position: 'absolute', left: 18, top: 78, fontFamily: SANS, fontSize: 18, lineHeight: '25px', color: TEXT}}>
              {c.k.map((line) => (
                <div key={line}>{line}</div>
              ))}
            </div>
            <div style={{position: 'absolute', left: 18, bottom: 14, fontFamily: SANS, fontSize: 16, color: c.n === 3 ? FAIL : MUTED}}>{c.members}</div>
          </Box>
        );
      })}
    </AbsoluteFill>
  );
};

// Scene 2 — class 0: the handler performs the operation and resumes k once (measure/dispatch.py).

const DISPATCH_LINES: Lines = [
  [0, 'Class 0, dispatch: the handler does the operation and resumes k once.'],
  [7.4, 'The same program runs under a provider router, or under a test handler.'],
  [13.6, 'This is the runtime power of dependency injection. A function call would do.'],
];
const DISPATCH = f(21);
const SUMMARIZE = ['@do', 'def summarize(text):', '    summary = yield Complete(', '        "gpt-4o", f"summarize: {text}")', '    return summary'];
const D = {x: 60, y: 130, w: 560, size: 19, line: 30};
const DH = {x: 680, y: 130, w: 540, h: 200};
const HANDLERS = [
  {name: 'routed', about: 'routes by model prefix', at: 1.2, body: ['@handler', 'def routed(effect: Complete, k):', '    provider = PROVIDERS[effect.model.split("-")[0]]', '    return (yield Resume(k, f"{provider}: summary"))']},
  {name: 'canned', about: 'a test handler', at: 10.0, body: ['@handler', 'def canned(effect: Complete, k):', '    return (yield Resume(k, "canned summary"))']},
];
// The two runs of measure/dispatch.py: when the effect leaves, and what summarize() answered (dispatch.log).
const RUNS = [
  {at: 7.8, call: 'routed(summarize("the report"))', answer: "'openai: summary'", reply: '"openai: summary"'},
  {at: 10.4, call: 'canned(summarize("the report"))', answer: "'canned summary'", reply: '"canned summary"'},
];

const DispatchScene: React.FC = () => {
  const frame = useSceneFrame();
  const which = frame < f(9.8) ? 0 : 1;
  const run = RUNS.reduce((found, r, i) => (frame >= f(r.at) ? i : found), -1);
  const s = run >= 0 ? RUNS[run].at : 0;
  const band = frame < f(1.0) ? null : run < 0 || frame < f(s + 1.6) ? 2 : mix(2, 4, ramp(frame, s + 1.6, s + 1.9));
  const yieldAt = {x: D.x + D.w + 16, y: lineMid(D.y, D.line, 2)};
  const handlerAt = {x: DH.x + 110, y: DH.y + DH.h - 28};
  const resumed = RUNS.filter((r) => frame >= f(r.at + 1.6)).length;
  return (
    <AbsoluteFill>
      <Headlines lines={DISPATCH_LINES} />
      <CodeBlock lines={SUMMARIZE} {...D} band={band} opacity={fadeAt(frame, 0)} />
      {HANDLERS.map((h, i) => (
        <Box key={h.name} x={DH.x} y={DH.y} w={DH.w} h={DH.h} color={TEST} on={run >= 0 && frame >= f(s + 0.7) && frame < f(s + 1.0)} opacity={i === 0 ? fadeAt(frame, h.at) * (1 - fadeAt(frame, 9.5)) : fadeAt(frame, h.at)}>
          <div style={{position: 'absolute', left: 20, top: 12, fontFamily: SANS, fontSize: 22, fontWeight: 700, color: TEST}}>
            <span style={{fontFamily: MONO}}>{h.name}</span>
            <span style={{fontWeight: 400, color: MUTED, marginLeft: 14, fontSize: 19}}>{h.about}</span>
          </div>
          <Code lines={h.body} x={20} y={56} size={15} line={26} />
        </Box>
      ))}
      {run >= 0 && (
        <>
          <Trip a={yieldAt} b={handlerAt} from={s} to={s + 0.7} text="Complete(...)" color={ACCENT} />
          <Trip a={handlerAt} b={yieldAt} from={s + 1.0} to={s + 1.6} text={RUNS[run].reply} color={TEST} />
        </>
      )}
      <Label x={D.x} y={D.y + 190} size={19} bold color={ACCENT} opacity={fadeAt(frame, 2.0)}>
        k resumed: <span style={{color: TEXT}}>{resumed === 0 ? '—' : 'once, right away'}</span>
        <span style={{color: MUTED, fontWeight: 400, marginLeft: 14}}>{which === 0 ? 'in this run' : 'in each run'}</span>
      </Label>
      <div style={{position: 'absolute', left: 60, top: 352, width: 1160, height: 100, boxSizing: 'border-box', padding: '10px 18px', background: '#0b0d13', borderRadius: 12, opacity: fadeAt(frame, 7.6)}}>
        <div style={{fontFamily: SANS, fontSize: 16, lineHeight: '22px', color: MUTED}}>what summarize() answers</div>
        {RUNS.map((r) => (
          <div key={r.call} style={{display: 'flex', height: 32, alignItems: 'center', fontFamily: MONO, fontSize: 18, opacity: fadeAt(frame, r.at + 1.8)}}>
            <div style={{width: 420, color: TEXT}}>{r.call}</div>
            <div style={{color: ACCENT}}>→ {r.answer}</div>
          </div>
        ))}
      </div>
      <Box x={60} y={476} w={1160} h={190} color={PROD} opacity={fadeAt(frame, 13.8, 0.5)}>
        <div style={{position: 'absolute', left: 20, top: 12, fontFamily: SANS, fontSize: 21, fontWeight: 700, color: PROD}}>the same choice with dependency injection</div>
        <Code lines={['def summarize_di(text, complete):', '    return complete("gpt-4o", f"summarize: {text}")']} x={20} y={50} size={17} line={27} />
        <div style={{position: 'absolute', left: 20, top: 116, fontFamily: MONO, fontSize: 17, color: TEXT, opacity: fadeAt(frame, 15.0)}}>
          summarize_di("the report", canned_client) <span style={{color: ACCENT}}>→ 'canned summary'</span>
        </div>
        <div style={{position: 'absolute', left: 20, top: 148, fontFamily: SANS, fontSize: 18, color: MUTED, opacity: fadeAt(frame, 16.8)}}>
          No continuation is needed. What doeff adds is where the stack is chosen: per run, per test, per scope.
        </div>
      </Box>
    </AbsoluteFill>
  );
};

// Scene 3 — class 1: work around one resume. memo elides a repeated call; retry re-yields the effect
// (measure/middleware.py).

const MIDDLE_LINES: Lines = [
  [0, 'Class 1, data middleware: k is still resumed once, with work around it.'],
  [7.0, 'memo reads the effect as data. A repeated question comes from its cache.'],
  [14.0, 'retry yields the effect again per attempt. Two fail; the third answers.'],
  [21.0, 'Then k is resumed once, with the first success: retrying is class 1.'],
  [28.0, 'Inspect, accumulate, elide, transform, repeat: each fits around one resume.'],
];
const MIDDLE = f(35.6);
const SWITCH = 14.0;
const PROGRAM = {x: 60, y: 120, w: 280, h: 250};
const MID = {x: 430, y: 120, w: 420, h: 250};
const PROVIDER = {x: 940, y: 120, w: 280, h: 250};
const rowY = (i: number) => PROGRAM.y + 92 + i * 44;
// The wire along the bottom of the boxes that the effects and answers travel on, one at a time.
const WIRE = PROGRAM.y + PROGRAM.h - 28;
const MEMO = ['@handler', 'def memo(effect: Complete, k):', '    if effect not in cache:', '        cache[effect] = yield effect  # the next handler out', '    return (yield Resume(k, cache[effect]))'];
const RETRY = [
  '@handler',
  'def retry(effect: Complete, k):',
  '    for attempt in range(3):',
  '        safe = yield Try(effect)  # a fresh attempt each time',
  '        if safe.is_ok():',
  '            return (yield Resume(k, safe.value))',
  '        last = safe.error',
  '    raise last',
];
// The three Complete effects of three_questions() through memo (middleware.log): a miss goes on to the provider.
const ASKS = [
  {prompt: 'plan', at: 7.3, hit: false},
  {prompt: 'risks', at: 9.3, hit: false},
  {prompt: 'plan', at: 11.9, hit: true},
];
// The attempts retry makes for one_question() against a provider that fails twice (middleware.log).
const ATTEMPTS = [
  {at: 15.2, ok: false},
  {at: 16.8, ok: false},
  {at: 18.4, ok: true},
];
const MIDDLE_KINDS = [
  {verb: 'inspect', example: 'cache keys, structured logs'},
  {verb: 'accumulate', example: 'one budget across calls'},
  {verb: 'elide', example: 'cache hit, dry-run'},
  {verb: 'transform', example: 'rewrite the model'},
  {verb: 'repeat', example: 'retry the operation'},
];

const MiddleScene: React.FC = () => {
  const frame = useSceneFrame();
  const memoSeen = 1 - fadeAt(frame, SWITCH, 0.4);
  const retrySeen = fadeAt(frame, SWITCH + 0.2, 0.4);
  const midRight = {x: MID.x + MID.w, y: 0};
  const progRight = PROGRAM.x + PROGRAM.w;
  const calls = ASKS.filter((a) => !a.hit && frame >= f(a.at + 1.3)).map((a) => a.prompt);
  const cached = ASKS.filter((a) => !a.hit && frame >= f(a.at + 2.0)).map((a) => a.prompt);
  const hitNow = frame >= f(ASKS[2].at + 0.6) && frame < f(ASKS[2].at + 1.4);
  const failed = ATTEMPTS.filter((a) => frame >= f(a.at + 0.9)).length;
  const resumed = frame >= f(21.9);
  const kindsSeen = fadeAt(frame, 28.2, 0.5);
  const codeBand = retrySeen > 0 ? (frame < f(14.8) ? null : frame < f(21.3) ? 3 : 5) : frame < f(7.3) ? null : frame >= f(ASKS[2].at) ? 4 : 2;
  return (
    <AbsoluteFill>
      <Headlines lines={MIDDLE_LINES} />
      <Box x={PROGRAM.x} y={PROGRAM.y} w={PROGRAM.w} h={PROGRAM.h} color={ACCENT} opacity={fadeAt(frame, 0.4)}>
        <div style={{position: 'absolute', left: 18, top: 12, fontFamily: MONO, fontSize: 20, fontWeight: 700, color: ACCENT}}>{frame < f(SWITCH + 0.2) ? 'three_questions()' : 'one_question()'}</div>
        <div style={{position: 'absolute', left: 18, top: 44, fontFamily: SANS, fontSize: 16, color: MUTED}}>{frame < f(SWITCH + 0.2) ? 'yields three Complete effects' : 'yields one Complete effect'}</div>
        <div style={{opacity: memoSeen}}>
          {ASKS.map((a, i) => (
            <div key={i} style={{position: 'absolute', left: 18, top: rowY(i) - PROGRAM.y - 13, fontFamily: MONO, fontSize: 17, color: frame >= f(a.at + (a.hit ? 1.4 : 2.6)) ? TEXT : MUTED}}>
              Complete("{a.prompt}"){frame >= f(a.at + (a.hit ? 1.4 : 2.6)) ? ' ✓' : ''}
            </div>
          ))}
        </div>
        <div style={{opacity: retrySeen}}>
          <div style={{position: 'absolute', left: 18, top: rowY(0) - PROGRAM.y - 13, fontFamily: MONO, fontSize: 17, color: resumed ? TEXT : MUTED}}>Complete("plan"){resumed ? ' ✓' : ''}</div>
          <div style={{position: 'absolute', left: 18, top: rowY(1) - PROGRAM.y - 13, width: 270, whiteSpace: 'normal', fontFamily: SANS, fontSize: 16, lineHeight: '22px', color: TEXT, opacity: fadeAt(frame, 22.4)}}>
            the code after the yield runs once
          </div>
        </div>
      </Box>
      <Box x={MID.x} y={MID.y} w={MID.w} h={MID.h} color={TEST} on={hitNow || (frame >= f(21.0) && frame < f(21.6))} opacity={fadeAt(frame, 0.8)}>
        <div style={{position: 'absolute', left: 18, top: 12, fontFamily: MONO, fontSize: 22, fontWeight: 700, color: TEST}}>{frame < f(SWITCH + 0.2) ? 'memo' : 'retry'}</div>
        <div style={{opacity: memoSeen}}>
          <div style={{position: 'absolute', left: 18, top: 50, fontFamily: SANS, fontSize: 16, color: MUTED}}>cache, keyed by the effect itself</div>
          {cached.map((prompt, i) => (
            <div
              key={prompt}
              style={{
                position: 'absolute', left: 18, top: 84 + i * 40, padding: '4px 10px', borderRadius: 8, border: `2px solid ${hitNow && prompt === 'plan' ? LOOK : '#2a2e40'}`,
                fontFamily: MONO, fontSize: 15, color: TEXT, whiteSpace: 'nowrap', opacity: fadeAt(frame, ASKS[i].at + 2.0),
              }}
            >
              Complete("{prompt}") → "answer to '{prompt}'"
            </div>
          ))}
          <Label x={18} y={176} size={17} bold color={LOOK} opacity={between(frame, ASKS[2].at + 0.6, SWITCH - 0.3)}>
            hit: no call, k resumed from the cache
          </Label>
        </div>
        <div style={{opacity: retrySeen}}>
          <div style={{position: 'absolute', left: 18, top: 50, fontFamily: SANS, fontSize: 16, color: MUTED}}>re-yields the effect per attempt</div>
          {ATTEMPTS.map((a, i) => (
            <div key={i} style={{position: 'absolute', left: 18, top: 84 + i * 30, fontFamily: MONO, fontSize: 16, color: a.ok ? TEST : FAIL, opacity: fadeAt(frame, a.at + 0.9)}}>
              attempt {i + 1}: {a.ok ? 'answers' : 'raises TimeoutError'}
            </div>
          ))}
          <Label x={18} y={182} size={17} bold color={LOOK} opacity={fadeAt(frame, 21.9)}>
            resumes k once
          </Label>
        </div>
      </Box>
      <Box x={PROVIDER.x} y={PROVIDER.y} w={PROVIDER.w} h={PROVIDER.h} color={PROD} on={ASKS.some((a) => !a.hit && frame >= f(a.at + 1.1) && frame < f(a.at + 1.4))} opacity={fadeAt(frame, 1.2)}>
        <div style={{position: 'absolute', left: 18, top: 12, fontFamily: MONO, fontSize: 22, fontWeight: 700, color: PROD}}>{frame < f(SWITCH + 0.2) ? 'provider' : 'flaky provider'}</div>
        <div style={{position: 'absolute', left: 18, top: 50, fontFamily: SANS, fontSize: 16, color: MUTED}}>the next handler out</div>
        <div style={{opacity: memoSeen}}>
          <div style={{position: 'absolute', left: 18, top: 92, fontFamily: SANS, fontSize: 17, color: TEXT}}>calls:</div>
          {calls.map((prompt, i) => (
            <div key={prompt} style={{position: 'absolute', left: 18, top: 122 + i * 30, fontFamily: MONO, fontSize: 17, color: PROD}}>'{prompt}'</div>
          ))}
          <Label x={18} y={190} size={18} bold color={TEXT} opacity={fadeAt(frame, ASKS[2].at + 1.5) * memoSeen}>
            2 calls for 3 effects
          </Label>
        </div>
        <div style={{opacity: retrySeen}}>
          <div style={{position: 'absolute', left: 18, top: 92, fontFamily: SANS, fontSize: 17, color: TEXT}}>fails twice, then answers</div>
          <div style={{position: 'absolute', left: 18, top: 126, fontFamily: MONO, fontSize: 17, color: PROD}}>attempts: {failed}</div>
        </div>
      </Box>
      {ASKS.map((a, i) => (
        <React.Fragment key={i}>
          <Trip a={{x: progRight, y: WIRE}} b={{x: MID.x + 10, y: WIRE}} from={a.at} to={a.at + 0.6} text={`Complete("${a.prompt}")`} color={ACCENT} />
          {!a.hit && <Trip a={{x: midRight.x - 10, y: WIRE}} b={{x: PROVIDER.x + 10, y: WIRE}} from={a.at + 0.6} to={a.at + 1.1} text="yield effect" color={ACCENT} />}
          {!a.hit && <Trip a={{x: PROVIDER.x + 10, y: WIRE}} b={{x: midRight.x - 10, y: WIRE}} from={a.at + 1.4} to={a.at + 1.9} text="answer" color={PROD} />}
          <Trip a={{x: MID.x + 10, y: WIRE}} b={{x: progRight, y: WIRE}} from={a.at + (a.hit ? 0.9 : 2.0)} to={a.at + (a.hit ? 1.4 : 2.5)} text={`"answer to '${a.prompt}'"`} color={TEST} />
        </React.Fragment>
      ))}
      <Trip a={{x: progRight, y: WIRE}} b={{x: MID.x + 10, y: WIRE}} from={14.5} to={15.1} text={'Complete("plan")'} color={ACCENT} />
      {ATTEMPTS.map((a, i) => (
        <React.Fragment key={i}>
          <Trip a={{x: midRight.x - 10, y: WIRE}} b={{x: PROVIDER.x + 10, y: WIRE}} from={a.at} to={a.at + 0.5} text="Try(effect)" color={ACCENT} />
          <Trip a={{x: PROVIDER.x + 10, y: WIRE}} b={{x: midRight.x - 10, y: WIRE}} from={a.at + 0.6} to={a.at + 1.1} text={a.ok ? 'Ok(answer)' : 'Err(TimeoutError)'} color={a.ok ? TEST : FAIL} />
        </React.Fragment>
      ))}
      <Trip a={{x: MID.x + 10, y: WIRE}} b={{x: progRight, y: WIRE}} from={21.3} to={21.9} text={`"answer to 'plan'"`} color={TEST} />
      <CodeBlock lines={MEMO} x={60} y={400} w={1160} size={18} line={30} band={codeBand} opacity={fadeAt(frame, 7.0) * memoSeen} />
      <CodeBlock lines={RETRY} x={60} y={400} w={1160} size={18} line={30} band={codeBand} opacity={retrySeen * (1 - kindsSeen)} />
      {MIDDLE_KINDS.map((kind, i) => (
        <div
          key={kind.verb}
          style={{
            position: 'absolute', left: 60 + i * 236, top: 420, width: 216, height: 150, boxSizing: 'border-box', borderRadius: 14, background: PANEL,
            border: `2px solid ${TEST}`, padding: '14px 16px', opacity: fadeAt(frame, 28.4 + i * 0.5, 0.4),
          }}
        >
          <div style={{fontFamily: SANS, fontSize: 24, fontWeight: 700, color: TEST}}>{kind.verb}</div>
          <div style={{fontFamily: SANS, fontSize: 17, lineHeight: '24px', color: MUTED, marginTop: 10, whiteSpace: 'normal'}}>{kind.example}</div>
        </div>
      ))}
      <Label x={640} y={600} size={20} color={TEXT} center opacity={fadeAt(frame, 31.4)}>
        the machinery: effects as data, handler state, and <span style={{fontFamily: MONO, color: ACCENT}}>yield effect</span> to re-dispatch
      </Label>
    </AbsoluteFill>
  );
};

// Scene 4 — class 2: k escapes the handler. In-flight dedup parks a k until the one call returns
// (measure/scheduling.py; times on the virtual clock).

const SCHED_LINES: Lines = [
  [0, 'Class 2, scheduling: k escapes the handler, parked and resumed later.'],
  [7.8, 'Task 1 asks for prices. The call takes 2 s on a virtual clock.'],
  [14.6, '0.5 s later, task 2 asks for the same prices. dedup parks its k.'],
  [22.2, 'The call returns; both tasks resume with one value. 2 calls, 3 requests.'],
  [29.8, 'Dependency injection has no continuations, so it cannot reach this class.'],
];
const SCHED = f(36);
// Scene seconds to seconds on the virtual clock (09:00:00 + s), and back.
const SCENE_T = [8.0, 15.0, 18.6, 22.4, 26.6];
const SIM_T = [0, 0.5, 1.0, 2.0, 3.0];
const simAt = (frame: number) => interpolate(frame, SCENE_T.map(f), SIM_T, clamp);
const sceneAt = (sim: number) => interpolate(sim, SIM_T, SCENE_T, clamp);
const X0 = 330;
const PX = 245;
const X = (sim: number) => X0 + sim * PX;
const LANE = {task1: 196, task2: 256, task3: 316, dedup: 396, prices: 474, news: 524};
// The requests of scheduling.log: who asks for what, when, and when the answer arrives.
const REQUESTS = [
  {name: 'task 1', y: LANE.task1, key: 'prices', ask: 0, get: 2},
  {name: 'task 2', y: LANE.task2, key: 'prices', ask: 0.5, get: 2},
  {name: 'task 3', y: LANE.task3, key: 'news', ask: 1, get: 3},
];
const CALLS = [
  {key: 'prices', y: LANE.prices, from: 0, to: 2},
  {key: 'news', y: LANE.news, from: 1, to: 3},
];
const stamp = (sim: number) => {
  const tenths = Math.floor(sim * 10 + 1e-6);
  return `09:00:${String(Math.floor(tenths / 10)).padStart(2, '0')}.${tenths % 10}`;
};

const SchedScene: React.FC = () => {
  const frame = useSceneFrame();
  const sim = simAt(frame);
  const clockSeen = fadeAt(frame, 7.6, 0.5);
  const lanesSeen = fadeAt(frame, 1.0, 0.5);
  const parkedOn = sim >= 0.5;
  const resumedAt = sceneAt(2);
  const resumeGlow = frame >= f(resumedAt + 0.9) && frame < f(resumedAt + 1.6);
  const bar = (from: number, to: number) => ({left: X(from), width: Math.max(0, X(Math.min(sim, to)) - X(from))});
  return (
    <AbsoluteFill>
      <Headlines lines={SCHED_LINES} />
      <div style={{position: 'absolute', right: 60, top: 112, fontFamily: MONO, fontSize: 24, fontWeight: 700, color: LOOK, opacity: clockSeen}}>
        <span style={{fontFamily: SANS, fontSize: 17, fontWeight: 400, color: MUTED, marginRight: 12}}>virtual clock</span>
        {stamp(sim)}
      </div>
      <div style={{position: 'absolute', left: 60, top: 116, fontFamily: MONO, fontSize: 18, color: TEXT, opacity: fadeAt(frame, 0.6)}}>
        run(scheduled(sim_time_handler(...)(backend(dedup(main())))))
      </div>
      {[
        ['task 1', LANE.task1, ACCENT], ['task 2', LANE.task2, ACCENT], ['task 3', LANE.task3, ACCENT], ['dedup', LANE.dedup, TEST], ['backend', (LANE.prices + LANE.news) / 2, PROD],
      ].map(([name, y, color]) => (
        <div key={name as string} style={{position: 'absolute', left: 60, top: (y as number) - 13, fontFamily: MONO, fontSize: 20, fontWeight: 700, color: color as string, opacity: lanesSeen}}>
          {name}
        </div>
      ))}
      {Object.entries(LANE).map(([name, y]) => (
        <div key={name} style={{position: 'absolute', left: X0, top: y, width: X(3.5) - X0, height: 2, background: '#2a2e40', opacity: lanesSeen}} />
      ))}
      {[0, 1, 2, 3].map((s) => (
        <div key={s} style={{position: 'absolute', left: X(s), top: 552, transform: 'translateX(-50%)', fontFamily: MONO, fontSize: 15, color: MUTED, opacity: clockSeen}}>
          09:00:0{s}
        </div>
      ))}
      {REQUESTS.map((r) => {
        const asked = sim >= r.ask;
        const got = sim >= r.get;
        return (
          <React.Fragment key={r.name}>
            {asked && <div style={{position: 'absolute', top: r.y - 8, height: 16, borderRadius: 8, background: ACCENT, opacity: 0.35, ...bar(r.ask, r.get)}} />}
            {asked && <div style={{position: 'absolute', left: X(r.ask) + 6, top: r.y - 34, fontFamily: MONO, fontSize: 15, color: MUTED}}>asks '{r.key}'</div>}
            {got && <div style={{position: 'absolute', left: X(r.get) + 10, top: r.y - 12, fontFamily: MONO, fontSize: 17, fontWeight: 700, color: TEST, whiteSpace: 'nowrap', opacity: fadeAt(frame, sceneAt(r.get) + 1.0)}}>gets '{r.key}@v1'</div>}
          </React.Fragment>
        );
      })}
      {CALLS.map((c) => (
        sim >= c.from && (
          <div key={c.key} style={{position: 'absolute', top: c.y - 15, height: 30, borderRadius: 8, boxSizing: 'border-box', border: `2px solid ${PROD}`, background: '#3a2a1a', ...bar(c.from, c.to)}}>
            <div style={{position: 'absolute', left: 10, top: 3, fontFamily: MONO, fontSize: 15, color: PROD, whiteSpace: 'nowrap'}}>call '{c.key}'</div>
          </div>
        )
      ))}
      {parkedOn && (
        <div
          style={{
            position: 'absolute', top: LANE.dedup - 18, height: 36, borderRadius: 8, boxSizing: 'border-box', border: `3px dashed ${resumeGlow ? LOOK : ACCENT}`, background: '#1d1a33',
            boxShadow: resumeGlow ? `0 0 18px ${LOOK}aa` : 'none', ...bar(0.5, 2),
          }}
        >
          <div style={{position: 'absolute', left: 10, top: 5, fontFamily: MONO, fontSize: 15, color: TEXT, whiteSpace: 'nowrap'}}>k of task 2, parked</div>
        </div>
      )}
      <Label x={X(0.5) + 6} y={LANE.dedup + 22} size={15} color={LOOK} opacity={between(frame, sceneAt(0.5) + 0.8, resumedAt)}>
        'prices' is in flight: no second call
      </Label>
      <div style={{position: 'absolute', left: X(sim), top: 170, width: 2, height: 374, background: LOOK, opacity: clockSeen * 0.8}} />
      <Trip a={{x: X(0), y: LANE.task1}} b={{x: X(0), y: LANE.prices}} from={8.2} to={9.2} text={'Fetch("prices")'} color={ACCENT} />
      <Trip a={{x: X(0.5), y: LANE.task2}} b={{x: X(0.5), y: LANE.dedup}} from={sceneAt(0.5) + 0.1} to={sceneAt(0.5) + 0.8} text={'Fetch("prices")'} color={ACCENT} />
      <Trip a={{x: X(1), y: LANE.task3}} b={{x: X(1), y: LANE.news}} from={sceneAt(1) + 0.1} to={sceneAt(1) + 1.0} text={'Fetch("news")'} color={ACCENT} />
      <Trip a={{x: X(2), y: LANE.prices}} b={{x: X(2), y: LANE.task1}} from={resumedAt + 0.2} to={resumedAt + 1.0} text="'prices@v1'" color={TEST} />
      <Trip a={{x: X(2) + 40, y: LANE.prices}} b={{x: X(2) + 40, y: LANE.dedup}} from={resumedAt + 0.2} to={resumedAt + 0.8} text="'prices@v1'" color={TEST} />
      <Trip a={{x: X(2) + 40, y: LANE.dedup}} b={{x: X(2) + 40, y: LANE.task2}} from={resumedAt + 0.9} to={resumedAt + 1.4} text="Resume(k, ...)" color={TEST} />
      <Trip a={{x: X(3), y: LANE.news}} b={{x: X(3), y: LANE.task3}} from={sceneAt(3) + 0.1} to={sceneAt(3) + 1.0} text="'news@v1'" color={TEST} />
      <div style={{position: 'absolute', left: 0, right: 0, top: 590, textAlign: 'center', fontFamily: MONO, fontSize: 19, color: TEXT, opacity: fadeAt(frame, 27.6)}}>
        3 requests · backend calls: ['prices', 'news']
      </div>
      <div style={{position: 'absolute', left: 0, right: 0, top: 636, textAlign: 'center', fontFamily: SANS, fontSize: 20, color: TEXT, opacity: fadeAt(frame, 30.0)}}>
        <span style={{color: PROD, fontWeight: 700}}>DI</span> has no <span style={{fontFamily: MONO, color: ACCENT}}>k</span> to park. Also class 2: batching · hedged requests · rate limiting · virtual time
      </div>
    </AbsoluteFill>
  );
};

// Scene 5 — class 3 is absent: a second resume raises; re-running per branch with a shared memo is the approximation
// (measure/multishot.py).

const MULTI_LINES: Lines = [
  [0, 'Class 3, multi-shot, is deliberately absent. Every k in doeff is one-shot.'],
  [6.8, 'A handler that resumes k a second time gets a RuntimeError.'],
  [11.8, 'Instead: run the whole program again per branch, with a memoized prefix.'],
  [18.6, 'Both branches run; the expensive call before the branch happens once.'],
];
const MULTI = f(25.4);
const WHAT_IF = ['@do', 'def what_if():', '    plan = yield Complete("plan")', '    branch = yield Ask("branch")', '    return f"{plan} -> {branch}"'];
const M = {x: 60, y: 130, w: 560, size: 19, line: 30};
const TWICE = {x: 680, y: 130, w: 540, h: 210};
const BRANCHES = [
  {branch: 'a', y: 380, at: 12.8, hit: false},
  {branch: 'b', y: 480, at: 15.6, hit: true},
];
const MEMO_BOX = {x: 600, y: 356, w: 300, h: 196};
const CALLER = {x: 930, y: 356, w: 290, h: 196};

const MultiScene: React.FC = () => {
  const frame = useSceneFrame();
  const aSeen = 1 - fadeAt(frame, 11.8, 0.4);
  const yieldAt = {x: M.x + M.w + 16, y: lineMid(M.y, M.line, 2)};
  const handlerAt = {x: TWICE.x + 80, y: TWICE.y + TWICE.h - 30};
  const band = frame < f(1.0) ? null : frame < f(11.8) ? (frame < f(7.6) ? 2 : 3) : null;
  const cachedSeen = fadeAt(frame, BRANCHES[0].at + 2.0);
  const hitOn = frame >= f(BRANCHES[1].at + 0.6) && frame < f(BRANCHES[1].at + 1.3);
  return (
    <AbsoluteFill>
      <Headlines lines={MULTI_LINES} />
      <CodeBlock lines={WHAT_IF} {...M} band={band} opacity={fadeAt(frame, 0)} />
      <Box x={TWICE.x} y={TWICE.y} w={TWICE.w} h={TWICE.h} color={TEST} on={frame >= f(1.7) && frame < f(2.2)} opacity={fadeAt(frame, 0.6) * aSeen}>
        <div style={{position: 'absolute', left: 20, top: 12, fontFamily: SANS, fontSize: 22, fontWeight: 700, color: TEST}}>
          <span style={{fontFamily: MONO}}>twice</span>
          <span style={{fontWeight: 400, color: MUTED, marginLeft: 14, fontSize: 19}}>resumes k two times</span>
        </div>
        <Code lines={['@handler', '@do(non_tail=True)', 'def twice(effect: Complete, k):', '    yield Resume(k, "first")', '    return (yield Resume(k, "second"))']} x={20} y={56} size={16} line={27} />
      </Box>
      <Trip a={yieldAt} b={handlerAt} from={1.0} to={1.7} text={'Complete("plan")'} color={ACCENT} />
      <Trip a={handlerAt} b={yieldAt} from={7.0} to={7.6} text={'"first"'} color={TEST} />
      <Trip a={handlerAt} b={{x: yieldAt.x + 40, y: yieldAt.y}} from={8.2} to={8.8} text={'"second"'} color={FAIL} />
      <Label x={M.x} y={M.y + 192} size={18} color={MUTED} opacity={fadeAt(frame, 2.6) * aSeen}>
        one-shot, unlike Koka or Eff: the ceiling is explicit
      </Label>
      <div style={{position: 'absolute', left: 60, top: 400, width: 1160, textAlign: 'center', fontFamily: MONO, fontSize: 19, fontWeight: 700, color: FAIL, opacity: fadeAt(frame, 9.0) * aSeen}}>
        raises RuntimeError: continuation already consumed (one-shot violation)
      </div>
      {BRANCHES.map((b, i) => {
        const done = frame >= f(b.at + (b.hit ? 1.6 : 2.6));
        return (
          <React.Fragment key={b.branch}>
            <Box x={60} y={b.y - 40} w={480} h={80} color={ACCENT} opacity={fadeAt(frame, 12.2 + i * 0.3)}>
              <div style={{position: 'absolute', left: 18, top: 10, fontFamily: MONO, fontSize: 18, color: TEXT}}>
                run {i + 1}: what_if() with branch={'"'}{b.branch}{'"'}
              </div>
              <div style={{position: 'absolute', left: 18, top: 42, fontFamily: MONO, fontSize: 18, fontWeight: 700, color: ACCENT, opacity: done ? fadeAt(frame, b.at + (b.hit ? 1.6 : 2.6)) : 0}}>
                → 'plan v1 -&gt; {b.branch}'
              </div>
            </Box>
            <Trip a={{x: 540, y: b.y}} b={{x: MEMO_BOX.x + 40, y: b.y}} from={b.at} to={b.at + 0.6} text={'Complete("plan")'} color={ACCENT} />
            {!b.hit && <Trip a={{x: MEMO_BOX.x + MEMO_BOX.w - 30, y: b.y}} b={{x: CALLER.x + 50, y: b.y}} from={b.at + 0.6} to={b.at + 1.1} text="yield effect" color={ACCENT} />}
            {!b.hit && <Trip a={{x: CALLER.x + 50, y: b.y}} b={{x: MEMO_BOX.x + MEMO_BOX.w - 30, y: b.y}} from={b.at + 1.4} to={b.at + 1.9} text={'"plan v1"'} color={PROD} />}
            <Trip a={{x: MEMO_BOX.x + 40, y: b.y}} b={{x: 540, y: b.y}} from={b.at + (b.hit ? 0.9 : 2.0)} to={b.at + (b.hit ? 1.5 : 2.6)} text={'"plan v1"'} color={TEST} />
          </React.Fragment>
        );
      })}
      <Box x={MEMO_BOX.x} y={MEMO_BOX.y} w={MEMO_BOX.w} h={MEMO_BOX.h} color={TEST} on={hitOn} opacity={fadeAt(frame, 12.0)}>
        <div style={{position: 'absolute', left: 18, top: 12, fontFamily: MONO, fontSize: 21, fontWeight: 700, color: hitOn ? LOOK : TEST}}>memo</div>
        <div style={{position: 'absolute', left: 18, top: 44, fontFamily: SANS, fontSize: 16, color: MUTED}}>one cache, shared by both runs</div>
        <div style={{position: 'absolute', left: 18, top: 150, fontFamily: MONO, fontSize: 15, color: TEXT, opacity: cachedSeen}}>Complete("plan") → "plan v1"</div>
      </Box>
      <Box x={CALLER.x} y={CALLER.y} w={CALLER.w} h={CALLER.h} color={PROD} on={frame >= f(BRANCHES[0].at + 1.1) && frame < f(BRANCHES[0].at + 1.4)} opacity={fadeAt(frame, 12.4)}>
        <div style={{position: 'absolute', left: 18, top: 12, fontFamily: MONO, fontSize: 21, fontWeight: 700, color: PROD}}>provider</div>
        <div style={{position: 'absolute', left: 18, top: 44, fontFamily: SANS, fontSize: 16, color: MUTED}}>the expensive call</div>
        <div style={{position: 'absolute', left: 18, top: 150, fontFamily: MONO, fontSize: 16, color: PROD, opacity: fadeAt(frame, BRANCHES[0].at + 1.1)}}>calls: ['plan']</div>
      </Box>
      <div style={{position: 'absolute', left: 0, right: 0, top: 604, textAlign: 'center', fontFamily: SANS, fontSize: 21, color: TEXT, opacity: fadeAt(frame, 18.9)}}>
        two branches, one call: re-execution, not continuation copying
      </div>
    </AbsoluteFill>
  );
};

// Scene 6 — the evidence rule, with the costs of measure/overhead.py.

const RULE_LINES: Lines = [
  [0, 'When does adopting effects pay? One rule explains the audited projects.'],
  [5.6, '(a) You need to change what call boundaries do, at composition or run time.'],
  [12.5, '(b) The call behind a boundary must cost far more than intercepting it.'],
  [18.3, 'One effect cost 3.5 µs. Next to a 10 ms call, that is 0.03 %.'],
  [26.0, 'In a hot loop it is 49× a plain step: keep such loops below the boundary.'],
];
const RULE = f(33.6);
const LEFT = {x: 60, y: 252, w: 540, h: 400};
const RIGHT = {x: 640, y: 252, w: 580, h: 400};
const BAR = {x: RIGHT.x + 24, w: 520};
// overhead.log: per effect 3.5 us, per plain dict lookup 72 ns (49x), one 10 ms call (measured 10.1 ms) +0.03 %.
const COST = {effectUs: 3.5, plainNs: 72, times: 49, callMs: 10.1, percent: '0.03'};
const STACKS = [
  {name: 'simulated', color: TEST},
  {name: 'paper', color: TEST},
  {name: 'live', color: PROD},
];

const RuleScene: React.FC = () => {
  const frame = useSceneFrame();
  const aOn = frame >= f(5.6) && frame < f(12.5);
  const bOn = frame >= f(12.5);
  const fill1 = ramp(frame, 19.0, 20.0);
  const fill2 = ramp(frame, 26.4, 27.4);
  return (
    <AbsoluteFill>
      <Headlines lines={RULE_LINES} />
      <div
        style={{
          position: 'absolute', left: 60, top: 112, width: 1160, height: 116, boxSizing: 'border-box', borderRadius: 14, background: PANEL, border: `2px solid #2a2e40`,
          padding: '16px 24px', fontFamily: SANS, fontSize: 23, lineHeight: '36px', color: TEXT, opacity: fadeAt(frame, 0.6, 0.5),
        }}
      >
        Effects pay when <span style={{color: aOn ? LOOK : ACCENT, fontWeight: 700}}>(a) you manipulate call boundaries at composition or run time</span>, and{' '}
        <span style={{color: bOn ? LOOK : ACCENT, fontWeight: 700}}>(b) each call costs far more than its interception</span>.
      </div>
      <Box x={LEFT.x} y={LEFT.y} w={LEFT.w} h={LEFT.h} color={TEST} on={aOn} opacity={fadeAt(frame, 5.8, 0.5)}>
        <div style={{position: 'absolute', left: 20, top: 14, fontFamily: SANS, fontSize: 22, fontWeight: 700, color: aOn ? LOOK : TEST}}>(a) one program, many stacks</div>
        <div style={{position: 'absolute', left: 170, top: 66, width: 200, height: 54, boxSizing: 'border-box', borderRadius: 12, border: `3px solid ${ACCENT}`, background: '#1d1a33', fontFamily: MONO, fontSize: 19, color: ACCENT, textAlign: 'center', lineHeight: '48px'}}>
          pipeline()
        </div>
        {STACKS.map((s, i) => (
          <React.Fragment key={s.name}>
            <svg style={{position: 'absolute', left: 0, top: 0}} width={LEFT.w} height={LEFT.h}>
              <line x1={270} y1={122} x2={mix(270, 120 + i * 160, ramp(frame, 7.0 + i * 0.4, 7.4 + i * 0.4))} y2={mix(122, 166, ramp(frame, 7.0 + i * 0.4, 7.4 + i * 0.4))} stroke={MUTED} strokeWidth={2} />
            </svg>
            <div
              style={{
                position: 'absolute', left: 50 + i * 160, top: 168, width: 140, height: 48, boxSizing: 'border-box', borderRadius: 10, border: `2px solid ${s.color}`,
                fontFamily: MONO, fontSize: 18, color: s.color, textAlign: 'center', lineHeight: '44px', opacity: fadeAt(frame, 7.3 + i * 0.4),
              }}
            >
              {s.name}
            </div>
          </React.Fragment>
        ))}
        <div style={{position: 'absolute', left: 20, top: 240, width: 500, whiteSpace: 'normal', fontFamily: SANS, fontSize: 18, lineHeight: '26px', color: TEXT, opacity: fadeAt(frame, 8.6)}}>
          <span style={{color: TEST, fontWeight: 700}}>strong fits:</span> simulated, paper and live runs of one program; record and replay of expensive calls; hermetic tests
        </div>
        <div style={{position: 'absolute', left: 20, top: 320, width: 500, whiteSpace: 'normal', fontFamily: SANS, fontSize: 18, lineHeight: '26px', color: MUTED, opacity: fadeAt(frame, 10.2)}}>
          <span style={{color: FAIL, fontWeight: 700}}>poor fit:</span> code that only ever runs one way
        </div>
      </Box>
      <Box x={RIGHT.x} y={RIGHT.y} w={RIGHT.w} h={RIGHT.h} color={TEST} on={bOn && frame < f(18.3)} opacity={fadeAt(frame, 12.7, 0.5)}>
        <div style={{position: 'absolute', left: 20, top: 14, fontFamily: SANS, fontSize: 22, fontWeight: 700, color: bOn && frame < f(18.3) ? LOOK : TEST}}>(b) the cost of one effect</div>
        <div style={{position: 'absolute', left: 20, top: 50, fontFamily: SANS, fontSize: 16, color: MUTED}}>measured: Ask under reader, best of 5 runs of 20,000</div>
      </Box>
      <div style={{position: 'absolute', left: BAR.x, top: RIGHT.y + 92, fontFamily: SANS, fontSize: 18, color: TEXT, opacity: fadeAt(frame, 18.5)}}>a call that takes {COST.callMs} ms, through one effect</div>
      <div style={{position: 'absolute', left: BAR.x, top: RIGHT.y + 122, width: BAR.w * fill1, height: 30, borderRadius: 6, background: PROD, opacity: fadeAt(frame, 18.8)}} />
      <div style={{position: 'absolute', left: BAR.x + BAR.w * fill1, top: RIGHT.y + 118, width: 3, height: 38, background: LOOK, boxShadow: `0 0 10px ${LOOK}`, opacity: fadeAt(frame, 20.0)}} />
      <div style={{position: 'absolute', left: BAR.x, top: RIGHT.y + 160, fontFamily: MONO, fontSize: 18, color: TEXT, opacity: fadeAt(frame, 20.2)}}>
        {COST.callMs} ms <span style={{color: LOOK}}>+ {COST.effectUs} µs</span> = <span style={{color: TEST, fontWeight: 700}}>+{COST.percent} %</span>
      </div>
      <div style={{position: 'absolute', left: BAR.x, top: RIGHT.y + 222, fontFamily: SANS, fontSize: 18, color: TEXT, opacity: fadeAt(frame, 26.2)}}>a hot loop step</div>
      <div style={{position: 'absolute', left: BAR.x, top: RIGHT.y + 254, fontFamily: MONO, fontSize: 15, color: MUTED, opacity: fadeAt(frame, 26.2)}}>plain dict lookup · {COST.plainNs} ns</div>
      <div style={{position: 'absolute', left: BAR.x + 250, top: RIGHT.y + 254, width: Math.max(2, ((BAR.w - 250) / COST.times) * fill2), height: 18, borderRadius: 4, background: MUTED, opacity: fadeAt(frame, 26.2)}} />
      <div style={{position: 'absolute', left: BAR.x, top: RIGHT.y + 288, fontFamily: MONO, fontSize: 15, color: MUTED, opacity: fadeAt(frame, 26.6)}}>one effect · {COST.effectUs} µs</div>
      <div style={{position: 'absolute', left: BAR.x + 250, top: RIGHT.y + 288, width: (BAR.w - 250) * fill2, height: 18, borderRadius: 4, background: FAIL, opacity: fadeAt(frame, 26.6)}} />
      <div style={{position: 'absolute', left: BAR.x, top: RIGHT.y + 326, fontFamily: MONO, fontSize: 18, color: TEXT, opacity: fadeAt(frame, 27.6)}}>
        <span style={{color: FAIL, fontWeight: 700}}>{COST.times}×</span>
        <span style={{fontFamily: SANS, color: MUTED, marginLeft: 12}}>keep sub-millisecond hot paths below the boundary</span>
      </div>
    </AbsoluteFill>
  );
};

// The outro.

const OUTRO_LINES: Lines = [[0, 'What a handler does with k tells you its cost, and if you need effects.']];
const OUTRO = f(8.6);
const VERDICTS = [
  {n: 0, says: 'the power of DI', color: TEST},
  {n: 1, says: 'cheap to build', color: TEST},
  {n: 2, says: 'effects required', color: ACCENT},
  {n: 3, says: 'absent: re-run', color: FAIL},
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
      <Label x={640} y={380} size={22} color={MUTED} center opacity={fadeAt(frame, 1.0)}>
        what the handler does with <span style={{fontFamily: MONO, color: ACCENT}}>k</span> decides the class
      </Label>
      {VERDICTS.map((v, i) => (
        <div
          key={v.n}
          style={{
            position: 'absolute', left: 640 - (4 * 270 - 20) / 2 + i * 270, top: 430, width: 250, height: 84, boxSizing: 'border-box', borderRadius: 12,
            border: `2px ${v.n === 3 ? 'dashed' : 'solid'} ${v.color}`, background: PANEL, textAlign: 'center', paddingTop: 10, opacity: fadeAt(frame, 1.4 + i * 0.3),
          }}
        >
          <div style={{fontFamily: SANS, fontSize: 17, color: MUTED}}>Class {v.n}</div>
          <div style={{fontFamily: SANS, fontSize: 21, fontWeight: 700, color: v.color, marginTop: 4, whiteSpace: 'nowrap'}}>{v.says}</div>
        </div>
      ))}
    </AbsoluteFill>
  );
};

// The scenes in order; Root.tsx registers CAPABILITY_CLASSES.length frames of CAPABILITY_CLASSES.Video.
export const CAPABILITY_CLASSES = narrate('capability-classes', DURATIONS, [
  {length: TITLE, starts: startsOf(TITLE_LINES), ids: ['t1', 't2', 't3'], node: <TitleScene />},
  {length: DISPATCH, starts: startsOf(DISPATCH_LINES), ids: ['a1', 'a2', 'a3'], node: <DispatchScene />},
  {length: MIDDLE, starts: startsOf(MIDDLE_LINES), ids: ['b1', 'b2', 'b3', 'b4', 'b5'], node: <MiddleScene />},
  {length: SCHED, starts: startsOf(SCHED_LINES), ids: ['c1', 'c2', 'c3', 'c4', 'c5'], node: <SchedScene />},
  {length: MULTI, starts: startsOf(MULTI_LINES), ids: ['d1', 'd2', 'd3', 'd4'], node: <MultiScene />},
  {length: RULE, starts: startsOf(RULE_LINES), ids: ['e1', 'e2', 'e3', 'e4', 'e5'], node: <RuleScene />},
  {length: OUTRO, starts: [0], ids: ['z1'], node: <OutroScene />},
]);
