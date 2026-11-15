// The video at the top of the article "Record and replay" (docs/articles/record-and-replay.md): the idea in plain words
// (write down each question and its answer, then answer from the notes), latest_releases and github read from the top,
// then recording(tape) put between them, and github taken away for replaying(tape). The function never changes. Every
// code line on screen is a line of examples/record_and_replay/releases.py or of measure/opening.py (check_code.py checks
// it); every value is a line of measure/opening.py's output. The motions of a scene are at least 0.9 s apart (planOf
// checks it when the page is loaded).
import React from 'react';
import {AbsoluteFill} from 'remotion';
import {fadeAt} from '../../kit/motion';
import {narrate} from '../../kit/narrated';
import {CodeBlock, Headlines, IntroStill, Lines} from '../../kit/parts';
import {ACCENT, f, LOOK, MONO, MUTED, PANEL, PROD, SANS, TEST, TEXT} from '../../kit/theme';
import {useSceneFrame} from '../../kit/warp';
import {Box, Lit, mid, Say} from '../article2-opening/Article2Opening';
import {Geometry, K, Moving} from '../article-opening/ArticleOpening';
import {Landed, Layer, LitRange, Out, Panel, planOf, ringOf, Stack} from '../article3-opening/Article3Opening';
import DURATIONS from './durations.json';
import NARRATION from './narration.json';

const PAGE = 'article4-opening';

// latest_releases as releases.py writes it (lines 18-28): the code of the opening still and of the reading scene.
export const PROGRAM_CODE = [
  '@do',
  'def latest_releases(repos: list[str], token: str, api: str = GITHUB):',
  '    tags = {}',
  '    for repo in repos:',
  '        body = yield HttpRequest(',
  '            "GET",',
  '            f"{api}/repos/{repo}/releases/latest",',
  '            headers={"Authorization": f"Bearer {token}"},',
  '        )',
  '        tags[repo] = json.loads(body)["tag_name"]',
  '    return tags',
];

// A box with a word in it: the program, the world, a test.
export const Thing: React.FC<{x: number; y: number; w: number; h: number; text: string; color: string; from: number; until?: number; dim?: number; round?: number}> = ({
  x, y, w, h, text, color, from, until, dim = 1, round = 14,
}) => {
  const frame = useSceneFrame();
  const shown = fadeAt(frame, from, 0.4) * (until === undefined ? 1 : 1 - fadeAt(frame, until, 0.5));
  return (
    <div
      style={{
        position: 'absolute', left: x, top: y, width: w, height: h, boxSizing: 'border-box', borderRadius: round, border: `3px solid ${color}`, background: '#0b0d13',
        display: 'flex', alignItems: 'center', justifyContent: 'center', fontFamily: SANS, fontSize: 24, fontWeight: 700, color: TEXT, opacity: shown * dim,
      }}
    >
      {text}
    </div>
  );
};

// A row of a panel that fades in: a key and its value.
export const Row: React.FC<{x: number; y: number; from: number; left: string; right: string; color?: string; size?: number; lit?: boolean}> = ({
  x, y, from, left, right, color = TEST, size = 16, lit = false,
}) => {
  const frame = useSceneFrame();
  return (
    <div
      style={{
        position: 'absolute', left: x, top: y, fontFamily: MONO, fontSize: size, color: TEXT, whiteSpace: 'pre', opacity: fadeAt(frame, from),
        background: lit ? `${LOOK}33` : 'transparent', borderRadius: 6, padding: '0 4px',
      }}
    >
      <span style={{color: MUTED}}>{left}</span>
      <span style={{color}}>{right}</span>
    </div>
  );
};

// A number that shows `before` until `at`, then `after`, lit for a moment when it changes.
export const Counter: React.FC<{x: number; y: number; label: string; values: [number, string][]; from: number}> = ({x, y, label, values, from}) => {
  const frame = useSceneFrame();
  const shown = [...values].reverse().find(([at]) => frame >= f(at));
  const changed = values.some(([at], i) => i > 0 && frame >= f(at) && frame < f(at + 0.6));
  return (
    <div style={{position: 'absolute', left: x, top: y, fontFamily: MONO, fontSize: 24, fontWeight: 700, color: changed ? LOOK : TEXT, opacity: fadeAt(frame, from), whiteSpace: 'pre'}}>
      <span style={{color: MUTED}}>{label}</span>
      {shown ? shown[1] : values[0][1]}
    </div>
  );
};

