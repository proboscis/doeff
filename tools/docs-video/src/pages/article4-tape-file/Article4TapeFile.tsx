// The video at the section "Keep the tape in a file" of the article "Record and replay" (docs/articles/record-and-replay.md):
// json_tape, recorded and offline read from the top, then two runs on one file: the first sends each request out and the
// file keeps the answers, the second, on a new stack with offline, gets every answer from the file. Every code line on
// screen is a line of examples/record_and_replay/on_disk.py (check_code.py checks it); every value and the order of the
// memo effects is a line of measure/tape_file.py's output. The motions of a scene are at least 0.9 s apart (planOf).
import React from 'react';
import {AbsoluteFill} from 'remotion';
import {fadeAt} from '../../kit/motion';
import {narrate} from '../../kit/narrated';
import {CodeBlock, Headlines, Lines} from '../../kit/parts';
import {ACCENT, f, FAIL, LOOK, MUTED, PROD, SANS, TEST, TEXT} from '../../kit/theme';
import {useSceneFrame} from '../../kit/warp';
import {Box, Lit, Say} from '../article2-opening/Article2Opening';
import {Geometry, K, Moving} from '../article-opening/ArticleOpening';
import {Layer, LitRange, Out, Panel, planOf, ringOf, Stack} from '../article3-opening/Article3Opening';
import {Row, Thing} from '../article4-opening/Article4Opening';
import DURATIONS from './durations.json';
import NARRATION from './narration.json';

const PAGE = 'article4-tape-file';

// A pill that goes from ring a of a stack out (or back in) to ring b, on a row `dy` below the middle.
export const Hop: React.FC<{g: Geometry; a: number; b: number; text: string; color?: string; from: number; dy?: number; until?: number}> = ({
  g, a, b, text, color = ACCENT, from, dy = 0, until,
}) => (
  <Moving
    text={text} color={color} from={from} to={from + 0.7} until={until ?? from + 1.6}
    x0={ringOf(g, a).right - 8} y0={g.cy + dy} x1={ringOf(g, b).right - 8} y1={g.cy + dy}
  />
);

// recorded as on_disk.py writes it (lines 35-40).
const RECORDED_CODE = [
  'def recorded(program, tape, network, key=None):',
  '    memo = make_memo_rewriter(HttpRequest, key_fn=key) if key else make_memo_rewriter(HttpRequest)',
  '    program = memo(program)',
  '    program = json_tape(tape)(program)',
  '    program = network(program)',
  '    return run(scheduled(await_handler(slog_discard_handler(program))))',
];

// 1. The opening, still: what the video shows, what doeff is, and the function it follows.
const INTRO = planOf(PAGE, 'intro', DURATIONS, [
  ['t1', {}],
  ['t2', {}],
  ['t3', {}],
]);
// The still of IntroStill, with the code smaller: recorded's second line is long.
const Intro: React.FC = () => {
  const centered = {position: 'absolute', left: 0, right: 0, textAlign: 'center', fontFamily: SANS} as const;
  return (
    <AbsoluteFill style={{opacity: 0.9}}>
      <div style={{...centered, top: 62, fontSize: 60, fontWeight: 700, color: TEXT}}>Keep the tape in a file</div>
      <div style={{...centered, top: 152, fontSize: 24, color: MUTED}}>doeff: algebraic effects for Python</div>
      <div style={{position: 'absolute', left: 50, top: 226, fontFamily: SANS, fontSize: 20, color: ACCENT, whiteSpace: 'nowrap'}}>the function this video follows</div>
      <CodeBlock lines={RECORDED_CODE} x={50} y={260} w={1180} size={17} line={30} />
    </AbsoluteFill>
  );
};

