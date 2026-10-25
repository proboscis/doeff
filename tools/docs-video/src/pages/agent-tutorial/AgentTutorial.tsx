// The video on the Agent Tutorial page (docs/19-agent-tutorial.md): the code review system the page builds on
// doeff-agents, step by step, in motion. Seven scenes, each with headlines a synthetic voice reads (narration.json):
//   1. What we build: a script that drives a coding agent in a terminal session; a scripted terminal plays Claude.
//   2. Step 1: launch_session, monitor_session until a terminal status, capture_output, stop_session.
//   3. Under launch_session: a doeff program whose effects a handler answers; without a login it raises.
//   4. Step 2: session_scope stops the session however the with block is left.
//   5. Step 3: on_status_change runs on changes only; BLOCKED waits for input, answered with send_message.
//   6. Step 4: a Semaphore limits the batch; gather keeps the order of the files.
//   7. The outro: Step 5 puts the steps together; the driver underneath is effects and handlers.
// Every status, time, effect and order on screen is printed by a program in measure/ (its .log is next to it). The
// programs run the page's code against measure/stand_ins.py: a scripted terminal that plays Claude Code's screens
// and an in-memory world, so no run needs tmux, Claude Code or a login.
import React from 'react';
import {AbsoluteFill, interpolate} from 'remotion';
import {clamp, fadeAt, mix, ramp} from '../../kit/motion';
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

// The scripted terminal of measure/stand_ins.py: a pane with a dashed test-coloured border, its title, and the screen.
// A line given as [text, colour] is drawn in that colour (the part of the screen the driver is reading).
type ScreenLine = string | [string, string];
const Terminal: React.FC<{x: number; y: number; w: number; h: number; title: string; lines: ScreenLine[]; size?: number; line?: number; on?: boolean; opacity?: number}> = ({
  x, y, w, h, title, lines, size = 13, line = 20, on = false, opacity = 1,
}) => (
  <Box x={x} y={y} w={w} h={h} color={TEST} on={on} dashed opacity={opacity}>
    <div style={{position: 'absolute', left: 14, top: 8, fontFamily: SANS, fontSize: 15, fontWeight: 700, color: on ? LOOK : TEST, whiteSpace: 'nowrap'}}>{title}</div>
    <div style={{position: 'absolute', left: 14, right: 14, top: 36, bottom: 10, background: '#0b0d13', borderRadius: 8, padding: '8px 10px', boxSizing: 'border-box', overflow: 'hidden'}}>
      {lines.map((text, i) => {
        const [body, color] = typeof text === 'string' ? [text, TEXT] : text;
        return (
          <div key={i} style={{fontFamily: MONO, fontSize: size, lineHeight: `${line}px`, color, whiteSpace: 'pre'}}>
            {body}
          </div>
        );
      })}
    </div>
  </Box>
);

// Printed lines of a run: each appears at its second, in its colour.
type Printed = {at: number; text: string; color?: string};
const Prints: React.FC<{rows: Printed[]; x: number; y: number; size?: number; line?: number}> = ({rows, x, y, size = 15, line = 26}) => {
  const frame = useSceneFrame();
  return (
    <>
      {rows.map((row, i) => (
        <div
          key={i}
          style={{
            position: 'absolute', left: x, top: y + i * line + mix(8, 0, ramp(frame, row.at, row.at + 0.3)), fontFamily: MONO, fontSize: size, lineHeight: `${line}px`,
            color: row.color ?? TEXT, whiteSpace: 'pre', opacity: fadeAt(frame, row.at, 0.3),
          }}
        >
          {row.text}
        </div>
      ))}
    </>
  );
};

// What the scripted Claude Code shows (measure/stand_ins.py) while it reviews src/utils.py, by screen.
const ASKED = '❯ Review the code in src/utils.py';
const READY: ScreenLine[] = ['[Screen Reader Mode: on via flag]', 'Claude Code v2.1.209', '❯ ', ['auto mode on (shift+tab to cycle)', LOOK]];
const working = (seconds: number, look: boolean): ScreenLine[] => [
  ASKED,
  [`✻ Reviewing src/utils.py… (${seconds}s · esc to interrupt)`, look ? LOOK : TEXT],
  'auto mode on (shift+tab to cycle)',
];
const REVIEWED: ScreenLine[] = [
  ASKED,
  '⏺ Review of src/utils.py',
  '  1. parse_port() accepts 0; reject ports outside 1-65535.',
  '  2. load_config() hides FileNotFoundError; name the missing file.',
  '  3. retry() always sleeps 1 s; back off instead.',
];

// Scene 1 — what we build.

const TITLE_LINES: Lines = [
  [0, 'Agent Tutorial: a code review system on doeff-agents, which drives Claude Code in a terminal session.'],
  [12.5, "A scripted terminal plays Claude's screens: the runs shown need no tmux, no Claude, no login."],
];
const TITLE = f(21);
const PARTS = [
  {name: 'your script', color: ACCENT, dashed: false, about: ['review_file(path)', 'launch · monitor', 'capture · stop']},
  {name: 'terminal session', color: MUTED, dashed: false, about: ['a tmux pane:', 'the agent reads it,', 'the script watches it']},
  {name: 'Claude Code', color: TEST, dashed: true, about: ['reads the prompt,', 'reviews the code,', 'prints its review']},
];
const PART = {y: 320, w: 330, h: 190, gap: 70};
const partX = (i: number) => 640 - (3 * PART.w + 2 * PART.gap) / 2 + i * (PART.w + PART.gap);

