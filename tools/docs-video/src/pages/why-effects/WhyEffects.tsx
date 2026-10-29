// The video on "Why Effects?" (docs/20-why-effects-over-di.md): a handler gets k, the rest of the program, and what
// it does with k decides what the effect means. Scenes, each with headlines a synthetic voice reads (narration.json):
//   1. The idea: a handler gets the effect and k; it can resume, drop, ask again, keep the answers, or hold.
//   2. The running example: illustrated_summary yields Chat and Draw; canned handlers resume k.
//   3. Drop k: cost_cap(3) answers Draw itself, so the image model is never called.
//   4. Ask again: retry performs Chat on the handlers outside it until one answers; k resumes once.
//   5. Keep the answers: a recorder files each answer under its effect, and replay answers from the tape.
//   6. Hold k: the scheduler and a virtual clock park the k of two sleeping reminders and wake them in time order.
//   7. Compared with DI: an injected client answers a call and returns; it gets no k.
//   8. Compared with plain async: async spreads to every caller; total_size yields Spawn and Gather instead.
//   9. Composability: effects are rows, handlers columns; a new effect is a row, a new concern a column, and with
//      DI each check mark is a wrapper class.
// Then the outro. Every output, order and time shown comes from measure/: continuation.py (scenes 2-5),
// virtual_clock.py (6), async_vs_effects.py (8) and wrappers.py (7 and 9: one handler, or a wrapper per client),
// each next to its .log.
import React from 'react';
import {AbsoluteFill, interpolate} from 'remotion';
import {clamp, fadeAt, mix, ramp} from '../../kit/motion';
import {narrate} from '../../kit/narrated';
import {CodeBlock, glow, Headlines, lineMid, Lines, PAD, Pill, startsOf} from '../../kit/parts';
import {ACCENT, f, FAIL, LOOK, MONO, MUTED, PANEL, PROD, SANS, TEST, TEXT} from '../../kit/theme';
import {useSceneFrame} from '../../kit/warp';
import DURATIONS from './durations.json';

// The running example, as on the page.
const SUMMARY = [
  '@do',
  'def illustrated_summary(topic):',
  '    summary = yield Chat("gpt-4o", f"Summarize {topic}")',
  '    picture = yield Draw("imagen-3", summary)',
  '    return f"{summary} | {picture}"',
];
const CODE = {x: 60, y: 140, w: 700, size: 19, line: 32};
const CHAT_Y = lineMid(CODE.y, CODE.line, 2);
const DRAW_Y = lineMid(CODE.y, CODE.line, 3);
// Where an effect leaves the code box, and the top and bottom of the handler walls beside it.
const OUT_X = CODE.x + CODE.w + 40;
const WALL = {top: 130, bottom: 340};

// The width a pill of this text takes (19 px mono), to keep one that sets off from a wall inside the frame.
const pillW = (text: string) => text.length * 11.5 + 28;
const inFrame = (x: number, text: string) => Math.min(x, 1270 - pillW(text) / 2);

// Between two times, the eased point between a and b; the pill is shown from `from` until `until`.
const along = (frame: number, from: number, to: number, a: number, b: number) => mix(a, b, ramp(frame, from, to));
const during = (frame: number, from: number, until: number) => frame >= f(from) && frame < f(until);
const fadeOut = (frame: number, at: number, length = 0.3) => 1 - ramp(frame, at, at + length);

// A handler, drawn as a wall the effects travel out to: a bar, its name under it, and a note under the name.
const Wall: React.FC<{x: number; name: string; color?: string; on?: boolean; opacity?: number; note?: string; noteColor?: string}> = ({
  x, name, color = TEST, on = false, opacity = 1, note, noteColor = MUTED,
}) => (
  <>
    <div
      style={{
        position: 'absolute', left: x - 4, top: WALL.top, width: 8, height: WALL.bottom - WALL.top, borderRadius: 4, background: on ? LOOK : color,
        boxShadow: on ? `0 0 22px ${LOOK}` : `0 0 8px ${color}66`, opacity,
      }}
    />
    <div
      style={{
        position: 'absolute', left: x, top: WALL.bottom + 8, transform: 'translateX(-50%)', fontFamily: MONO, fontSize: 15, fontWeight: 700,
        color: on ? LOOK : color, whiteSpace: 'nowrap', opacity,
      }}
    >
      {name}
    </div>
    {note && (
      <div style={{position: 'absolute', left: x, top: WALL.bottom + 30, transform: 'translateX(-50%)', fontFamily: SANS, fontSize: 14, color: noteColor, whiteSpace: 'nowrap', opacity}}>
        {note}
      </div>
    )}
  </>
);

// A dark box of output lines, with the call that printed them above in grey.
const Output: React.FC<{x: number; y: number; w: number; call: string; lines: {text: string; color?: string; opacity?: number}[]; opacity?: number}> = ({
  x, y, w, call, lines, opacity = 1,
}) => (
  <div
    style={{
      position: 'absolute', left: x, top: y, width: w, boxSizing: 'border-box', padding: '10px 18px', background: '#0b0d13', borderRadius: 10,
      fontFamily: MONO, fontSize: 18, lineHeight: '30px', opacity,
    }}
  >
    <div style={{color: MUTED, whiteSpace: 'pre'}}>{call}</div>
    {lines.map((line, i) => (
      <div key={i} style={{color: line.color ?? TEXT, whiteSpace: 'pre', opacity: line.opacity ?? 1}}>
        {line.text}
      </div>
    ))}
  </div>
);

// A small card for a k: the rest of a program, held by whoever holds the card.
const KCard: React.FC<{x: number; y: number; label: string; opacity?: number; color?: string; w?: number}> = ({x, y, label, opacity = 1, color = ACCENT, w}) => (
  <div
    style={{
      position: 'absolute', left: x, top: y, transform: 'translate(-50%, -50%)', width: w, boxSizing: 'border-box', padding: '4px 12px',
      borderRadius: 10, background: '#1d1a33', border: `3px solid ${color}`, boxShadow: `0 0 14px ${color}88`, fontFamily: SANS, fontSize: 17,
      fontWeight: 700, color: TEXT, whiteSpace: 'nowrap', textAlign: 'center', opacity,
    }}
  >
    {label}
  </div>
);

// Scene 1 — the idea: with each effect, a handler gets k, and what it does with k decides what the effect means.

const IDEA_LINES: Lines = [
  [0, 'Why effects? Because a handler gets the rest of your program, called k.'],
  [5.6, 'What it does with k decides what the effect means.'],
];
const IDEA = f(11);
const CHOICES = [
  {what: 'resume it now', example: 'canned answers'},
  {what: 'drop it', example: 'a cost cap'},
  {what: 'ask again', example: 'retries'},
  {what: 'keep the answers', example: 'record · replay'},
  {what: 'hold it', example: 'a virtual clock'},
];