// 1. The opening, still: what the video shows, what doeff is, and the function it follows.
const INTRO = planOf(PAGE, 'intro', DURATIONS, [
  ['o1', {}],
  ['o2', {}],
  ['o3', {}],
]);
const Intro: React.FC = () => (
  <AbsoluteFill>
    <div style={{position: 'absolute', inset: 0, opacity: 0.9}}>
      <IntroStill title="Record and replay" label="the function this video follows" code={PROGRAM_CODE} w={900} />
    </div>
  </AbsoluteFill>
);

// 2. The idea in plain words: a program asks, notes keep each question and its answer, and the notes answer later.
const IDEA = planOf(PAGE, 'idea', DURATIONS, [
  ['o4', {ask: 0.3, answer: 1.2}],
  ['o5', {note1: 0.2, note2: 1.1}],
  ['o6', {gone: 0.2, fromNotes: 1.1}],
  ['o7', {same: 0.3}],
]);
const IDEA_LINES: Lines = [[0, 'Write down the answers, then answer from the notes']];
const PROGRAM = {x: 120, y: 200, w: 220, h: 96};
const WORLD = {x: 900, y: 190, w: 260, h: 116};
const NOTES = {x: 470, y: 380, w: 340, h: 200};
const Idea: React.FC = () => {
  const {t} = IDEA;
  const right = PROGRAM.x + PROGRAM.w;
  const row = (i: number, from: number) => (
    <Row key={i} x={NOTES.x + 24} y={NOTES.y + 56 + i * 40} from={from} left={`question ${i + 1}  `} right={`answer ${i + 1}`} size={20} />
  );
  return (
    <AbsoluteFill>
      <Headlines lines={IDEA_LINES} />
      <Thing {...PROGRAM} text="a program" color={ACCENT} from={0.1} />
      <Thing {...WORLD} text="the world" color={PROD} from={0.1} until={t.gone} round={58} />
      <Say x={WORLD.x} w={WORLD.w} y={WORLD.y + WORLD.h + 12} from={t.gone + 0.3} size={20} color={MUTED} center>nobody here</Say>
      <Moving text="a question" color={ACCENT} from={t.ask} to={t.ask + 0.7} until={t.note1 + 0.4} x0={right + 80} y0={226} x1={WORLD.x - 80} y1={226} />
      <Moving text="an answer" color={PROD} from={t.answer} to={t.answer + 0.7} until={t.note1 + 0.4} x0={WORLD.x - 80} y0={272} x1={right + 80} y1={272} />
      <Panel x={NOTES.x} y={NOTES.y} w={NOTES.w} h={NOTES.h} title="notes" from={t.note1} color={TEST} />
      {row(0, t.note1)}
      {row(1, t.note2)}
      <Moving text="answer 1" color={TEST} from={t.fromNotes} to={t.fromNotes + 0.8} until={t.same + 2.4} x0={NOTES.x + 60} y0={NOTES.y - 10} x1={right + 60} y1={PROGRAM.y + PROGRAM.h + 30} />
      <Say x={PROGRAM.x - 20} y={PROGRAM.y + PROGRAM.h + 80} from={t.same} size={22} color={LOOK}>{'runs the same way'}</Say>
    </AbsoluteFill>
  );
};