const TitleScene: React.FC = () => {
  const frame = useSceneFrame();
  const on = [frame >= f(5.6) && frame < f(7.4), frame >= f(7.4) && frame < f(9.4), frame >= f(9.4) && frame < f(11.4) || frame >= f(12.9)];
  const midY = PART.y + PART.h / 2;
  const out = ramp(frame, 7.0, 9.0);
  const back = ramp(frame, 9.6, 11.6);
  const left = partX(0) + PART.w;
  const right = partX(2);
  const scripted = frame >= f(12.9);
  return (
    <AbsoluteFill>
      <Headlines lines={TITLE_LINES} />
      <div style={{position: 'absolute', left: 0, right: 0, top: 112 + mix(16, 0, ramp(frame, 0.2, 0.8)), textAlign: 'center', opacity: fadeAt(frame, 0.2, 0.5)}}>
        <div style={{fontFamily: SANS, fontSize: 72, fontWeight: 700, color: TEXT}}>Agent Tutorial</div>
        <div style={{fontFamily: SANS, fontSize: 28, color: ACCENT, marginTop: 6}}>an automated code review system on doeff-agents</div>
      </div>
      {[0, 1].map((i) => (
        <div key={i} style={{position: 'absolute', left: partX(i) + PART.w, top: midY - 1, width: PART.gap, height: 2, background: MUTED, opacity: fadeAt(frame, 2.4 + i * 0.4)}} />
      ))}
      {PARTS.map((part, i) => (
        <Box key={part.name} x={partX(i)} y={PART.y} w={PART.w} h={PART.h} color={part.color} on={on[i]} dashed={part.dashed} opacity={fadeAt(frame, 1.2 + i * 0.5, 0.5)}>
          <div style={{position: 'absolute', left: 20, top: 14, fontFamily: SANS, fontSize: i === 2 && scripted ? 23 : 25, fontWeight: 700, color: on[i] ? LOOK : part.color, whiteSpace: 'nowrap'}}>
            {i === 2 && scripted ? 'scripted Claude Code' : part.name}
          </div>
          <div style={{position: 'absolute', left: 20, top: 60, fontFamily: i === 0 ? MONO : SANS, fontSize: i === 0 ? 17 : 19, lineHeight: '30px', color: MUTED}}>
            {part.about.map((line) => (
              <div key={line}>{line}</div>
            ))}
          </div>
        </Box>
      ))}
      {frame >= f(7.0) && frame < f(9.3) && <Pill x={mix(left - 40, right + 40, out)} y={midY - 22} text="the prompt" color={ACCENT} opacity={1 - ramp(frame, 9.0, 9.3)} />}
      {frame >= f(9.6) && frame < f(11.9) && <Pill x={mix(right + 40, left - 40, back)} y={midY + 22} text="its screen" color={TEST} opacity={1 - ramp(frame, 11.6, 11.9)} />}
      <Label x={640} y={548} size={21} bold color={TEST} center opacity={fadeAt(frame, 13.2)}>
        measure/stand_ins.py: scripted screens and an in-memory world
      </Label>
      <Label x={640} y={588} size={19} color={MUTED} center opacity={fadeAt(frame, 14.0)}>
        The page&apos;s code runs unchanged on top of them; only the backend and the io_root are the stand-ins.
      </Label>
    </AbsoluteFill>
  );
};

// Scene 2 — Step 1: launch, monitor until terminal, capture, stop (measure/lifecycle.py).

const LIFE_LINES: Lines = [
  [0, 'Step 1: launch, watch until it ends, read the screen, stop.'],
  [5.85, 'launch_session types the claude command, waits for the ready screen, then pastes the prompt.'],
  [13.05, 'monitor_session reads the pane once a second. "esc to interrupt" on screen means running.'],
  [20.45, 'At 3.0 s the shell prompt is back: the agent exited, a terminal status. The loop ends.'],
  [27.15, 'capture_output reads the review off the screen. stop_session kills the session.'],
];
const LIFE = f(34);
const LIFE_CODE = [
  'session = launch_session(name, config)',
  'try:',
  '    for _ in range(120):',
  '        monitor_session(session)',
  '        if session.is_terminal:',
  '            break',
  '        time.sleep(1)',
  '    return capture_output(session, lines=100)',
  'finally:',
  '    stop_session(session)',
];
const LC = {x: 50, y: 120, w: 600, size: 17, line: 27};
// The rows of measure/lifecycle.log, with the second each is shown.
const LIFE_PRINTS: Printed[] = [
  {at: 7.0, text: '[ 0.0s] launch_session  -> booting', color: MUTED},
  {at: 13.4, text: '[ 0.0s] monitor_session -> running', color: ACCENT},
  {at: 15.6, text: '[ 1.0s] monitor_session -> running', color: ACCENT},
  {at: 17.8, text: '[ 2.0s] monitor_session -> running', color: ACCENT},
  {at: 20.8, text: '[ 3.0s] monitor_session -> exited', color: LOOK},
  {at: 27.5, text: '[ 3.0s] capture_output  -> the review', color: TEXT},
  {at: 30.6, text: '[ 3.0s] stop_session    -> stopped', color: TEST},
];
const STATUSES = [
  {name: 'booting', at: 7.0, color: MUTED},
  {name: 'running', at: 13.4, color: ACCENT},
  {name: 'exited', at: 20.8, color: LOOK},
  {name: 'stopped', at: 30.6, color: TEST},
];

