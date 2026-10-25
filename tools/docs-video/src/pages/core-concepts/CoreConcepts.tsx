// The video on the Core Concepts page (docs/02-core-concepts.md): the model the chapter defines, in motion. Seven
// scenes, each with headlines a synthetic voice reads (narration.json):
//   1. The three pieces: an effect names an operation, a handler receives it with k, the rest of the program.
//   2. A program is a generator; each effect travels out to the innermost handler whose annotation matches it.
//   3. A handler stays installed around k, so the effects of the rest reach it too (after(job())).
//   4. What the handler does with k decides what the effect means: resume, throw into, pass, hold, drop; k is one-shot.
//   5. Handlers compose by nesting, and the order matters: writer needs state outside it.
//   6. run: calling a @do function builds a program; run evaluates it, yield by yield.
//   7. The outro.
// Every output, order and error on screen is printed by a program in measure/ (its .log is next to it).
import React from 'react';
import {AbsoluteFill} from 'remotion';
import {fadeAt, mix, ramp} from '../../kit/motion';
import {narrate} from '../../kit/narrated';
import {CodeBlock, glow, Headlines, highlightYield, lineMid, Lines, PAD, Pill, startsOf} from '../../kit/parts';
import {ACCENT, f, LOOK, MONO, MUTED, PANEL, PROD, SANS, TEST, TEXT} from '../../kit/theme';
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

// The layers of an onion of handlers around a code box: layer 0 is the innermost.
const onion = (cx: number, cy: number, w: number, h: number, margin: number) => (i: number) => {
  const lw = w + 2 * margin * (i + 1);
  const lh = h + 2 * margin * (i + 1);
  return {x: cx - lw / 2, y: cy - lh / 2, w: lw, h: lh};
};

// Scene 1 — the three pieces.

const TITLE_LINES: Lines = [
  [0, 'Core Concepts: effects, handlers and continuations.'],
  [4.5, 'An effect is a plain value that names an operation. It does nothing by itself.'],
  [9.5, 'A handler receives it, together with k: the rest of the program.'],
];
const TITLE = f(15);
const PIECES = [
  {name: 'effect', mono: false, color: ACCENT, about: ['a plain value that', 'names an operation']},
  {name: 'handler', mono: false, color: TEST, about: ['a function of (effect, k)', 'that decides what the', 'effect means']},
  {name: 'k', mono: true, color: ACCENT, about: ['the continuation: the rest', 'of the program, from the', 'yield on']},
];
const CARD = {y: 320, w: 340, h: 250, gap: 30};
const cardX = (i: number) => 640 - (3 * CARD.w + 2 * CARD.gap) / 2 + i * (CARD.w + CARD.gap);

const TitleScene: React.FC = () => {
  const frame = useSceneFrame();
  const move = ramp(frame, 10.0, 10.8);
  const kMove = ramp(frame, 10.4, 11.2);
  const slot = (i: number, row: number) => ({x: cardX(i) + CARD.w / 2, y: CARD.y + 170 + row * 44});
  const pill = {x: mix(slot(0, 0).x, slot(1, 0).x, move), y: slot(0, 0).y};
  const k = {x: mix(slot(2, 0).x, slot(1, 1).x, kMove), y: mix(slot(2, 0).y, slot(1, 1).y, kMove)};
  return (
    <AbsoluteFill>
      <Headlines lines={TITLE_LINES} />
      <div style={{position: 'absolute', left: 0, right: 0, top: 112 + mix(16, 0, ramp(frame, 0.2, 0.8)), textAlign: 'center', opacity: fadeAt(frame, 0.2, 0.5)}}>
        <div style={{fontFamily: SANS, fontSize: 72, fontWeight: 700, color: TEXT}}>Core Concepts</div>
        <div style={{fontFamily: SANS, fontSize: 28, color: ACCENT, marginTop: 6}}>the model doeff runs: algebraic effects</div>
      </div>
      {PIECES.map((piece, i) => {
        const on = (i === 0 && frame >= f(4.5) && frame < f(9.9)) || (i === 1 && frame >= f(10.8));
        return (
          <Box key={piece.name} x={cardX(i)} y={CARD.y} w={CARD.w} h={CARD.h} color={piece.color} on={on} dashed={piece.mono} opacity={fadeAt(frame, 1.0 + i * 0.5, 0.5)}>
            <div style={{position: 'absolute', left: 22, top: 14, fontFamily: piece.mono ? MONO : SANS, fontSize: 28, fontWeight: 700, color: on ? LOOK : piece.color}}>{piece.name}</div>
            <div style={{position: 'absolute', left: 22, top: 60, fontFamily: SANS, fontSize: 19, lineHeight: '27px', color: MUTED}}>
              {piece.about.map((line) => (
                <div key={line}>{line}</div>
              ))}
            </div>
          </Box>
        );
      })}
      <Pill x={pill.x} y={pill.y} text={'Step("load")'} color={ACCENT} opacity={fadeAt(frame, 2.0)} />
      <div
        style={{
          position: 'absolute', left: k.x, top: k.y, transform: 'translate(-50%, -50%)', padding: '4px 14px', borderRadius: 10, background: '#1d1a33',
          border: `3px solid ${ACCENT}`, boxShadow: `0 0 14px ${ACCENT}88`, fontFamily: MONO, fontSize: 17, color: TEXT, whiteSpace: 'nowrap', opacity: fadeAt(frame, 2.5),
        }}
      >
        {'k: yield Step("train") …'}
      </div>
      <Label x={640} y={600} size={22} bold color={TEST} center opacity={fadeAt(frame, 11.4)}>
        the handler holds both, and decides
      </Label>
    </AbsoluteFill>
  );
};

// Scene 2 — an effect travels out to the innermost handler whose annotation matches it (measure/dispatch.py).

