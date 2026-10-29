// The video on the CLI Auto-Discovery page (docs/14-cli-auto-discovery.md): how doeff run picks the handlers for a
// program by where the program lives. Eight scenes, each with headlines a synthetic voice reads (narration.json):
//   1. A program only yields effects; which handlers answer them is decided when it runs. --program is enough.
//   2. The markers: # doeff: interpreter, default on a function, # doeff: default on an annotated variable.
//   3. Interpreter discovery: the module path gives a hierarchy, and the closest marked interpreter wins.
//   4. Env discovery: every marked env on the path is collected, root first, and merged left to right.
//   5. The run: the chosen interpreter installs its handlers, and reader answers login()'s Asks from the merged env.
//   6. Overrides: --env and --interpreter, what each turns off, their deprecation, and --hy.
//   7. No marked interpreter on the path: the fallback interpreter, which ignores envs.
//   8. The outro.
// Every module path, value, order, result and error on screen is printed by measure/run.sh (discover.log and
// cli.log next to it) over the example project in measure/project.
import React from 'react';
import {AbsoluteFill} from 'remotion';
import {fadeAt, mix, ramp} from '../../kit/motion';
import {narrate} from '../../kit/narrated';
import {CodeBlock, glow, Headlines, lineMid, Lines, PAD, Pill, startsOf} from '../../kit/parts';
import {ACCENT, f, FAIL, LOOK, MONO, MUTED, PANEL, SANS, TEST, TEXT} from '../../kit/theme';
import {useSceneFrame} from '../../kit/warp';
import DURATIONS from './durations.json';

// Seen between two seconds of a scene: fades in at the first and out at the second.
const between = (frame: number, from: number, to: number, length = 0.3) => fadeAt(frame, from, length) * (1 - fadeAt(frame, to, length));

// A label in the scenes' sans font.
const Label: React.FC<{x: number; y: number; size?: number; color?: string; bold?: boolean; opacity?: number; children: React.ReactNode; center?: boolean; mono?: boolean}> = ({
  x, y, size = 20, color = MUTED, bold = false, opacity = 1, children, center = false, mono = false,
}) => (
  <div
    style={{
      position: 'absolute', left: x, top: y, fontFamily: mono ? MONO : SANS, fontSize: size, fontWeight: bold ? 700 : 400, color, opacity,
      whiteSpace: 'nowrap', transform: center ? 'translateX(-50%)' : undefined,
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
      position: 'absolute', left: x, top: y, width: w, height: h, boxSizing: 'border-box', borderRadius: 14, background: PANEL,
      border: `3px ${dashed ? 'dashed' : 'solid'} ${color}`, ...glow(on, color), opacity,
    }}
  >
    {children}
  </div>
);

// A ring around line i of a code box at (x, y): a marker, or the line that is at work.
const Ring: React.FC<{x: number; y: number; w: number; line: number; i: number; rows?: number; color: string; opacity: number}> = ({x, y, w, line, i, rows = 1, color, opacity}) => (
  <div
    style={{
      position: 'absolute', left: x + 6, top: y + PAD + i * line - 2, width: w - 12, height: rows * line + 4, boxSizing: 'border-box',
      border: `3px solid ${color}`, borderRadius: 8, boxShadow: `0 0 12px ${color}88`, opacity,
    }}
  />
);

// The layers of an onion of handlers around a code box: layer 0 is the innermost.
const onion = (cx: number, cy: number, w: number, h: number, margin: number) => (i: number) => {
  const lw = w + 2 * margin * (i + 1);
  const lh = h + 2 * margin * (i + 1);
  return {x: cx - lw / 2, y: cy - lh / 2, w: lw, h: lh};
};

const PROGRAM = 'myapp.features.auth.login.login_program';

// Scene 1 — a program only yields effects; the handlers are decided when it runs (cli.log, the first three runs).

const TITLE_LINES: Lines = [
  [0, 'CLI Auto-Discovery: doeff run finds the interpreter and the envs for your program.'],
  [5, 'A program only yields effects. Which handlers answer them is decided when it runs.'],
  [10.5, 'Without discovery, every command names them. With markers, --program is enough.'],
];
const TITLE = f(18);
const LOGIN = ['@do', 'def login():', '    host = yield Ask("db_host")', '    timeout = yield Ask("timeout")', '    return f"{host}, timeout {timeout}"'];
const T = {x: 110, y: 300, w: 500, size: 19, line: 30};
const LONG = [
  `$ doeff run --program ${PROGRAM} \\`,
  '    --interpreter myapp.features.auth.auth_interpreter \\',
  '    --env myapp.base_env --env myapp.features.features_env \\',
  '    --env myapp.features.auth.auth_env',
  'localhost, timeout 30',
];
const SHORT = [`$ doeff run --program ${PROGRAM}`, 'localhost, timeout 30'];