const IdeaScene: React.FC = () => {
  const frame = useSceneFrame();
  const code = {x: 60, y: 270, w: 700, size: 18, line: 30};
  const box = {x: 850, y: 270, w: 370, h: 174};
  const kFrom = {x: code.x + 300, y: lineMid(code.y, code.line, 3) + 15};
  const kTo = {x: box.x + box.w / 2, y: box.y + 125};
  const go = ramp(frame, 2.6, 3.4);
  const holding = frame >= f(3.4);
  return (
    <AbsoluteFill>
      <Headlines lines={IDEA_LINES} />
      <div style={{position: 'absolute', left: 0, right: 0, top: 96 + mix(14, 0, ramp(frame, 0.2, 0.8)), textAlign: 'center', opacity: fadeAt(frame, 0.2, 0.5)}}>
        <div style={{fontFamily: SANS, fontSize: 64, fontWeight: 700, color: TEXT}}>Why Effects?</div>
        <div style={{fontFamily: SANS, fontSize: 26, color: ACCENT, marginTop: 2}}>What a handler does with the rest of the program</div>
      </div>
      <CodeBlock lines={SUMMARY} {...code} band={frame < f(1.2) ? null : 2} dim={(i) => (i >= 3 ? interpolate(frame, [f(2.6), f(3.0)], [1, 0.35], clamp) : 1)} opacity={fadeAt(frame, 0.8)} />
      <div
        style={{
          position: 'absolute', left: box.x, top: box.y, width: box.w, height: box.h, boxSizing: 'border-box', borderRadius: 18, background: PANEL,
          border: `3px solid ${TEST}`, ...glow(holding && frame < f(5.6), TEST), opacity: fadeAt(frame, 1.0),
        }}
      >
        <div style={{position: 'absolute', left: 20, top: 12, fontFamily: SANS, fontSize: 24, fontWeight: 700, color: TEST}}>a handler</div>
        <div style={{position: 'absolute', left: 20, top: 46, fontFamily: SANS, fontSize: 17, color: MUTED}}>gets the effect, and k</div>
      </div>
      {frame >= f(1.6) && (
        <Pill
          x={along(frame, 1.6, 2.4, code.x + code.w + 60, box.x + box.w / 2)}
          y={along(frame, 1.6, 2.4, lineMid(code.y, code.line, 2), box.y + 88)}
          text={'Chat("gpt-4o", ...)'}
          color={ACCENT}
        />
      )}
      {frame >= f(2.4) && (
        <KCard x={mix(kFrom.x, kTo.x, go)} y={mix(kFrom.y, kTo.y, go)} label="k = the rest of the program" opacity={fadeAt(frame, 2.4)} />
      )}
      <div style={{position: 'absolute', left: 60, top: 480, fontFamily: SANS, fontSize: 21, fontWeight: 700, color: TEXT, opacity: fadeAt(frame, 5.8)}}>
        With k, a handler can ...
      </div>
      {CHOICES.map((choice, i) => (
        <div
          key={choice.what}
          style={{
            position: 'absolute', left: 60 + i * 236, top: 520, width: 220, height: 96, boxSizing: 'border-box', borderRadius: 14, background: PANEL,
            border: `2px solid ${ACCENT}`, ...glow(during(frame, 6.0 + i * 0.8, 6.8 + i * 0.8), ACCENT), opacity: fadeAt(frame, 6.0 + i * 0.8),
            textAlign: 'center', paddingTop: 16,
          }}
        >
          <div style={{fontFamily: SANS, fontSize: 21, fontWeight: 700, color: TEXT}}>{choice.what}</div>
          <div style={{fontFamily: SANS, fontSize: 16, color: MUTED, marginTop: 8}}>{choice.example}</div>
        </div>
      ))}
    </AbsoluteFill>
  );
};

// Scene 2 — the running example: two effects, two canned handlers that resume k with an answer.

const EXAMPLE_LINES: Lines = [
  [0, 'The running example: a summary, then a picture.'],
  [4, 'The workflow yields Chat and Draw. It never says who answers them.'],
  [9.3, 'Handlers answer by resuming k. Each next example adds one handler.'],
];
const EXAMPLE = f(15);
const IMAGES_X = 1000;
const CHAT_X = 1160;

const ExampleScene: React.FC = () => {
  const frame = useSceneFrame();
  const band = frame < f(4.3) ? null : frame < f(6.9) ? 2 : frame < f(9.0) ? mix(2, 3, ramp(frame, 6.9, 7.2)) : mix(3, 4, ramp(frame, 9.0, 9.3));
  return (
    <AbsoluteFill>
      <Headlines lines={EXAMPLE_LINES} />
      <CodeBlock lines={SUMMARY} {...CODE} band={band} opacity={fadeAt(frame, 0)} />
      <Wall x={IMAGES_X} name="canned_images" note="handles Draw" on={during(frame, 7.5, 8.1)} opacity={fadeAt(frame, 1.2)} />
      <Wall x={CHAT_X} name="canned_chat" note="handles Chat" on={during(frame, 5.5, 6.1)} opacity={fadeAt(frame, 1.8)} />
      <div style={{position: 'absolute', left: 840, top: 420, width: 400, fontFamily: SANS, fontSize: 18, lineHeight: '26px', color: MUTED, opacity: fadeAt(frame, 2.4)}}>
        Each handler answers the effects its annotation names. Chat passes canned_images by.
      </div>
      {during(frame, 4.5, 5.7) && <Pill x={along(frame, 4.5, 5.5, OUT_X, CHAT_X)} y={CHAT_Y} text="Chat" color={ACCENT} opacity={fadeOut(frame, 5.4)} />}
      {during(frame, 6.1, 7.1) && (
        <Pill x={along(frame, 6.1, 6.9, inFrame(CHAT_X, '"summary by gpt-4o"'), OUT_X)} y={CHAT_Y} text={'"summary by gpt-4o"'} color={TEST} opacity={fadeOut(frame, 6.8)} />
      )}
      {during(frame, 7.1, 7.7) && <Pill x={along(frame, 7.1, 7.5, OUT_X, IMAGES_X)} y={DRAW_Y} text="Draw" color={ACCENT} opacity={fadeOut(frame, 7.4)} />}
      {during(frame, 8.1, 8.9) && (
        <Pill x={along(frame, 8.1, 8.7, inFrame(IMAGES_X, '"image by imagen-3"'), OUT_X)} y={DRAW_Y} text={'"image by imagen-3"'} color={TEST} opacity={fadeOut(frame, 8.6)} />
      )}
      <Output
        x={60} y={420} w={720} opacity={fadeAt(frame, 9.5)}
        call={'run(handler(canned_chat)(handler(canned_images)(\n    illustrated_summary("tides"))))'}
        lines={[{text: 'summary by gpt-4o | image by imagen-3', color: TEST}]}
      />
      <div style={{position: 'absolute', left: 60, top: 580, width: 720, fontFamily: SANS, fontSize: 19, color: MUTED, opacity: fadeAt(frame, 10.4)}}>
        The scenes that follow add one handler around this call, and leave the workflow as it is.
      </div>
    </AbsoluteFill>
  );
};

// Scene 3 — drop k: a cost cap answers the effect over the budget itself, so the rest of the workflow never runs.

const DROP_LINES: Lines = [
  [0, 'Drop it: a cost cap of three cents, around every model call.'],
  [5, 'Chat costs one cent. The cap passes it on.'],
  [9.5, 'Draw would bring it to five. So the cap answers itself, and drops k.'],
  [15.2, 'The image model is never called.'],
];
const DROP = f(18.5);
const CAP_X = 840;