const LifeScene: React.FC = () => {
  const frame = useSceneFrame();
  const loop = [3, 4, 6][Math.floor(Math.max(0, frame - f(13.05)) / f(0.8)) % 3];
  const band = stepAt<number | null>(frame, [[0, null], [5.85, 0], [13.05, loop], [20.45, 4], [21.6, 5], [27.15, 7], [30.3, 9]]);
  const polled = Math.min(2, Math.floor(Math.max(0, frame - f(13.05)) / f(2.2)));
  const screen = stepAt<ScreenLine[]>(frame, [
    [0, ['$ ']],
    [6.3, ['$ claude --ax-screen-reader --dangerously-skip-permissions']],
    [8.2, READY],
    [10.8, working(0, false)],
    [13.05, working(polled, true)],
    [20.45, [...REVIEWED, ['$ ', LOOK]]],
    [27.15, [...REVIEWED.map((line): ScreenLine => [typeof line === 'string' ? line : line[0], LOOK]), '$ ']],
  ]);
  const killed = ramp(frame, 30.6, 31.2);
  const current = STATUSES.reduce((found, s, i) => (frame >= f(s.at) ? i : found), -1);
  return (
    <AbsoluteFill>
      <Headlines lines={LIFE_LINES} />
      <CodeBlock lines={LIFE_CODE} {...LC} band={band} opacity={fadeAt(frame, 0)} />
      <Terminal
        x={680} y={120} w={560} h={294} title="src/utils.py · the scripted terminal" lines={screen} size={12} line={20}
        on={frame >= f(13.05) && frame < f(27.15)} opacity={fadeAt(frame, 0.4) * (1 - 0.6 * killed)}
      />
      <Label x={960} y={330} size={24} bold color={TEST} center opacity={killed}>
        session killed
      </Label>
      <Label x={712} y={300} size={16} bold color={LOOK} opacity={between(frame, 8.4, 10.8)}>
        the ready screen: now the prompt goes in
      </Label>
      <Prints rows={LIFE_PRINTS} x={690} y={440} size={15} line={26} />
      {STATUSES.map((s, i) => (
        <div
          key={s.name}
          style={{
            position: 'absolute', left: 50 + i * 155, top: 452, width: 135, height: 50, boxSizing: 'border-box', borderRadius: 12, background: PANEL,
            border: `3px solid ${s.color}`, ...glow(current === i, s.color), fontFamily: MONO, fontSize: 19, fontWeight: 700, lineHeight: '44px', textAlign: 'center',
            color: current === i ? LOOK : s.color, opacity: current >= i ? 1 : 0.25 * fadeAt(frame, 1.0),
          }}
        >
          {s.name}
        </div>
      ))}
      <Label x={50} y={520} size={17} mono color={MUTED} opacity={fadeAt(frame, 21.2)}>
        is_terminal: exited, done, failed or stopped
      </Label>
      <Label x={50} y={556} size={18} color={TEST} opacity={fadeAt(frame, 22.0)}>
        The scripted Claude prints its review, then exits to the shell.
      </Label>
    </AbsoluteFill>
  );
};

// Scene 3 — under launch_session: a doeff program, effects and a handler (measure/driver_effects.py).

const EFF_LINES: Lines = [
  [0, 'launch_session is plain Python on top, and a doeff program underneath.'],
  [5.65, 'Before it opens a terminal, it yields effects: is claude on PATH? where is home? is there a login?'],
  [13.65, 'A handler answers each effect and resumes the program. Here an in-memory world answers.'],
  [23.4, 'Without a login, the same program raises at its check, and no terminal session is opened.'],
];
const EFFECTS = f(32);
const EFF_CODE = [
  '@do',
  'def cli_available(name):',
  '    found = yield which_executable(name)',
  '    return found is not None',
  ' ',
  '@do',
  'def claude_pre_launch():',
  '    home = yield home_path()',
  '    claude_json = posixpath.join(home, ".claude.json")',
  '    present = yield path_exists(claude_json)',
  '    if not present: raise RuntimeError(...)',
  '    raw_text = yield read_text(claude_json)',
  '    ...  # then the config files',
];
const EC = {x: 50, y: 120, w: 640, size: 16, line: 25};
const HANDLER = {x: 740, y: 120, w: 490, h: 72};
// measure/driver_effects.log: each effect, its answer, the code line that yields it, and the second it goes out.
const ANSWERED = [
  {effect: "WhichExecutable('claude')", answer: "'/usr/local/bin/claude'", line: 2, at: 6.2},
  {effect: 'HomePath()', answer: "'/home/dev'", line: 7, at: 8.0},
  {effect: "PathExists('/home/dev/.claude.json')", answer: 'True', line: 9, at: 9.8},
  {effect: "ReadText('/home/dev/.claude.json')", answer: '\'{"oauthAccount": …}\'', line: 11, at: 11.6},
];
const REST = [
  "MakeDirs('/home/dev/.claude') -> None",
  "PathExists('…/config.json') -> False",
  "WriteText('…/config.json', …) -> None",
  "PathExists('…/settings.json') -> False",
  "WriteText('…/settings.json', '{}') -> None",
];
const EFF_PRINTS: Printed[] = [
  ...ANSWERED.map((e) => ({at: e.at + 1.3, text: `${e.effect} -> ${e.answer}`})),
  ...REST.map((text, i) => ({at: 14.2 + i * 0.45, text, color: MUTED})),
  {at: 17.0, text: '-> the terminal opens: claude, then the prompt', color: TEST},
];
const FAIL_PRINTS: Printed[] = [
  {at: 24.0, text: "WhichExecutable('claude') -> '/usr/local/bin/claude'", color: MUTED},
  {at: 24.5, text: "HomePath() -> '/home/dev'", color: MUTED},
  {at: 25.0, text: "PathExists('/home/dev/.claude.json') -> False", color: FAIL},
  {at: 26.0, text: 'RuntimeError: /home/dev/.claude.json not found.', color: FAIL},
  {at: 27.0, text: 'terminal sessions: []', color: TEXT},
];