const TitleScene: React.FC = () => {
  const frame = useSceneFrame();
  const program = between(frame, 5.2, 10.3);
  const askAt = ramp(frame, 6.4, 7.4);
  return (
    <AbsoluteFill>
      <Headlines lines={TITLE_LINES} />
      <div style={{position: 'absolute', left: 0, right: 0, top: 112 + mix(16, 0, ramp(frame, 0.2, 0.8)), textAlign: 'center', opacity: fadeAt(frame, 0.2, 0.5)}}>
        <div style={{fontFamily: SANS, fontSize: 72, fontWeight: 700, color: TEXT}}>CLI Auto-Discovery</div>
        <div style={{fontFamily: SANS, fontSize: 28, color: ACCENT, marginTop: 6}}>doeff run picks the handlers by where the program lives</div>
      </div>
      <CodeBlock lines={LOGIN} x={T.x} y={T.y} w={T.w} size={T.size} line={T.line} band={frame >= f(6.0) && frame < f(10.3) ? 2 : null} opacity={program} />
      <Box x={720} y={T.y} w={450} h={PAD * 2 + LOGIN.length * T.line} color={TEST} dashed on={frame >= f(7.4) && frame < f(10.0)} opacity={program}>
        <div style={{position: 'absolute', left: 0, right: 0, top: 30, textAlign: 'center', fontFamily: SANS, fontSize: 24, fontWeight: 700, color: TEST}}>the handlers</div>
        <div style={{position: 'absolute', left: 0, right: 0, top: 70, textAlign: 'center', fontFamily: SANS, fontSize: 20, color: MUTED}}>not in the program:</div>
        <div style={{position: 'absolute', left: 0, right: 0, top: 100, textAlign: 'center', fontFamily: SANS, fontSize: 20, color: MUTED}}>chosen when it runs</div>
      </Box>
      {frame >= f(6.4) && frame < f(7.8) && (
        <Pill x={mix(T.x + T.w + 20, 720, askAt)} y={lineMid(T.y, T.line, 2)} text={'Ask("db_host")'} color={ACCENT} opacity={program * (1 - ramp(frame, 7.5, 7.8))} />
      )}
      <div style={{opacity: between(frame, 10.6, 13.6)}}>
        <Label x={60} y={268} size={18} bold>without discovery</Label>
        <CodeBlock lines={LONG} x={60} y={296} w={1160} size={18} line={30} dim={(i) => (i === 4 ? 0.7 : 1)} />
      </div>
      <div style={{opacity: fadeAt(frame, 13.6, 0.4)}}>
        <Label x={60} y={268} size={18} bold color={TEST}>with markers</Label>
        <CodeBlock lines={SHORT} x={60} y={296} w={1160} size={18} line={30} dim={(i) => (i === 1 ? 0.7 : 1)} />
        <Label x={60} y={390} size={19} color={TEXT}>the same interpreter, the same envs, the same answer</Label>
      </div>
      {/* The two runs give the same line (cli.log: the long form and the short form both print localhost, timeout 30). */}
      <Label x={640} y={600} size={22} bold color={ACCENT} center opacity={fadeAt(frame, 15.0)}>
        the program says what it needs; doeff run finds who answers
      </Label>
    </AbsoluteFill>
  );
};

// Scene 2 — the markers, and what doeff-indexer finds (measure/project/myapp/__init__.py, discover.log).

const MARKER_LINES: Lines = [
  [0, 'Mark a function with # doeff: interpreter, default. It becomes a default interpreter.'],
  [6, 'Mark an annotated variable with # doeff: default. It becomes a default env.'],
  [12, 'doeff-indexer scans the source and lists every marked symbol. A plain assignment is skipped.'],
];
const MARKERS = f(20);
const ROOT_FILE = [
  '# myapp/__init__.py',
  'def base_interpreter(prog: Program, *, env: Program[dict] = Pure({})):',
  '    """# doeff: interpreter, default"""',
  '    return run(scheduled(state()(writer(reader(env=run(env))(prog)))))',
  ' ',
  '# doeff: default',
  'base_env: Program[dict] = Pure({"db_host": "localhost", ...})',
  ' ',
  '# doeff: default',
  'plain_env = Pure({"timeout": 99})',
];
const MK = {x: 30, y: 130, w: 740, size: 16, line: 30};
const FOUND = [
  {group: 'interpreters', paths: ['myapp.base_interpreter', 'myapp.features.auth.auth_interpreter'], at: 13.0, color: TEST},
  {group: 'envs', paths: ['myapp.base_env', 'myapp.features.features_env', 'myapp.features.auth.auth_env'], at: 14.2, color: TEXT},
];
const F = {x: 800, y: 130, w: 450};