const DropScene: React.FC = () => {
  const frame = useSceneFrame();
  const spent = frame < f(5.8) ? 0 : frame < f(10.4) ? 1 : 5;
  const band = frame < f(5.2) ? null : frame < f(9.6) ? 2 : 3;
  const restDim = interpolate(frame, [f(11.4), f(12.0)], [1, 0.25], clamp);
  return (
    <AbsoluteFill>
      <Headlines lines={DROP_LINES} />
      <CodeBlock lines={SUMMARY} {...CODE} band={band} dim={(i) => (i >= 3 ? restDim : 1)} opacity={fadeAt(frame, 0)} />
      <div
        style={{
          position: 'absolute', left: CODE.x + 6, top: CODE.y + PAD + 3 * CODE.line - 2, width: CODE.w - 12, height: 2 * CODE.line + 4,
          boxSizing: 'border-box', border: `3px dashed ${FAIL}`, borderRadius: 8, opacity: fadeAt(frame, 11.4),
        }}
      />
      <div style={{position: 'absolute', left: 560, top: lineMid(CODE.y, CODE.line, 4), transform: 'translateY(-50%)', fontFamily: SANS, fontSize: 19, fontWeight: 700, color: FAIL, opacity: fadeAt(frame, 11.6)}}>
        k dropped
      </div>
      <Wall x={CAP_X} name="cost_cap(3)" note="Chat · Draw" on={during(frame, 5.6, 6.4) || during(frame, 10.2, 11.2)} opacity={fadeAt(frame, 0.6)} />
      <Wall
        x={IMAGES_X} name="canned_images" opacity={fadeAt(frame, 1.0) * (frame < f(15.3) ? 1 : 0.5)} note={frame >= f(15.3) ? 'never called' : 'handles Draw'}
        noteColor={frame >= f(15.3) ? FAIL : MUTED}
      />
      <Wall x={CHAT_X} name="canned_chat" note="handles Chat" on={during(frame, 7.1, 7.7)} opacity={fadeAt(frame, 1.4)} />
      {during(frame, 5.0, 7.1) && (
        <Pill x={frame < f(6.4) ? along(frame, 5.0, 5.6, OUT_X, CAP_X) : along(frame, 6.4, 7.1, CAP_X, CHAT_X)} y={CHAT_Y} text="Chat" color={ACCENT} opacity={fadeOut(frame, 7.0, 0.1)} />
      )}
      {during(frame, 7.7, 8.7) && (
        <Pill x={along(frame, 7.7, 8.5, inFrame(CHAT_X, '"summary by gpt-4o"'), OUT_X)} y={CHAT_Y} text={'"summary by gpt-4o"'} color={TEST} opacity={fadeOut(frame, 8.4)} />
      )}
      {during(frame, 9.6, 11.4) && (
        <Pill x={along(frame, 9.6, 10.2, OUT_X, CAP_X)} y={DRAW_Y} text="Draw" color={frame < f(10.6) ? ACCENT : FAIL} opacity={fadeOut(frame, 11.0, 0.4)} />
      )}
      <div style={{position: 'absolute', left: 60, top: 372, width: 720, height: 96, boxSizing: 'border-box', borderRadius: 14, background: PANEL, border: '2px solid #2a2e40', opacity: fadeAt(frame, 0.6)}}>
        <div style={{position: 'absolute', left: 18, top: 10, fontFamily: MONO, fontSize: 18, color: spent > 3 ? FAIL : TEXT}}>{`spent ${spent} of 3 cents`}</div>
        <div style={{position: 'absolute', left: 300, top: 12, fontFamily: SANS, fontSize: 16, color: MUTED}}>gpt-4o: 1 cent · imagen-3: 4 cents</div>
        {[0, 1, 2, 3, 4].map((cent) => (
          <div
            key={cent}
            style={{
              position: 'absolute', left: 18 + cent * 72, top: 46, width: 64, height: 32, borderRadius: 6, boxSizing: 'border-box',
              border: `2px solid ${cent < 3 ? TEST : FAIL}`, background: cent < spent ? (spent > 3 ? FAIL : TEST) : 'transparent',
              opacity: cent < spent ? 0.85 : 0.4,
            }}
          />
        ))}
        <div style={{position: 'absolute', left: 18 + 3 * 72 - 5, top: 40, width: 3, height: 44, background: LOOK}} />
        <div style={{position: 'absolute', left: 400, top: 52, fontFamily: SANS, fontSize: 16, color: LOOK}}>the limit: 3 cents</div>
      </div>
      <Output
        x={60} y={490} w={1160} opacity={fadeAt(frame, 0.6)}
        call={'run(providers(cost_cap(3)(illustrated_summary("tides"))))'}
        lines={[{text: frame >= f(12.2) ? 'stopped before imagen-3: over 3 cents' : ' ', color: FAIL, opacity: fadeAt(frame, 12.2)}]}
      />
      <div style={{position: 'absolute', left: 60, top: 600, width: 1160, fontFamily: SANS, fontSize: 19, color: MUTED, opacity: fadeAt(frame, 15.8)}}>
        The cap returned without resuming k, and its answer became the run's answer. illustrated_summary has no if for it.
      </div>
    </AbsoluteFill>
  );
};

// Scene 4 — ask again: retry performs the effect on the handlers outside it until one answers, then resumes k once.

const RETRY_LINES: Lines = [
  [0, 'Ask again: retry performs the effect once more, on the handlers outside it.'],
  [6.1, 'The provider times out twice. The third attempt answers.'],
  [10.5, 'Then k resumes once, with the answer that worked.'],
];
const RETRY = f(15);
const RETRY_X = 840;
// The three attempts of measure/continuation.py: the second each one leaves retry, and what came back.
const ATTEMPTS = [
  {at: 6.2, back: 'ConnectionError', color: FAIL, line: 'attempt 1: timed out'},
  {at: 7.5, back: 'ConnectionError', color: FAIL, line: 'attempt 2: timed out'},
  {at: 8.8, back: '"summary by gpt-4o"', color: TEST, line: 'attempt 3: summary by gpt-4o'},
];

const RetryScene: React.FC = () => {
  const frame = useSceneFrame();
  const band = frame < f(0.8) ? null : frame < f(11.4) ? 2 : mix(2, 3, ramp(frame, 11.4, 11.7));
  const held = frame >= f(1.4) && frame < f(11.0);
  return (
    <AbsoluteFill>
      <Headlines lines={RETRY_LINES} />
      <CodeBlock lines={SUMMARY} {...CODE} band={band} dim={(i) => (i >= 3 ? interpolate(frame, [f(1.4), f(1.8), f(11.0), f(11.4)], [1, 0.35, 0.35, 1], clamp) : 1)} opacity={fadeAt(frame, 0)} />
      <Wall x={RETRY_X} name="retry" note="Chat · Draw" on={held && !ATTEMPTS.some((a) => during(frame, a.at, a.at + 1.2))} opacity={fadeAt(frame, 0.4)} />
      <Wall x={IMAGES_X} name="canned_images" note="handles Draw" opacity={fadeAt(frame, 0.8)} />
      <Wall
        x={CHAT_X} name="flaky_chat" note="times out twice" noteColor={PROD} color={PROD}
        on={ATTEMPTS.some((a) => during(frame, a.at + 0.5, a.at + 0.8))} opacity={fadeAt(frame, 1.2)}
      />
      {during(frame, 0.8, 1.5) && <Pill x={along(frame, 0.8, 1.4, OUT_X, RETRY_X)} y={CHAT_Y} text="Chat" color={ACCENT} opacity={fadeOut(frame, 1.35, 0.15)} />}
      {frame >= f(1.4) && (
        <KCard x={RETRY_X} y={300} label="k · held" w={110} opacity={fadeAt(frame, 1.4) * (frame < f(11.0) ? 1 : fadeOut(frame, 11.0))} />
      )}
      {ATTEMPTS.map((attempt) => (
        <React.Fragment key={attempt.at}>
          {during(frame, attempt.at, attempt.at + 0.55) && <Pill x={along(frame, attempt.at, attempt.at + 0.5, RETRY_X, CHAT_X)} y={CHAT_Y} text="Chat" color={ACCENT} />}
          {during(frame, attempt.at + 0.8, attempt.at + 1.25) && (
            <Pill x={along(frame, attempt.at + 0.8, attempt.at + 1.2, inFrame(CHAT_X, attempt.back), RETRY_X)} y={CHAT_Y} text={attempt.back} color={attempt.color} opacity={fadeOut(frame, 1.1 + attempt.at)} />
          )}
        </React.Fragment>
      ))}
      {during(frame, 10.7, 11.5) && (
        <Pill x={along(frame, 10.7, 11.3, inFrame(RETRY_X, '"summary by gpt-4o"'), OUT_X)} y={CHAT_Y} text={'"summary by gpt-4o"'} color={TEST} opacity={fadeOut(frame, 11.2)} />
      )}
      <Output
        x={60} y={410} w={720} opacity={fadeAt(frame, 0.4)}
        call="retry saw:"
        lines={ATTEMPTS.map((attempt) => ({text: attempt.line, color: attempt.color, opacity: fadeAt(frame, attempt.at + 1.2)}))}
      />
      <Output
        x={820} y={410} w={400} opacity={fadeAt(frame, 11.6)}
        call="provider calls:"
        lines={[{text: '3', color: TEXT}, {text: 'k resumed: once', color: TEST}]}
      />
      <div style={{position: 'absolute', left: 60, top: 590, width: 1160, fontFamily: SANS, fontSize: 19, color: MUTED, opacity: fadeAt(frame, 12.2)}}>
        Each attempt is a new effect. k resumes at most once, so a retry performs the effect again; it never resumes k twice.
      </div>
    </AbsoluteFill>
  );
};