const EffectsScene: React.FC = () => {
  const frame = useSceneFrame();
  const sending = ANSWERED.reduce((found, e, i) => (frame >= f(e.at) ? i : found), -1);
  const band = stepAt<number | null>(frame, [
    [0, null],
    ...ANSWERED.map((e): [number, number | null] => [e.at - 0.2, e.line]),
    [14.0, 12],
    [23.4, 9],
    [25.6, 10],
  ]);
  const handlerOn = ANSWERED.some((e) => frame >= f(e.at + 0.6) && frame < f(e.at + 0.9)) || (frame >= f(14.0) && frame < f(16.4));
  const startX = EC.x + EC.w;
  const lineY = (i: number) => lineMid(EC.y, EC.line, i);
  const failing = frame >= f(25.6);
  return (
    <AbsoluteFill>
      <Headlines lines={EFF_LINES} />
      <CodeBlock lines={EFF_CODE} {...EC} band={band} opacity={fadeAt(frame, 0)} />
      <Box x={HANDLER.x} y={HANDLER.y} w={HANDLER.w} h={HANDLER.h} color={TEST} on={handlerOn} opacity={fadeAt(frame, 1.0)}>
        <div style={{position: 'absolute', left: 16, top: 8, fontFamily: MONO, fontSize: 17, fontWeight: 700, color: handlerOn ? LOOK : TEST, whiteSpace: 'nowrap'}}>
          fake_driver_io_handler(world)
        </div>
        <div style={{position: 'absolute', left: 16, top: 38, fontFamily: SANS, fontSize: 15, color: MUTED, whiteSpace: 'nowrap'}}>
          in memory: claude on PATH, a login in ~/.claude.json
        </div>
      </Box>
      {ANSWERED.map((e, i) => {
        if (sending !== i || frame >= f(e.at + 1.6)) return null;
        const out = ramp(frame, e.at, e.at + 0.6);
        const back = ramp(frame, e.at + 0.9, e.at + 1.5);
        const goingOut = frame < f(e.at + 0.9);
        const t = goingOut ? out : 1 - back;
        return (
          <Pill
            key={e.effect}
            x={mix(startX - 30, HANDLER.x + 20, t)}
            y={mix(lineY(e.line), HANDLER.y + HANDLER.h + 16, t)}
            text={goingOut ? e.effect.replace(/\('\/home\/dev\/\.claude\.json'\)/, '(…)') : e.answer}
            color={goingOut ? ACCENT : TEST}
            left
            opacity={goingOut ? 1 : 1 - ramp(frame, e.at + 1.4, e.at + 1.6)}
          />
        );
      })}
      <Prints rows={EFF_PRINTS} x={748} y={234} size={13} line={22} />
      <Label x={50} y={470} size={17} mono color={TEXT} opacity={fadeAt(frame, 1.6)}>
        launch_session(name, config, io_root=recording_root)
      </Label>
      <Label x={50} y={500} size={16} color={MUTED} opacity={fadeAt(frame, 2.2)}>
        runs adapter.available() and adapter.pre_launch() through io_root,
      </Label>
      <Label x={50} y={524} size={16} color={MUTED} opacity={fadeAt(frame, 2.2)}>
        then opens the terminal session and pastes the prompt
      </Label>
      <Label x={50} y={566} size={18} bold color={ACCENT} opacity={fadeAt(frame, 18.0)}>
        The decisions stay in the program; the handler only answers.
      </Label>
      <Box x={740} y={470} w={490} h={168} color={FAIL} on={failing && frame < f(27.4)} opacity={fadeAt(frame, 23.6)}>
        <div style={{position: 'absolute', left: 16, top: 8, fontFamily: SANS, fontSize: 16, fontWeight: 700, color: FAIL}}>a world without ~/.claude.json</div>
      </Box>
      <Prints rows={FAIL_PRINTS} x={756} y={502} size={13} line={24} />
      <Label x={50} y={606} size={18} bold color={FAIL} opacity={fadeAt(frame, 26.4)}>
        raised before backend.new_session: no terminal was opened
      </Label>
    </AbsoluteFill>
  );
};

// Scene 4 — Step 2: session_scope stops the session however the block is left (measure/cleanup.py).

const CLEAN_LINES: Lines = [
  [0, 'Step 2: session_scope stops the session on the way out of the with block.'],
  [6.65, 'The review finishes: the block returns, and stop_session runs.'],
  [12.05, 'A timeout returns early from inside the loop: stop_session still runs.'],
  [18.35, 'An exception leaves the block: stop_session runs, and the error goes on to the caller.'],
];
const CLEAN = f(25);
const CLEAN_CODE = [
  'with session_scope(name, config) as session:',
  '    start = time.time()',
  '    while not session.is_terminal:',
  '        if time.time() - start > timeout:',
  '            return {"success": False, "error": "Timeout"}',
  '        monitor_session(session)',
  '        time.sleep(1)',
  '    output = capture_output(session, lines=200)',
  '    return ...',
  '# leaving the block: stop_session(session)',
];
const CC = {x: 50, y: 130, w: 640, size: 16, line: 27};
// measure/cleanup.log: how each run left the block, the code line it left at, and the second the scene shows it.
const WAYS = [
  {title: 'the review finishes (1.5 s)', result: "returned {'ended': 'exited', ...}", killed: "killed ['review-utils']", line: 8, at: 7.0, color: TEST},
  {title: 'a 2 s timeout, a 5 s review', result: "returned {'success': False, 'error': 'Timeout'}", killed: "killed ['review-api']", line: 4, at: 12.4, color: PROD},
  {title: 'an exception inside the block', result: "raised ValueError('bad review prompt')", killed: "killed ['review-cli']", line: 5, at: 18.7, color: FAIL},
];