const MarkerScene: React.FC = () => {
  const frame = useSceneFrame();
  const scan = ramp(frame, 12.2, 13.0);
  const codeH = PAD * 2 + ROOT_FILE.length * MK.line;
  return (
    <AbsoluteFill>
      <Headlines lines={MARKER_LINES} />
      <CodeBlock lines={ROOT_FILE} x={MK.x} y={MK.y} w={MK.w} size={MK.size} line={MK.line} dim={(i) => (i === 0 ? 0.6 : i === 9 ? 1 - 0.6 * fadeAt(frame, 15.6) : 1)} opacity={fadeAt(frame, 0)} />
      <Ring x={MK.x} y={MK.y} w={MK.w} line={MK.line} i={2} color={ACCENT} opacity={fadeAt(frame, 1.2) * (frame < f(6) || frame >= f(12) ? 1 : 0.35)} />
      <Ring x={MK.x} y={MK.y} w={MK.w} line={MK.line} i={5} rows={2} color={ACCENT} opacity={fadeAt(frame, 6.6) * (frame < f(12) ? 1 : 0.6)} />
      <Ring x={MK.x} y={MK.y} w={MK.w} line={MK.line} i={8} rows={2} color={FAIL} opacity={fadeAt(frame, 15.4)} />
      <Label x={MK.x} y={MK.y + codeH + 14} size={19} color={TEXT} opacity={between(frame, 1.8, 5.8)}>
        a function with <span style={{fontFamily: MONO, color: ACCENT}}>interpreter, default</span> in its docstring or on its def line
      </Label>
      <Label x={MK.x} y={MK.y + codeH + 14} size={19} color={TEXT} opacity={between(frame, 7.2, 11.8)}>
        a variable with a type annotation, under <span style={{fontFamily: MONO, color: ACCENT}}># doeff: default</span> or on its line
      </Label>
      <Label x={MK.x} y={MK.y + codeH + 14} size={19} color={FAIL} opacity={fadeAt(frame, 15.8)}>
        <span style={{fontFamily: MONO}}>plain_env</span> has no annotation: the indexer skips it
      </Label>
      {frame >= f(12.2) && frame < f(13.2) && (
        <div style={{position: 'absolute', left: MK.x, top: MK.y + scan * codeH - 2, width: MK.w, height: 4, background: LOOK, boxShadow: `0 0 16px ${LOOK}`, opacity: 1 - ramp(frame, 13.0, 13.2)}} />
      )}
      <Box x={F.x} y={F.y} w={F.w} h={codeH} color="#2a2e40" opacity={fadeAt(frame, 12.4)}>
        <div style={{position: 'absolute', left: 20, top: 14, fontFamily: SANS, fontSize: 20, fontWeight: 700, color: TEXT}}>doeff-indexer finds</div>
        <div style={{position: 'absolute', left: 20, top: 42, fontFamily: SANS, fontSize: 15, color: MUTED}}>every marked symbol in the project</div>
        {FOUND.map((group, g) => (
          <div key={group.group} style={{position: 'absolute', left: 20, top: 80 + g * 104, opacity: fadeAt(frame, group.at)}}>
            <div style={{fontFamily: SANS, fontSize: 16, fontWeight: 700, color: group.color === TEXT ? MUTED : group.color}}>{group.group}</div>
            {group.paths.map((path, i) => (
              <div key={path} style={{fontFamily: MONO, fontSize: 15, lineHeight: '24px', color: TEXT, opacity: fadeAt(frame, group.at + 0.2 + i * 0.25)}}>
                {path}
              </div>
            ))}
          </div>
        ))}
      </Box>
    </AbsoluteFill>
  );
};

// Scene 3 — the closest marked interpreter on the program's module path wins (discover.log).

const INTERP_LINES: Lines = [
  [0, `doeff run cuts the program's module path into a hierarchy, from the root package down.`],
  [6, 'Only interpreters marked on that path count: in the module, or a package above it.'],
  [12, 'Two are on the path: base_interpreter at index 0, auth_interpreter at index 2.'],
  [17, 'The closest, deepest one wins: auth_interpreter runs the program.'],
];
const INTERP = f(23);
const HIERARCHY = [
  {module: 'myapp', file: 'myapp/__init__.py', interpreter: 'base_interpreter'},
  {module: 'myapp.features', file: 'myapp/features/__init__.py', interpreter: null},
  {module: 'myapp.features.auth', file: 'myapp/features/auth/__init__.py', interpreter: 'auth_interpreter'},
  {module: 'myapp.features.auth.login', file: 'myapp/features/auth/login.py', interpreter: null},
];
const rowY = (i: number) => 196 + i * 100;
const ROW = {x: 60, w: 560, h: 72, indent: 30};