// Scene 5 — keep the answers: effects are data, so a recorder files each answer under its effect, and replay answers
// from the tape alone.

const TAPE_LINES: Lines = [
  [0, 'Keep the answers: effects are data, so a recorder can file each answer.'],
  [5.8, 'Replay runs from the tape, with no provider at all.'],
  [9.8, 'An effect it has never seen stops the run.'],
];
const TAPE = f(14);
const TAPE_CODE = {x: 60, y: 120, w: 700, size: 17, line: 28};
const TAPE_CHAT_Y = lineMid(TAPE_CODE.y, TAPE_CODE.line, 2);
const TAPE_DRAW_Y = lineMid(TAPE_CODE.y, TAPE_CODE.line, 3);
const RECORDER_X = 860;
const PROVIDERS_X = 1120;
// The tape of measure/continuation.py, and the second each row is filed.
const ROWS = [
  {at: 2.5, effect: "Chat(model='gpt-4o', prompt='Summarize tides')", answer: "'summary by gpt-4o'"},
  {at: 4.9, effect: "Draw(model='imagen-3', prompt='summary by gpt-4o')", answer: "'image by imagen-3'"},
];

// One trip of an effect from the code out to the recorder, on to the providers and back, filing the answer.
const recordTrip = (frame: number, start: number, y: number, effect: string, answer: string) => (
  <>
    {during(frame, start, start + 1.0) && (
      <Pill x={frame < f(start + 0.5) ? along(frame, start, start + 0.5, OUT_X, RECORDER_X) : along(frame, start + 0.5, start + 1.0, RECORDER_X, PROVIDERS_X)} y={y} text={effect} color={ACCENT} />
    )}
    {during(frame, start + 1.2, start + 1.75) && <Pill x={along(frame, start + 1.2, start + 1.7, inFrame(PROVIDERS_X, answer), RECORDER_X)} y={y} text={answer} color={TEST} />}
    {during(frame, start + 1.8, start + 2.35) && <Pill x={along(frame, start + 1.8, start + 2.3, RECORDER_X, OUT_X)} y={y} text={answer} color={TEST} opacity={fadeOut(frame, start + 2.2)} />}
  </>
);

// One trip of an effect from the code to replay and back with the answer the tape gives.
const replayTrip = (frame: number, start: number, y: number, effect: string, answer: string, color: string) => (
  <>
    {during(frame, start, start + 0.55) && <Pill x={along(frame, start, start + 0.5, OUT_X, RECORDER_X)} y={y} text={effect} color={ACCENT} />}
    {during(frame, start + 1.0, start + 1.6) && <Pill x={along(frame, start + 1.0, start + 1.5, RECORDER_X, OUT_X)} y={y} text={answer} color={color} opacity={fadeOut(frame, start + 1.4)} />}
  </>
);

const TapeScene: React.FC = () => {
  const frame = useSceneFrame();
  const replaying = frame >= f(5.9);
  const waves = frame >= f(9.9);
  const band = frame < f(0.8) ? null : frame < f(3.2) ? 2 : frame < f(5.6) ? 3 : frame < f(6.1) ? null : frame < f(7.6) ? 2 : frame < f(9.4) ? 3 : frame < f(10.1) ? null : 2;
  const lookedUp = (row: number) => (row === 0 && during(frame, 6.6, 7.3)) || (row === 1 && during(frame, 8.1, 8.8));
  const missed = during(frame, 10.6, 11.6);
  const call = waves ? 'run(replay(tape)(illustrated_summary("waves")))' : replaying ? 'run(replay(tape)(illustrated_summary("tides")))' : 'run(providers(record(tape)(illustrated_summary("tides"))))';
  const result = waves
    ? {text: "not recorded: Chat(model='gpt-4o', prompt='Summarize waves')", color: FAIL, opacity: fadeAt(frame, 11.8)}
    : replaying
      ? {text: 'summary by gpt-4o | image by imagen-3', color: TEST, opacity: fadeAt(frame, 9.2)}
      : {text: 'summary by gpt-4o | image by imagen-3', color: TEST, opacity: fadeAt(frame, 5.4)};
  return (
    <AbsoluteFill>
      <Headlines lines={TAPE_LINES} />
      <CodeBlock lines={SUMMARY} {...TAPE_CODE} band={band} opacity={fadeAt(frame, 0)} />
      <Wall x={RECORDER_X} name={replaying ? 'replay(tape)' : 'record(tape)'} note="Chat · Draw" on={lookedUp(0) || lookedUp(1) || missed} opacity={fadeAt(frame, 0.4)} />
      <Wall
        x={PROVIDERS_X} name="providers" note={replaying ? 'not installed' : 'canned_chat · canned_images'}
        opacity={fadeAt(frame, 0.6) * interpolate(frame, [f(5.9), f(6.4)], [1, 0.4], clamp)}
      />
      {recordTrip(frame, 0.8, TAPE_CHAT_Y, 'Chat', "'summary by gpt-4o'")}
      {recordTrip(frame, 3.2, TAPE_DRAW_Y, 'Draw', "'image by imagen-3'")}
      {replayTrip(frame, 6.1, TAPE_CHAT_Y, 'Chat', "'summary by gpt-4o'", TEST)}
      {replayTrip(frame, 7.6, TAPE_DRAW_Y, 'Draw', "'image by imagen-3'", TEST)}
      {replayTrip(frame, 10.1, TAPE_CHAT_Y, 'Chat(..."Summarize waves")', 'LookupError', FAIL)}
      <div style={{position: 'absolute', left: 60, top: 400, width: 1160, height: 132, boxSizing: 'border-box', borderRadius: 14, background: PANEL, border: `2px solid ${missed ? FAIL : '#2a2e40'}`, opacity: fadeAt(frame, 0.4)}}>
        <div style={{position: 'absolute', left: 18, top: 8, fontFamily: SANS, fontSize: 17, fontWeight: 700, color: MUTED}}>tape: the effect → the answer it got</div>
        {ROWS.map((row, i) => (
          <div
            key={row.effect}
            style={{
              position: 'absolute', left: 10, right: 10, top: 40 + i * 42, height: 38, borderRadius: 8, fontFamily: MONO, fontSize: 17, lineHeight: '38px',
              background: lookedUp(i) ? `${LOOK}33` : 'transparent', opacity: fadeAt(frame, row.at) * (missed ? 0.35 : 1),
            }}
          >
            <span style={{position: 'absolute', left: 10, color: ACCENT}}>{row.effect}</span>
            <span style={{position: 'absolute', left: 700, color: TEST}}>{`→ ${row.answer}`}</span>
          </div>
        ))}
      </div>
      <Output x={60} y={556} w={1160} call={call} lines={[result]} opacity={fadeAt(frame, 0.4)} />
    </AbsoluteFill>
  );
};