const CleanScene: React.FC = () => {
  const frame = useSceneFrame();
  const way = WAYS.reduce((found, w, i) => (frame >= f(w.at) ? i : found), -1);
  const leaving = way >= 0 ? WAYS[way] : null;
  const band = leaving === null ? null : frame < f(leaving.at + 1.6) ? leaving.line : 9;
  const stopOn = WAYS.some((w) => frame >= f(w.at + 1.6) && frame < f(w.at + 2.6));
  return (
    <AbsoluteFill>
      <Headlines lines={CLEAN_LINES} />
      <CodeBlock lines={CLEAN_CODE} {...CC} band={band} dim={(i) => (i === 9 ? (stopOn ? 1 : 0.75) : 1)} opacity={fadeAt(frame, 0)} />
      <div
        style={{
          position: 'absolute', left: CC.x + 6, top: CC.y + PAD + 9 * CC.line - 2, width: CC.w - 12, height: CC.line + 4, borderRadius: 8, boxSizing: 'border-box',
          border: `2px solid ${TEST}`, ...glow(stopOn, TEST), opacity: fadeAt(frame, 2.0),
        }}
      />
      {leaving !== null && frame >= f(leaving.at + 0.6) && frame < f(leaving.at + 1.8) && (
        <Pill
          x={CC.x + CC.w - 120}
          y={mix(lineMid(CC.y, CC.line, leaving.line), lineMid(CC.y, CC.line, 9), ramp(frame, leaving.at + 0.6, leaving.at + 1.5))}
          text={way === 2 ? 'ValueError' : 'return'}
          color={leaving.color}
          opacity={1 - ramp(frame, leaving.at + 1.5, leaving.at + 1.8)}
        />
      )}
      {WAYS.map((w, i) => (
        <Box key={w.title} x={740} y={130 + i * 150} w={490} h={130} color={w.color} on={way === i && frame < f(w.at + 3.0)} opacity={fadeAt(frame, w.at - 0.2)}>
          <div style={{position: 'absolute', left: 16, top: 10, fontFamily: SANS, fontSize: 19, fontWeight: 700, color: w.color}}>{w.title}</div>
          <div style={{position: 'absolute', left: 16, top: 48, fontFamily: MONO, fontSize: 14, color: i === 2 ? FAIL : TEXT, opacity: fadeAt(frame, w.at + 0.6), whiteSpace: 'nowrap'}}>
            {w.result}
          </div>
          <div style={{position: 'absolute', left: 16, top: 84, fontFamily: MONO, fontSize: 14, fontWeight: 700, color: TEST, opacity: fadeAt(frame, w.at + 1.7), whiteSpace: 'nowrap'}}>
            {`stop_session ran · ${w.killed}`}
          </div>
        </Box>
      ))}
      <Label x={50} y={455} size={16} color={MUTED} opacity={fadeAt(frame, 18.8)}>
        (the run raises after monitor_session, to leave the block by an exception)
      </Label>
      <Label x={50} y={520} size={20} bold color={FAIL} opacity={fadeAt(frame, 21.0)}>
        ValueError goes on to the caller of review_file
      </Label>
      <Label x={50} y={570} size={18} color={TEST} opacity={fadeAt(frame, 22.0)}>
        Three ways out, three sessions killed, no pane left behind.
      </Label>
    </AbsoluteFill>
  );
};

// Scene 5 — Step 3: on_status_change runs on changes only; BLOCKED waits for input (measure/callbacks.py).

const CB_LINES: Lines = [
  [0, 'Step 3: on_status_change runs only when the status changes.'],
  [6.85, 'Eight polls, four changes: running, blocked, running again, exited.'],
  [13.0, 'Blocked: the agent waits for input. It asks to run the tests; the loop answers with send_message.'],
  [21.75, 'The callback keeps the history, with the second of each change.'],
];
const CALLBACKS = f(27);
const CB_CODE = [
  'def on_status_change(self, old, new, output):',
  '    elapsed = (datetime.now() - self.started_at).total_seconds()',
  '    self.status_history.append((elapsed, new))',
  '    print(f"  [{elapsed:.1f}s] {old.value} -> {new.value}")',
  ' ',
  'monitor_session(session, on_status_change=progress.on_status_change)',
  'if session.status == SessionStatus.BLOCKED:',
  '    send_message(session, "1")',
];
const CBC = {x: 50, y: 120, w: 760, size: 15, line: 24};
// measure/callbacks.log: the seconds of the run, and the status from each second on.
const AXIS = {x0: 110, x1: 1170, y: 420};
const RUN_END = 3.5;
const axisX = (s: number) => mix(AXIS.x0, AXIS.x1, s / RUN_END);
const SEGMENTS = [
  {from: 0, to: 1.5, name: 'running', color: ACCENT},
  {from: 1.5, to: 2.0, name: 'blocked', color: PROD},
  {from: 2.0, to: 3.5, name: 'running', color: ACCENT},
];
const POLLS = [0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5];
const CHANGES = [0, 1.5, 2.0, 3.5];
// The playhead sweeps the run's 3.5 s between these seconds of the scene.
const SWEEP = {from: 7.2, to: 12.6};
const sceneOf = (s: number) => mix(SWEEP.from, SWEEP.to, s / RUN_END);
const CB_PRINTS: Printed[] = [
  {at: sceneOf(0) + 0.2, text: '[0.0s] booting -> running', color: ACCENT},
  {at: sceneOf(1.5) + 0.2, text: '[1.5s] running -> blocked', color: PROD},
  {at: sceneOf(1.5) + 0.5, text: "[1.5s] send_message(session, '1')", color: TEST},
  {at: sceneOf(2.0) + 0.2, text: '[2.0s] blocked -> running', color: ACCENT},
  {at: sceneOf(3.5) + 0.2, text: '[3.5s] running -> exited', color: LOOK},
];