const InterpreterScene: React.FC = () => {
  const frame = useSceneFrame();
  const climb = ramp(frame, 17.4, 18.4);
  const lookY = mix(rowY(3), rowY(2), climb);
  const chosen = frame >= f(18.4);
  return (
    <AbsoluteFill>
      <Headlines lines={INTERP_LINES} />
      <div style={{position: 'absolute', left: 60, top: 118, fontFamily: MONO, fontSize: 21, color: TEXT, opacity: fadeAt(frame, 0.2)}}>
        <span style={{color: MUTED}}>--program </span>
        <span style={{color: frame >= f(1.2) ? ACCENT : TEXT}}>myapp.features.auth.login</span>
        <span style={{color: MUTED}}>.login_program</span>
      </div>
      {HIERARCHY.map((row, i) => {
        // Each candidate glows while its index is read out, and the winner once it is chosen.
        const read = 12.4 + i * 0.8;
        const on = (row.interpreter !== null && frame >= f(read) && frame < f(read + 1.4)) || (i === 2 && chosen && frame < f(20.5));
        return (
          <Box key={row.module} x={ROW.x + i * ROW.indent} y={rowY(i)} w={ROW.w - i * ROW.indent} h={ROW.h} color={i === 3 ? ACCENT : '#2a2e40'} on={on} opacity={fadeAt(frame, 1.6 + i * 0.6)}>
            <div style={{position: 'absolute', left: 18, top: 9, fontFamily: MONO, fontSize: 20, fontWeight: 700, color: TEXT}}>{row.module}</div>
            <div style={{position: 'absolute', left: 18, top: 40, fontFamily: MONO, fontSize: 14, color: MUTED}}>{row.file}</div>
            <div style={{position: 'absolute', right: 18, top: 22, fontFamily: SANS, fontSize: 18, color: frame >= f(12) && row.interpreter !== null ? LOOK : MUTED, opacity: fadeAt(frame, 3.5 + i * 0.3)}}>index {i}</div>
          </Box>
        );
      })}
      {HIERARCHY.map((row, i) => {
        const at = 6.6 + i * 0.4;
        const top = rowY(i);
        if (row.interpreter === null) {
          return (
            <Label key={row.module} x={720} y={top + 24} size={18} opacity={fadeAt(frame, at)}>
              no marked interpreter
            </Label>
          );
        }
        const winner = row.interpreter === 'auth_interpreter';
        const dim = !winner && chosen ? 1 - 0.6 * ramp(frame, 18.4, 18.9) : 1;
        return (
          <React.Fragment key={row.module}>
            <div style={{position: 'absolute', left: ROW.x + ROW.w + 8, top: top + ROW.h / 2 - 1, width: 82, height: 2, background: TEST, opacity: fadeAt(frame, at) * dim}} />
            <Box x={710} y={top} w={500} h={ROW.h} color={TEST} on={winner && chosen && frame < f(20.5)} opacity={fadeAt(frame, at) * dim}>
              <div style={{position: 'absolute', left: 18, top: 9, fontFamily: MONO, fontSize: 20, fontWeight: 700, color: winner && chosen ? LOOK : TEST}}>{row.interpreter}</div>
              <div style={{position: 'absolute', left: 18, top: 40, fontFamily: MONO, fontSize: 14, color: MUTED}}>{'"""# doeff: interpreter, default"""'}</div>
            </Box>
          </React.Fragment>
        );
      })}
      {frame >= f(17.2) && frame < f(19.2) && (
        <div
          style={{
            position: 'absolute', left: 40, top: lookY - 8, width: 1190, height: ROW.h + 16, boxSizing: 'border-box', borderRadius: 18,
            border: `3px solid ${LOOK}`, boxShadow: `0 0 18px ${LOOK}88`, opacity: between(frame, 17.2, 18.9),
          }}
        />
      )}
      <div style={{position: 'absolute', left: 0, right: 0, top: 612, textAlign: 'center', fontFamily: MONO, fontSize: 21, color: MUTED, opacity: between(frame, 12.4, 18.0)}}>
        candidates: base_interpreter (0), auth_interpreter (2) → the highest index
      </div>
      <div style={{position: 'absolute', left: 0, right: 0, top: 612, textAlign: 'center', fontFamily: MONO, fontSize: 22, fontWeight: 700, color: TEST, opacity: fadeAt(frame, 18.6)}}>
        chosen: myapp.features.auth.auth_interpreter
      </div>
    </AbsoluteFill>
  );
};

// Scene 4 — every marked env on the path is collected, root first, and merged left to right (discover.log).

const ENV_LINES: Lines = [
  [0, 'Envs are not chosen. Every marked env on the path is collected, root first.'],
  [5.5, 'They merge left to right. A later value overrides an earlier one.'],
  [11, 'timeout is 10 in base_env, 20 in features_env and 30 in auth_env. 30 wins.'],
  [17, 'The merged env keeps every key from all three.'],
];
const ENVS = f(22);
type Entry = [string, string];
const ENV_CARDS: {name: string; module: string; entries: Entry[]; at: number}[] = [
  {name: 'base_env', module: 'myapp', entries: [['db_host', "'localhost'"], ['timeout', '10'], ['debug', 'False']], at: 6.0},
  {name: 'features_env', module: 'myapp.features', entries: [['api_key', "'secret123'"], ['timeout', '20']], at: 7.6},
  {name: 'auth_env', module: 'myapp.features.auth', entries: [['oauth_provider', "'google'"], ['timeout', '30']], at: 9.2},
];
const EC = {y: 120, w: 360, h: 180, gap: 40};
const cardX = (i: number) => 640 - (3 * EC.w + 2 * EC.gap) / 2 + i * (EC.w + EC.gap);
// The merged env in its key order (discover.log, merged:).
const MERGED = ['db_host', 'timeout', 'debug', 'api_key', 'oauth_provider'];
const MG = {x: 300, y: 380, w: 680, row: 40};
const mergedY = (i: number) => MG.y + 54 + i * MG.row + MG.row / 2;
const TRIP = 0.6;
// Envs are data, not handlers: their boxes take a neutral border.
const ENV = '#4a5070';