// 3. HttpRequest and latest_releases read from the top: what the function does, line by line, and where it pauses.
const READ_CODE = [
  '@dataclass(frozen=True)',
  'class HttpRequest(EffectBase):',
  '    method: str',
  '    url: str',
  '    headers: dict[str, str] = field(default_factory=dict)',
  ' ',
  ...PROGRAM_CODE,
];
// releases.py's line number of a line of READ_CODE (one of the two blank lines 16-17 is left out).
const R = (line: number) => (line <= 16 ? line - 11 : line - 12);
const CODE: Box = {x: 30, y: 78, w: 700, line: 24};
const READ = planOf(PAGE, 'read', DURATIONS, [
  ['o8', {code: 0.1, effect: 1.0}],
  ['o9', {method: 0.2, url: 1.1, headers: 2.0}],
  ['o10', {start: 0.2}],
  ['o11', {loop: 0.2, request: 1.1}],
  ['o12', {pause: 0.2, handler: 1.1}],
  ['o13', {k: 0.2}],
  ['o14', {body: 0.2, keep: 1.1}],
  ['o15', {result: 0.2}],
]);
const READ_LINES: Lines = [[0, 'A question is a value: HttpRequest']];
const HANDLER = {x: 800, y: 300, w: 400, h: 230};
const Read: React.FC = () => {
  const {t} = READ;
  const frame = useSceneFrame();
  const paused = frame >= f(t.pause) && frame < f(t.body);
  return (
    <AbsoluteFill>
      <Headlines lines={READ_LINES} />
      <CodeBlock
        lines={READ_CODE} x={CODE.x} y={CODE.y} w={CODE.w} size={14} line={CODE.line} opacity={fadeAt(frame, t.code)}
        dim={(i) => (paused && i > R(26) ? 0.35 : 1)}
      />
      <LitRange box={CODE} a={R(11)} b={R(12)} from={t.effect} until={t.method} />
      <Lit box={CODE} i={R(13)} from={t.method} until={t.url} />
      <Lit box={CODE} i={R(14)} from={t.url} until={t.headers} />
      <Lit box={CODE} i={R(15)} from={t.headers} until={t.start} />
      <Lit box={CODE} i={R(20)} from={t.start} until={t.loop} />
      <Lit box={CODE} i={R(21)} from={t.loop} until={t.request} />
      <LitRange box={CODE} a={R(22)} b={R(26)} from={t.request} until={t.k} />
      <LitRange box={CODE} a={R(27)} b={R(28)} from={t.k} until={t.body} color={K} />
      <Lit box={CODE} i={R(27)} from={t.body} until={t.result} />
      <Lit box={CODE} i={R(28)} from={t.result} />
      <Panel x={800} y={82} w={400} h={190} title="the question, as a value" from={t.effect}>
        <Row x={18} y={44} from={t.method} left="method   " right='"GET"' color={ACCENT} />
        <Row x={18} y={74} from={t.url} left="url      " right='".../acme/server/..."' color={ACCENT} />
        <Row x={18} y={104} from={t.headers} left="headers  " right='{"Authorization": …}' color={ACCENT} />
      </Panel>
      <Panel x={HANDLER.x} y={HANDLER.y} w={HANDLER.w} h={HANDLER.h} title="a handler, outside the function" from={t.handler} color={TEST} />
      <Landed text="HttpRequest(…)" color={ACCENT} from={t.request} x0={CODE.x + CODE.w - 160} y0={mid(CODE, R(22))} x1={HANDLER.x + 120} y1={HANDLER.y + 70} until={t.body} />
      <Landed text="k" color={K} from={t.handler} x0={CODE.x + CODE.w - 80} y0={mid(CODE, R(27))} x1={HANDLER.x + 290} y1={HANDLER.y + 70} until={t.body} />
      <Say x={HANDLER.x + 20} y={HANDLER.y + 110} from={t.k} size={18} color={K}>{'k: the rest of the function,\nlines 27-28, resumed once'}</Say>
      <Moving text={`'{"tag_name": "v2.4.1"}'`} color={TEST} from={t.body} to={t.body + 0.7} until={t.keep + 0.4} x0={HANDLER.x + 200} y0={HANDLER.y + 190} x1={CODE.x + CODE.w - 170} y1={mid(CODE, R(27))} />
      <Say x={HANDLER.x} y={HANDLER.y + HANDLER.h + 20} from={t.keep} mono size={18}>
        <span style={{color: MUTED}}>tags </span>
        <span style={{color: LOOK}}>{'{"acme/server": "v2.4.1", …}'}</span>
      </Say>
    </AbsoluteFill>
  );
};