// 2. The idea in plain words: notes in memory go with the process; notes in a file stay for the next run.
const IDEA = planOf(PAGE, 'idea', DURATIONS, [
  ['t4', {notes: 0.2, end: 1.1}],
  ['t5', {file: 0.2, ci: 1.1}],
]);
const IDEA_LINES: Lines = [[0, 'Notes that outlive the process']];
const Idea: React.FC = () => {
  const {t} = IDEA;
  const frame = useSceneFrame();
  return (
    <AbsoluteFill>
      <Headlines lines={IDEA_LINES} />
      <div
        style={{
          position: 'absolute', left: 100, top: 150, width: 420, height: 300, boxSizing: 'border-box', borderRadius: 16, border: `3px dashed ${ACCENT}`,
          opacity: fadeAt(frame, 0.1) * (1 - 0.7 * fadeAt(frame, t.end, 0.5)),
        }}
      >
        <div style={{position: 'absolute', left: 14, top: 8, fontSize: 20, fontWeight: 700, color: ACCENT, fontFamily: SANS}}>a process</div>
      </div>
      <Thing x={180} y={250} w={260} h={110} text="notes in memory" color={TEST} from={t.notes} until={t.end} />
      <Say x={130} y={470} from={t.end + 0.3} size={20} color={MUTED}>{'the process ends:\nthe notes are gone'}</Say>
      <Thing x={640} y={250} w={240} h={110} text="releases.json" color={TEST} from={t.file} round={6} />
      <Thing x={980} y={250} w={200} h={110} text="CI" color={PROD} from={t.ci} />
      <Moving text="reads" color={TEST} from={t.ci} to={t.ci + 0.7} x0={880} y0={305} x1={960} y1={305} until={t.ci + 3} />
      <Say x={640} y={400} from={t.ci + 0.4} size={20} color={LOOK}>{'the file stays:\nanother run reads it'}</Say>
    </AbsoluteFill>
  );
};

// 3. json_tape read from the top: storage, one handler that answers the three memo effects from a JSON file.
const TAPE_CODE = [
  'def json_tape(path: Path):',
  '    @handler',
  '    def storage(effect: MemoExistsEffect | MemoGetEffect | MemoPutEffect, k):',
  '        tape = json.loads(path.read_text()) if path.exists() else {}',
  '        if isinstance(effect, MemoExistsEffect):',
  '            return (yield Resume(k, effect.key in tape))',
  '        if isinstance(effect, MemoGetEffect):',
  '            return (yield Resume(k, tape[effect.key]))',
  '        tape[effect.key] = effect.value',
  '        path.write_text(json.dumps(tape, indent=2))',
  '        return (yield Resume(k, None))',
  ' ',
  '    return storage',
];
const J = (line: number) => line - 20;
const TC: Box = {x: 30, y: 78, w: 820, line: 26};
const TAPE = planOf(PAGE, 'json_tape', DURATIONS, [
  ['t6', {code: 0.1, storage: 1.0}],
  ['t7', {read: 0.2}],
  ['t8', {exists: 0.2, yes: 1.1}],
  ['t9', {get: 0.2}],
  ['t10', {put: 0.2, save: 1.1}],
]);
const TAPE_LINES: Lines = [[0, 'json_tape: storage over a JSON file']];
const JsonTape: React.FC = () => {
  const {t} = TAPE;
  const frame = useSceneFrame();
  return (
    <AbsoluteFill>
      <Headlines lines={TAPE_LINES} />
      <CodeBlock lines={TAPE_CODE} x={TC.x} y={TC.y} w={TC.w} size={14} line={TC.line} opacity={fadeAt(frame, t.code)} />
      <LitRange box={TC} a={J(21)} b={J(22)} from={t.storage} until={t.read} />
      <Lit box={TC} i={J(23)} from={t.read} until={t.exists} />
      <LitRange box={TC} a={J(24)} b={J(25)} from={t.exists} until={t.get} />
      <LitRange box={TC} a={J(26)} b={J(27)} from={t.get} until={t.put} />
      <Lit box={TC} i={J(28)} from={t.put} until={t.save} />
      <LitRange box={TC} a={J(29)} b={J(30)} from={t.save} />
      <Panel x={880} y={82} w={370} h={480} title="what storage answers" from={t.storage} color={TEST}>
        <Row x={14} y={50} from={t.read} left="every effect: " right="read the file" color={TEST} size={15} />
        <Row x={14} y={110} from={t.exists} left="" right="MemoExists(key)" color={ACCENT} />
        <Row x={34} y={138} from={t.yes} left="-> " right="True / False" color={K} />
        <Row x={14} y={200} from={t.get} left="" right="MemoGet(key)" color={ACCENT} />
        <Row x={34} y={228} from={t.get} left="-> " right="the stored answer" color={K} />
        <Row x={14} y={290} from={t.put} left="" right="MemoPut(key, value)" color={ACCENT} />
        <Row x={34} y={318} from={t.save} left="-> " right="None, file saved" color={K} />
      </Panel>
    </AbsoluteFill>
  );
};