const DISPATCH_LINES: Lines = [
  [0, 'A program is a generator. It pauses at each yield and hands the effect outward.'],
  [5.5, 'The innermost handler whose annotation matches the effect receives it.'],
  [10.5, 'Ask is not a Fetch, so typed is skipped without a call. Ask goes on out to reader.'],
  [16.5, 'Fetch reaches typed. Both answers come back, and greeting() returns.'],
];
const DISPATCH = f(22);
const GREETING = ['@do', 'def greeting():', '    name = yield Ask("name")', '    body = yield Fetch("https://example.com")', '    return f"{name}: {body}"'];
const G = {cx: 640, cy: 400, w: 580, size: 19, line: 30, margin: 70};
const gCode = {x: G.cx - G.w / 2, y: G.cy - (PAD * 2 + GREETING.length * G.line) / 2};
const gLayer = onion(G.cx, G.cy, G.w, PAD * 2 + GREETING.length * G.line, G.margin);
const G_LAYERS = [
  {name: 'typed', handles: '(effect: Fetch, k)'},
  {name: 'reader(env={"name": "ada"})', handles: 'answers Ask'},
];

const DispatchScene: React.FC = () => {
  const frame = useSceneFrame();
  const startX = G.cx + G.w / 2;
  const edge = (i: number) => G.cx + gLayer(i).w / 2;
  const askY = lineMid(gCode.y, G.line, 2);
  const fetchY = lineMid(gCode.y, G.line, 3);
  const band = frame < f(5.5) ? null : frame < f(16.5) ? 2 : frame < f(19.4) ? mix(2, 3, ramp(frame, 16.5, 16.8)) : mix(3, 4, ramp(frame, 19.4, 19.7));
  const askAt = frame < f(11.0) ? mix(startX, edge(0), ramp(frame, 6.0, 7.0)) : mix(edge(0), edge(1), ramp(frame, 11.0, 11.8));
  const working = [frame >= f(17.4) && frame < f(18.2), frame >= f(11.8) && frame < f(12.8)];
  const comparing = frame >= f(7.0) && frame < f(11.0);
  return (
    <AbsoluteFill>
      <Headlines lines={DISPATCH_LINES} />
      {G_LAYERS.map((layer, i) => {
        const r = gLayer(i);
        const on = working[i];
        return (
          <div
            key={layer.name}
            style={{
              position: 'absolute', left: r.x, top: r.y, width: r.w, height: r.h, boxSizing: 'border-box', borderRadius: 22,
              border: `3px solid ${TEST}`, ...glow(on, TEST), opacity: fadeAt(frame, 0.8 + i * 0.8, 0.5),
            }}
          >
            <div style={{position: 'absolute', left: 16, top: 10, fontFamily: SANS, fontSize: 17, fontWeight: 700, color: on ? LOOK : TEST, whiteSpace: 'nowrap'}}>
              <span style={{fontFamily: MONO}}>{layer.name}</span>
              <span style={{fontFamily: MONO, fontWeight: 400, marginLeft: 14, color: i === 0 && comparing ? LOOK : MUTED}}>{layer.handles}</span>
            </div>
          </div>
        );
      })}
      <CodeBlock lines={GREETING} x={gCode.x} y={gCode.y} w={G.w} size={G.size} line={G.line} band={band} opacity={fadeAt(frame, 0)} />
      <Label x={gCode.x} y={gCode.y + PAD * 2 + GREETING.length * G.line + 6} size={17} opacity={between(frame, 1.2, 5.3)}>
        a generator: it pauses at each yield
      </Label>
      {frame >= f(6.0) && frame < f(12.2) && <Pill x={askAt} y={askY} text={'Ask("name")'} color={ACCENT} opacity={1 - ramp(frame, 11.9, 12.2)} />}
      <Label x={570} y={gLayer(0).y + 11} size={16} bold color={LOOK} opacity={between(frame, 10.6, 13.0)}>
        ← Ask is not a Fetch: skipped
      </Label>
      {frame >= f(12.6) && frame < f(13.8) && <Pill x={mix(edge(1), startX, ramp(frame, 12.6, 13.4))} y={askY} text={'"ada"'} color={TEST} opacity={1 - ramp(frame, 13.5, 13.8)} />}
      {frame >= f(16.8) && frame < f(17.7) && <Pill x={mix(startX, edge(0), ramp(frame, 16.8, 17.4))} y={fetchY} text="Fetch(url)" color={ACCENT} opacity={1 - ramp(frame, 17.4, 17.7)} />}
      {frame >= f(18.2) && frame < f(19.4) && (
        <Pill x={mix(edge(0), startX, ramp(frame, 18.2, 19.0))} y={fetchY} text={'"got https://example.com"'} color={TEST} opacity={1 - ramp(frame, 19.1, 19.4)} />
      )}
      <div style={{position: 'absolute', left: 0, right: 0, top: 650, textAlign: 'center', fontFamily: MONO, fontSize: 22, fontWeight: 700, color: ACCENT, opacity: fadeAt(frame, 19.8)}}>
        {'greeting() → "ada: got https://example.com"'}
      </div>
    </AbsoluteFill>
  );
};

// Scene 3 — a handler stays installed around k: after(job()) receives both Steps (measure/stays_installed.py).

const STAYS_LINES: Lines = [
  [0, 'A handler stays installed around k. The rest of the program still runs under it.'],
  [5.5, 'after receives Step("load") and resumes k. Inside k, Step("train") reaches after again.'],
  [12, 'Resume gives back the result of the rest. after adds to it on the way out.'],
];
const STAYS = f(19);
const JOB = ['@do', 'def job():', '    yield Step("load")', '    yield Step("train")', '    return "model"'];
const AFTER = ['@handler', '@do(non_tail=True)', 'def after(effect: Step, k):', '    result = yield Resume(k, None)', '    return f"{result} after {effect.name}"'];
const S = {x: 60, w: 540, size: 19, line: 30, job: 130, after: 340};
// The nested frames of the run, outermost first: what is running inside what.
const FRAMES = [
  {x: 650, y: 130, w: 570, h: 450, color: TEST, dashed: false, label: 'after, for Step("load")', at: 6.3},
  {x: 672, y: 178, w: 526, h: 334, color: ACCENT, dashed: true, label: 'k: the rest of job(), still under after', at: 6.8},
  {x: 694, y: 226, w: 482, h: 248, color: TEST, dashed: false, label: 'after, for Step("train")', at: 9.3},
  {x: 716, y: 274, w: 438, h: 118, color: ACCENT, dashed: true, label: 'k: the rest of job()', at: 9.8},
];