const CallbackScene: React.FC = () => {
  const frame = useSceneFrame();
  const run = interpolate(frame, [f(SWEEP.from), f(SWEEP.to)], [0, RUN_END], clamp);
  const asking = frame >= f(13.0) && frame < f(21.75);
  const answered = ramp(frame, 16.4, 17.4);
  const history = frame >= f(21.75);
  return (
    <AbsoluteFill>
      <Headlines lines={CB_LINES} />
      <CodeBlock lines={CB_CODE} {...CBC} band={stepAt<number | null>(frame, [[0, null], [1.2, 5], [3.4, 0], [7.2, 3], [13.4, 6], [16.0, 7], [21.75, 2]])} opacity={fadeAt(frame, 0)} />
      <Box x={840} y={120} w={390} h={216} color={MUTED} opacity={fadeAt(frame, 6.85)}>
        <div style={{position: 'absolute', left: 14, top: 8, fontFamily: SANS, fontSize: 15, fontWeight: 700, color: MUTED}}>printed</div>
      </Box>
      <Prints rows={CB_PRINTS} x={856} y={150} size={14} line={34} />
      {SEGMENTS.map((s) => (
        <div
          key={s.from}
          style={{
            position: 'absolute', left: axisX(s.from), top: AXIS.y - 44, width: Math.max(0, axisX(Math.min(s.to, Math.max(s.from, run))) - axisX(s.from)), height: 30,
            background: s.color, opacity: 0.85, boxShadow: s.name === 'blocked' && asking ? `0 0 18px ${LOOK}` : 'none',
          }}
        >
          <div style={{position: 'absolute', left: 8, top: 4, fontFamily: MONO, fontSize: 15, fontWeight: 700, color: '#0f1117', whiteSpace: 'nowrap', opacity: run >= s.to - 0.05 ? 1 : 0}}>{s.name}</div>
        </div>
      ))}
      <Label x={axisX(3.5) + 14} y={AXIS.y - 40} size={15} mono bold color={LOOK} opacity={run >= 3.45 ? 1 : 0}>
        exited
      </Label>
      <div style={{position: 'absolute', left: AXIS.x0, top: AXIS.y, width: AXIS.x1 - AXIS.x0, height: 2, background: MUTED, opacity: fadeAt(frame, 6.85)}} />
      {[0, 1, 2, 3].map((s) => (
        <Label key={s} x={axisX(s)} y={AXIS.y + 34} size={14} mono center opacity={fadeAt(frame, 6.85)}>
          {`${s}.0 s`}
        </Label>
      ))}
      {POLLS.map((s) => {
        const changed = CHANGES.includes(s);
        const reached = run >= s - 0.01;
        return (
          <div
            key={s}
            style={{
              position: 'absolute', left: axisX(s) - 8, top: AXIS.y - 7, width: 16, height: 16, borderRadius: 8, boxSizing: 'border-box',
              background: reached ? (changed ? LOOK : MUTED) : PANEL, border: `2px solid ${changed ? LOOK : MUTED}`, boxShadow: reached && changed ? `0 0 12px ${LOOK}` : 'none',
              opacity: fadeAt(frame, 6.85),
            }}
          />
        );
      })}
      <Label x={AXIS.x0} y={AXIS.y + 62} size={15} color={MUTED} opacity={fadeAt(frame, 8.0)}>
        each dot: one monitor_session call, every 0.5 s · yellow: the status changed, on_status_change ran
      </Label>
      <Terminal
        x={110} y={530} w={660} h={130} title="the scripted terminal, at 1.5 s"
        lines={[ASKED, ['? Run pytest tests/test_utils.py?', PROD], '  1. Yes   2. No', ['Esc to cancel', LOOK]]}
        size={13} line={17} on={asking && answered < 1} opacity={between(frame, 13.2, 21.5)}
      />
      {frame >= f(16.4) && frame < f(17.7) && <Pill x={mix(1010, 790, answered)} y={636} text={"send_message(session, '1')"} color={TEST} opacity={1 - ramp(frame, 17.4, 17.7)} />}
      <Label x={800} y={540} size={17} color={MUTED} opacity={between(frame, 14.2, 21.5)}>
        a prompt on screen, nothing changing:
      </Label>
      <Label x={800} y={566} size={17} bold color={PROD} opacity={between(frame, 14.2, 21.5)}>
        BLOCKED, not terminal
      </Label>
      <Label x={800} y={622} size={17} color={TEST} opacity={between(frame, 18.0, 21.5)}>
        the agent goes on: running again
      </Label>
      <Label x={110} y={560} size={18} mono color={TEXT} opacity={fadeAt(frame, 22.0)}>
        status_history:
      </Label>
      <Label x={110} y={592} size={18} mono color={TEXT} opacity={fadeAt(frame, 22.4)}>
        {"[(0.0, 'running'), (1.5, 'blocked'), (2.0, 'running'), (3.5, 'exited')]"}
      </Label>
      <Label x={110} y={636} size={17} color={MUTED} opacity={fadeAt(frame, 23.0) * (history ? 1 : 0)}>
        monitor_session ran 8 times; on_status_change ran 4 times
      </Label>
    </AbsoluteFill>
  );
};

// Scene 6 — Step 4: a Semaphore limits the batch, gather keeps the order (measure/batch.py).