// 4. recorded read from the top: the rewriter, the storage around it, the network around both, then the run.
const RD: Box = {x: 30, y: 78, w: 1220, line: 28};
const D = (line: number) => line - 35;
const RG: Geometry = {cx: 330, cy: 480, bw: 230, bh: 64, gap: 32, font: 15};
const RECORDED = planOf(PAGE, 'recorded', DURATIONS, [
  ['t11', {rewriter: 0.2}],
  ['t12', {memo: 0.2, hit: 1.1, miss: 2.0}],
  ['t13', {storage: 0.2, network: 1.1}],
  ['t14', {run: 0.2, rings: 1.1}],
]);
const RECORDED_LINES: Lines = [[0, "recorded: doeff's recorder, a file, a network"]];
const Recorded: React.FC = () => {
  const {t} = RECORDED;
  const frame = useSceneFrame();
  const layers: Layer[] = [
    {label: 'memo', at: 0, color: K, from: t.memo, glow: [t.memo, t.storage]},
    {label: 'json_tape(tape)', at: 1, color: TEST, from: t.storage, glow: [t.storage, t.network]},
    {label: 'network', at: 2, color: PROD, from: t.network, glow: [t.network, t.run]},
    {label: '', at: 3, color: MUTED, from: t.rings},
    {label: '', at: 3.3, color: MUTED, from: t.rings},
    {label: '', at: 3.6, color: MUTED, from: t.rings},
  ];
  return (
    <AbsoluteFill>
      <Headlines lines={RECORDED_LINES} />
      <CodeBlock lines={RECORDED_CODE} x={RD.x} y={RD.y} w={RD.w} size={15} line={RD.line} opacity={fadeAt(frame, 0.1)} />
      <Lit box={RD} i={D(36)} from={t.rewriter} until={t.memo} />
      <Lit box={RD} i={D(37)} from={t.memo} until={t.storage} />
      <Lit box={RD} i={D(38)} from={t.storage} until={t.network} />
      <Lit box={RD} i={D(39)} from={t.network} until={t.run} />
      <Lit box={RD} i={D(40)} from={t.run} />
      <Stack layers={layers} g={RG} title="latest_releases" from={0.1} />
      <Out g={RG} ring={1} text="hit: from storage" color={TEST} from={t.hit} dy={-16} until={t.hit + 0.9} />
      <Out g={RG} ring={2} text="miss: sent out" color={PROD} from={t.miss} dy={16} until={t.miss + 0.9} />
      <Say x={760} y={330} from={t.memo} size={19} color={K}>{'memo = make_memo_rewriter(HttpRequest)\nfrom doeff_core_effects'}</Say>
      <Say x={760} y={430} from={t.rings} size={19} color={MUTED}>{'scheduled, await_handler,\nslog_discard_handler: the run'}</Say>
    </AbsoluteFill>
  );
};

// 5. offline, in the network's place: any request that reaches it raises LookupError.
const OFFLINE_CODE = [
  '@handler',
  'def offline(effect: HttpRequest, k):',
  '    raise LookupError(f"not recorded: {effect.method} {effect.url}")',
];
const OC: Box = {x: 30, y: 78, w: 900, line: 30};
const OFFLINE = planOf(PAGE, 'offline', DURATIONS, [
  ['t15', {code: 0.2, swap: 1.1}],
  ['t16', {raise: 0.2}],
]);
const OFFLINE_LINES: Lines = [[0, 'offline: where the network would be']];
const OG: Geometry = {cx: 330, cy: 420, bw: 230, bh: 70, gap: 40, font: 16};
const Offline: React.FC = () => {
  const {t} = OFFLINE;
  const frame = useSceneFrame();
  const layers: Layer[] = [
    {label: 'memo', at: 0, color: K},
    {label: 'json_tape(tape)', at: 1, color: TEST},
    {label: 'network', at: 2, color: PROD, until: t.swap},
    {label: 'offline', at: 2, color: FAIL, from: t.swap + 0.3, glow: [t.raise, t.raise + 2]},
  ];
  return (
    <AbsoluteFill>
      <Headlines lines={OFFLINE_LINES} />
      <CodeBlock lines={OFFLINE_CODE} x={OC.x} y={OC.y} w={OC.w} size={17} line={OC.line} opacity={fadeAt(frame, t.code)} />
      <LitRange box={OC} a={0} b={1} from={t.swap} until={t.raise} />
      <Lit box={OC} i={2} from={t.raise} color={FAIL} />
      <Stack layers={layers} g={OG} title="latest_releases" from={0} />
      <Moving text="LookupError" color={FAIL} from={t.raise} to={t.raise + 0.7} until={t.raise + 3} x0={ringOf(OG, 2).right - 8} y0={OG.cy + 20} x1={ringOf(OG, 2).right + 170} y1={OG.cy + 20} />
    </AbsoluteFill>
  );
};

