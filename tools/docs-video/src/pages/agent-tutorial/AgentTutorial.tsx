// The video on the Agent Tutorial page (docs/19-agent-tutorial.md): the automated code reviewer the page builds,
// written as doeff Programs that yield doeff-agents effects and run under handlers. Seven scenes, each with
// headlines a synthetic voice reads (narration.json):
//   1. What we build: a Program yields effects, the handler around it decides how each one is met; the four effects.
//   2. Step 1: review_file yields LaunchSession, AwaitResult, FollowUp and StopSession; calling it runs nothing.
//   3. Step 2: scripted_agent, a @handler of the effect and k, resumes k with its answers and logs each effect.
//   4. Step 3: the script plays the agent asking, timing out, and returning a result off the schema.
//   5. Step 4: review_all spawns a task per file under scheduled, try_handler and scripted_agent.
//   6. Steps 5 and 6: scenario_agent_handler in a test, daemon_agent_handlers() and the session host in production.
//   7. The outro: the Program says what it needs, the handler decides how.
// Every value, log line and order on screen is printed by a program in measure/ (its .log is next to it). The
// programs import the page's own code blocks (measure/chapter.py), so they measure exactly what the page prints.
import React from 'react';
import {AbsoluteFill} from 'remotion';
import {fadeAt, mix, ramp} from '../../kit/motion';
import {LEAD, narrate, Scene, TAIL} from '../../kit/narrated';
import {CodeBlock, glow, Headlines, lineMid, Lines, PAD, Pill, startsOf} from '../../kit/parts';
import {ACCENT, f, FAIL, LOOK, MONO, MUTED, PANEL, PROD, SANS, TEST, TEXT} from '../../kit/theme';
import {useSceneFrame, WarpContext} from '../../kit/warp';
import DURATIONS from './durations.json';

// Seen between two seconds of a scene: fades in at the first and out at the second.
const between = (frame: number, from: number, to: number, length = 0.3) => fadeAt(frame, from, length) * (1 - fadeAt(frame, to, length));
// The last value whose second has come: steps[i] = [second, value].
const stepAt = <T,>(frame: number, steps: [number, T][]): T => steps.reduce((found, [at, value]) => (frame >= f(at) ? value : found), steps[0][1]);