const StaysScene: React.FC = () => {
  const frame = useSceneFrame();
  const jobBand = frame < f(5.5) ? null : frame < f(8.2) ? 2 : frame < f(11.6) ? mix(2, 3, ramp(frame, 8.2, 8.5)) : mix(3, 4, ramp(frame, 11.6, 11.9));
  const afterBand = frame < f(6.3) ? null : frame < f(13.0) ? 3 : 4;
  const working = (i: number) => (i === 0 ? (frame >= f(6.3) && frame < f(6.9)) || (frame >= f(15.8) && frame < f(17.0)) : i === 2 ? (frame >= f(9.3) && frame < f(9.9)) || (frame >= f(13.8) && frame < f(15.0)) : false);
  const up1 = ramp(frame, 13.0, 13.8);
  const up2 = ramp(frame, 15.0, 15.8);
  return (
    <AbsoluteFill>
      <Headlines lines={STAYS_LINES} />
      <CodeBlock lines={JOB} x={S.x} y={S.job} w={S.w} size={S.size} line={S.line} band={jobBand} opacity={fadeAt(frame, 0)} />
      <CodeBlock lines={AFTER} x={S.x} y={S.after} w={S.w} size={S.size} line={S.line} band={afterBand} opacity={fadeAt(frame, 0.4)} />
      <div style={{position: 'absolute', left: S.x, top: 560, fontFamily: MONO, fontSize: 20, color: TEXT, opacity: fadeAt(frame, 1.2)}}>
        run(after(job()))<span style={{fontFamily: SANS, color: MUTED, marginLeft: 16}}>after is installed once</span>
      </div>
      {FRAMES.map((frameBox, i) => (
        <Box key={frameBox.label} x={frameBox.x} y={frameBox.y} w={frameBox.w} h={frameBox.h} color={frameBox.color} dashed={frameBox.dashed} on={working(i)} opacity={fadeAt(frame, frameBox.at, 0.4)}>
          <div style={{position: 'absolute', left: 14, top: 8, fontFamily: MONO, fontSize: 16, fontWeight: 700, color: working(i) ? LOOK : frameBox.color, whiteSpace: 'nowrap'}}>{frameBox.label}</div>
        </Box>
      ))}
      <Label x={736} y={320} size={18} color={TEXT} opacity={fadeAt(frame, 11.9)}>
        <span style={{fontFamily: MONO}}>job() returns "model"</span>
      </Label>
      <Label x={714} y={428} size={18} color={TEXT} opacity={fadeAt(frame, 14.0)}>
        <span style={{fontFamily: MONO}}>returns "model after train"</span>
      </Label>
      <Label x={670} y={534} size={18} color={TEXT} opacity={fadeAt(frame, 16.0)}>
        <span style={{fontFamily: MONO}}>returns "model after train after load"</span>
      </Label>
      {frame >= f(5.7) && frame < f(6.6) && (
        <Pill x={mix(S.x + S.w + 30, 900, ramp(frame, 5.7, 6.3))} y={mix(lineMid(S.job, S.line, 2), 150, ramp(frame, 5.7, 6.3))} text={'Step("load")'} color={ACCENT} opacity={1 - ramp(frame, 6.3, 6.6)} />
      )}
      {frame >= f(8.6) && frame < f(9.6) && (
        <Pill x={mix(S.x + S.w + 30, 930, ramp(frame, 8.6, 9.3))} y={mix(lineMid(S.job, S.line, 3), 246, ramp(frame, 8.6, 9.3))} text={'Step("train")'} color={ACCENT} opacity={1 - ramp(frame, 9.3, 9.6)} />
      )}
      {frame >= f(13.0) && frame < f(14.0) && <Pill x={935} y={mix(345, 440, up1)} text={'"model"'} color={TEST} opacity={1 - ramp(frame, 13.7, 14.0)} />}
      {frame >= f(15.0) && frame < f(16.0) && <Pill x={935} y={mix(440, 546, up2)} text={'"model after train"'} color={TEST} opacity={1 - ramp(frame, 15.7, 16.0)} />}
      <div style={{position: 'absolute', left: 650, top: 610, width: 570, textAlign: 'center', fontFamily: MONO, fontSize: 21, fontWeight: 700, color: ACCENT, opacity: fadeAt(frame, 16.8)}}>
        → model after train after load
      </div>
    </AbsoluteFill>
  );
};

// Scene 4 — what the handler does with k decides what the effect means (measure/contract.py).