const BATCH_LINES: Lines = [
  [0, 'Step 4: many files at once, with asyncio and a limit.'],
  [6.25, 'A Semaphore of 2 lets two reviews run. The next one starts when a slot frees up.'],
  [13.15, 'Four reviews: 7.0 s two at a time, 13.1 s one at a time.'],
  [21.1, 'api.py finishes first, but gather returns the results in the order of the files.'],
];
const BATCH = f(27.5);
const BATCH_CODE = [
  'semaphore = asyncio.Semaphore(max_concurrent)',
  ' ',
  'async def limited_review(file_path):',
  '    async with semaphore:',
  '        return await review_file_async(file_path)',
  ' ',
  'results = await asyncio.gather(*[limited_review(f) for f in files])',
];
const BC = {x: 50, y: 120, w: 660, size: 15, line: 23};
const WORK = [
  {name: 'utils.py', s: 3.0},
  {name: 'api.py', s: 1.0},
  {name: 'models.py', s: 2.5},
  {name: 'cli.py', s: 1.5},
];
// measure/batch.log: when each review started and finished, by run and lane.
const GANTT = {x0: 230, x1: 1190};
const SPAN = 13.1;
const gX = (s: number) => mix(GANTT.x0, GANTT.x1, s / SPAN);
const RUNS = [
  {
    label: 'max_concurrent=2', total: 7.0, sweep: [6.6, 12.2], lanes: ['slot 1', 'slot 2'], y: 360,
    bars: [
      {name: 'utils.py', lane: 0, from: 0, to: 4.0},
      {name: 'api.py', lane: 1, from: 0, to: 2.0},
      {name: 'models.py', lane: 1, from: 2.0, to: 6.0},
      {name: 'cli.py', lane: 0, from: 4.0, to: 7.0},
    ],
  },
  {
    label: 'max_concurrent=1', total: 13.1, sweep: [13.5, 19.6], lanes: ['one slot'], y: 480,
    bars: [
      {name: 'utils.py', lane: 0, from: 0, to: 4.0},
      {name: 'api.py', lane: 0, from: 4.0, to: 6.0},
      {name: 'models.py', lane: 0, from: 6.0, to: 10.1},
      {name: 'cli.py', lane: 0, from: 10.1, to: 13.1},
    ],
  },
];
const LANE = {h: 34, gap: 8};

const BatchScene: React.FC = () => {
  const frame = useSceneFrame();
  const orders = frame >= f(21.1);
  return (
    <AbsoluteFill>
      <Headlines lines={BATCH_LINES} />
      <CodeBlock lines={BATCH_CODE} {...BC} band={stepAt<number | null>(frame, [[0, null], [1.4, 0], [6.25, 3], [21.1, 6]])} opacity={fadeAt(frame, 0)} />
      <Label x={750} y={126} size={17} bold color={TEST} opacity={fadeAt(frame, 1.6)}>
        the scripted agent&apos;s work per file
      </Label>
      {WORK.map((w, i) => (
        <Label key={w.name} x={750 + (i % 2) * 230} y={162 + Math.floor(i / 2) * 32} size={17} mono color={TEXT} opacity={fadeAt(frame, 2.0 + i * 0.3)}>
          {`${w.name.padEnd(10)} ${w.s.toFixed(1)} s`}
        </Label>
      ))}
      <Label x={750} y={240} size={15} color={MUTED} opacity={fadeAt(frame, 3.4)}>
        polled every 1 s (async_monitor_session), so each review
      </Label>
      <Label x={750} y={262} size={15} color={MUTED} opacity={fadeAt(frame, 3.4)}>
        ends at the first poll after its work is done
      </Label>
      {RUNS.map((r, ri) => {
        const at = interpolate(frame, [f(r.sweep[0]), f(r.sweep[1])], [0, r.total], clamp);
        const seen = fadeAt(frame, ri === 0 ? 5.6 : 13.15);
        return (
          <React.Fragment key={r.label}>
            <Label x={50} y={r.y - 34} size={17} mono bold color={ACCENT} opacity={seen}>
              {r.label}
            </Label>
            {r.lanes.map((lane, li) => (
              <React.Fragment key={lane}>
                <Label x={50} y={r.y + li * (LANE.h + LANE.gap) + 7} size={15} color={MUTED} opacity={seen}>
                  {lane}
                </Label>
                <div style={{position: 'absolute', left: GANTT.x0, top: r.y + li * (LANE.h + LANE.gap), width: GANTT.x1 - GANTT.x0, height: LANE.h, background: '#141722', opacity: seen}} />
              </React.Fragment>
            ))}
            {r.bars.map((b) => {
              if (at <= b.from) return null;
              const end = Math.min(b.to, at);
              const done = at >= b.to;
              const first = ri === 0 && b.name === 'api.py' && orders;
              return (
                <div
                  key={b.name}
                  style={{
                    position: 'absolute', left: gX(b.from) + 1, top: r.y + b.lane * (LANE.h + LANE.gap), width: gX(end) - gX(b.from) - 2, height: LANE.h, boxSizing: 'border-box',
                    borderRadius: 6, background: done ? '#1d1a33' : '#2a2450', border: `2px solid ${first ? LOOK : ACCENT}`, boxShadow: first ? `0 0 14px ${LOOK}` : 'none', overflow: 'hidden',
                  }}
                >
                  <div style={{position: 'absolute', left: 8, top: 6, fontFamily: MONO, fontSize: 15, fontWeight: 700, color: first ? LOOK : TEXT, whiteSpace: 'nowrap'}}>{b.name}</div>
                </div>
              );
            })}
            {at > 0 && at < r.total && <div style={{position: 'absolute', left: gX(at) - 1, top: r.y - 6, width: 3, height: r.lanes.length * (LANE.h + LANE.gap) + 4, background: LOOK, boxShadow: `0 0 10px ${LOOK}`}} />}
            <Label x={gX(r.total) + 10} y={r.y + 6} size={18} mono bold color={LOOK} opacity={at >= r.total ? fadeAt(frame, r.sweep[1]) : 0}>
              {`${r.total.toFixed(1)} s`}
            </Label>
          </React.Fragment>
        );
      })}
      <div style={{position: 'absolute', left: GANTT.x0, top: 532, width: GANTT.x1 - GANTT.x0, height: 2, background: MUTED, opacity: fadeAt(frame, 5.6)}} />
      {[0, 2, 4, 6, 8, 10, 12].map((s) => (
        <Label key={s} x={gX(s)} y={540} size={13} mono center opacity={fadeAt(frame, 5.6)}>
          {`${s} s`}
        </Label>
      ))}
      <Label x={50} y={590} size={18} mono color={TEXT} opacity={fadeAt(frame, 21.4)}>
        {'finished in:   '}
        <span style={{color: LOOK, fontWeight: 700}}>api.py</span>
        {', utils.py, models.py, cli.py'}
      </Label>
      <Label x={50} y={626} size={18} mono color={TEXT} opacity={fadeAt(frame, 22.4)}>
        {'gather order:  utils.py, '}
        <span style={{color: LOOK, fontWeight: 700}}>api.py</span>
        {', models.py, cli.py'}
      </Label>
      <Label x={50} y={668} size={16} color={MUTED} opacity={fadeAt(frame, 23.4)}>
        (the order the files were given; most at once: 2, then 1)
      </Label>
    </AbsoluteFill>
  );
};