const EnvScene: React.FC = () => {
  const frame = useSceneFrame();
  // Each key leaves its card in turn; it lands in the merged env TRIP s later.
  const trips = ENV_CARDS.flatMap((card, c) => card.entries.map(([key, value], k) => ({card: c, k, key, value, leave: card.at + k * 0.3})));
  const landed = (key: string) => trips.filter((trip) => trip.key === key && frame >= f(trip.leave + TRIP));
  const timeoutHits = [11.6, 13.0, 14.4];
  return (
    <AbsoluteFill>
      <Headlines lines={ENV_LINES} />
      {ENV_CARDS.map((card, c) => (
        <Box key={card.name} x={cardX(c)} y={EC.y} w={EC.w} h={EC.h} color={ENV} on={frame >= f(card.at) && frame < f(card.at + 1.0)} opacity={fadeAt(frame, 0.8 + c * 0.7, 0.4)}>
          <div style={{position: 'absolute', left: 18, top: 10, fontFamily: MONO, fontSize: 21, fontWeight: 700, color: TEXT}}>
            <span style={{color: LOOK, marginRight: 10}}>{c + 1}</span>
            {card.name}
          </div>
          <div style={{position: 'absolute', left: 18, top: 42, fontFamily: MONO, fontSize: 14, color: MUTED}}>{card.module}</div>
          {card.entries.map(([key, value], k) => {
            const hit = key === 'timeout' && frame >= f(timeoutHits[c]) && frame < f(timeoutHits[c] + 1.4);
            return (
              <div key={key} style={{position: 'absolute', left: 18, top: 76 + k * 30, fontFamily: MONO, fontSize: 18, color: hit ? LOOK : TEXT, fontWeight: hit ? 700 : 400}}>
                {key}: {value}
              </div>
            );
          })}
        </Box>
      ))}
      <Label x={640} y={EC.y + EC.h + 14} size={18} center opacity={fadeAt(frame, 3.0)}>
        root first: <span style={{fontFamily: MONO}}>myapp → myapp.features → myapp.features.auth</span>
      </Label>
      <Box x={MG.x} y={MG.y} w={MG.w} h={54 + MERGED.length * MG.row + 14} color={ENV} on={frame >= f(17.2) && frame < f(18.6)} opacity={fadeAt(frame, 5.6)}>
        <div style={{position: 'absolute', left: 20, top: 12, fontFamily: SANS, fontSize: 21, fontWeight: 700, color: TEXT}}>the merged env</div>
        {MERGED.map((key, i) => {
          const got = landed(key);
          const last = got[got.length - 1];
          const history = got.map((trip) => trip.value);
          return (
            <div key={key} style={{position: 'absolute', left: 20, top: 54 + i * MG.row + 6, display: 'flex', fontFamily: MONO, fontSize: 19, opacity: got.length ? 1 : 0}}>
              <span style={{width: 190, color: TEXT}}>{key}</span>
              <span style={{color: key === 'timeout' && frame >= f(11.6) ? LOOK : TEXT, fontWeight: 700}}>{last ? last.value : ''}</span>
              {key === 'timeout' && (
                <span style={{marginLeft: 30, color: MUTED, opacity: fadeAt(frame, 11.6)}}>
                  {history.slice(0, -1).map((value) => (
                    <span key={value} style={{textDecoration: 'line-through', marginRight: 16}}>{value}</span>
                  ))}
                  {frame >= f(14.6) && <span style={{fontFamily: SANS, color: LOOK}}>the last one wins</span>}
                </span>
              )}
            </div>
          );
        })}
      </Box>
      {trips.map((trip) => {
        const t = ramp(frame, trip.leave, trip.leave + TRIP);
        const from = {x: cardX(trip.card) + EC.w / 2, y: EC.y + EC.h - 10};
        const to = {x: MG.x + 330, y: mergedY(MERGED.indexOf(trip.key))};
        return frame >= f(trip.leave) && frame < f(trip.leave + TRIP + 0.2) ? (
          <Pill key={`${trip.card}-${trip.key}`} x={mix(from.x, to.x, t)} y={mix(from.y, to.y, t)} text={`${trip.key}: ${trip.value}`} color={TEST} opacity={1 - ramp(frame, trip.leave + TRIP, trip.leave + TRIP + 0.2)} />
        ) : null;
      })}
      <div style={{position: 'absolute', left: 0, right: 0, top: 664, textAlign: 'center', fontFamily: MONO, fontSize: 16, color: TEXT, opacity: fadeAt(frame, 18.0)}}>
        {"{'db_host': 'localhost', 'timeout': 30, 'debug': False, 'api_key': 'secret123', 'oauth_provider': 'google'}"}
      </div>
    </AbsoluteFill>
  );
};

// Scene 5 — the chosen interpreter runs the program: reader answers login()'s Asks from the merged env (discover.log).

const RUN_LINES: Lines = [
  [0, 'Then doeff run calls auth_interpreter with the program, and the merged env as env.'],
  [6, 'The interpreter installs its handlers around the program. reader holds the merged env.'],
  [11, 'login() yields Ask("db_host"), then Ask("timeout"). reader answers each, and k resumes.'],
  [17.5, 'The program never named a handler or a config file. It returns "localhost, timeout 30".'],
];
const RUN = f(24);
const AUTH = [
  'def auth_interpreter(prog: Program, *, env: Program[dict] = Pure({})):',
  '    return run(scheduled(state()(writer(reader(env=run(env))(prog)))))',
];
const A = {x: 250, y: 108, w: 780, size: 16, line: 26};
const R = {cx: 640, cy: 446, w: 520, size: 18, line: 28, margin: 30};
const rH = PAD * 2 + LOGIN.length * R.line;
const rCode = {x: R.cx - R.w / 2, y: R.cy - rH / 2};
const rLayer = onion(R.cx, R.cy, R.w, rH, R.margin);
const LAYERS = [
  {name: 'reader(env=the merged env)', handles: 'answers Ask'},
  {name: 'writer', handles: 'Tell'},
  {name: 'state()', handles: 'Get · Put'},
  {name: 'scheduled', handles: 'tasks'},
];