const CONTRACT_LINES: Lines = [
  [0, 'What a handler does with k decides what the effect means. One program, six handlers:'],
  [5, 'Resume k, and the rest runs with the answer.'],
  [10, 'Throw into k, and the program sees the error at the yield.'],
  [15, 'Pass the effect on, and the next handler out answers instead.'],
  [20, 'Hold k while other tasks run, then resume it. The scheduler works this way.'],
  [25.5, "Or drop k. The rest never runs, and the handler's value is the answer."],
  [30.5, 'k is one-shot. Resuming it a second time raises a RuntimeError.'],
];
const CONTRACT = f(36);
const PAGE = ['@do', 'def page():', '    body = yield Fetch(url)', '    return f"body={body}"'];
type Kind = 'resume' | 'throw' | 'pass' | 'hold' | 'drop' | 'twice';
type Action = {kind: Kind; verb: string; at: number; body: string[]; answer: string; reply: string};
// The handlers of measure/contract.py and what page() answered under each (contract.log).
const ACTIONS: Action[] = [
  {kind: 'resume', verb: 'resume', at: 5, body: ['return (yield Resume(k, f"got {effect.url}"))'], answer: "'body=got https://example.com'", reply: '"got https://example.com"'},
  {kind: 'throw', verb: 'throw into', at: 10, body: ['return (yield ResumeThrow(k, TimeoutError("slow")))'], answer: 'raises TimeoutError: slow, at body = yield Fetch(url)', reply: 'TimeoutError("slow")'},
  {kind: 'pass', verb: 'pass', at: 15, body: ['yield Pass(effect, k)'], answer: "'body=from the next handler out'", reply: '"from the next handler out"'},
  {kind: 'hold', verb: 'hold', at: 20, body: ['task = yield Spawn(other())', 'value = yield Wait(task)', 'return (yield Resume(k, value))'], answer: "'body=other task done'", reply: '"other task done"'},
  {kind: 'drop', verb: 'drop', at: 25.5, body: ['return f"stopped at {effect.url}"'], answer: "'stopped at https://example.com'", reply: '"stopped at https://example.com"'},
  {kind: 'twice', verb: 'resume twice', at: 30.5, body: ['yield Resume(k, "first")', 'return (yield Resume(k, "second"))'], answer: 'raises RuntimeError: continuation already consumed (one-shot violation)', reply: '"second"'},
];
const P = {x: 60, y: 130, w: 470, size: 22, line: 40};
const H = {x: 600, y: 130, w: 620, h: 210};
const OUT = {x: 600, y: 356, w: 620, h: 64};
const yieldY = lineMid(P.y, P.line, 2);
// The vertical middle of row i of the answers table.
const answerY = (i: number) => 436 + 10 + 22 + i * 35 + 17;
const handlerIn = {x: H.x + 90, y: H.y + H.h - 32};