// The outro.

const OUTRO_LINES: Lines = [
  [0, 'Step 5 puts it together: a scope per session, callbacks for progress, a limited batch, timeouts.'],
  [7.6, 'Under the session API, the driver is a doeff program: effects go out, handlers send answers back.'],
];
const OUTRO = f(16.6);
const STEPS = [
  {n: '1', name: 'launch · monitor', sub: 'capture · stop'},
  {n: '2', name: 'session_scope', sub: 'always stopped'},
  {n: '3', name: 'on_status_change', sub: 'BLOCKED needs input'},
  {n: '4', name: 'Semaphore + gather', sub: 'a limit, in order'},
  {n: '5', name: 'code_reviewer.py', sub: 'timeouts on every wait'},
];
const CHIP = {w: 218, h: 92, gap: 14, y: 140};
const chipX = (i: number) => 640 - (STEPS.length * CHIP.w + (STEPS.length - 1) * CHIP.gap) / 2 + i * (CHIP.w + CHIP.gap);
const LAYERS = [
  {name: 'launch_session(...)', sub: 'the session API: plain Python', color: MUTED, x: 90},
  {name: 'yield effect', sub: 'a doeff program decides', color: ACCENT, x: 490},
  {name: 'handler', sub: 'answers, and resumes k', color: TEST, x: 890},
];

const OutroScene: React.FC = () => {
  const frame = useSceneFrame();
  const out = ramp(frame, 9.6, 10.6);
  const back = ramp(frame, 11.0, 12.0);
  return (
    <AbsoluteFill>
      <Headlines lines={OUTRO_LINES} />
      {STEPS.map((s, i) => (
        <Box key={s.n} x={chipX(i)} y={CHIP.y} w={CHIP.w} h={CHIP.h} color={i === 4 ? LOOK : TEST} on={frame >= f(0.6 + i * 0.9) && frame < f(1.5 + i * 0.9)} opacity={fadeAt(frame, 0.4 + i * 0.9)}>
          <div style={{position: 'absolute', left: 14, top: 10, fontFamily: SANS, fontSize: 15, fontWeight: 700, color: MUTED}}>{`Step ${s.n}`}</div>
          <div style={{position: 'absolute', left: 14, top: 32, fontFamily: MONO, fontSize: 16, fontWeight: 700, color: TEXT, whiteSpace: 'nowrap'}}>{s.name}</div>
          <div style={{position: 'absolute', left: 14, top: 60, fontFamily: SANS, fontSize: 14, color: MUTED, whiteSpace: 'nowrap'}}>{s.sub}</div>
        </Box>
      ))}
      {LAYERS.map((l, i) => (
        <Box key={l.name} x={l.x} y={300} w={300} h={100} color={l.color} on={(i === 1 && frame >= f(9.4) && frame < f(9.8)) || (i === 2 && frame >= f(10.6) && frame < f(11.0))} opacity={fadeAt(frame, 7.9 + i * 0.4)}>
          <div style={{position: 'absolute', left: 0, right: 0, top: 16, textAlign: 'center', fontFamily: MONO, fontSize: 20, fontWeight: 700, color: l.color}}>{l.name}</div>
          <div style={{position: 'absolute', left: 0, right: 0, top: 56, textAlign: 'center', fontFamily: SANS, fontSize: 16, color: MUTED}}>{l.sub}</div>
        </Box>
      ))}
      <div style={{position: 'absolute', left: 390, top: 349, width: 100, height: 2, background: MUTED, opacity: fadeAt(frame, 8.3)}} />
      <div style={{position: 'absolute', left: 790, top: 349, width: 100, height: 2, background: MUTED, opacity: fadeAt(frame, 8.7)}} />
      {frame >= f(9.6) && frame < f(10.9) && <Pill x={mix(800, 880, out)} y={330} text="effect" color={ACCENT} opacity={1 - ramp(frame, 10.6, 10.9)} />}
      {frame >= f(11.0) && frame < f(12.3) && <Pill x={mix(880, 800, back)} y={372} text="answer" color={TEST} opacity={1 - ramp(frame, 12.0, 12.3)} />}
      <div style={{position: 'absolute', left: 0, right: 0, top: 460 + mix(16, 0, ramp(frame, 12.6, 13.2)), textAlign: 'center', opacity: fadeAt(frame, 12.6, 0.5)}}>
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
  {length: TITLE, starts: startsOf(TITLE_LINES), ids: ['t1', 't3'], node: <TitleScene />},
  {length: LIFE, starts: startsOf(LIFE_LINES), ids: ['l1', 'l2', 'l3', 'l4', 'l5'], node: <LifeScene />},
  {length: EFFECTS, starts: startsOf(EFF_LINES), ids: ['e1', 'e2', 'e3', 'e4'], node: <EffectsScene />},
  {length: CLEAN, starts: startsOf(CLEAN_LINES), ids: ['c1', 'c2', 'c3', 'c4'], node: <CleanScene />},
  {length: CALLBACKS, starts: startsOf(CB_LINES), ids: ['s1', 's2', 's3', 's4'], node: <CallbackScene />},
  {length: BATCH, starts: startsOf(BATCH_LINES), ids: ['b1', 'b2', 'b3', 'b4'], node: <BatchScene />},
  {length: OUTRO, starts: startsOf(OUTRO_LINES), ids: ['z1', 'z2'], node: <OutroScene />},
].map(tight));