const RunScene: React.FC = () => {
  const frame = useSceneFrame();
  const startX = R.cx + R.w / 2;
  const edge = R.cx + rLayer(0).w / 2;
  const trips = [
    {y: lineMid(rCode.y, R.line, 2), ask: 'Ask("db_host")', answer: "'localhost'", leave: 11.6},
    {y: lineMid(rCode.y, R.line, 3), ask: 'Ask("timeout")', answer: '30', leave: 13.8},
  ];
  const band = frame < f(11.2) ? null : frame < f(13.4) ? 2 : frame < f(15.6) ? mix(2, 3, ramp(frame, 13.4, 13.7)) : mix(3, 4, ramp(frame, 15.6, 15.9));
  const readerOn = trips.some((trip) => frame >= f(trip.leave + 0.6) && frame < f(trip.leave + 1.2)) || (frame >= f(6.4) && frame < f(8.0));
  return (
    <AbsoluteFill>
      <Headlines lines={RUN_LINES} />
      <CodeBlock lines={AUTH} x={A.x} y={A.y} w={A.w} size={A.size} line={A.line} opacity={fadeAt(frame, 0.2)} />
      <div style={{position: 'absolute', left: 0, right: 0, top: 186, textAlign: 'center', fontFamily: MONO, fontSize: 18, color: TEXT, opacity: fadeAt(frame, 1.4)}}>
        auth_interpreter(<span style={{color: ACCENT}}>login_program</span>, env=<span style={{color: TEST}}>the merged env</span>)
      </div>
      {LAYERS.map((layer, i) => {
        const r = rLayer(i);
        const on = i === 0 && readerOn;
        return (
          <div
            key={layer.name}
            style={{
              position: 'absolute', left: r.x, top: r.y, width: r.w, height: r.h, boxSizing: 'border-box', borderRadius: 20,
              border: `3px solid ${TEST}`, ...glow(on, TEST), opacity: fadeAt(frame, 6.0 + (3 - i) * 0.5, 0.4),
            }}
          >
            <div style={{position: 'absolute', left: 14, top: 4, fontFamily: MONO, fontSize: 15, fontWeight: 700, color: on ? LOOK : TEST, whiteSpace: 'nowrap'}}>
              {layer.name}
              <span style={{fontWeight: 400, color: MUTED, marginLeft: 12}}>{layer.handles}</span>
            </div>
          </div>
        );
      })}
      <CodeBlock lines={LOGIN} x={rCode.x} y={rCode.y} w={R.w} size={R.size} line={R.line} band={band} opacity={fadeAt(frame, 2.4)} />
      {trips.map((trip) => (
        <React.Fragment key={trip.ask}>
          {frame >= f(trip.leave) && frame < f(trip.leave + 0.8) && (
            <Pill x={mix(startX, edge, ramp(frame, trip.leave, trip.leave + 0.6))} y={trip.y} text={trip.ask} color={ACCENT} opacity={1 - ramp(frame, trip.leave + 0.6, trip.leave + 0.8)} />
          )}
          {frame >= f(trip.leave + 1.0) && frame < f(trip.leave + 1.8) && (
            <Pill x={mix(edge, startX, ramp(frame, trip.leave + 1.0, trip.leave + 1.6))} y={trip.y} text={trip.answer} color={TEST} opacity={1 - ramp(frame, trip.leave + 1.6, trip.leave + 1.8)} />
          )}
        </React.Fragment>
      ))}
      <Label x={rCode.x} y={rCode.y + rH + 4} size={16} bold color={ACCENT} opacity={between(frame, 12.4, 16.4)}>
        <span style={{fontFamily: MONO}}>k</span> resumes with the answer
      </Label>
      <div style={{position: 'absolute', left: 0, right: 0, top: 676, textAlign: 'center', fontFamily: MONO, fontSize: 22, fontWeight: 700, color: ACCENT, opacity: fadeAt(frame, 17.6)}}>
        {"→ 'localhost, timeout 30'"}
      </div>
    </AbsoluteFill>
  );
};

// Scene 6 — overrides: --env, --interpreter, their deprecation, and --hy (cli.log).

const OVERRIDE_LINES: Lines = [
  [0, 'To run the same program with something else, say so on the command line.'],
  [5, '--env replaces the discovered envs. auth_interpreter is still discovered.'],
  [11, '--interpreter turns off discovery of both. With no --env, the first Ask fails.'],
  [17, 'Both flags warn that they are deprecated. --hy composes the handlers inline instead.'],
];
const OVERRIDE = f(25);
type Run = {command: string[]; interpreter: string; envs: string; result: string; failed?: boolean; at: number};
// The runs of cli.log, in its order (the long form of scene 1 left out).
const RUNS: Run[] = [
  {command: [`$ doeff run --program ${PROGRAM}`], interpreter: 'auth_interpreter (discovered)', envs: 'base_env, features_env, auth_env', result: "'localhost, timeout 30'", at: 0.8},
  {command: ['$ doeff run --program …login_program --env tests.fakes.test_env'], interpreter: 'auth_interpreter (discovered)', envs: 'test_env', result: "'fake-db, timeout 1'", at: 5.4},
  {command: ['$ doeff run --program …login_program --interpreter tests.fakes.test_interpreter'], interpreter: 'test_interpreter', envs: 'none', result: "MissingEnvKeyError: Ask: key not found: 'db_host'", failed: true, at: 11.4},
  {command: ['$ doeff run --program …login_program --interpreter tests.fakes.test_interpreter --env tests.fakes.test_env'], interpreter: 'test_interpreter', envs: 'test_env', result: "'fake-db, timeout 1'", at: 14.0},
  {
    command: [
      "$ doeff run --hy '(import myapp.features.auth.login [login-program])",
      '    (import doeff-core-effects [reader])',
      `    ((reader :env {"db_host" "fake-db" "timeout" 1}) login-program)'`,
    ],
    interpreter: 'none (reader by hand)',
    envs: 'none',
    result: "'fake-db, timeout 1'",
    at: 20.0,
  },
];
const RUN_ROW = 26;
const runY = (i: number) => 112 + [0, 82, 164, 246, 392][i];