const ContractScene: React.FC = () => {
  const frame = useSceneFrame();
  const current = ACTIONS.reduce((found, action, i) => (frame >= f(action.at) ? i : found), -1);
  const action = current >= 0 ? ACTIONS[current] : null;
  const s = action ? action.at : 0;
  const t = (from: number, to: number) => ramp(frame, s + from, s + to);
  const on = (from: number, to = Infinity) => frame >= f(s + from) && frame < f(s + to);
  const resumedAt = action ? {resume: 2.4, throw: 2.4, pass: 3.1, hold: 3.6, drop: Infinity, twice: 2.0}[action.kind] : Infinity;
  const band = !action || frame < f(s + 0.2) ? null : frame < f(s + resumedAt) ? 2 : mix(2, 3, t(resumedAt, resumedAt + 0.3));
  const dropped = action?.kind === 'drop' ? t(1.5, 2.1) : 0;
  const kState = !action
    ? ''
    : action.kind === 'drop' && on(1.5)
      ? 'dropped: it never runs'
      : action.kind === 'twice' && on(2.6)
        ? 'already consumed'
        : on(resumedAt)
          ? 'resumed'
          : action.kind === 'hold' && on(1.4)
            ? 'held'
            : on(0.2)
              ? 'paused'
              : '';
  const working = action !== null && on(1.1, action.kind === 'pass' ? 1.5 : 3.6);
  const threw = action?.kind === 'throw' && on(2.3);
  return (
    <AbsoluteFill>
      <Headlines lines={CONTRACT_LINES} />
      <CodeBlock lines={PAGE} {...P} band={band} dim={(i) => (i === 3 ? 1 - 0.75 * dropped : 1)} opacity={fadeAt(frame, 0)} />
      <div
        style={{
          position: 'absolute', left: P.x + 6, top: P.y + PAD + 3 * P.line - 2, width: P.w - 12, height: P.line + 4, boxSizing: 'border-box',
          border: `3px solid ${ACCENT}`, borderRadius: 8, opacity: fadeAt(frame, 0.8) * (1 - 0.6 * dropped),
        }}
      />
      {threw && (
        <div
          style={{
            position: 'absolute', left: P.x + 6, top: P.y + PAD + 2 * P.line - 2, width: P.w - 12, height: P.line + 4, boxSizing: 'border-box',
            border: `3px solid ${PROD}`, borderRadius: 8, boxShadow: `0 0 16px ${PROD}`, opacity: fadeAt(frame, s + 2.3) * (1 - fadeAt(frame, s + 4.4)),
          }}
        />
      )}
      <Label x={P.x} y={P.y + 196} size={21} bold color={ACCENT} opacity={fadeAt(frame, 0.8)}>
        k = the rest of page()
        <span style={{color: MUTED, fontWeight: 400, marginLeft: 16}}>{kState}</span>
      </Label>
      <Box x={H.x} y={H.y} w={H.w} h={H.h} color={TEST} on={working} opacity={fadeAt(frame, 0.4)}>
        <div style={{position: 'absolute', left: 20, top: 12, fontFamily: SANS, fontSize: 22, fontWeight: 700, color: working ? LOOK : TEST}}>
          {action ? `a handler that ${{resume: 'resumes k', throw: 'throws into k', pass: 'passes the effect on', hold: 'holds k', drop: 'drops k', twice: 'resumes k twice'}[action.kind]}` : 'a handler of Fetch'}
        </div>
        <div style={{position: 'absolute', left: 20, top: 50, fontFamily: MONO, fontSize: 17, color: MUTED}}>{'@handler  def h(effect: Fetch, k):'}</div>
        {ACTIONS.map((a, i) => (
          <div key={a.kind} style={{position: 'absolute', left: 44, top: 80, fontFamily: MONO, fontSize: 17, lineHeight: '27px', color: TEXT, whiteSpace: 'pre', opacity: i === current ? fadeAt(frame, a.at, 0.3) : 0}}>
            {a.body.map((line) => (
              <div key={line}>{line.includes('yield') ? highlightYield(line) : line}</div>
            ))}
          </div>
        ))}
      </Box>
      <Box x={OUT.x} y={OUT.y} w={OUT.w} h={OUT.h} color={action?.kind === 'hold' ? ACCENT : TEST} on={action?.kind === 'pass' && on(2.1, 2.7)} opacity={action && (action.kind === 'pass' || action.kind === 'hold') ? between(frame, s, s + 4.6) : 0}>
        {action?.kind === 'pass' && <div style={{position: 'absolute', left: 190, top: 16, fontFamily: SANS, fontSize: 20, fontWeight: 700, color: TEST}}>the next handler out answers Fetch</div>}
        {action?.kind === 'hold' && (
          <>
            <div style={{position: 'absolute', left: 20, top: 16, fontFamily: SANS, fontSize: 20, fontWeight: 700, color: ACCENT}}>other() runs</div>
            <div style={{position: 'absolute', left: 170, top: 24, width: 420, height: 14, borderRadius: 7, background: '#2a2e40'}} />
            <div style={{position: 'absolute', left: 170, top: 24, width: 420 * t(1.4, 2.8), height: 14, borderRadius: 7, background: ACCENT, boxShadow: `0 0 10px ${ACCENT}`}} />
          </>
        )}
      </Box>
      {action && on(0.4, 1.4) && <Pill x={mix(P.x + P.w + 20, handlerIn.x, t(0.4, 1.1))} y={mix(yieldY, handlerIn.y, t(0.4, 1.1))} text="Fetch(url)" color={ACCENT} opacity={1 - t(1.1, 1.4)} />}
      {action?.kind === 'pass' && on(1.4, 2.4) && <Pill x={handlerIn.x} y={mix(handlerIn.y, OUT.y + OUT.h / 2, t(1.5, 2.1))} text="Fetch(url)" color={ACCENT} opacity={1 - t(2.1, 2.4)} />}
      {action && action.kind !== 'drop' && on(resumedAt - 0.8, resumedAt + 0.2) && (
        <Pill
          x={mix(action.kind === 'pass' ? OUT.x + 300 : handlerIn.x, P.x + P.w + 20, t(resumedAt - 0.8, resumedAt))}
          y={mix(action.kind === 'pass' ? OUT.y + OUT.h / 2 : handlerIn.y, yieldY, t(resumedAt - 0.8, resumedAt))}
          text={action.kind === 'twice' ? '"first"' : action.reply}
          color={action.kind === 'throw' ? PROD : TEST}
          opacity={1 - t(resumedAt, resumedAt + 0.2)}
        />
      )}
      {action?.kind === 'twice' && on(2.6, 3.6) && (
        <Pill x={mix(handlerIn.x, P.x + P.w + 60, t(2.6, 3.1))} y={mix(handlerIn.y, yieldY, t(2.6, 3.1))} text={action.reply} color={PROD} opacity={1 - t(3.3, 3.6)} />
      )}
      {action?.kind === 'drop' && on(1.8, 2.8) && <Pill x={mix(handlerIn.x + 140, 520, t(1.8, 2.6))} y={mix(handlerIn.y, answerY(4), t(1.8, 2.6))} text={action.reply} color={TEST} opacity={1 - t(2.6, 2.8)} />}
      <div
        style={{
          position: 'absolute', left: 60, top: 436, width: 1160, height: 252, boxSizing: 'border-box', padding: '10px 18px', background: '#0b0d13',
          borderRadius: 12, opacity: fadeAt(frame, 0.6),
        }}
      >
        <div style={{fontFamily: SANS, fontSize: 16, lineHeight: '22px', color: MUTED}}>what page() answers under each handler</div>
        {ACTIONS.map((a) => {
          const shown = frame >= f(a.at + {resume: 2.6, throw: 2.6, pass: 3.2, hold: 3.8, drop: 2.6, twice: 3.3}[a.kind]);
          const now = a === action;
          return (
            <div key={a.kind} style={{display: 'flex', height: 35, alignItems: 'center', opacity: shown ? fadeAt(frame, a.at + {resume: 2.6, throw: 2.6, pass: 3.2, hold: 3.8, drop: 2.6, twice: 3.3}[a.kind]) : 0}}>
              <div style={{width: 170, fontFamily: SANS, fontSize: 19, fontWeight: 700, color: now ? LOOK : TEST}}>{a.verb}</div>
              <div style={{fontFamily: MONO, fontSize: 18, color: a.answer.startsWith('raises') ? PROD : TEXT, whiteSpace: 'nowrap'}}>{a.answer}</div>
            </div>
          );
        })}
      </div>
    </AbsoluteFill>
  );
};

// Scene 5 — handlers compose by nesting, and the order matters (measure/composition.py).

const COMPOSE_LINES: Lines = [
  [0, 'Handlers compose by nesting. Each one wraps the program the next one returns.'],
  [5.5, 'Ask stops at reader. Get and Put pass reader and writer, and reach state.'],
  [11, 'writer answers Tell by yielding a Get and a Put of its own. They travel out to state.'],
  [17.5, "So the order matters. Put state inside writer, and writer's Get finds no handler."],
];
const COMPOSE = f(25);
const UPDATE = [
  '@do',
  'def update_counter():',
  '    counter_key = yield Ask("counter_key")',
  '    current = yield Get(counter_key)',
  '    yield Put(counter_key, current + 1)',
  '    yield Tell(f"counter updated: {current} -> {current + 1}")',
  '    return current + 1',
];
const C = {cx: 640, cy: 382, w: 664, size: 16, line: 24, margin: 46};
const cH = PAD * 2 + UPDATE.length * C.line;
const cCode = {x: C.cx - C.w / 2, y: C.cy - cH / 2};
const cLayer = onion(C.cx, C.cy, C.w, cH, C.margin);
const IN_ORDER = [
  {name: 'reader(env=...)', handles: 'handles Ask'},
  {name: 'writer', handles: 'handles Tell'},
  {name: 'state(initial={"count": 0})', handles: 'handles Get · Put'},
];
const REVERSED = [IN_ORDER[0], IN_ORDER[2], IN_ORDER[1]];
// One trip out from a code line (or from a layer's edge) to a layer, and the answer back.
type Out = {y: number; from: number; to: number; text: string; leave: number; land: number; back?: string};