// Scene 6 — hold k: the scheduler and a virtual clock park the k of each sleeping task and wake them in time order.

const HOLD_LINES: Lines = [
  [0, 'Hold it: the scheduler is a handler that keeps the k of each waiting task.'],
  [5.6, 'Tea sleeps every 2 hours, stretch every 3. The clock parks each k.'],
  [11.4, 'When every task waits, the clock jumps to the next wake-up.'],
  [17.5, 'Six hours in under 15 ms, the same lines every run.'],
];
const HOLD = f(23);
const DAY = [
  '@do',
  'def day():',
  '    tea = yield Spawn(reminder("tea", 2, 3))',
  '    stretch = yield Spawn(reminder("stretch", 3, 2))',
  '    yield Gather(tea, stretch)',
];
// The log of measure/virtual_clock.py, with the second each line is written.
const REMINDERS = [
  {at: 12.4, lane: 'tea', hour: 2, line: '2h tea'},
  {at: 14.0, lane: 'stretch', hour: 3, line: '3h stretch'},
  {at: 15.4, lane: 'tea', hour: 4, line: '4h tea'},
  {at: 17.0, lane: 'stretch', hour: 6, line: '6h stretch'},
  {at: 17.3, lane: 'tea', hour: 6, line: '6h tea'},
] as const;
// The parked k's, earliest wake-up first, from the run's trace: park until 2h, 3h; wake 2h; park until 4h; ...
const PARKED: {at: number; items: string[]}[] = [
  {at: 0, items: []},
  {at: 6.4, items: ['tea 2h']},
  {at: 7.2, items: ['tea 2h', 'stretch 3h']},
  {at: 11.9, items: ['stretch 3h']},
  {at: 12.8, items: ['stretch 3h', 'tea 4h']},
  {at: 13.6, items: ['tea 4h']},
  {at: 14.3, items: ['tea 4h', 'stretch 6h']},
  {at: 15.0, items: ['stretch 6h']},
  {at: 15.7, items: ['stretch 6h', 'tea 6h']},
  {at: 16.7, items: ['tea 6h']},
  {at: 17.1, items: []},
];
const CLOCK_KEYS: [number, number][] = [[11.4, 0], [11.9, 2], [13.2, 2], [13.6, 3], [14.6, 3], [15.0, 4], [16.0, 4], [16.7, 6]];
const LANES = {tea: 410, stretch: 490} as const;
const hourX = (hour: number) => 90 + hour * 95;
const PANEL_BOX = {x: 740, y: 120, w: 480, h: 290};
const parkedY = (i: number) => PANEL_BOX.y + 150 + i * 56;

// Where a parked k card is: its place in the list now, slid from its place in the list before, or faded in or out.
const parkedPose = (frame: number, item: string) => {
  const now = PARKED.reduce((found, state, i) => (frame >= f(state.at) ? i : found), 0);
  const t = ramp(frame, PARKED[now].at, PARKED[now].at + 0.4);
  const was = now > 0 ? PARKED[now - 1].items.indexOf(item) : -1;
  const is = PARKED[now].items.indexOf(item);
  if (was >= 0 && is >= 0) return {y: mix(parkedY(was), parkedY(is), t), opacity: 1};
  if (is >= 0) return {y: parkedY(is), opacity: t};
  if (was >= 0) return {y: parkedY(was), opacity: 1 - t};
  return null;
};

const HoldScene: React.FC = () => {
  const frame = useSceneFrame();
  const hours = interpolate(frame, CLOCK_KEYS.map(([s]) => f(s)), CLOCK_KEYS.map(([, h]) => h), clamp);
  const band = frame < f(1.0) ? null : frame < f(2.0) ? 2 : frame < f(3.0) ? 3 : 4;
  const items = Array.from(new Set(PARKED.flatMap((state) => state.items)));
  const jumping = CLOCK_KEYS.some(([s], i) => i % 2 === 0 && i + 1 < CLOCK_KEYS.length && during(frame, s, CLOCK_KEYS[i + 1][0]));
  return (
    <AbsoluteFill>
      <Headlines lines={HOLD_LINES} />
      <CodeBlock lines={DAY} x={60} y={120} w={640} size={17} line={27} band={band} opacity={fadeAt(frame, 0)} />
      <div style={{position: 'absolute', left: 60, top: 292, width: 640, fontFamily: SANS, fontSize: 17, lineHeight: '25px', color: MUTED, opacity: fadeAt(frame, 2.0)}}>
        reminder(name, every_hours, times) yields Sleep(every_hours * 3600), then Now(), and logs the hour.
      </div>
      <div
        style={{
          position: 'absolute', left: PANEL_BOX.x, top: PANEL_BOX.y, width: PANEL_BOX.w, height: PANEL_BOX.h, boxSizing: 'border-box', borderRadius: 18,
          background: PANEL, border: `3px solid ${ACCENT}`, ...glow(jumping, ACCENT), opacity: fadeAt(frame, 0.3),
        }}
      >
        <div style={{position: 'absolute', left: 20, top: 12, fontFamily: MONO, fontSize: 20, fontWeight: 700, color: ACCENT}}>scheduled(virtual_clock()(...))</div>
        <div style={{position: 'absolute', left: 20, top: 50, fontFamily: SANS, fontSize: 18, color: MUTED}}>virtual time</div>
        <div style={{position: 'absolute', left: 140, top: 40, fontFamily: MONO, fontSize: 34, fontWeight: 700, color: jumping ? LOOK : TEXT}}>{`${hours.toFixed(1)}h`}</div>
        <div style={{position: 'absolute', left: 20, top: 100, fontFamily: SANS, fontSize: 16, color: MUTED}}>parked k's, earliest wake-up first</div>
      </div>
      {items.map((item) => {
        const pose = parkedPose(frame, item);
        if (!pose) return null;
        const [name, hour] = item.split(' ');
        return <KCard key={item} x={PANEL_BOX.x + PANEL_BOX.w / 2} y={pose.y} w={400} label={`k of ${name} · wakes at ${hour}`} opacity={pose.opacity} />;
      })}
      {(['tea', 'stretch'] as const).map((lane) => (
        <React.Fragment key={lane}>
          <div style={{position: 'absolute', left: hourX(0) + 14, top: LANES[lane] - 30, fontFamily: SANS, fontSize: 17, color: MUTED, opacity: fadeAt(frame, 4.0)}}>
            {lane === 'tea' ? 'tea · sleeps 2h, 3 times' : 'stretch · sleeps 3h, 2 times'}
          </div>
          <div style={{position: 'absolute', left: hourX(0), top: LANES[lane], width: hourX(6) - hourX(0), height: 2, background: '#3a3f55', opacity: fadeAt(frame, 4.0)}} />
        </React.Fragment>
      ))}
      {[0, 1, 2, 3, 4, 5, 6].map((hour) => (
        <div key={hour} style={{position: 'absolute', left: hourX(hour), top: 512, transform: 'translateX(-50%)', fontFamily: MONO, fontSize: 15, color: MUTED, opacity: fadeAt(frame, 4.0)}}>
          {`${hour}h`}
        </div>
      ))}
      <div style={{position: 'absolute', left: hourX(hours) - 1, top: 402, width: 3, height: 104, background: LOOK, boxShadow: `0 0 10px ${LOOK}`, opacity: fadeAt(frame, 4.0)}} />
      {REMINDERS.map((reminder) => (
        <div
          key={reminder.line}
          style={{
            position: 'absolute', left: hourX(reminder.hour) - 9, top: LANES[reminder.lane] - 8, width: 18, height: 18, borderRadius: 9, background: TEST,
            boxShadow: `0 0 12px ${TEST}`, opacity: fadeAt(frame, reminder.at),
          }}
        />
      ))}
      <Output
        x={740} y={430} w={480} call="log"
        lines={REMINDERS.map((reminder) => ({text: reminder.line, opacity: fadeAt(frame, reminder.at)}))}
        opacity={fadeAt(frame, 0.3)}
      />
      <div style={{position: 'absolute', left: 60, top: 560, width: 640, fontFamily: SANS, fontSize: 21, fontWeight: 700, lineHeight: '31px', color: TEST, opacity: fadeAt(frame, 18.0)}}>
        7 runs: 0.42–12.65 ms each, the same five lines in the same order.
      </div>
    </AbsoluteFill>
  );
};