const OverrideScene: React.FC = () => {
  const frame = useSceneFrame();
  const current = RUNS.reduce((found, run, i) => (frame >= f(run.at) ? i : found), -1);
  return (
    <AbsoluteFill>
      <Headlines lines={OVERRIDE_LINES} />
      {RUNS.map((run, i) => (
        <div key={i} style={{position: 'absolute', left: 60, top: runY(i), width: 1160, opacity: fadeAt(frame, run.at, 0.4)}}>
          <div style={{fontFamily: MONO, fontSize: 15, lineHeight: `${RUN_ROW}px`, color: i === current ? TEXT : MUTED, whiteSpace: 'pre'}}>
            {run.command.map((line) => (
              <div key={line}>{line}</div>
            ))}
          </div>
          <div style={{display: 'flex', fontFamily: SANS, fontSize: 15, lineHeight: '28px', color: MUTED, whiteSpace: 'nowrap', opacity: fadeAt(frame, run.at + 0.6)}}>
            <span style={{width: 360}}>
              interpreter <span style={{fontFamily: MONO, color: i === current ? TEST : TEXT}}>{run.interpreter}</span>
            </span>
            <span style={{width: 340}}>
              envs <span style={{fontFamily: MONO, color: TEXT}}>{run.envs}</span>
            </span>
            <span style={{fontFamily: MONO, fontWeight: 700, color: run.failed ? FAIL : ACCENT}}>→ {run.result}</span>
          </div>
        </div>
      ))}
      <div
        style={{
          position: 'absolute', left: 60, top: 112 + 328 - 8, width: 1160, height: 48, boxSizing: 'border-box', borderRadius: 10, border: `2px dashed ${MUTED}`,
          padding: '0 16px', lineHeight: '44px', fontFamily: MONO, fontSize: 15, color: TEXT, opacity: fadeAt(frame, 17.6),
        }}
      >
        [doeff] DeprecationWarning: <span style={{color: LOOK}}>--interpreter is deprecated</span> · <span style={{color: LOOK}}>--env is deprecated</span>
        <span style={{fontFamily: SANS, color: MUTED, marginLeft: 16}}>both point to --hy</span>
      </div>
      {current >= 0 && (
        <div style={{position: 'absolute', left: 40, top: runY(current) + 8, width: 6, height: RUNS[current].command.length * RUN_ROW + 20, borderRadius: 3, background: LOOK}} />
      )}
      <Label x={640} y={666} size={20} bold color={TEST} center opacity={fadeAt(frame, 21.4)}>
        with --hy, the override is a handler you compose around the program
      </Label>
    </AbsoluteFill>
  );
};

// Scene 7 — no marked interpreter on the path: the fallback interpreter ignores envs (cli.log, other.job).

const FALLBACK_LINES: Lines = [
  [0, 'No marked interpreter on the path? doeff run does not stop. It uses its fallback interpreter.'],
  [7, 'It ignores envs, so Ask("timeout") finds no handler. --format json shows what was chosen.'],
];
const FALLBACK = f(15);
// The handlers of doeff.cli.run_services:default_interpreter, innermost first (cli.log, the UnhandledEffect).
const DEFAULT_STACK = ['await_handler', '_listen_handler', '_slog_handler', '_try_handler', '_writer_handler', 'state', 'lazy_ask', 'scheduled'];
const STACK = {x: 700, y: 152, w: 400, row: 46};