// A label in the scenes' sans font.
const Label: React.FC<{x: number; y: number; size?: number; color?: string; bold?: boolean; mono?: boolean; opacity?: number; children: React.ReactNode; center?: boolean; w?: number}> = ({
  x, y, size = 20, color = MUTED, bold = false, mono = false, opacity = 1, children, center = false, w,
}) => (
  <div
    style={{
      position: 'absolute', left: x, top: y, width: w, fontFamily: mono ? MONO : SANS, fontSize: size, fontWeight: bold ? 700 : 400, color, opacity,
      whiteSpace: 'pre', textAlign: center ? 'center' : 'left', transform: center && w === undefined ? 'translateX(-50%)' : undefined,
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

// The title and the lines under it, inside a Box.
const BoxText: React.FC<{title: string; color: string; on?: boolean; lines?: string[]; mono?: boolean; size?: number}> = ({title, color, on = false, lines = [], mono = false, size = 16}) => (
  <>
    <div style={{position: 'absolute', left: 18, top: 12, fontFamily: MONO, fontSize: 19, fontWeight: 700, color: on ? LOOK : color, whiteSpace: 'nowrap'}}>{title}</div>
    <div style={{position: 'absolute', left: 18, top: 46, fontFamily: mono ? MONO : SANS, fontSize: size, lineHeight: `${size + 10}px`, color: MUTED, whiteSpace: 'pre'}}>
      {lines.map((line) => (
        <div key={line}>{line}</div>
      ))}
    </div>
  </>
);

// Printed lines of a run: each appears at its second, in its colour.
type Printed = {at: number; text: string; color?: string};
const Prints: React.FC<{rows: Printed[]; x: number; y: number; size?: number; line?: number; opacity?: number}> = ({rows, x, y, size = 15, line = 26, opacity = 1}) => {
  const frame = useSceneFrame();
  return (
    <>
      {rows.map((row, i) => (
        <div
          key={i}
          style={{
            position: 'absolute', left: x, top: y + i * line + mix(8, 0, ramp(frame, row.at, row.at + 0.3)), fontFamily: MONO, fontSize: size, lineHeight: `${line}px`,
            color: row.color ?? TEXT, whiteSpace: 'pre', opacity: fadeAt(frame, row.at, 0.3) * opacity,
          }}
        >
          {row.text}
        </div>
      ))}
    </>
  );
};

// An effect that goes out from (x0, y0) to a handler at (x1, y1) at its second, waits there for hold seconds, and comes
// back as the answer the handler resumes the program with.
const Trip: React.FC<{at: number; hold?: number; x0: number; y0: number; x1: number; y1: number; out: string; back: string; backColor?: string}> = ({
  at, hold = 0.3, x0, y0, x1, y1, out, back, backColor = TEST,
}) => {
  const frame = useSceneFrame();
  const turn = at + 0.6 + hold;
  if (frame < f(at) || frame >= f(turn + 0.8)) return null;
  const going = frame < f(turn);
  const t = going ? ramp(frame, at, at + 0.6) : 1 - ramp(frame, turn, turn + 0.6);
  return (
    <Pill
      x={mix(x0, x1, t)} y={mix(y0, y1, t)} text={going ? out : back} color={going ? ACCENT : backColor} left
      opacity={going ? 1 : 1 - ramp(frame, turn + 0.6, turn + 0.8)}
    />
  );
};

// An effect that goes one way, from (x0, y0) to (x1, y1), and fades out on arrival.
const Fly: React.FC<{at: number; x0: number; y0: number; x1: number; y1: number; text: string; color?: string}> = ({at, x0, y0, x1, y1, text, color = ACCENT}) => {
  const frame = useSceneFrame();
  if (frame < f(at) || frame >= f(at + 1.0)) return null;
  const t = ramp(frame, at, at + 0.7);
  return <Pill x={mix(x0, x1, t)} y={mix(y0, y1, t)} text={text} color={color} left opacity={1 - ramp(frame, at + 0.75, at + 1.0)} />;
};

// Scene 1 — what we build: a Program, its effects, and the handler that meets them.

const TITLE_LINES: Lines = [
  [0, 'Agent Tutorial: an automated code reviewer, written as a doeff program.'],
  [5.1, 'The program yields effects. The handler around it decides how each one is met.'],
  [13.6, 'A review needs four effects: launch a session, await its result, follow up, stop it.'],
];
const TITLE = f(20.4);
const PROGRAM = {x: 120, y: 262, w: 400, h: 150};
const AROUND = {x: 760, y: 262, w: 400, h: 150};
const FOUR = [
  {effect: 'LaunchSession(spec)', asks: 'a session that runs the prompt', answer: 'a SessionHandle'},
  {effect: 'AwaitResult(handle)', asks: 'its result, or why there is none', answer: 'an AwaitOutcome'},
  {effect: 'FollowUp(handle, message)', asks: 'a message sent into it', answer: 'the SessionHandle'},
  {effect: 'StopSession(handle)', asks: 'the session stopped', answer: 'None'},
];

const TitleScene: React.FC = () => {
  const frame = useSceneFrame();
  const handlerOn = frame >= f(7.0) && frame < f(7.6);
  const programOn = frame >= f(5.4) && frame < f(6.1);
  return (
    <AbsoluteFill>
      <Headlines lines={TITLE_LINES} />
      <div style={{position: 'absolute', left: 0, right: 0, top: 104 + mix(16, 0, ramp(frame, 0.2, 0.8)), textAlign: 'center', opacity: fadeAt(frame, 0.2, 0.5)}}>
        <div style={{fontFamily: SANS, fontSize: 64, fontWeight: 700, color: TEXT}}>Agent Tutorial</div>
        <div style={{fontFamily: SANS, fontSize: 26, color: ACCENT, marginTop: 4}}>an automated code reviewer, written as a doeff program</div>
      </div>
      <Box {...PROGRAM} color={ACCENT} on={programOn} opacity={fadeAt(frame, 1.6, 0.5)}>
        <BoxText title="@do review_file(path)" color={ACCENT} on={programOn} lines={['a Program: it says what it needs', 'by yielding effects']} size={18} />
      </Box>
      <Box {...AROUND} color={TEST} on={handlerOn} opacity={fadeAt(frame, 5.3, 0.5)}>
        <BoxText title="the handler around it" color={TEST} on={handlerOn} lines={['decides how each effect is met,', 'then resumes the program']} size={18} />
      </Box>
      <div style={{position: 'absolute', left: PROGRAM.x + PROGRAM.w, top: PROGRAM.y + PROGRAM.h / 2 - 1, width: AROUND.x - PROGRAM.x - PROGRAM.w, height: 2, background: MUTED, opacity: fadeAt(frame, 5.6)}} />
      <Trip at={6.0} hold={0.4} x0={PROGRAM.x + PROGRAM.w - 150} y0={PROGRAM.y + 50} x1={AROUND.x - 20} y1={PROGRAM.y + 50} out="effect" back="value" />
      <Label x={AROUND.x + 20} y={AROUND.y + AROUND.h + 18} size={19} bold color={TEST} opacity={between(frame, 9.0, 13.4)}>
        in a test: a script answers
      </Label>
      <Label x={AROUND.x + 20} y={AROUND.y + AROUND.h + 52} size={19} bold color={PROD} opacity={between(frame, 10.6, 13.4)}>
        in production: Claude Code answers
      </Label>
      <Label x={PROGRAM.x + 20} y={PROGRAM.y + PROGRAM.h + 18} size={18} color={MUTED} opacity={between(frame, 9.0, 13.4)}>
        the same program in both
      </Label>
      {[
        ['the Program yields', 120, ACCENT],
        ['it asks for', 500, MUTED],
        ['the handler resumes it with', 900, TEST],
      ].map(([text, x, color]) => (
        <Label key={text as string} x={x as number} y={444} size={16} bold color={color as string} opacity={fadeAt(frame, 13.8)}>
          {text}
        </Label>
      ))}
      {FOUR.map((e, i) => {
        const y = 480 + i * 52;
        const seen = fadeAt(frame, 14.2 + i * 0.9);
        const on = frame >= f(14.2 + i * 0.9) && frame < f(15.1 + i * 0.9);
        return (
          <React.Fragment key={e.effect}>
            <div
              style={{
                position: 'absolute', left: 110, top: y, width: 1060, height: 44, borderRadius: 10, background: PANEL, boxSizing: 'border-box',
                border: `2px solid ${on ? LOOK : '#2a2e40'}`, opacity: seen,
              }}
            />
            <Label x={130} y={y + 10} size={18} mono bold color={ACCENT} opacity={seen}>{e.effect}</Label>
            <Label x={500} y={y + 11} size={17} color={TEXT} opacity={seen}>{e.asks}</Label>
            <Label x={900} y={y + 10} size={18} mono bold color={TEST} opacity={seen}>{e.answer}</Label>
          </React.Fragment>
        );
      })}
    </AbsoluteFill>
  );
};

// Scene 2 — Step 1: review_file as a Program (measure/review.py).

const PROG_LINES: Lines = [
  [0, 'Step 1: the review as a program. review_file yields the four effects.'],
  [6.5, 'Calling it only builds the program. Nothing runs until a handler answers its effects.'],
  [13.2, 'LaunchSession goes out; the handler resumes the program with a session handle.'],
  [18.9, "AwaitResult waits in one step. Waiting is the handler's job: no polling loop, no sleep."],
  [26.7, 'The outcome comes back: the agent exited, with a result checked against the schema.'],
  [33.0, 'StopSession is in a finally block, so the session is stopped on every path.'],
];
const PROG = f(39.9);
const PROG_CODE = [
  '@do',
  'def review_file(path: str):',
  '    session = yield LaunchSession(review_spec(path))',
  '    try:',
  '        outcome = yield AwaitResult(session, timeout_seconds=600)',
  '        if outcome.status is AwaitStatus.AWAITING_INPUT:',
  '            yield FollowUp(session, "Decide on your own and finish the review.")',
  '            outcome = yield AwaitResult(session, timeout_seconds=600)',
  '        if outcome.result is None or outcome.validation_error is not None:',
  '            reason = outcome.validation_error or outcome.status.value',
  '            raise ReviewFailed(f"{path}: {reason}")',
  '        return outcome.result',
  '    finally:',
  '        yield StopSession(session)',
];
const PC = {x: 40, y: 112, w: 760, size: 14, line: 23};
const HANDLER = {x: 840, y: 112, w: 400, h: PAD * 2 + PROG_CODE.length * PC.line};
// measure/review.log: the Program before any handler runs it, the session id, the answers and the result.
const PROG_PRINTS: Printed[] = [
  {at: 7.0, text: "program = review_file('src/utils.py')", color: TEXT},
  {at: 8.2, text: "type(program).__name__ -> 'Call'   a Program, not run yet", color: MUTED},
  {at: 9.8, text: "review_spec('src/utils.py').session_id -> 'review-src-utils.py-1'", color: MUTED},
  {at: 22.0, text: 'no polling loop and no sleep in the program: the handler waits', color: LOOK},
  {at: 28.4, text: 'outcome.status is AwaitStatus.EXITED, validation_error is None', color: TEXT},
  {at: 35.4, text: "returns {'verdict': 'approve', 'issues': []}; the session is stopped", color: TEST},
];

const ProgramScene: React.FC = () => {
  const frame = useSceneFrame();
  const band = stepAt<number | null>(frame, [
    [0, null], [1.4, 2], [2.4, 4], [3.4, 6], [4.4, 13], [5.6, null],
    [13.4, 2], [19.0, 4], [27.4, 5], [28.2, 8], [29.4, 11], [33.2, 12], [33.8, 13],
  ]);
  const y = (i: number) => lineMid(PC.y, PC.line, i);
  const waiting = frame >= f(20.0) && frame < f(26.9);
  const handlerOn = (frame >= f(14.4) && frame < f(14.8)) || waiting || (frame >= f(34.6) && frame < f(35.0));
  const answer = stepAt<[string, string]>(frame, [
    [0, ['', MUTED]],
    [15.0, ["SessionHandle(\n  'review-src-utils.py-1')", TEST]],
    [20.0, ['waiting on the session:\nwatching it, checking its\nresult against the schema', LOOK]],
    [26.9, ["AwaitOutcome(\n  status=EXITED,\n  result={'verdict': 'approve',\n          'issues': []},\n  validation_error=None)", TEST]],
    [34.6, ['stopped the session\nresumed with None', TEST]],
  ]);
  return (
    <AbsoluteFill>
      <Headlines lines={PROG_LINES} />
      <CodeBlock lines={PROG_CODE} {...PC} band={band} opacity={fadeAt(frame, 0)} />
      <Box {...HANDLER} color={TEST} on={handlerOn} opacity={fadeAt(frame, 0.6)}>
        <div style={{position: 'absolute', left: 18, top: 12, fontFamily: SANS, fontSize: 20, fontWeight: 700, color: handlerOn ? LOOK : TEST}}>the handler around it</div>
        <div style={{position: 'absolute', left: 18, top: 48, fontFamily: SANS, fontSize: 15, color: MUTED}}>answers each effect, resumes the program</div>
        <div key={answer[0]} style={{position: 'absolute', left: 18, top: 124, fontFamily: MONO, fontSize: 17, lineHeight: '27px', color: answer[1], whiteSpace: 'pre'}}>{answer[0]}</div>
      </Box>
      <Trip at={13.8} x0={PC.x + 480} y0={y(2)} x1={HANDLER.x + 20} y1={HANDLER.y + 94} out="LaunchSession(spec)" back="SessionHandle" />
      <Trip at={19.4} hold={6.9} x0={PC.x + 520} y0={y(4)} x1={HANDLER.x + 20} y1={HANDLER.y + 94} out="AwaitResult(session)" back="AwaitOutcome" />
      <Trip at={34.0} x0={PC.x + 360} y0={y(13)} x1={HANDLER.x + 20} y1={HANDLER.y + 94} out="StopSession(session)" back="None" />
      <Prints rows={PROG_PRINTS} x={PC.x} y={492} size={16} line={31} />
    </AbsoluteFill>
  );
};

// Scene 3 — Step 2: scripted_agent, a test handler (measure/review.py).

const HANDLER_LINES: Lines = [
  [0, 'Step 2: a test handler. @handler makes a function of the effect and k a handler.'],
  [7.9, 'k is the rest of the program. Resume(k, value) goes on with value as the result of the yield.'],
  [16.0, 'The annotation names the effects it takes. Any other effect goes past, to the next handler out.'],
  [22.8, 'Under scripted_agent the review returns approve; the log reads launch, await, stop.'],
  [30.5, 'review_file did not change for the test. The test chose the handler.'],
];
const HANDLER_SCENE = f(35.5);
const HANDLER_CODE = [
  '@handler',
  'def answer(effect: SessionEffect, k):',
  '    if isinstance(effect, LaunchSessionEffect):',
  '        log.append(f"launch {effect.spec.session_id}")',
  '        return (yield Resume(k, SessionHandle(...)))',
  '    session_id = effect.handle.session_id',
  '    if isinstance(effect, AwaitResultEffect):',
  '        outcome = pending[session_id].pop(0)',
  '        log.append(f"await {session_id}: {outcome.status.value}")',
  '        return (yield Resume(k, outcome))',
  '    ...  # FollowUpEffect: log it, resume with the handle',
  '    log.append(f"stop {session_id}")',
  '    return (yield Resume(k, None))',
];
const HC = {x: 40, y: 112, w: 700, size: 15, line: 24};
const PROGRAM_PART = [
  'session = yield LaunchSession(...)',
  'try:',
  '    outcome = yield AwaitResult(...)',
  '    ...',
  '    return outcome.result',
  'finally:',
  '    yield StopSession(session)',
];
const PM = {x: 780, y: 112, w: 460, size: 15, line: 24};
// measure/review.log: the run under scripted_agent.
const RUN_PRINTS: Printed[] = [
  {at: 23.0, text: 'run(agent(review_file("src/utils.py")))', color: TEXT},
  {at: 24.0, text: "-> {'verdict': 'approve', 'issues': []}", color: TEST},
  {at: 25.0, text: 'log: launch review-src-utils.py-1', color: MUTED},
  {at: 25.8, text: '     await review-src-utils.py-1: exited', color: MUTED},
  {at: 26.6, text: '     stop review-src-utils.py-1', color: MUTED},
];

const HandlerScene: React.FC = () => {
  const frame = useSceneFrame();
  const band = stepAt<number | null>(frame, [[0, null], [1.0, 0], [2.6, 1], [8.6, 4], [16.2, 1], [22.8, null], [25.0, 3], [25.8, 8], [26.6, 11], [28.0, null]]);
  const programBand = stepAt<number | null>(frame, [[0, null], [8.2, 0], [12.4, 2], [15.6, null]]);
  const k = between(frame, 8.6, 22.6);
  const resuming = frame >= f(10.6) && frame < f(12.4);
  const t = ramp(frame, 10.6, 11.6);
  const passing = ramp(frame, 18.8, 20.6);
  return (
    <AbsoluteFill>
      <Headlines lines={HANDLER_LINES} />
      <CodeBlock lines={HANDLER_CODE} {...HC} band={band} opacity={fadeAt(frame, 0)} />
      <Label x={PM.x} y={PM.y + PAD * 2 + PROGRAM_PART.length * PM.line + 8} size={16} bold color={ACCENT} opacity={fadeAt(frame, 1.6)}>
        review_file, paused at its first yield
      </Label>
      <CodeBlock lines={PROGRAM_PART} {...PM} band={programBand} opacity={fadeAt(frame, 1.4)} />
      <div
        style={{
          position: 'absolute', left: PM.x + 6, top: PM.y + PAD + PM.line - 2, width: PM.w - 12, height: (PROGRAM_PART.length - 1) * PM.line + 4,
          borderRadius: 8, boxSizing: 'border-box', border: `2px dashed ${LOOK}`, opacity: k,
        }}
      />
      <Label x={PM.x} y={PM.y + PAD * 2 + PROGRAM_PART.length * PM.line + 40} size={19} bold color={LOOK} opacity={k}>
        k: the rest of the program
      </Label>
      {resuming && (
        <Pill
          x={mix(HC.x + 440, PM.x + 250, t)} y={mix(lineMid(HC.y, HC.line, 4), lineMid(PM.y, PM.line, 0), t)}
          text="Resume(k, SessionHandle)" color={TEST} left opacity={1 - ramp(frame, 12.0, 12.4)}
        />
      )}
      <Label x={PM.x} y={PM.y + PAD * 2 + PROGRAM_PART.length * PM.line + 72} size={16} color={MUTED} opacity={between(frame, 12.4, 22.6)}>
        {'the yield evaluates to the SessionHandle,\nand the program goes on to AwaitResult'}
      </Label>
      <Label x={HC.x} y={476} size={14} mono color={TEXT} opacity={between(frame, 16.2, 22.6)}>
        SessionEffect = LaunchSessionEffect | AwaitResultEffect | FollowUpEffect | StopSessionEffect
      </Label>
      <Label x={HC.x} y={512} size={17} color={MUTED} opacity={between(frame, 16.8, 22.6)}>
        the effects in the annotation come to answer; any other effect goes past it:
      </Label>
      <div
        style={{
          position: 'absolute', left: 520, top: 560, width: 220, height: 80, borderRadius: 14, boxSizing: 'border-box', background: PANEL,
          border: `3px solid ${TEST}`, opacity: between(frame, 17.4, 22.6),
        }}
      >
        <div style={{position: 'absolute', left: 0, right: 0, top: 24, textAlign: 'center', fontFamily: MONO, fontSize: 19, fontWeight: 700, color: TEST}}>answer</div>
      </div>
      <Label x={780} y={586} size={17} color={MUTED} opacity={between(frame, 20.4, 22.6)}>
        to the next handler out
      </Label>
      {frame >= f(18.8) && frame < f(22.6) && (
        <Pill x={mix(HC.x + 20, 1000, passing)} y={600} text="Spawn(...)" color={ACCENT} left opacity={between(frame, 18.8, 22.4)} />
      )}
      <Prints rows={RUN_PRINTS} x={HC.x} y={480} size={17} line={36} opacity={1 - fadeAt(frame, 35.0)} />
      <Label x={PM.x} y={500} size={24} bold color={ACCENT} opacity={fadeAt(frame, 30.8)}>
        review_file: unchanged
      </Label>
      <Label x={PM.x} y={544} size={24} bold color={TEST} opacity={fadeAt(frame, 32.2)}>
        the test chose the handler
      </Label>
    </AbsoluteFill>
  );
};

// Scene 4 — Step 3: the other outcomes the script plays (measure/failures.py).

const FAIL_LINES: Lines = [
  [0, 'Step 3: when the agent does not finish, the script plays the other outcomes.'],
  [5.3, 'The agent stops to ask: a FollowUp, another AwaitResult, and the review comes back.'],
  [12.7, 'Nothing comes back within the timeout: review_file raises ReviewFailed.'],
  [19.0, 'A result that does not match the schema comes back with the reason: it raises too.'],
  [25.2, 'On every path, the finally block stops the session.'],
];
const FAILURES = f(30.0);
// measure/failures.log: each script, the effects scripted_agent answered in order, and what review_file did.
const CASES = [
  {
    title: 'the agent asks first', color: PROD, script: ['outcomes: [asks, approve]', 'asks = AWAITING_INPUT'], at: 5.6,
    log: ['launch review-src-api.py-1', 'await …: awaiting-input', 'follow-up review-src-api.py-1', 'await …: exited', 'stop review-src-api.py-1'],
    end: "returns {'verdict': 'approve',\n         'issues': []}", endColor: TEST,
  },
  {
    title: 'nothing within the timeout', color: FAIL, script: ['outcomes: [slow]', 'slow = TIMED_OUT'], at: 13.0,
    log: ['launch review-src-slow.py-1', 'await …: timed-out', 'stop review-src-slow.py-1'],
    end: 'raises ReviewFailed:\nsrc/slow.py: timed-out', endColor: FAIL,
  },
  {
    title: 'a result off the schema', color: FAIL, script: ["outcomes: [wrong]", "wrong = EXITED, verdict 'maybe'"], at: 19.3,
    log: ['launch review-src-odd.py-1', 'await …: exited', 'stop review-src-odd.py-1'],
    end: "raises ReviewFailed:\nsrc/odd.py: 'maybe' is not one\nof ['approve', 'request-changes']", endColor: FAIL,
  },
];
const CARD = {y: 112, w: 390, h: 486, gap: 15};
const cardX = (i: number) => 40 + i * (CARD.w + CARD.gap);
const STEP = 0.9;

const FailureScene: React.FC = () => {
  const frame = useSceneFrame();
  const stops = frame >= f(25.4);
  return (
    <AbsoluteFill>
      <Headlines lines={FAIL_LINES} />
      {CASES.map((c, i) => {
        const x = cardX(i);
        const ends = c.at + c.log.length * STEP;
        const on = frame >= f(c.at) && frame < f(ends + 0.8);
        return (
          <React.Fragment key={c.title}>
            <Box x={x} y={CARD.y} w={CARD.w} h={CARD.h} color={c.color} on={on} opacity={fadeAt(frame, 0.6 + i * 0.6)}>
              <div style={{position: 'absolute', left: 18, top: 12, fontFamily: SANS, fontSize: 21, fontWeight: 700, color: on ? LOOK : c.color}}>{c.title}</div>
              <div style={{position: 'absolute', left: 18, top: 50, fontFamily: MONO, fontSize: 14, lineHeight: '22px', color: MUTED, whiteSpace: 'pre'}}>{c.script.join('\n')}</div>
              <div style={{position: 'absolute', left: 18, top: 110, fontFamily: SANS, fontSize: 14, fontWeight: 700, color: MUTED}}>scripted_agent&apos;s log</div>
            </Box>
            {c.log.map((line, j) => {
              const at = c.at + j * STEP;
              const isStop = line.startsWith('stop');
              const lit = (isStop && stops) || line.startsWith('follow-up');
              return (
                <div
                  key={j}
                  style={{
                    position: 'absolute', left: x + 14, top: CARD.y + 138 + j * 34, width: CARD.w - 28, height: 30, boxSizing: 'border-box', borderRadius: 8,
                    border: `2px solid ${lit ? (isStop ? TEST : PROD) : 'transparent'}`, boxShadow: isStop && stops ? `0 0 12px ${TEST}` : 'none',
                    fontFamily: MONO, fontSize: 14, lineHeight: '26px', paddingLeft: 8, color: isStop && stops ? TEST : TEXT, whiteSpace: 'nowrap', opacity: fadeAt(frame, at),
                  }}
                >
                  {line}
                </div>
              );
            })}
            <div
              style={{
                position: 'absolute', left: x + 22, top: CARD.y + 330, fontFamily: MONO, fontSize: 15, lineHeight: '24px', fontWeight: 700, color: c.endColor,
                whiteSpace: 'pre', opacity: fadeAt(frame, ends + 0.2),
              }}
            >
              {c.end}
            </div>
          </React.Fragment>
        );
      })}
      <Label x={640} y={622} size={22} bold color={TEST} center opacity={fadeAt(frame, 25.8)}>
        finally: yield StopSession(session) — three paths, three sessions stopped
      </Label>
    </AbsoluteFill>
  );
};

// Scene 5 — Step 4: several files, under three handlers (measure/batch.py).

const BATCH_LINES: Lines = [
  [0, 'Step 4: several files. Spawn starts a task per file; Gather collects the results in order.'],
  [8.3, 'Each effect goes to its own handler: scheduled, try_handler, scripted_agent.'],
  [14.8, 'A semaphore of 2: at most two sessions at once. The scripted agent answers at once, so they run one by one.'],
  [24.3, 'Try turns the timeout of b.py into an Err, so one failure does not stop the others.'],
  [32.0, 'All three sessions are stopped, the failed one too.'],
];
const BATCH = f(36.6);
const BATCH_CODE = [
  '@do',
  'def review_all(paths, at_most):',
  '    sessions = yield CreateSemaphore(at_most)',
  ' ',
  '    @do',
  '    def one(path):',
  '        yield AcquireSemaphore(sessions)',
  '        try:',
  '            return (yield review_file(path))',
  '        finally:',
  '            yield ReleaseSemaphore(sessions)',
  ' ',
  '    tasks = []',
  '    for path in paths:',
  '        tasks.append((yield Spawn(Try(one(path)))))',
  '    return (yield Gather(*tasks))',
];
const BC = {x: 40, y: 112, w: 540, size: 14, line: 22};
const STACK = [
  {name: 'scheduled', takes: 'Spawn · Gather · CreateSemaphore · Acquire/Release', color: MUTED, x: 620, y: 112, w: 620, h: 300, at: 9.5},
  {name: 'try_handler', takes: 'Try', color: MUTED, x: 640, y: 186, w: 580, h: 210, at: 11.0},
  {name: 'scripted_agent(outcomes, log)', takes: 'LaunchSession · AwaitResult · FollowUp · StopSession', color: TEST, x: 660, y: 260, w: 540, h: 120, at: 12.5},
];
// measure/batch.log: the sessions in the order scripted_agent answered them, and the three results.
const SESSIONS = [
  {name: 'a.py', said: 'launch · await: exited · stop', color: ACCENT},
  {name: 'b.py', said: 'launch · await: timed-out · stop', color: FAIL},
  {name: 'c.py', said: 'launch · await: exited · stop', color: ACCENT},
];
const LANES = {x0: 150, x1: 1240, y: 552, h: 40, gap: 10};
const RESULTS: Printed[] = [
  {at: 25.2, text: "a: Ok({'verdict': 'approve', 'issues': []})", color: TEST},
  {at: 26.0, text: "b: Err(ReviewFailed('b.py: timed-out'))", color: FAIL},
  {at: 26.8, text: "c: Ok({'verdict': 'approve', 'issues': []})", color: TEST},
];

const BatchScene: React.FC = () => {
  const frame = useSceneFrame();
  const y = (i: number) => lineMid(BC.y, BC.line, i);
  const band = stepAt<number | null>(frame, [[0, null], [1.4, 14], [4.0, 15], [6.0, null], [9.0, 14], [12.2, 8], [15.0, 2], [16.4, 6], [24.5, 14], [26.0, 15], [32.2, 9]]);
  const sweep = (frame - f(16.4)) / f(6.0);
  const cell = (LANES.x1 - LANES.x0) / 3;
  return (
    <AbsoluteFill>
      <Headlines lines={BATCH_LINES} />
      <CodeBlock lines={BATCH_CODE} {...BC} band={band} opacity={fadeAt(frame, 0)} />
      {STACK.map((s) => {
        const on = frame >= f(s.at + 0.6) && frame < f(s.at + 1.3);
        return (
          <Box key={s.name} x={s.x} y={s.y} w={s.w} h={s.h} color={s.color} on={on} opacity={fadeAt(frame, 2.0)}>
            <div style={{position: 'absolute', left: 16, top: 8, fontFamily: MONO, fontSize: 17, fontWeight: 700, color: on ? LOOK : s.color === MUTED ? TEXT : s.color}}>{s.name}</div>
            <div style={{position: 'absolute', left: 16, top: 34, fontFamily: MONO, fontSize: 13, color: MUTED, whiteSpace: 'nowrap'}}>{`takes: ${s.takes}`}</div>
          </Box>
        );
      })}
      <Label x={680} y={326} size={16} mono bold color={ACCENT} opacity={fadeAt(frame, 2.6)}>
        review_all(["a.py", "b.py", "c.py"], 2)
      </Label>
      <Fly at={STACK[0].at} x0={BC.x + 380} y0={y(14)} x1={STACK[0].x + 300} y1={STACK[0].y + 16} text="Spawn" />
      <Fly at={STACK[1].at} x0={BC.x + 380} y0={y(14)} x1={STACK[1].x + 300} y1={STACK[1].y + 16} text="Try" />
      <Fly at={STACK[2].at} x0={BC.x + 380} y0={y(8)} x1={STACK[2].x + 360} y1={STACK[2].y + 16} text="LaunchSession" />
      <Prints rows={RESULTS} x={640} y={420} size={15} line={26} />
      <Label x={40} y={514} size={16} color={MUTED} opacity={fadeAt(frame, 15.2)}>
        the semaphore&apos;s two slots, in the order scripted_agent answered the effects
      </Label>
      {['slot 1', 'slot 2'].map((lane, i) => (
        <React.Fragment key={lane}>
          <Label x={40} y={LANES.y + i * (LANES.h + LANES.gap) + 9} size={16} color={MUTED} opacity={fadeAt(frame, 15.4)}>{lane}</Label>
          <div style={{position: 'absolute', left: LANES.x0, top: LANES.y + i * (LANES.h + LANES.gap), width: LANES.x1 - LANES.x0, height: LANES.h, background: '#141722', opacity: fadeAt(frame, 15.4)}} />
        </React.Fragment>
      ))}
      <Label x={LANES.x0 + 16} y={LANES.y + LANES.h + LANES.gap + 10} size={15} color={MUTED} opacity={fadeAt(frame, 22.6)}>
        not used: each session was stopped before the next one was launched
      </Label>
      {SESSIONS.map((s, i) => {
        const shown = Math.max(0, Math.min(1, sweep * 3 - i));
        if (shown <= 0) return null;
        const failed = s.name === 'b.py' && frame >= f(24.5);
        const stopped = frame >= f(32.2);
        return (
          <div
            key={s.name}
            style={{
              position: 'absolute', left: LANES.x0 + i * cell + 2, top: LANES.y, width: (cell - 4) * shown, height: LANES.h, boxSizing: 'border-box', borderRadius: 6,
              background: '#1d1a33', border: `2px solid ${stopped ? TEST : failed ? FAIL : s.color}`, boxShadow: stopped || failed ? `0 0 12px ${stopped ? TEST : FAIL}` : 'none',
              overflow: 'hidden',
            }}
          >
            <div style={{position: 'absolute', left: 10, top: 9, fontFamily: MONO, fontSize: 15, fontWeight: 700, color: s.name === 'b.py' ? FAIL : TEXT, whiteSpace: 'nowrap'}}>
              {`${s.name}: ${s.said}`}
            </div>
          </div>
        );
      })}
      <Label x={40} y={668} size={18} mono bold color={LOOK} opacity={fadeAt(frame, 22.4)}>
        most sessions open at once: 1
      </Label>
      <Label x={520} y={668} size={18} mono bold color={TEST} opacity={fadeAt(frame, 32.4)}>
        stop review-a.py-1 · stop review-b.py-1 · stop review-c.py-1
      </Label>
    </AbsoluteFill>
  );
};

// Scene 6 — Steps 5 and 6: the packaged test handler (measure/scenario.py), and the production handlers.

const SWAP_LINES: Lines = [
  [0, 'Step 5: doeff-agents ships a test handler of the same kind, made by scenario_agent_handler.'],
  [7.6, 'The same review_file returns request-changes, with one issue, from the scripted step.'],
  [14.2, 'Step 6: in production, daemon_agent_handlers() sends the same effects to the session host.'],
  [20.6, 'The host starts Claude Code in a terminal, watches it, checks its result, and stops it.'],
  [27.5, 'The programs never changed. Only the handlers did.'],
];
const SWAP = f(32.4);
const SCENARIO_CODE = [
  'scenario = scenario_agent_handler(',
  '    scripts={',
  '        "review-src-utils.py-1": [',
  '            ScenarioStep.success({"verdict": "request-changes",',
  '                                  "issues": ["unused import"]}),',
  '        ],',
  '    },',
  ')',
  'run(scenario(review_file("src/utils.py")))',
];
const DAEMON_CODE = [
  'time_handler, agent_handler = daemon_agent_handlers()',
  'program = time_handler(agent_handler(',
  '    try_handler(review_all(sys.argv[1:], 2))))',
  'run(scheduled(program))',
];
const ZC = {x: 40, y: 112, w: 720, size: 15, line: 24};
// measure/scenario.log.
const SCENARIO_PRINTS: Printed[] = [
  {at: 8.2, text: "-> {'verdict': 'request-changes', 'issues': ['unused import']}", color: TEST},
];
const CHAIN = [
  {name: 'review_all', sub: 'the same Program', color: ACCENT, x: 40, w: 250},
  {name: 'agent_handler', sub: 'from daemon_agent_handlers()', color: PROD, x: 350, w: 280},
  {name: 'doeff-sessionhost', sub: 'owns the sessions', color: PROD, x: 690, w: 260},
  {name: 'Claude Code', sub: 'in a tmux terminal', color: PROD, x: 1010, w: 230},
];
const CHAIN_Y = 290;
const HOST = ['starts Claude Code in a terminal', 'watches it', 'checks the result against the schema', 'stops it'];
const RUNS = [
  ['a test that sees every effect', 'scripted_agent(outcomes, log)', TEST],
  ['a test with a packaged script', 'scenario_agent_handler(scripts=...)', TEST],
  ['production, through the session host', 'daemon_agent_handlers()', PROD],
  ['production, from the calling process', 'agent_effectful_handlers()', PROD],
];

const SwapScene: React.FC = () => {
  const frame = useSceneFrame();
  const step5 = between(frame, 0, 14.0);
  const step6 = between(frame, 14.2, 27.3);
  const table = fadeAt(frame, 27.6);
  const scenarioOn = frame >= f(8.0) && frame < f(8.8);
  const goes = ramp(frame, 15.6, 18.0);
  return (
    <AbsoluteFill>
      <Headlines lines={SWAP_LINES} />
      <CodeBlock lines={SCENARIO_CODE} {...ZC} band={stepAt<number | null>(frame, [[0, null], [1.4, 0], [3.0, 3], [8.0, 8]])} opacity={step5} />
      <Box x={800} y={112} w={440} h={240} color={TEST} on={scenarioOn} opacity={step5 * fadeAt(frame, 1.0)}>
        <BoxText title="scenario_agent_handler" color={TEST} on={scenarioOn} lines={['in doeff_agents: makes a test handler', 'from a script, one ScenarioStep', 'per AwaitResult; install it', 'around a Program like any handler']} size={17} />
      </Box>
      <Prints rows={SCENARIO_PRINTS} x={ZC.x} y={ZC.y + PAD * 2 + SCENARIO_CODE.length * ZC.line + 30} size={17} line={36} opacity={step5} />
      <CodeBlock lines={DAEMON_CODE} {...ZC} band={stepAt<number | null>(frame, [[0, null], [14.6, 0], [16.0, 1]])} opacity={step6} />
      <Label x={800} y={124} size={17} color={MUTED} opacity={step6}>
        {'in place of scripted_agent,\nthe production handlers;\nthe time handler goes outside'}
      </Label>
      {CHAIN.map((c, i) => {
        const on = (i === 1 && frame >= f(16.4) && frame < f(17.0)) || (i === 2 && frame >= f(17.4) && frame < f(18.4)) || (i === 3 && frame >= f(21.0) && frame < f(26.6));
        return (
          <React.Fragment key={c.name}>
            <Box x={c.x} y={CHAIN_Y} w={c.w} h={100} color={c.color} on={on} dashed={i === 3} opacity={step6 * fadeAt(frame, 14.6 + i * 0.4)}>
              <div style={{position: 'absolute', left: 0, right: 0, top: 18, textAlign: 'center', fontFamily: MONO, fontSize: 19, fontWeight: 700, color: on ? LOOK : c.color}}>{c.name}</div>
              <div style={{position: 'absolute', left: 0, right: 0, top: 56, textAlign: 'center', fontFamily: SANS, fontSize: 15, color: MUTED}}>{c.sub}</div>
            </Box>
            {i > 0 && <div style={{position: 'absolute', left: CHAIN[i - 1].x + CHAIN[i - 1].w, top: CHAIN_Y + 49, width: c.x - CHAIN[i - 1].x - CHAIN[i - 1].w, height: 2, background: MUTED, opacity: step6}} />}
          </React.Fragment>
        );
      })}
      {frame >= f(15.6) && frame < f(18.4) && (
        <Pill x={mix(CHAIN[0].x + 120, CHAIN[2].x + 40, goes)} y={CHAIN_Y - 22} text="LaunchSession(spec)" color={ACCENT} left opacity={1 - ramp(frame, 18.0, 18.4)} />
      )}
      {HOST.map((text, i) => {
        const at = 21.0 + i * 1.4;
        const on = frame >= f(at) && frame < f(at + 1.4);
        return (
          <div
            key={text}
            style={{
              position: 'absolute', left: 40 + i * 302, top: 440, width: 290, height: 64, boxSizing: 'border-box', borderRadius: 12, background: PANEL,
              border: `2px solid ${PROD}`, ...glow(on, PROD), fontFamily: SANS, fontSize: 16, fontWeight: 700, color: on ? LOOK : TEXT, textAlign: 'center',
              lineHeight: '22px', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '0 14px', opacity: step6 * fadeAt(frame, at - 0.2),
            }}
          >
            {text}
          </div>
        );
      })}
      <Label x={640} y={540} size={18} color={MUTED} center opacity={step6 * fadeAt(frame, 26.6)}>
        A session lives on even when the program that launched it exits.
      </Label>
      <Label x={110} y={120} size={17} bold color={MUTED} opacity={table}>the run</Label>
      <Label x={660} y={120} size={17} bold color={MUTED} opacity={table}>the handlers around the Program</Label>
      {RUNS.map(([run, handlers, color], i) => (
        <React.Fragment key={run}>
          <div style={{position: 'absolute', left: 90, top: 160 + i * 72, width: 1100, height: 60, borderRadius: 12, background: PANEL, border: '2px solid #2a2e40', opacity: table * fadeAt(frame, 27.8 + i * 0.3)}} />
          <Label x={110} y={177 + i * 72} size={19} color={TEXT} opacity={table * fadeAt(frame, 27.8 + i * 0.3)}>{run}</Label>
          <Label x={660} y={177 + i * 72} size={19} mono bold color={color} opacity={table * fadeAt(frame, 27.8 + i * 0.3)}>{handlers}</Label>
        </React.Fragment>
      ))}
      <Label x={640} y={480} size={24} bold color={ACCENT} center opacity={fadeAt(frame, 29.4)}>
        review_file and review_all: the same in every run
      </Label>
    </AbsoluteFill>
  );
};

// The outro.

const OUTRO_LINES: Lines = [[0, 'The program says what it needs. The handler decides how: a script in a test, Claude Code in production.']];
const OUTRO = f(7.1);
const CHOICES = [
  {name: 'scripted_agent', sub: 'a script, in a test', color: TEST},
  {name: 'scenario_agent_handler', sub: 'a packaged script, in a test', color: TEST},
  {name: 'daemon_agent_handlers()', sub: 'Claude Code, in production', color: PROD},
];

// How far below the Program's middle the line to choice i ends.
const rise = (i: number) => 140 + i * 100 + 38 - 264;

const OutroScene: React.FC = () => {
  const frame = useSceneFrame();
  const lit = Math.floor(Math.max(0, frame - f(1.2)) / f(0.8));
  return (
    <AbsoluteFill>
      <Headlines lines={OUTRO_LINES} />
      <Box x={140} y={200} w={380} h={130} color={ACCENT} opacity={fadeAt(frame, 0.2)}>
        <BoxText title="review_file · review_all" color={ACCENT} lines={['@do Programs that yield', 'doeff-agents effects']} size={18} />
      </Box>
      {CHOICES.map((c, i) => {
        const on = lit === i;
        return (
          <React.Fragment key={c.name}>
            <div
              style={{
                position: 'absolute', left: 520, top: 264, width: Math.hypot(240, rise(i)), height: 2, background: on ? LOOK : MUTED, transformOrigin: '0 50%',
                transform: `rotate(${Math.atan2(rise(i), 240) * (180 / Math.PI)}deg)`,
                opacity: fadeAt(frame, 0.6 + i * 0.3),
              }}
            />
            <Box x={760} y={140 + i * 100} w={400} h={76} color={c.color} on={on} opacity={fadeAt(frame, 0.6 + i * 0.3)}>
              <div style={{position: 'absolute', left: 18, top: 10, fontFamily: MONO, fontSize: 19, fontWeight: 700, color: on ? LOOK : c.color}}>{c.name}</div>
              <div style={{position: 'absolute', left: 18, top: 42, fontFamily: SANS, fontSize: 15, color: MUTED}}>{c.sub}</div>
            </Box>
          </React.Fragment>
        );
      })}
      <div style={{position: 'absolute', left: 0, right: 0, top: 490 + mix(16, 0, ramp(frame, 3.6, 4.2)), textAlign: 'center', opacity: fadeAt(frame, 3.6, 0.5)}}>
        <div style={{fontFamily: SANS, fontSize: 60, fontWeight: 700, color: TEXT}}>doeff</div>
        <div style={{fontFamily: SANS, fontSize: 28, color: ACCENT, marginTop: 4}}>Algebraic effects for Python</div>
      </div>
    </AbsoluteFill>
  );
};

// The mp4 must stay within 6 MB (tests/test_docs_site_videos.py), so no headline holds the screen longer than its
// line needs: each slot is cut to LEAD + line + TAIL, and the scene's design time is mapped onto the cut slots (the
// motion inside a cut slot runs faster). A slot shorter than its line is still stretched by narrate.
const said = DURATIONS as Record<string, number>;

// Lets a scene keep its design times when its slots are cut: its frames go through toDesign after narrate's warp.
const Retimed: React.FC<{toDesign: (frame: number) => number; children: React.ReactNode}> = ({toDesign, children}) => {
  const outer = React.useContext(WarpContext);
  return <WarpContext.Provider value={(real) => toDesign(outer(real))}>{children}</WarpContext.Provider>;
};

const tight = (scene: Scene): Scene => {
  const ends = scene.starts.map((from, i) => (i + 1 < scene.starts.length ? scene.starts[i + 1] : scene.length));
  const lengths = scene.starts.map((from, i) => {
    const seconds = said[scene.ids[i]];
    return seconds ? Math.min(ends[i] - from, f(LEAD + seconds + TAIL)) : ends[i] - from;
  });
  const starts = lengths.map((_, i) => lengths.slice(0, i).reduce((sum, length) => sum + length, 0));
  const toDesign = (frame: number) => {
    const i = starts.reduce((found, start, j) => (frame >= start ? j : found), 0);
    return scene.starts[i] + ((frame - starts[i]) * (ends[i] - scene.starts[i])) / lengths[i];
  };
  return {...scene, starts, length: lengths.reduce((sum, length) => sum + length, 0), node: <Retimed toDesign={toDesign}>{scene.node}</Retimed>};
};

// The scenes in order; Root.tsx registers AGENT_TUTORIAL.length frames of AGENT_TUTORIAL.Video.
export const AGENT_TUTORIAL = narrate('agent-tutorial', DURATIONS, [
  {length: TITLE, starts: startsOf(TITLE_LINES), ids: ['t1', 't2', 't3'], node: <TitleScene />},
  {length: PROG, starts: startsOf(PROG_LINES), ids: ['p1', 'p2', 'p3', 'p4', 'p5', 'p6'], node: <ProgramScene />},
  {length: HANDLER_SCENE, starts: startsOf(HANDLER_LINES), ids: ['h1', 'h2', 'h3', 'h4', 'h5'], node: <HandlerScene />},
  {length: FAILURES, starts: startsOf(FAIL_LINES), ids: ['f1', 'f2', 'f3', 'f4', 'f5'], node: <FailureScene />},
  {length: BATCH, starts: startsOf(BATCH_LINES), ids: ['b1', 'b2', 'b3', 'b4', 'b5'], node: <BatchScene />},
  {length: SWAP, starts: startsOf(SWAP_LINES), ids: ['z1', 'z2', 'z3', 'z4', 'z5'], node: <SwapScene />},
  {length: OUTRO, starts: startsOf(OUTRO_LINES), ids: ['o1'], node: <OutroScene />},
].map(tight));