// Scene 7 — compared with DI: an injected client answers and returns, without k; a concern is a wrapper per client.

const DI_LINES: Lines = [
  [0, 'With dependency injection, an injected client answers a call, and returns.'],
  [6.2, 'It never gets the rest, so it cannot drop it, or hold it while others run.'],
];
const DI = f(12);
const DI_CODE = [
  'def illustrated_summary(topic, llm, images):',
  '    summary = llm.chat("gpt-4o", f"Summarize {topic}")',
  '    picture = images.draw("imagen-3", summary)',
  '    return f"{summary} | {picture}"',
];
const CAN = [
  {what: 'the arguments of one call', client: 'model, prompt', handler: 'the effect, and k'},
  {what: 'drop the rest (a cost cap)', client: '✗', handler: '✓'},
  {what: 'hold the rest while other tasks run', client: '✗', handler: '✓'},
];

const DiScene: React.FC = () => {
  const frame = useSceneFrame();
  const callY = lineMid(130, 30, 1);
  return (
    <AbsoluteFill>
      <Headlines lines={DI_LINES} />
      <AbsoluteFill>
        <CodeBlock lines={DI_CODE} x={60} y={130} w={700} size={18} line={30} band={frame < f(1.0) ? null : 1} opacity={fadeAt(frame, 0)} />
        <div
          style={{
            position: 'absolute', left: 860, top: 130, width: 360, height: 120, boxSizing: 'border-box', borderRadius: 18, background: PANEL,
            border: `3px solid ${PROD}`, ...glow(during(frame, 1.9, 2.5), PROD), opacity: fadeAt(frame, 0.4),
          }}
        >
          <div style={{position: 'absolute', left: 20, top: 12, fontFamily: MONO, fontSize: 20, fontWeight: 700, color: PROD}}>llm = CannedChat()</div>
          <div style={{position: 'absolute', left: 20, top: 48, fontFamily: SANS, fontSize: 17, color: MUTED}}>an injected client</div>
          <div style={{position: 'absolute', left: 20, top: 76, fontFamily: SANS, fontSize: 17, color: MUTED, opacity: fadeAt(frame, 3.2)}}>answered, returned, done</div>
        </div>
        {during(frame, 1.2, 1.95) && <Pill x={along(frame, 1.2, 1.9, OUT_X, 860)} y={callY} text="llm.chat(...)" color={ACCENT} />}
        {during(frame, 2.5, 3.3) && <Pill x={along(frame, 2.5, 3.2, 900, OUT_X)} y={callY} text={'"summary by gpt-4o"'} color={TEST} opacity={fadeOut(frame, 3.1)} />}
        <div style={{position: 'absolute', left: 60, top: 320, width: 1160, height: 210, boxSizing: 'border-box', borderRadius: 14, background: PANEL, opacity: fadeAt(frame, 6.4)}}>
          <div style={{position: 'absolute', left: 560, top: 14, fontFamily: SANS, fontSize: 19, fontWeight: 700, color: PROD}}>an injected client</div>
          <div style={{position: 'absolute', left: 860, top: 14, fontFamily: SANS, fontSize: 19, fontWeight: 700, color: TEST}}>a handler</div>
          {CAN.map((row, i) => (
            <React.Fragment key={row.what}>
              <div style={{position: 'absolute', left: 24, top: 60 + i * 48, fontFamily: SANS, fontSize: 20, color: TEXT, opacity: fadeAt(frame, 6.8 + i * 1.2)}}>{i === 0 ? 'gets' : row.what}</div>
              <div style={{position: 'absolute', left: 560, top: 60 + i * 48, fontFamily: i === 0 ? MONO : SANS, fontSize: i === 0 ? 18 : 24, fontWeight: 700, color: i === 0 ? TEXT : FAIL, opacity: fadeAt(frame, 7.0 + i * 1.2)}}>{row.client}</div>
              <div style={{position: 'absolute', left: 860, top: 60 + i * 48, fontFamily: SANS, fontSize: i === 0 ? 20 : 24, fontWeight: 700, color: TEST, opacity: fadeAt(frame, 7.2 + i * 1.2)}}>{row.handler}</div>
            </React.Fragment>
          ))}
        </div>
      </AbsoluteFill>
    </AbsoluteFill>
  );
};

// Scene 8 — compared with plain async: async spreads up the call chain; with effects the program stays plain and a
// handler decides whether a Fetch waits on an event loop.

const ASYNC_LINES: Lines = [
  [0, 'Plain async spreads: whatever awaits must be async, and so must its callers.'],
  [7, 'With effects, total_size yields Spawn and Gather. No async, no await.'],
  [12.6, 'A handler decides how Fetch is answered. Both give 36.'],
];
const ASYNC = f(18.5);
const CHAIN = [
  {name: 'fetch(url)', why: 'await asyncio.sleep(...)'},
  {name: 'total_size(urls)', why: 'await asyncio.gather(...)'},
  {name: 'every caller', why: 'must await total_size'},
  {name: 'every test', why: 'must run an event loop'},
];
const TOTAL = [
  '@do',
  'def total_size(urls):  # no async, no await',
  '    tasks = []',
  '    for url in urls:',
  '        tasks.append((yield Spawn(fetch(url))))',
  '    pages = yield Gather(*tasks)',
  '    return sum(len(page) for page in pages)',
];
// The runs of measure/async_vs_effects.py: 5 runs of each.
const HANDLERS = [
  {name: 'async_http', how: 'Await(download(url)) under await_handler', y: 400, at: 12.8, result: '36 · 11.08–27.58 ms'},
  {name: 'instant_http', how: 'answers at once, no event loop', y: 490, at: 14.6, result: '36 · 0.29–0.39 ms'},
];