// 4. github: the handler that answers the way the API does; run under it, two requests, two tags, sent: 2.
const GITHUB_CODE = [
  '@handler',
  'def github(effect: HttpRequest, k):',
  '    sent[effect.url] += 1',
  '    repo = effect.url.removeprefix(f"{GITHUB}/repos/").removesuffix("/releases/latest")',
  '    return (yield Resume(k, json.dumps({"tag_name": TAGS[repo]})))',
  ' ',
  'tags = run(github(latest_releases(REPOS, "secret")))',
];
const GC: Box = {x: 30, y: 78, w: 1220, line: 26};
const G = (line: number) => line - 37;
const GG: Geometry = {cx: 330, cy: 460, bw: 250, bh: 84, gap: 46, font: 17};
const GITHUB = planOf(PAGE, 'github', DURATIONS, [
  ['o16', {ring: 0.2, out: 1.1, count: 2.0}],
  ['o17', {repo: 0.2}],
  ['o18', {resume: 0.2, back: 1.1}],
  ['o19', {run: 0.2, second: 1.1, count2: 2.0, result: 2.9}],
  ['o20', {sent: 0.2}],
]);
const GITHUB_LINES: Lines = [[0, 'github answers the way the API does']];
const SIDE = {x: 640, y: 300, w: 610, h: 300};
const Github: React.FC = () => {
  const {t} = GITHUB;
  const frame = useSceneFrame();
  const layers: Layer[] = [{label: 'github', at: 0, color: PROD, from: t.ring, glow: [t.out, t.run]}];
  return (
    <AbsoluteFill>
      <Headlines lines={GITHUB_LINES} />
      <CodeBlock lines={GITHUB_CODE} x={GC.x} y={GC.y} w={GC.w} size={15} line={GC.line} opacity={fadeAt(frame, t.ring)} />
      <Lit box={GC} i={G(39)} from={t.count} until={t.repo} />
      <Lit box={GC} i={G(40)} from={t.repo} until={t.resume} />
      <Lit box={GC} i={G(41)} from={t.resume} until={t.run} />
      <Lit box={GC} i={6} from={t.run} />
      <Stack layers={layers} g={GG} title="latest_releases" />
      <Out g={GG} ring={0} text="HttpRequest" from={t.out} dy={-22} until={t.back} />
      <Out g={GG} ring={0} text="body" color={TEST} from={t.back} dy={22} back />
      <Out g={GG} ring={0} text="HttpRequest" from={t.second} dy={-22} until={t.second + 0.8} />
      <Panel x={SIDE.x} y={SIDE.y} w={SIDE.w} h={SIDE.h} title="what it shows" from={t.ring} color={PROD}>
        <Counter x={18} y={44} label="sent  " values={[[0, '0'], [t.count, '1'], [t.count2, '2']]} from={t.ring} />
        <Row x={18} y={94} from={t.repo} left="repo  " right='"acme/server"' color={PROD} />
        <Row x={18} y={164} from={t.result} left="" right={"{'acme/server': 'v2.4.1',\n 'acme/client': 'v1.9.0'}"} color={LOOK} size={18} />
      </Panel>
      <div
        style={{
          position: 'absolute', left: SIDE.x + 6, top: SIDE.y + 38, width: 190, height: 44, borderRadius: 10, border: `3px solid ${LOOK}`,
          opacity: fadeAt(frame, t.sent) * (frame < f(t.sent + 2.2) ? 1 : 0.4),
        }}
      />
    </AbsoluteFill>
  );
};