// The file after the first run (measure/tape_file.log, t5 file): a hash of the whole request as each key.
const FILE_ROWS: [string, string][] = [
  ['"f9af44c4711b…"', '{"tag_name": "v2.4.1"}'],
  ['"8537d5d5f13e…"', '{"tag_name": "v1.9.0"}'],
];
const FILE = {x: 720, y: 300, w: 530, h: 230};
const FileRows: React.FC<{from: [number, number]; lit?: (i: number) => boolean}> = ({from, lit = () => false}) => (
  <>
    {FILE_ROWS.map(([key, value], i) => (
      <Row key={key} x={FILE.x + 16} y={FILE.y + 50 + i * 34} from={from[i]} left={`${key}: `} right={value} size={15} lit={lit(i)} />
    ))}
  </>
);
const SG: Geometry = {cx: 320, cy: 420, bw: 230, bh: 70, gap: 42, font: 16};

// 6. The first run, github as the network: MemoExists says no, the request goes out, MemoPut writes the answer.
const FIRST = planOf(PAGE, 'first', DURATIONS, [
  ['t17', {ring: 0.2}],
  ['t18', {exists: 0.2, no: 1.1, out: 2.0}],
  ['t19', {answer: 0.2, put: 1.1, row: 2.0, second: 2.9, sent: 3.8}],
]);
const FIRST_LINES: Lines = [[0, 'First run: nothing in the file yet']];
const First: React.FC = () => {
  const {t} = FIRST;
  const layers: Layer[] = [
    {label: 'memo', at: 0, color: K, from: 0.1},
    {label: 'json_tape(tape)', at: 1, color: TEST, from: 0.1, glow: [t.exists, t.out]},
    {label: 'github', at: 2, color: PROD, from: t.ring, glow: [t.out + 0.5, t.answer + 0.6]},
  ];
  return (
    <AbsoluteFill>
      <Headlines lines={FIRST_LINES} />
      <Stack layers={layers} g={SG} title="latest_releases" from={0.1} />
      <Hop g={SG} a={0} b={1} text="MemoExists" from={t.exists} dy={-60} until={t.no} />
      <Hop g={SG} a={1} b={0} text="False" color={TEST} from={t.no} dy={-60} until={t.out} />
      <Hop g={SG} a={0} b={2} text="HttpRequest" from={t.out} dy={-20} until={t.answer} />
      <Hop g={SG} a={2} b={0} text="body" color={PROD} from={t.answer} dy={20} until={t.put} />
      <Hop g={SG} a={0} b={1} text="MemoPut" from={t.put} dy={60} until={t.row + 0.4} />
      <Out g={SG} ring={2} text="the same, for acme/client" from={t.second} dy={0} until={t.second + 0.9} />
      <Panel x={FILE.x} y={FILE.y} w={FILE.w} h={FILE.h} title="releases.json" from={0.1} color={TEST} />
      <FileRows from={[t.row, t.second + 0.6]} />
      <Say x={FILE.x + 16} y={FILE.y + 150} from={t.sent} mono size={22}>
        <span style={{color: MUTED}}>sent </span>
        <span style={{color: LOOK}}>2</span>
      </Say>
      <Say x={720} y={110} from={t.exists} mono size={20} color={TEXT}>{'per request, in order:\nMemoExists -> False\ngithub answers\nMemoPut'}</Say>
    </AbsoluteFill>
  );
};