const ComposeScene: React.FC = () => {
  const frame = useSceneFrame();
  const startX = C.cx + C.w / 2;
  const edge = (i: number) => C.cx + cLayer(i).w / 2;
  const at = (i: number) => lineMid(cCode.y, C.line, i);
  const swap = ramp(frame, 17.8, 18.6);
  const trips: Out[] = [
    {y: at(2), from: startX, to: edge(0), text: 'Ask', leave: 6.0, land: 6.6, back: '"count"'},
    {y: at(3), from: startX, to: edge(2), text: 'Get', leave: 7.8, land: 8.6, back: '0'},
    {y: at(4), from: startX, to: edge(2), text: 'Put', leave: 9.6, land: 10.4},
    {y: at(5), from: startX, to: edge(1), text: 'Tell', leave: 11.4, land: 12.0},
    {y: at(5) + 34, from: edge(1), to: edge(2), text: 'Get', leave: 12.6, land: 13.1},
    {y: at(5) + 34, from: edge(1), to: edge(2), text: 'Put', leave: 13.9, land: 14.4},
    {y: at(5), from: startX, to: edge(2), text: 'Tell', leave: 19.0, land: 19.8},
  ];
  // The line the program is on: Ask, Get, Put, Tell, then the return; after the swap it stops at the Tell.
  const steps: [number, number][] = [[5.5, 2], [7.6, 3], [9.4, 4], [11.2, 5], [15.9, 6]];
  const stepped = steps.reduce((line, [from, to], i) => (i === 0 ? to : mix(line, to, ramp(frame, from, from + 0.3))), 2);
  const band = frame < f(5.5) ? null : frame < f(17.4) ? stepped : frame < f(18.8) ? null : 5;
  const working = (layer: number) =>
    trips.some((trip) => trip.to === edge(layer) && frame >= f(trip.land) && frame < f(trip.land + 0.7)) || (frame >= f(11.4) && frame < f(15.4) && layer === 1 && swap === 0);
  const unhandled = ramp(frame, 20.4, 21.4);
  return (
    <AbsoluteFill>
      <Headlines lines={COMPOSE_LINES} />
      {IN_ORDER.map((_, i) => {
        const r = cLayer(i);
        const on = working(i) || (frame >= f(19.8) && frame < f(20.6) && i === 2);
        const normal = IN_ORDER[i];
        const reversed = REVERSED[i];
        return (
          <div
            key={i}
            style={{
              position: 'absolute', left: r.x, top: r.y, width: r.w, height: r.h, boxSizing: 'border-box', borderRadius: 22,
              border: `3px solid ${TEST}`, ...glow(on, TEST), opacity: fadeAt(frame, 0.6 + i * 0.7, 0.5),
            }}
          >
            {[normal, reversed].map((layer, j) => (
              <div
                key={j}
                style={{
                  position: 'absolute', left: 16, top: 12, fontFamily: SANS, fontSize: 16, fontWeight: 700, color: on ? LOOK : TEST, whiteSpace: 'nowrap',
                  opacity: normal === reversed ? 1 : j === 0 ? 1 - swap : swap,
                }}
              >
                <span style={{fontFamily: MONO}}>{layer.name}</span>
                <span style={{color: MUTED, fontWeight: 400, marginLeft: 12}}>{layer.handles}</span>
              </div>
            ))}
          </div>
        );
      })}
      <CodeBlock lines={UPDATE} x={cCode.x} y={cCode.y} w={C.w} size={C.size} line={C.line} band={band} />
      {trips.map((trip, i) => {
        const out = frame >= f(trip.leave) && frame < f(trip.land + 0.6);
        const back = trip.back !== undefined && frame >= f(trip.land + 0.6) && frame < f(trip.land + 1.3);
        return (
          <React.Fragment key={i}>
            {out && <Pill x={mix(trip.from, trip.to, ramp(frame, trip.leave, trip.land))} y={trip.y} text={trip.text} color={ACCENT} opacity={1 - ramp(frame, trip.land + 0.4, trip.land + 0.6)} />}
            {back && <Pill x={mix(trip.to, trip.from, ramp(frame, trip.land + 0.6, trip.land + 1.1))} y={trip.y} text={trip.back ?? ''} color={TEST} opacity={1 - ramp(frame, trip.land + 1.1, trip.land + 1.3)} />}
          </React.Fragment>
        );
      })}
      {frame >= f(20.3) && (
        <Pill x={mix(edge(2), 1222, unhandled)} y={at(5) + 34} text="Get" color={unhandled < 1 ? ACCENT : PROD} />
      )}
      <div style={{position: 'absolute', left: 0, right: 0, top: 646, textAlign: 'center', fontFamily: MONO, fontSize: 17, color: TEXT}}>
        <div style={{position: 'absolute', left: 0, right: 0, opacity: fadeAt(frame, 1.0) * (1 - swap)}}>{'state(initial={"count": 0})(writer(reader(env=...)(update_counter())))'}</div>
        <div style={{position: 'absolute', left: 0, right: 0, opacity: swap}}>{'writer(state(initial={"count": 0})(reader(env=...)(update_counter())))'}</div>
      </div>
      <div style={{position: 'absolute', left: 0, right: 0, top: 680, textAlign: 'center', fontFamily: MONO, fontSize: 19, fontWeight: 700}}>
        <div style={{position: 'absolute', left: 0, right: 0, color: ACCENT, opacity: fadeAt(frame, 16.0) * (1 - swap)}}>{'→ 1 · log: counter updated: 0 -> 1'}</div>
        <div style={{position: 'absolute', left: 0, right: 0, color: PROD, opacity: fadeAt(frame, 21.4)}}>{'raises UnhandledEffect: unhandled effect: Get'}</div>
      </div>
    </AbsoluteFill>
  );
};