// 5. recording(tape) between latest_releases and github: record yields the request on, keeps the answer, resumes k.
const RECORD_CODE = [
  'def recording(tape: dict):',
  '    @handler',
  '    def record(effect: HttpRequest, k):',
  '        answer = yield effect',
  '        tape[(effect.method, effect.url)] = answer',
  '        return (yield Resume(k, answer))',
  ' ',
  '    return record',
];
const RC: Box = {x: 640, y: 78, w: 610, line: 26};
const RL = (line: number) => line - 44;
const RG: Geometry = {cx: 300, cy: 400, bw: 240, bh: 80, gap: 50, font: 17};
const RECORD = planOf(PAGE, 'record', DURATIONS, [
  ['o21', {ring: 0.3}],
  ['o22', {inward: 0.2, on: 1.1, answer: 2.0}],
  ['o23', {keep: 0.2, resume: 1.1, second: 2.0}],
]);
const RECORD_LINES: Lines = [[0, 'recording(tape): keep every answer on its way back']];
const TAPE = {x: 640, y: 360, w: 610, h: 220};
// The pairs record keeps (measure/opening.log, o6 tape).
export const TAPE_ROWS: [string, string][] = [
  ['("GET", ".../acme/server/releases/latest")', '{"tag_name": "v2.4.1"}'],
  ['("GET", ".../acme/client/releases/latest")', '{"tag_name": "v1.9.0"}'],
];
export const TapeRows: React.FC<{x: number; y: number; from: [number, number]; lit?: (i: number) => boolean}> = ({x, y, from, lit = () => false}) => (
  <>
    {TAPE_ROWS.map(([key, answer], i) => (
      <React.Fragment key={key}>
        <Row x={x} y={y + i * 60} from={from[i]} left="" right={key} color={TEXT} size={15} lit={lit(i)} />
        <Row x={x + 20} y={y + i * 60 + 24} from={from[i]} left="-> " right={answer} color={TEST} size={15} lit={lit(i)} />
      </React.Fragment>
    ))}
  </>
);
const Record: React.FC = () => {
  const {t} = RECORD;
  const frame = useSceneFrame();
  const layers: Layer[] = [
    {label: 'record', at: 0, color: TEST, from: t.ring, glow: [t.inward, t.second]},
    {label: 'github', at: 1, color: PROD, from: 0.1, glow: [t.on + 0.5, t.answer + 0.8]},
  ];
  const r0 = ringOf(RG, 0).right;
  const r1 = ringOf(RG, 1).right;
  return (
    <AbsoluteFill>
      <Headlines lines={RECORD_LINES} />
      <CodeBlock lines={RECORD_CODE} x={RC.x} y={RC.y} w={RC.w} size={15} line={RC.line} opacity={fadeAt(frame, 0.1)} />
      <LitRange box={RC} a={RL(44)} b={RL(46)} from={t.ring} until={t.on} />
      <Lit box={RC} i={RL(47)} from={t.on} until={t.keep} />
      <Lit box={RC} i={RL(48)} from={t.keep} until={t.resume} />
      <Lit box={RC} i={RL(49)} from={t.resume} until={t.second + 1.2} />
      <Stack layers={layers} g={RG} title="latest_releases" />
      <Out g={RG} ring={0} text="HttpRequest" from={t.inward} dy={-22} until={t.on} />
      <Moving text="HttpRequest" color={ACCENT} from={t.on} to={t.on + 0.7} until={t.answer} x0={r0 - 40} y0={RG.cy - 22} x1={r1 - 6} y1={RG.cy - 22} />
      <Moving text="body" color={TEST} from={t.answer} to={t.answer + 0.7} until={t.resume} x0={r1 - 6} y0={RG.cy + 22} x1={r0 - 10} y1={RG.cy + 22} />
      <Out g={RG} ring={0} text="body" color={TEST} from={t.resume} dy={22} back until={t.second} />
      <Moving text="HttpRequest → body" color={ACCENT} from={t.second} to={t.second + 0.7} until={t.second + 1.0} x0={RG.cx + 60} y0={RG.cy} x1={r1 - 60} y1={RG.cy} />
      <Panel x={TAPE.x} y={TAPE.y} w={TAPE.w} h={TAPE.h} title="tape" from={t.ring} color={TEST}>
        <TapeRows x={14} y={44} from={[t.keep, t.second + 0.6]} lit={(i) => (i === 0 ? frame >= f(t.keep) && frame < f(t.resume) : false)} />
      </Panel>
      <Say x={RG.cx - 200} y={RG.cy + 190} from={t.second + 0.6} mono size={20}>
        <span style={{color: MUTED}}>sent </span>
        <span style={{color: LOOK}}>2</span>
      </Say>
    </AbsoluteFill>
  );
};