const AsyncScene: React.FC = () => {
  const frame = useSceneFrame();
  const band = frame < f(7.8) ? null : frame < f(9.2) ? 4 : 5;
  return (
    <AbsoluteFill>
      <Headlines lines={ASYNC_LINES} />
      <div style={{position: 'absolute', left: 60, top: 116, fontFamily: SANS, fontSize: 22, fontWeight: 700, color: PROD, opacity: fadeAt(frame, 0.3)}}>plain async</div>
      {CHAIN.map((link, i) => {
        const y = 500 - i * 100;
        const at = 0.8 + i * 1.1;
        return (
          <React.Fragment key={link.name}>
            <div
              style={{
                position: 'absolute', left: 60, top: y, width: 520, height: 70, boxSizing: 'border-box', borderRadius: 12, background: PANEL,
                border: `3px solid ${frame >= f(at) ? PROD : '#2a2e40'}`, ...glow(during(frame, at, at + 0.6), PROD), opacity: fadeAt(frame, 0.3),
              }}
            >
              <div style={{position: 'absolute', left: 18, top: 8, fontFamily: MONO, fontSize: 19, fontWeight: 700, color: TEXT}}>{link.name}</div>
              <div style={{position: 'absolute', left: 18, top: 38, fontFamily: SANS, fontSize: 15, color: MUTED}}>{link.why}</div>
            </div>
            <Pill x={470} y={y + 35} text="async" color={PROD} left opacity={fadeAt(frame, at)} />
            {i > 0 && (
              <div style={{position: 'absolute', left: 300, top: y + 70, width: 3, height: 30, background: PROD, opacity: fadeAt(frame, at - 0.3)}} />
            )}
          </React.Fragment>
        );
      })}
      <div style={{position: 'absolute', left: 60, top: 600, width: 560, fontFamily: MONO, fontSize: 17, lineHeight: '26px', color: MUTED, opacity: fadeAt(frame, 16.4)}}>
        asyncio.run(total_size(urls))
        <div style={{color: TEXT}}>36 · 10.79–11.72 ms</div>
      </div>
      <div style={{position: 'absolute', left: 660, top: 116, fontFamily: SANS, fontSize: 22, fontWeight: 700, color: ACCENT, opacity: fadeAt(frame, 7.0)}}>effects</div>
      <CodeBlock lines={TOTAL} x={660} y={150} w={560} size={16} line={26} band={band} opacity={fadeAt(frame, 7.0)} />
      <div style={{position: 'absolute', left: 660, top: 362, fontFamily: SANS, fontSize: 16, color: MUTED, opacity: fadeAt(frame, 9.8)}}>
        Spawn and Gather are effects too, answered by scheduled().
      </div>
      {HANDLERS.map((h) => (
        <React.Fragment key={h.name}>
          <div
            style={{
              position: 'absolute', left: 660, top: h.y + 4, width: 560, height: 76, boxSizing: 'border-box', borderRadius: 12, background: PANEL,
              border: `3px solid ${TEST}`, ...glow(during(frame, h.at + 0.6, h.at + 1.4), TEST), opacity: fadeAt(frame, 12.4),
            }}
          >
            <div style={{position: 'absolute', left: 18, top: 8, fontFamily: MONO, fontSize: 19, fontWeight: 700, color: TEST}}>{h.name}</div>
            <div style={{position: 'absolute', left: 18, top: 40, fontFamily: SANS, fontSize: 15, color: MUTED}}>{h.how}</div>
            <div style={{position: 'absolute', right: 18, top: 10, fontFamily: MONO, fontSize: 19, fontWeight: 700, color: TEXT, opacity: fadeAt(frame, h.at + 1.4)}}>{h.result}</div>
          </div>
          {['a.com', 'b.com'].map((url, i) =>
            during(frame, h.at, h.at + 0.7) ? (
              <Pill key={url} x={760 + i * 200} y={along(frame, h.at, h.at + 0.6, 280, h.y + 20)} text={`Fetch("${url}")`} color={ACCENT} opacity={fadeOut(frame, h.at + 0.5, 0.2)} />
            ) : null,
          )}
        </React.Fragment>
      ))}
    </AbsoluteFill>
  );
};

// Scene 9 — composability: effects are rows, handlers columns; a new effect adds a row, a new concern a column.

const GRID_LINES: Lines = [
  [0, 'Effects are rows, and handlers columns. Each handler minds one concern.'],
  [6.2, 'A new effect is one row. A new concern is one column.'],
  [12, 'With dependency injection, each ✓ would be a wrapper class.'],
];
const GRID = f(17.5);
// The page's table (The Composability Argument): which handler acts on which effect.
const EFFECT_ROWS = [
  {name: 'Chat', marks: [1, 1, 1, 0, 0]},
  {name: 'Draw', marks: [1, 1, 1, 0, 0]},
  {name: 'GetSecret', marks: [0, 0, 0, 0, 0]},
  {name: 'Sleep, Now', marks: [0, 0, 0, 1, 0]},
  {name: 'Spawn, Gather', marks: [0, 0, 0, 0, 1]},
];
const HANDLER_COLS = ['log_calls', 'cost_cap', 'retry', 'virtual_clock', 'scheduled'];
// The new row and column: an Embed effect named by the three call handlers, and a trace handler annotated with
// EffectBase, which sees every effect.
const NEW_ROW = {name: 'Embed', marks: [1, 1, 1, 0, 0], at: 6.6};
const NEW_COL = {name: 'trace', at: 9.0};
const GRID_X = 250;
const COL_W = 155;
const ROW_H = 50;
const GRID_Y = 180;