// Scene 6 — run: calling a @do function builds a program, and run evaluates it yield by yield (measure/run_flow.py).

const RUN_LINES: Lines = [
  [0, 'Calling a @do function runs nothing yet. It builds a program: an Expand node.'],
  [5, 'run() takes that one program, evaluates it, and returns its value.'],
  [9.5, 'Each yield splits the program in two: the expression, and the rest, waiting for its result.'],
  [15.5, 'The VM evaluates a control node itself. An effect goes to the handlers, with the rest as k.'],
  [21.5, 'The answer is sent back into the generator until it returns: "Ada".'],
];
const RUN = f(27);
const FETCH_USER = ['@do', 'def fetch_user(user_id):', '    db = yield Ask("db")', '    return db[user_id]'];
const R = {x: 60, y: 120, w: 520, size: 22, line: 40};
const CHAR = 22 * 0.602;
const GEN = {x: 660, y: 196, w: 220, h: 64};
const VM = {x: 1000, y: 196, w: 220, h: 64};
const ROWS = [
  {y: 300, kind: 'control node', names: 'Pure · Expand · …', does: 'the VM evaluates it', color: ACCENT},
  {y: 370, kind: 'effect', names: 'Ask · Get · …', does: 'handlers get (effect, k)', color: TEST},
];

const RunScene: React.FC = () => {
  const frame = useSceneFrame();
  const band = frame < f(9.6) ? null : frame < f(23.0) ? 2 : mix(2, 3, ramp(frame, 23.0, 23.3));
  const askOut = ramp(frame, 16.2, 17.0);
  const askDown = ramp(frame, 17.6, 18.3);
  const sentBack = ramp(frame, 22.0, 22.8);
  const rowOn = [frame >= f(15.6) && frame < f(16.2), frame >= f(18.3) && frame < f(21.9)];
  const exprX = R.x + 24 + 15 * CHAR;
  const restRest = fadeAt(frame, 10.4) * (1 - fadeAt(frame, 19.4));
  return (
    <AbsoluteFill>
      <Headlines lines={RUN_LINES} />
      <CodeBlock lines={FETCH_USER} {...R} band={band} opacity={fadeAt(frame, 0)} />
      <div style={{position: 'absolute', left: R.x, top: 330, fontFamily: MONO, fontSize: 21, lineHeight: '36px', color: TEXT}}>
        <div style={{opacity: fadeAt(frame, 0.8)}}>program = fetch_user(42)</div>
        <div style={{opacity: fadeAt(frame, 2.0)}}>
          isinstance(program, Expand)<span style={{color: ACCENT, marginLeft: 14}}># True</span>
        </div>
        <div style={{fontFamily: SANS, fontSize: 19, color: MUTED, opacity: fadeAt(frame, 2.8)}}>nothing in fetch_user has run yet</div>
      </div>
      <div
        style={{
          position: 'absolute', left: exprX - 4, top: R.y + PAD + 2 * R.line, width: 9 * CHAR + 8, height: R.line, boxSizing: 'border-box',
          border: `3px solid ${LOOK}`, borderRadius: 8, opacity: fadeAt(frame, 10.0) * (1 - fadeAt(frame, 17.0)),
        }}
      />
      <Label x={exprX + 9 * CHAR + 18} y={R.y + PAD + 2 * R.line + 8} size={17} bold color={LOOK} opacity={fadeAt(frame, 10.0) * (1 - fadeAt(frame, 17.0))}>
        expr
      </Label>
      <div
        style={{
          position: 'absolute', left: R.x + 6, top: R.y + PAD + 3 * R.line - 2, width: R.w - 12, height: R.line + 4, boxSizing: 'border-box',
          border: `3px solid ${ACCENT}`, borderRadius: 8, opacity: fadeAt(frame, 10.4) * (1 - fadeAt(frame, 23.4)),
        }}
      />
      <Label x={R.x + 330} y={R.y + PAD + 3 * R.line + 8} size={17} bold color={ACCENT} opacity={restRest}>
        rest
      </Label>
      <Label x={R.x + 330} y={R.y + PAD + 3 * R.line + 8} size={17} bold color={ACCENT} opacity={fadeAt(frame, 19.4) * (1 - fadeAt(frame, 23.4))}>
        <span style={{fontFamily: MONO}}>k</span>
      </Label>
      <div style={{position: 'absolute', left: 640, top: 120, width: 580, height: 330, boxSizing: 'border-box', borderRadius: 18, border: `2px solid #2a2e40`, opacity: fadeAt(frame, 5.2)}}>
        <div style={{position: 'absolute', left: 18, top: 14, fontFamily: MONO, fontSize: 17, color: TEXT, whiteSpace: 'nowrap'}}>{'run(reader(env={"db": {42: "Ada"}})(program))'}</div>
      </div>
      <Box x={GEN.x} y={GEN.y} w={GEN.w} h={GEN.h} color={ACCENT} on={(frame >= f(16.0) && frame < f(16.4)) || (frame >= f(22.6) && frame < f(23.2))} opacity={fadeAt(frame, 5.8)}>
        <div style={{position: 'absolute', left: 0, right: 0, top: 16, textAlign: 'center', fontFamily: SANS, fontSize: 21, fontWeight: 700, color: ACCENT}}>the generator</div>
      </Box>
      <Box x={VM.x} y={VM.y} w={VM.w} h={VM.h} color={TEST} on={frame >= f(17.0) && frame < f(17.6)} opacity={fadeAt(frame, 6.2)}>
        <div style={{position: 'absolute', left: 0, right: 0, top: 16, textAlign: 'center', fontFamily: SANS, fontSize: 21, fontWeight: 700, color: TEST}}>the VM</div>
      </Box>
      <Label x={GEN.x + GEN.w} y={GEN.y - 2} w={VM.x - GEN.x - GEN.w} size={14} center opacity={fadeAt(frame, 6.6)}>yields</Label>
      <div style={{position: 'absolute', left: GEN.x + GEN.w + 10, top: GEN.y + 20, width: VM.x - GEN.x - GEN.w - 20, height: 2, background: MUTED, opacity: fadeAt(frame, 6.6)}} />
      <div style={{position: 'absolute', left: GEN.x + GEN.w + 10, top: GEN.y + 44, width: VM.x - GEN.x - GEN.w - 20, height: 2, background: MUTED, opacity: fadeAt(frame, 7.0)}} />
      <Label x={GEN.x + GEN.w} y={GEN.y + 48} w={VM.x - GEN.x - GEN.w} size={14} center opacity={fadeAt(frame, 7.0)}>gen.send(value)</Label>
      {ROWS.map((row, i) => (
        <Box key={row.kind} x={660} y={row.y} w={560} h={56} color={row.color} on={rowOn[i]} opacity={fadeAt(frame, 15.6 + i * 0.4)}>
          <div style={{position: 'absolute', left: 16, top: 6, fontFamily: SANS, fontSize: 18, fontWeight: 700, color: rowOn[i] ? LOOK : row.color}}>{row.kind}</div>
          <div style={{position: 'absolute', left: 16, top: 29, fontFamily: MONO, fontSize: 14, color: MUTED}}>{row.names}</div>
          <div style={{position: 'absolute', right: 16, top: 16, fontFamily: SANS, fontSize: 18, color: TEXT}}>{row.does}</div>
        </Box>
      ))}
      {frame >= f(16.2) && frame < f(18.6) && (
        <Pill
          x={frame < f(17.6) ? mix(R.x + R.w + 20, 940, askOut) : 940}
          y={frame < f(17.6) ? mix(lineMid(R.y, R.line, 2), GEN.y + 20, askOut) : mix(GEN.y + 20, ROWS[1].y + 28, askDown)}
          text={'Ask("db")'}
          color={ACCENT}
          opacity={1 - ramp(frame, 18.3, 18.6)}
        />
      )}
      {frame >= f(22.0) && frame < f(23.1) && (
        <Pill x={mix(940, R.x + R.w + 20, sentBack)} y={mix(ROWS[1].y + 28, lineMid(R.y, R.line, 2), sentBack)} text={'{42: "Ada"}'} color={TEST} opacity={1 - ramp(frame, 22.8, 23.1)} />
      )}
      <div style={{position: 'absolute', left: 0, right: 0, top: 488, textAlign: 'center', fontFamily: MONO, fontSize: 25, color: TEXT, opacity: fadeAt(frame, 10.4)}}>
        yield expr <span style={{color: MUTED}}>≡</span> Bind(<span style={{color: LOOK}}>expr</span>, <span style={{color: ACCENT}}>λresult. rest_of_program</span>)
      </div>
      <Label x={640} y={538} size={20} bold color={ACCENT} center opacity={fadeAt(frame, 19.4)}>
        the λ, the rest of the program, is the continuation k
      </Label>
      <div style={{position: 'absolute', left: 0, right: 0, top: 600, textAlign: 'center', fontFamily: MONO, fontSize: 24, fontWeight: 700, color: ACCENT, opacity: fadeAt(frame, 23.6)}}>
        run(...) → 'Ada'
      </div>
    </AbsoluteFill>
  );
};