// 6. github taken away, replaying(tape) alone: replay answers each request from the tape, and nothing is sent.
const REPLAY_CODE = [
  'def replaying(tape: dict):',
  '    @handler',
  '    def replay(effect: HttpRequest, k):',
  '        key = (effect.method, effect.url)',
  '        if key not in tape:',
  '            error = LookupError(f"not recorded: {effect.method} {effect.url}")',
  '            return (yield ResumeThrow(k, error))',
  '        return (yield Resume(k, tape[key]))',
];
const PC: Box = {x: 560, y: 78, w: 690, line: 26};
const PL = (line: number) => line - 54;
const REPLAY = planOf(PAGE, 'replay', DURATIONS, [
  ['o24', {away: 0.2, ring: 1.1}],
  ['o25', {inward: 0.2, find: 1.1, back: 2.0}],
  ['o26', {second: 0.2, result: 1.1}],
]);
const REPLAY_LINES: Lines = [[0, 'replaying(tape): answer from the tape']];
const Replay: React.FC = () => {
  const {t} = REPLAY;
  const frame = useSceneFrame();
  const layers: Layer[] = [
    {label: 'record', at: 0, color: TEST, from: 0, until: t.away},
    {label: 'github', at: 1, color: PROD, from: 0, until: t.away},
    {label: 'replay', at: 0, color: K, from: t.ring, glow: [t.inward, t.result]},
  ];
  return (
    <AbsoluteFill>
      <Headlines lines={REPLAY_LINES} />
      <CodeBlock lines={REPLAY_CODE} x={PC.x} y={PC.y} w={PC.w} size={14} line={PC.line} opacity={fadeAt(frame, t.ring)} />
      <Lit box={PC} i={PL(57)} from={t.inward} until={t.find} />
      <Lit box={PC} i={PL(58)} from={t.find} until={t.back} />
      <Lit box={PC} i={PL(61)} from={t.back} until={t.result + 1.2} />
      <Stack layers={layers} g={RG} title="latest_releases" />
      <Out g={RG} ring={0} text="HttpRequest" from={t.inward} dy={-22} until={t.back} />
      <Out g={RG} ring={0} text="body" color={TEST} from={t.back} dy={22} back />
      <Moving text="HttpRequest → body" color={ACCENT} from={t.second} to={t.second + 0.7} until={t.second + 1.0} x0={RG.cx + 60} y0={RG.cy} x1={ringOf(RG, 0).right - 60} y1={RG.cy} />
      <Panel x={TAPE.x} y={TAPE.y} w={TAPE.w} h={TAPE.h} title="tape" from={0} color={TEST}>
        <TapeRows x={14} y={44} from={[0, 0]} lit={(i) => (i === 0 ? frame >= f(t.find) && frame < f(t.second) : frame >= f(t.second) && frame < f(t.result))} />
      </Panel>
      <Say x={60} y={RG.cy + 160} from={t.result} mono size={18}>
        <span style={{color: LOOK}}>{"{'acme/server': 'v2.4.1', 'acme/client': 'v1.9.0'}"}</span>
      </Say>
      <Say x={60} y={RG.cy + 200} from={t.ring} mono size={20}>
        <span style={{color: MUTED}}>sent </span>
        <span style={{color: frame >= f(t.result) ? LOOK : TEXT}}>0</span>
      </Say>
    </AbsoluteFill>
  );
};

// 7. The three stacks around the same function: only the handlers changed.
const SAME = planOf(PAGE, 'same', DURATIONS, [['o27', {github: 0.2, record: 1.1, replay: 2.0, note: 2.9}]], 1.4);
const SAME_LINES: Lines = [[0, 'One function, three stacks']];
const tiny = (cx: number): Geometry => ({cx, cy: 330, bw: 200, bh: 60, gap: 30, font: 14});
const Same: React.FC = () => {
  const {t} = SAME;
  const stacks: [number, string, Layer[], number][] = [
    [220, 'asks the API', [{label: 'github', at: 0, color: PROD}], t.github],
    [640, 'records', [{label: 'record', at: 0, color: TEST}, {label: 'github', at: 1, color: PROD}], t.record],
    [1060, 'replays', [{label: 'replay', at: 0, color: K}], t.replay],
  ];
  return (
    <AbsoluteFill>
      <Headlines lines={SAME_LINES} />
      {stacks.map(([cx, name, layers, from]) => (
        <React.Fragment key={name}>
          <Stack layers={layers.map((layer) => ({...layer, from}))} g={tiny(cx)} from={from} title="latest_releases" />
          <Say x={cx - 150} w={300} y={480} from={from} size={20} color={MUTED} center>{name}</Say>
        </React.Fragment>
      ))}
      <Say x={0} w={1280} y={560} from={t.note} size={26} color={LOOK} center>latest_releases: 0 lines changed</Say>
    </AbsoluteFill>
  );
};

export const ARTICLE4_OPENING = narrate(PAGE, DURATIONS, NARRATION, [
  {length: INTRO.length, starts: INTRO.starts, ids: INTRO.ids, node: <Intro />},
  {length: IDEA.length, starts: IDEA.starts, ids: IDEA.ids, node: <Idea />},
  {length: READ.length, starts: READ.starts, ids: READ.ids, node: <Read />},
  {length: GITHUB.length, starts: GITHUB.starts, ids: GITHUB.ids, node: <Github />},
  {length: RECORD.length, starts: RECORD.starts, ids: RECORD.ids, node: <Record />},
  {length: REPLAY.length, starts: REPLAY.starts, ids: REPLAY.ids, node: <Replay />},
  {length: SAME.length, starts: SAME.starts, ids: SAME.ids, node: <Same />},
]);