const GridScene: React.FC = () => {
  const frame = useSceneFrame();
  const rows = [...EFFECT_ROWS, NEW_ROW];
  const cols = [...HANDLER_COLS, NEW_COL.name];
  const asClass = ramp(frame, 12.4, 13.0);
  const rowShown = (r: number) => (r < EFFECT_ROWS.length ? fadeAt(frame, 0.8 + r * 0.4) : fadeAt(frame, NEW_ROW.at, 0.5));
  const colShown = (c: number) => (c < HANDLER_COLS.length ? fadeAt(frame, 0.4) : fadeAt(frame, NEW_COL.at, 0.5));
  const mark = (r: number, c: number) => (c === HANDLER_COLS.length ? 1 : rows[r].marks[c]);
  return (
    <AbsoluteFill>
      <Headlines lines={GRID_LINES} />
      <div style={{position: 'absolute', left: GRID_X, top: 118, fontFamily: SANS, fontSize: 17, color: MUTED, opacity: fadeAt(frame, 0.4)}}>handlers (one concern each)</div>
      {cols.map((name, c) => (
        <div
          key={name}
          style={{
            position: 'absolute', left: GRID_X + c * COL_W, top: GRID_Y - 36, width: COL_W, textAlign: 'center', fontFamily: MONO, fontSize: 16, fontWeight: 700,
            color: TEST, opacity: colShown(c),
          }}
        >
          {name}
        </div>
      ))}
      {frame >= f(NEW_COL.at) && (
        <div
          style={{
            position: 'absolute', left: GRID_X + HANDLER_COLS.length * COL_W + 4, top: GRID_Y - 42, width: COL_W - 8, height: ROW_H * rows.length + 48,
            borderRadius: 12, border: `2px solid ${TEST}`, ...glow(during(frame, NEW_COL.at, NEW_COL.at + 1.6), TEST), opacity: fadeAt(frame, NEW_COL.at, 0.5),
          }}
        />
      )}
      {frame >= f(NEW_ROW.at) && (
        <div
          style={{
            position: 'absolute', left: 52, top: GRID_Y + EFFECT_ROWS.length * ROW_H + 2, width: GRID_X - 52 + HANDLER_COLS.length * COL_W, height: ROW_H - 4,
            borderRadius: 12, border: `2px solid ${ACCENT}`, ...glow(during(frame, NEW_ROW.at, NEW_ROW.at + 1.6), ACCENT), opacity: fadeAt(frame, NEW_ROW.at, 0.5),
          }}
        />
      )}
      {rows.map((row, r) => (
        <React.Fragment key={row.name}>
          <div style={{position: 'absolute', left: 60, top: GRID_Y + r * ROW_H, height: ROW_H, lineHeight: `${ROW_H}px`, fontFamily: MONO, fontSize: 18, fontWeight: 700, color: ACCENT, opacity: rowShown(r)}}>
            {row.name}
          </div>
          <div style={{position: 'absolute', left: 60, top: GRID_Y + r * ROW_H, width: GRID_X - 60 + HANDLER_COLS.length * COL_W, height: 1, background: '#2a2e40', opacity: rowShown(r)}} />
          {cols.map((col, c) => {
            const on = mark(r, c) === 1;
            const shown = rowShown(r) * colShown(c);
            const cx = GRID_X + c * COL_W + COL_W / 2;
            const cy = GRID_Y + r * ROW_H + ROW_H / 2;
            return (
              <React.Fragment key={col}>
                {!on && <div style={{position: 'absolute', left: cx, top: cy, transform: 'translate(-50%, -50%)', fontFamily: SANS, fontSize: 22, color: MUTED, opacity: shown}}>·</div>}
                {on && (
                  <div style={{position: 'absolute', left: cx, top: cy, transform: 'translate(-50%, -50%)', fontFamily: SANS, fontSize: 26, fontWeight: 700, color: TEST, opacity: shown * (1 - asClass)}}>✓</div>
                )}
                {on && asClass > 0 && (
                  <div
                    style={{
                      position: 'absolute', left: cx, top: cy, transform: `translate(-50%, -50%) scale(${mix(0.6, 1, asClass)})`, padding: '2px 10px', borderRadius: 8,
                      border: `2px solid ${PROD}`, background: PANEL, fontFamily: MONO, fontSize: 14, fontWeight: 700, color: PROD, opacity: shown * asClass,
                    }}
                  >
                    wrapper
                  </div>
                )}
              </React.Fragment>
            );
          })}
        </React.Fragment>
      ))}
      <div style={{position: 'absolute', left: 60, top: 500, width: 1160, fontFamily: SANS, fontSize: 19, lineHeight: '30px', color: MUTED}}>
        <div style={{whiteSpace: 'pre', opacity: fadeAt(frame, 0.8)}}>{'✓ = the handler acts on the effect      · = the effect passes it by, with no line of code'}</div>
        <div style={{color: ACCENT, opacity: fadeAt(frame, NEW_ROW.at + 0.4)}}>A new effect: one row (one class; the handlers that should see it name it)</div>
        <div style={{color: TEST, opacity: fadeAt(frame, NEW_COL.at + 0.4)}}>A new concern: one column (one handler; trace names EffectBase, so it sees every effect)</div>
        <div style={{color: PROD, opacity: fadeAt(frame, 13.0)}}>With DI, each ✓ is a wrapper class, and a clock for concurrent code has to be a scheduler too.</div>
      </div>
    </AbsoluteFill>
  );
};

// Outro — where plain Python or DI is better, where effects pay, and the name.

const OUTRO_LINES: Lines = [
  [0, 'Effects are not free. For code that runs one way, plain Python is better.'],
  [5.8, 'When you need to steer the rest of the program, use a handler.'],
];
const OUTRO = f(12);
const PLAIN = ['one provider, code that runs one way', 'finding an API through the editor', 'a tight numeric loop', 'backtracking: k is one-shot'];
const EFFECTS = ['budgets, early exit, retries', 'one concern across many kinds of calls', 'dry runs, record and replay', 'async I/O without async callers', 'concurrency on a virtual clock'];

const OutroScene: React.FC = () => {
  const frame = useSceneFrame();
  const column = (x: number, title: string, color: string, items: string[], at: number) => (
    <div style={{position: 'absolute', left: x, top: 120, width: 540, opacity: fadeAt(frame, at)}}>
      <div style={{fontFamily: SANS, fontSize: 24, fontWeight: 700, color}}>{title}</div>
      {items.map((item, i) => (
        <div key={item} style={{fontFamily: SANS, fontSize: 20, lineHeight: '30px', color: TEXT, marginTop: 10, opacity: fadeAt(frame, at + 0.3 + i * 0.3)}}>
          <span style={{color}}>▸ </span>
          {item}
        </div>
      ))}
    </div>
  );
  return (
    <AbsoluteFill>
      <Headlines lines={OUTRO_LINES} />
      {column(60, 'Plain functions or DI', PROD, PLAIN, 0.4)}
      {column(680, 'Effects', ACCENT, EFFECTS, 6.0)}
      <div style={{position: 'absolute', left: 0, right: 0, top: 480, textAlign: 'center', opacity: fadeAt(frame, 7.4, 0.5)}}>
        <div style={{fontFamily: SANS, fontSize: 60, fontWeight: 700, color: TEXT}}>doeff</div>
        <div style={{fontFamily: SANS, fontSize: 26, color: ACCENT, marginTop: 2}}>Algebraic effects for Python</div>
        <div style={{display: 'inline-block', marginTop: 18, padding: '6px 22px', borderRadius: 12, background: '#0b0d13', fontFamily: MONO, fontSize: 24, color: TEST}}>pip install doeff</div>
      </div>
    </AbsoluteFill>
  );
};

// The scenes in order; Root.tsx registers WHY_EFFECTS.length frames of WHY_EFFECTS.Video.
export const WHY_EFFECTS = narrate('why-effects', DURATIONS, [
  {length: IDEA, starts: startsOf(IDEA_LINES), ids: ['t1', 't2'], node: <IdeaScene />},
  {length: EXAMPLE, starts: startsOf(EXAMPLE_LINES), ids: ['e1', 'e2', 'e3'], node: <ExampleScene />},
  {length: DROP, starts: startsOf(DROP_LINES), ids: ['d1', 'd2', 'd3', 'd4'], node: <DropScene />},
  {length: RETRY, starts: startsOf(RETRY_LINES), ids: ['r1', 'r2', 'r3'], node: <RetryScene />},
  {length: TAPE, starts: startsOf(TAPE_LINES), ids: ['p1', 'p2', 'p3'], node: <TapeScene />},
  {length: HOLD, starts: startsOf(HOLD_LINES), ids: ['h1', 'h2', 'h3', 'h4'], node: <HoldScene />},
  {length: DI, starts: startsOf(DI_LINES), ids: ['i1', 'i2'], node: <DiScene />},
  {length: ASYNC, starts: startsOf(ASYNC_LINES), ids: ['a1', 'a2', 'a3'], node: <AsyncScene />},
  {length: GRID, starts: startsOf(GRID_LINES), ids: ['c1', 'c2', 'c3'], node: <GridScene />},
  {length: OUTRO, starts: startsOf(OUTRO_LINES), ids: ['o1', 'o2'], node: <OutroScene />},
]);