// 7. The second run: a new stack with offline; MemoExists says yes and MemoGet gives the answer; offline is never reached.
const SECOND = planOf(PAGE, 'second', DURATIONS, [
  ['t20', {gone: 0.2, rebuild: 1.1}],
  ['t21', {exists: 0.2, yes: 1.1, get: 2.0, answer: 2.9}],
  ['t22', {second: 0.2, result: 1.1}],
]);
const SECOND_LINES: Lines = [[0, 'Second run: a new stack, the same file']];
const Second: React.FC = () => {
  const {t} = SECOND;
  const frame = useSceneFrame();
  const before: Layer[] = [
    {label: 'memo', at: 0, color: K, from: 0},
    {label: 'json_tape(tape)', at: 1, color: TEST, from: 0},
    {label: 'github', at: 2, color: PROD, from: 0},
  ];
  const layers: Layer[] = [
    {label: 'memo', at: 0, color: K, from: t.rebuild},
    {label: 'json_tape(tape)', at: 1, color: TEST, from: t.rebuild, glow: [t.exists, t.answer]},
    {label: 'offline', at: 2, color: FAIL, from: t.rebuild},
  ];
  return (
    <AbsoluteFill>
      <Headlines lines={SECOND_LINES} />
      <Stack layers={before} g={SG} title="latest_releases" from={0} opacity={1 - fadeAt(frame, t.gone, 0.5)} />
      <Stack layers={layers} g={SG} title="latest_releases" from={t.rebuild} />
      <Hop g={SG} a={0} b={1} text="MemoExists" from={t.exists} dy={-60} until={t.yes} />
      <Hop g={SG} a={1} b={0} text="True" color={TEST} from={t.yes} dy={-60} until={t.get} />
      <Hop g={SG} a={0} b={1} text="MemoGet" from={t.get} dy={20} until={t.answer} />
      <Out g={SG} ring={1} text="body" color={TEST} from={t.answer} dy={20} back until={t.second} />
      <Out g={SG} ring={1} text="the same, for acme/client" from={t.second} dy={0} until={t.second + 0.9} />
      <Panel x={FILE.x} y={FILE.y} w={FILE.w} h={FILE.h} title="releases.json" from={0} color={TEST} />
      <FileRows from={[0, 0]} lit={(i) => (i === 0 ? frame >= f(t.get) && frame < f(t.second) : frame >= f(t.second) && frame < f(t.result))} />
      <Say x={FILE.x + 16} y={FILE.y + 130} from={t.result} mono size={16} color={LOOK}>{"{'acme/server': 'v2.4.1',\n 'acme/client': 'v1.9.0'}"}</Say>
      <Say x={FILE.x + 16} y={FILE.y + 186} from={t.result} mono size={22}>
        <span style={{color: MUTED}}>sent </span>
        <span style={{color: LOOK}}>0</span>
      </Say>
      <Say x={720} y={110} from={t.exists} mono size={20} color={TEXT}>{'per request, in order:\nMemoExists -> True\nMemoGet\n(offline: never called)'}</Say>
    </AbsoluteFill>
  );
};

// 8. The rewriter yields three effects and never opens a file: answer them from a dict, and the tape is in memory.
const EFFECTS = planOf(PAGE, 'effects', DURATIONS, [
  ['t23', {exists: 0.2, get: 1.1, put: 2.0}],
  ['t24', {swap: 0.2, note: 1.1}],
], 1.4);
const EFFECTS_LINES: Lines = [[0, 'memo only yields effects']];
const Effects: React.FC = () => {
  const {t} = EFFECTS;
  const layers: Layer[] = [
    {label: 'memo', at: 0, color: K, from: 0.1},
    {label: 'json_tape(tape)', at: 1, color: TEST, from: 0.1, until: t.swap},
    {label: 'a dict', at: 1, color: ACCENT, from: t.swap + 0.3},
  ];
  return (
    <AbsoluteFill>
      <Headlines lines={EFFECTS_LINES} />
      <Stack layers={layers} g={SG} title="latest_releases" from={0.1} />
      {(['MemoExists', 'MemoGet', 'MemoPut'] as const).map((name, i) => (
        <Moving key={name} text={name} color={ACCENT} from={[t.exists, t.get, t.put][i]} to={[t.exists, t.get, t.put][i] + 0.7} x0={ringOf(SG, 0).right - 8} y0={SG.cy - 50 + i * 50} x1={820} y1={250 + i * 70} />
      ))}
      <Say x={720} y={480} from={t.note} size={22} color={LOOK}>{'the same memo,\nthe tape now in memory'}</Say>
    </AbsoluteFill>
  );
};

export const ARTICLE4_TAPE_FILE = narrate(PAGE, DURATIONS, NARRATION, [
  {length: INTRO.length, starts: INTRO.starts, ids: INTRO.ids, node: <Intro />},
  {length: IDEA.length, starts: IDEA.starts, ids: IDEA.ids, node: <Idea />},
  {length: TAPE.length, starts: TAPE.starts, ids: TAPE.ids, node: <JsonTape />},
  {length: RECORDED.length, starts: RECORDED.starts, ids: RECORDED.ids, node: <Recorded />},
  {length: OFFLINE.length, starts: OFFLINE.starts, ids: OFFLINE.ids, node: <Offline />},
  {length: FIRST.length, starts: FIRST.starts, ids: FIRST.ids, node: <First />},
  {length: SECOND.length, starts: SECOND.starts, ids: SECOND.ids, node: <Second />},
  {length: EFFECTS.length, starts: EFFECTS.starts, ids: EFFECTS.ids, node: <Effects />},
]);