// The outro.

const OUTRO_LINES: Lines = [[0, 'Effects name operations. Handlers decide what they mean, by what they do with k.']];
const OUTRO = f(9);
const VERBS = ['resume', 'throw into', 'pass', 'hold', 'drop'];

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
        a handler gets <span style={{fontFamily: MONO, color: ACCENT}}>(effect, k)</span> and can
      </Label>
      {VERBS.map((verb, i) => (
        <div
          key={verb}
          style={{
            position: 'absolute', left: 640 - (VERBS.length * 196 - 16) / 2 + i * 196, top: 430, width: 180, height: 52, boxSizing: 'border-box', borderRadius: 12,
            border: `2px solid ${TEST}`, background: PANEL, fontFamily: SANS, fontSize: 21, fontWeight: 700, color: TEST, lineHeight: '48px', textAlign: 'center',
            opacity: fadeAt(frame, 1.4 + i * 0.3),
          }}
        >
          {verb} k
        </div>
      ))}
      <Label x={640} y={540} size={22} color={TEXT} center opacity={fadeAt(frame, 3.4)}>
        State, exceptions, concurrency, observation and I/O are effects of this one kind.
      </Label>
    </AbsoluteFill>
  );
};

// The scenes in order; Root.tsx registers CORE_CONCEPTS.length frames of CORE_CONCEPTS.Video.
export const CORE_CONCEPTS = narrate('core-concepts', DURATIONS, [
  {length: TITLE, starts: startsOf(TITLE_LINES), ids: ['t1', 't2', 't3'], node: <TitleScene />},
  {length: DISPATCH, starts: startsOf(DISPATCH_LINES), ids: ['d1', 'd2', 'd3', 'd4'], node: <DispatchScene />},
  {length: STAYS, starts: startsOf(STAYS_LINES), ids: ['a1', 'a2', 'a3'], node: <StaysScene />},
  {length: CONTRACT, starts: startsOf(CONTRACT_LINES), ids: ['c1', 'c2', 'c3', 'c4', 'c5', 'c6', 'c7'], node: <ContractScene />},
  {length: COMPOSE, starts: startsOf(COMPOSE_LINES), ids: ['o1', 'o2', 'o3', 'o4'], node: <ComposeScene />},
  {length: RUN, starts: startsOf(RUN_LINES), ids: ['r1', 'r2', 'r3', 'r4', 'r5'], node: <RunScene />},
  {length: OUTRO, starts: [0], ids: ['z1'], node: <OutroScene />},
]);