const FallbackScene: React.FC = () => {
  const frame = useSceneFrame();
  const fall = ramp(frame, 7.6, 10.4);
  const bottom = STACK.y + DEFAULT_STACK.length * STACK.row;
  const askY = mix(STACK.y - 12, bottom + 56, fall);
  const passing = Math.floor((askY - STACK.y) / STACK.row);
  return (
    <AbsoluteFill>
      <Headlines lines={FALLBACK_LINES} />
      <div style={{position: 'absolute', left: 60, top: 132, fontFamily: MONO, fontSize: 20, color: TEXT, opacity: fadeAt(frame, 0.3)}}>
        <span style={{color: MUTED}}>--program </span>other.job.job_program
      </div>
      {['other', 'other.job'].map((module, i) => (
        <Box key={module} x={60 + i * 30} y={180 + i * 84} w={520 - i * 30} h={64} color="#2a2e40" opacity={fadeAt(frame, 0.8 + i * 0.5)}>
          <div style={{position: 'absolute', left: 18, top: 16, fontFamily: MONO, fontSize: 20, fontWeight: 700, color: TEXT}}>{module}</div>
          <div style={{position: 'absolute', right: 18, top: 20, fontFamily: SANS, fontSize: 17, color: MUTED}}>no marked interpreter</div>
        </Box>
      ))}
      <Label x={60} y={360} size={17} opacity={fadeAt(frame, 2.2)}>
        <span style={{fontFamily: MONO}}>myapp.base_interpreter</span> is marked, but not on this path
      </Label>
      <Box x={STACK.x - 20} y={STACK.y - 46} w={STACK.w + 40} h={DEFAULT_STACK.length * STACK.row + 60} color={TEST} dashed opacity={fadeAt(frame, 3.0)}>
        <div style={{position: 'absolute', left: 18, top: 8, fontFamily: MONO, fontSize: 16, fontWeight: 700, color: TEST}}>default_interpreter</div>
      </Box>
      {DEFAULT_STACK.map((name, i) => (
        <div
          key={name}
          style={{
            position: 'absolute', left: STACK.x, top: STACK.y + i * STACK.row, width: STACK.w, height: STACK.row - 8, boxSizing: 'border-box', borderRadius: 10,
            border: `2px solid ${frame >= f(7.6) && passing === i ? LOOK : '#2a2e40'}`, background: '#0b0d13', fontFamily: MONO, fontSize: 17, color: TEXT,
            lineHeight: `${STACK.row - 12}px`, paddingLeft: 16, opacity: fadeAt(frame, 3.4 + i * 0.12),
          }}
        >
          {name}
        </div>
      ))}
      <Label x={STACK.x + STACK.w + 30} y={STACK.y + 8} size={14} opacity={fadeAt(frame, 4.0)}>innermost</Label>
      <Label x={STACK.x + STACK.w + 30} y={bottom - STACK.row + 8} size={14} opacity={fadeAt(frame, 4.0)}>outermost</Label>
      {frame >= f(7.4) && <Pill x={STACK.x + STACK.w - 70} y={askY} text={'Ask("timeout")'} color={fall < 1 ? ACCENT : FAIL} />}
      <div style={{position: 'absolute', left: 60, top: 420, width: 580, fontFamily: MONO, fontSize: 15, lineHeight: '24px', color: FAIL, opacity: fadeAt(frame, 10.4)}}>
        <div>UnhandledEffect: no handler found for effect:</div>
        <div>Ask (Ask(&apos;timeout&apos;))</div>
      </div>
      <div style={{position: 'absolute', left: 60, top: 500, width: 580, fontFamily: MONO, fontSize: 15, lineHeight: '24px', color: TEXT, opacity: fadeAt(frame, 11.8)}}>
        <div style={{fontFamily: SANS, color: MUTED}}>other.job.ping_program --format json:</div>
        <div>{'"interpreter": "doeff.cli.run_services:'}</div>
        <div>{'                default_interpreter",'}</div>
        <div>{'"envs": [], "result": "pong"'}</div>
      </div>
    </AbsoluteFill>
  );
};

// The outro.

const OUTRO_LINES: Lines = [[0, 'Markers keep handlers next to the code. doeff run finds them by where the program lives.']];
const OUTRO = f(9);
const STEPS = ['marked symbols', 'the closest interpreter', 'envs merged, root first', 'the program runs'];

const OutroScene: React.FC = () => {
  const frame = useSceneFrame();
  return (
    <AbsoluteFill>
      <Headlines lines={OUTRO_LINES} />
      <div style={{position: 'absolute', left: 0, right: 0, top: 170 + mix(16, 0, ramp(frame, 0.2, 0.8)), textAlign: 'center', opacity: fadeAt(frame, 0.2, 0.5)}}>
        <div style={{fontFamily: SANS, fontSize: 64, fontWeight: 700, color: TEXT}}>doeff</div>
        <div style={{fontFamily: SANS, fontSize: 30, color: ACCENT, marginTop: 4}}>Algebraic effects for Python</div>
      </div>
      <Label x={640} y={380} size={22} center opacity={fadeAt(frame, 1.0)}>
        <span style={{fontFamily: MONO, color: TEXT}}>doeff run --program {PROGRAM}</span>
      </Label>
      {STEPS.map((step, i) => (
        <div
          key={step}
          style={{
            position: 'absolute', left: 640 - (STEPS.length * 286 - 16) / 2 + i * 286, top: 440, width: 270, height: 56, boxSizing: 'border-box', borderRadius: 12,
            border: `2px solid ${TEST}`, background: PANEL, fontFamily: SANS, fontSize: 18, fontWeight: 700, color: TEST, lineHeight: '52px', textAlign: 'center',
            opacity: fadeAt(frame, 1.6 + i * 0.4),
          }}
        >
          {step}
        </div>
      ))}
      <Label x={640} y={548} size={22} color={TEXT} center opacity={fadeAt(frame, 3.6)}>
        The program yields effects. Where it lives decides which handlers answer them.
      </Label>
    </AbsoluteFill>
  );
};

// The scenes in order; Root.tsx registers CLI_AUTO_DISCOVERY.length frames of CLI_AUTO_DISCOVERY.Video.
export const CLI_AUTO_DISCOVERY = narrate('cli-auto-discovery', DURATIONS, [
  {length: TITLE, starts: startsOf(TITLE_LINES), ids: ['t1', 't2', 't3'], node: <TitleScene />},
  {length: MARKERS, starts: startsOf(MARKER_LINES), ids: ['m1', 'm2', 'm3'], node: <MarkerScene />},
  {length: INTERP, starts: startsOf(INTERP_LINES), ids: ['i1', 'i2', 'i3', 'i4'], node: <InterpreterScene />},
  {length: ENVS, starts: startsOf(ENV_LINES), ids: ['e1', 'e2', 'e3', 'e4'], node: <EnvScene />},
  {length: RUN, starts: startsOf(RUN_LINES), ids: ['r1', 'r2', 'r3', 'r4'], node: <RunScene />},
  {length: OVERRIDE, starts: startsOf(OVERRIDE_LINES), ids: ['o1', 'o2', 'o3', 'o4'], node: <OverrideScene />},
  {length: FALLBACK, starts: startsOf(FALLBACK_LINES), ids: ['f1', 'f2'], node: <FallbackScene />},
  {length: OUTRO, starts: [0], ids: ['z1'], node: <OutroScene />},
]);
