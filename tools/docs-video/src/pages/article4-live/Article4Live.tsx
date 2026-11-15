// The video at the section "I/O: record from a live server, replay with it stopped" of the article "Record and replay"
// (docs/articles/record-and-replay.md): fetch and send read from the top, a record over real HTTP from a local server,
// the same program with the server stopped, how this differs from VCR.py and a mock, and the three files run. Every code
// line on screen is a line of examples/record_and_replay/live.py (check_code.py checks it); every output is a line of
// measure/live.py's output. The motions of a scene are at least 0.9 s apart (planOf checks it when the page is loaded).
import React from 'react';
import {AbsoluteFill} from 'remotion';
import {fadeAt} from '../../kit/motion';
import {narrate} from '../../kit/narrated';
import {CodeBlock, Headlines, IntroStill, Lines} from '../../kit/parts';
import {ACCENT, f, FAIL, LOOK, MONO, MUTED, PANEL, PROD, SANS, TEST, TEXT} from '../../kit/theme';
import {useSceneFrame} from '../../kit/warp';
import {Box, Lit, Say} from '../article2-opening/Article2Opening';
import {Geometry, K, Moving} from '../article-opening/ArticleOpening';
import {Layer, LitRange, Out, Panel, planOf, ringOf, Stack} from '../article3-opening/Article3Opening';
import {Row, Thing} from '../article4-opening/Article4Opening';
import DURATIONS from './durations.json';
import NARRATION from './narration.json';

const PAGE = 'article4-live';

// send as live.py writes it (lines 39-43).
const SEND_CODE = [
  '@handler',
  'def send(effect: HttpRequest, k):',
  '    request = Request(effect.url, method=effect.method, headers=effect.headers)',
  '    body = yield Await(asyncio.to_thread(fetch, request))',
  '    return (yield Resume(k, body))',
];

// A cross drawn over a box from `from` on.
const Cross: React.FC<{x: number; y: number; w: number; h: number; from: number}> = ({x, y, w, h, from}) => {
  const frame = useSceneFrame();
  return (
    <svg style={{position: 'absolute', left: x, top: y, opacity: fadeAt(frame, from)}} width={w} height={h}>
      <line x1={8} y1={8} x2={w - 8} y2={h - 8} stroke={FAIL} strokeWidth={6} />
      <line x1={w - 8} y1={8} x2={8} y2={h - 8} stroke={FAIL} strokeWidth={6} />
    </svg>
  );
};

// 1. The opening, still: what the video shows, what doeff is, and the handler it follows.
const INTRO = planOf(PAGE, 'intro', DURATIONS, [
  ['l1', {}],
  ['l2', {}],
  ['l3', {}],
]);
const Intro: React.FC = () => (
  <AbsoluteFill>
    <div style={{position: 'absolute', inset: 0, opacity: 0.9}}>
      <IntroStill title="Record from a live server" label="the handler this video follows" code={SEND_CODE} w={980} />
    </div>
  </AbsoluteFill>
);

// 2. The idea in plain words: record once against the real service, then tests read only the file.
const IDEA = planOf(PAGE, 'idea', DURATIONS, [
  ['l4', {record: 0.2, keep: 1.1}],
  ['l5', {cross: 0.2, tests: 1.1}],
]);
const IDEA_LINES: Lines = [[0, 'Record once, test from the file']];
const Idea: React.FC = () => {
  const {t} = IDEA;
  return (
    <AbsoluteFill>
      <Headlines lines={IDEA_LINES} />
      <Thing x={120} y={200} w={300} h={110} text="the real service" color={PROD} from={0.1} round={50} />
      <Cross x={120} y={200} w={300} h={110} from={t.cross} />
      <Thing x={560} y={200} w={240} h={110} text="releases.json" color={TEST} from={t.keep} round={6} />
      <Moving text="answers" color={PROD} from={t.record} to={t.record + 0.7} until={t.keep + 0.4} x0={440} y0={255} x1={540} y1={255} />
      <Thing x={940} y={200} w={220} h={110} text="tests" color={ACCENT} from={t.tests} />
      <Moving text="reads" color={TEST} from={t.tests} to={t.tests + 0.7} until={t.tests + 3} x0={820} y0={255} x1={920} y1={255} />
      <Say x={0} w={1280} y={400} from={t.tests + 0.5} size={24} color={LOOK} center>no network at all</Say>
    </AbsoluteFill>
  );
};

// 3. fetch read from the top: the one blocking part, a request over HTTP and its body as text.
const FETCH_CODE = [
  'def fetch(request: Request) -> str:',
  '    with urlopen(request, timeout=10) as response:',
  '        return response.read().decode()',
];
const FC: Box = {x: 60, y: 110, w: 900, line: 34};
const FETCH = planOf(PAGE, 'fetch', DURATIONS, [
  ['l6', {code: 0.1, open: 1.0}],
  ['l7', {body: 0.2, block: 1.1}],
]);
const FETCH_LINES: Lines = [[0, 'fetch: the blocking part']];
const Fetch: React.FC = () => {
  const {t} = FETCH;
  const frame = useSceneFrame();
  return (
    <AbsoluteFill>
      <Headlines lines={FETCH_LINES} />
      <CodeBlock lines={FETCH_CODE} x={FC.x} y={FC.y} w={FC.w} size={20} line={FC.line} opacity={fadeAt(frame, t.code)} />
      <Lit box={FC} i={1} from={t.open} until={t.body} />
      <Lit box={FC} i={2} from={t.body} />
      <Say x={60} y={300} from={t.body} mono size={20} color={TEST}>{'-> \'{"tag_name": "v2.4.1"}\''}</Say>
      <Say x={60} y={370} from={t.block} size={22} color={PROD}>{'the thread waits here until the response comes'}</Say>
    </AbsoluteFill>
  );
};

// 4. send read from the top: it builds the Request, yields Await so fetch runs on a worker thread, resumes its k.
const SC: Box = {x: 30, y: 78, w: 900, line: 30};
const SG: Geometry = {cx: 300, cy: 440, bw: 230, bh: 72, gap: 44, font: 16};
const SEND = planOf(PAGE, 'send', DURATIONS, [
  ['l8', {code: 0.1, request: 1.0}],
  ['l9', {await: 0.2, thread: 1.1}],
  ['l10', {body: 0.2, resume: 1.1}],
]);
const SEND_LINES: Lines = [[0, 'send: urllib on a worker thread']];
const Send: React.FC = () => {
  const {t} = SEND;
  const frame = useSceneFrame();
  const layers: Layer[] = [
    {label: 'send', at: 0, color: PROD, from: 0.1, glow: [t.request, t.resume]},
    {label: 'await_handler', at: 1, color: MUTED, from: 0.1, glow: [t.await + 0.5, t.body]},
  ];
  const outer = ringOf(SG, 1).right;
  return (
    <AbsoluteFill>
      <Headlines lines={SEND_LINES} />
      <CodeBlock lines={SEND_CODE} x={SC.x} y={SC.y} w={SC.w} size={16} line={SC.line} opacity={fadeAt(frame, t.code)} />
      <Lit box={SC} i={2} from={t.request} until={t.await} />
      <Lit box={SC} i={3} from={t.await} until={t.resume} />
      <Lit box={SC} i={4} from={t.resume} />
      <Stack layers={layers} g={SG} title="latest_releases" from={0.1} />
      <Moving text="Await(…)" color={PROD} from={t.await} to={t.await + 0.7} until={t.body} x0={ringOf(SG, 0).right - 8} y0={SG.cy - 22} x1={outer - 8} y1={SG.cy - 22} />
      <Thing x={outer + 120} y={SG.cy - 70} w={300} h={110} text="worker thread: fetch" color={PROD} from={t.thread} round={8} />
      <Moving text="body" color={TEST} from={t.body} to={t.body + 0.7} until={t.resume} x0={outer + 110} y0={SG.cy + 22} x1={ringOf(SG, 0).right - 8} y1={SG.cy + 22} />
      <Out g={SG} ring={0} text="body" color={TEST} from={t.resume} dy={22} back />
      <Say x={SG.cx - 200} y={SG.cy + 170} from={t.resume} size={19} color={K}>Resume(k, body): its k, used once</Say>
    </AbsoluteFill>
  );
};

// 5. The live record: a local server that answers like GitHub, recorded(…, send), two requests over HTTP, served: 2.
const LIVE_CODE = [
  'server = ThreadingHTTPServer(("127.0.0.1", 0), Releases)',
  'threading.Thread(target=server.serve_forever, daemon=True).start()',
  'api = f"http://127.0.0.1:{server.server_port}"',
  '...',
  'print(recorded(latest_releases(REPOS, "secret", api), tape, send))',
  'print(f"served: {served.total()}")',
  'server.shutdown()',
  'server.server_close()',
  '...',
  'print(recorded(latest_releases(REPOS, "secret", api), tape, offline))',
];
const LC: Box = {x: 30, y: 78, w: 840, line: 28};
const SERVER = {x: 900, y: 120, w: 340, h: 120};
const FILE = {x: 900, y: 380, w: 340, h: 170};
const Live: React.FC<{plan: ReturnType<typeof planOf>; stopped: boolean}> = ({plan, stopped}) => {
  const {t} = plan;
  const frame = useSceneFrame();
  return (
    <AbsoluteFill>
      <CodeBlock lines={LIVE_CODE} x={LC.x} y={LC.y} w={LC.w} size={15} line={LC.line} opacity={fadeAt(frame, 0.1)} />
      {!stopped && (
        <>
          <LitRange box={LC} a={0} b={2} from={t.server} until={t.recorded} />
          <Lit box={LC} i={4} from={t.recorded} until={t.served} />
          <Lit box={LC} i={5} from={t.served} />
        </>
      )}
      {stopped && (
        <>
          <LitRange box={LC} a={6} b={7} from={t.stop} until={t.offline} />
          <Lit box={LC} i={9} from={t.offline} />
        </>
      )}
      <Thing {...SERVER} text="local server" color={PROD} from={stopped ? 0 : t.server} dim={stopped ? 0.6 : 1} />
      {stopped && <Cross {...SERVER} from={t.stop} />}
      <Panel x={FILE.x} y={FILE.y} w={FILE.w} h={FILE.h} title="releases.json" from={stopped ? 0 : t.recorded} color={TEST}>
        <Row x={14} y={46} from={stopped ? 0 : t.file} left="" right='{"tag_name": "v2.4.1"}' size={15} lit={stopped && frame >= f(t.answers) && frame < f(t.result)} />
        <Row x={14} y={76} from={stopped ? 0 : t.file + 0.3} left="" right='{"tag_name": "v1.9.0"}' size={15} />
      </Panel>
      {!stopped && (
        <>
          <Moving text="GET /repos/acme/server/…" color={ACCENT} from={t.http1} to={t.http1 + 0.7} until={t.http1 + 0.9} x0={LC.x + LC.w - 120} y0={250} x1={SERVER.x + 40} y1={SERVER.y + SERVER.h + 20} />
          <Moving text="GET /repos/acme/client/…" color={ACCENT} from={t.http2} to={t.http2 + 0.7} until={t.http2 + 0.9} x0={LC.x + LC.w - 120} y0={250} x1={SERVER.x + 40} y1={SERVER.y + SERVER.h + 20} />
          <Say x={SERVER.x} y={SERVER.y + SERVER.h + 60} from={t.served} mono size={22}>
            <span style={{color: MUTED}}>served </span>
            <span style={{color: LOOK}}>2</span>
          </Say>
        </>
      )}
      {stopped && (
        <>
          <Moving text="body" color={TEST} from={t.answers} to={t.answers + 0.7} until={t.result} x0={FILE.x + 40} y0={FILE.y + 20} x1={LC.x + LC.w - 140} y1={LC.y + 9 * LC.line + 24} />
          <Say x={LC.x} y={LC.y + 10 * LC.line + 40} from={t.result} mono size={17} color={LOOK}>{"{'acme/server': 'v2.4.1', 'acme/client': 'v1.9.0'}"}</Say>
          <Say x={LC.x} y={LC.y + 10 * LC.line + 90} from={t.noasync} size={21} color={K}>latest_releases: no async anywhere</Say>
        </>
      )}
    </AbsoluteFill>
  );
};
const RECORD = planOf(PAGE, 'record', DURATIONS, [
  ['l11', {server: 0.2}],
  ['l12', {recorded: 0.2, http1: 1.1, http2: 2.0}],
  ['l13', {served: 0.2, file: 1.1}],
]);
const RECORD_LINES: Lines = [[0, 'Record over real HTTP']];
const Record: React.FC = () => (
  <AbsoluteFill>
    <Headlines lines={RECORD_LINES} />
    <Live plan={RECORD} stopped={false} />
  </AbsoluteFill>
);

// 6. The server stopped: the same program with offline, and the file answers.
const STOPPED = planOf(PAGE, 'stopped', DURATIONS, [
  ['l14', {stop: 0.2}],
  ['l15', {offline: 0.2, answers: 1.1, result: 2.0}],
  ['l16', {noasync: 0.2}],
]);
const STOPPED_LINES: Lines = [[0, 'Replay with the server stopped']];
const Stopped: React.FC = () => (
  <AbsoluteFill>
    <Headlines lines={STOPPED_LINES} />
    <Live plan={STOPPED} stopped />
  </AbsoluteFill>
);

// 7. VCR.py patches HTTP clients, a mock replaces a name; this recorder sees effects, whoever sends them.
const COMPARE = planOf(PAGE, 'compare', DURATIONS, [
  ['l17', {vcr: 0.2, mock: 1.1}],
  ['l18', {effects: 0.2}],
  ['l19', {chat: 0.2, draw: 1.1}],
]);
const COMPARE_LINES: Lines = [[0, 'Not a patch, not a mock']];
const Column: React.FC<{x: number; title: string; lines: string; color: string; from: number}> = ({x, title, lines, color, from}) => {
  const frame = useSceneFrame();
  return (
    <div
      style={{
        position: 'absolute', left: x, top: 110, width: 370, height: 300, boxSizing: 'border-box', padding: '16px 20px', background: PANEL, borderRadius: 14,
        border: `2px solid ${color}`, opacity: fadeAt(frame, from),
      }}
    >
      <div style={{fontFamily: SANS, fontSize: 24, fontWeight: 700, color}}>{title}</div>
      <div style={{fontFamily: SANS, fontSize: 20, color: TEXT, marginTop: 18, whiteSpace: 'pre', lineHeight: 1.5}}>{lines}</div>
    </div>
  );
};
const Compare: React.FC = () => {
  const {t} = COMPARE;
  const frame = useSceneFrame();
  return (
    <AbsoluteFill>
      <Headlines lines={COMPARE_LINES} />
      <Column x={30} title="VCR.py" lines={'patches the HTTP\nclients it knows'} color={MUTED} from={t.vcr} />
      <Column x={455} title="a mock" lines={'replaces a name\nthe code calls'} color={MUTED} from={t.mock} />
      <Column x={880} title="this recorder" lines={'a handler: it sees\neffects, whoever\nsends them'} color={TEST} from={t.effects} />
      <div style={{position: 'absolute', left: 900, top: 290, fontFamily: MONO, fontSize: 19, fontWeight: 700, color: ACCENT, opacity: fadeAt(frame, t.chat)}}>HttpRequest</div>
      <div style={{position: 'absolute', left: 900, top: 325, fontFamily: MONO, fontSize: 19, fontWeight: 700, color: ACCENT, opacity: fadeAt(frame, t.chat)}}>{"a model's answers"}</div>
      <div style={{position: 'absolute', left: 900, top: 360, fontFamily: MONO, fontSize: 19, fontWeight: 700, color: ACCENT, opacity: fadeAt(frame, t.draw)}}>a random draw</div>
    </AbsoluteFill>
  );
};

// 8. Try it: the three files, each run on its own (measure/live.log).
const TRY = planOf(PAGE, 'try', DURATIONS, [
  ['l20', {releases: 0.2, disk: 1.1, live: 2.0}],
  ['l21', {ci: 0.3}],
], 1.4);
const TRY_LINES: Lines = [[0, 'Try it']];
const TERMINALS: [string, string][] = [
  ['$ python releases.py', "-- record ...\n{'acme/server': 'v2.4.1', …}\nsent: 2\n-- replay ...\nsent: 0\n-- unknown ...\nLookupError: not recorded: …"],
  ['$ python on_disk.py', "-- first ...\nsent: 2\n-- again ...\nsent: 0\n-- token ...\nLookupError: not recorded: …\n-- keyed ..."],
  ['$ python live.py', "-- live ...\n{'acme/server': 'v2.4.1', …}\nserved: 2\n-- stopped ...\n{'acme/server': 'v2.4.1', …}"],
];
const Try: React.FC = () => {
  const {t} = TRY;
  const frame = useSceneFrame();
  const starts = [t.releases, t.disk, t.live];
  return (
    <AbsoluteFill>
      <Headlines lines={TRY_LINES} />
      {TERMINALS.map(([command, output], i) => (
        <div
          key={command}
          style={{
            position: 'absolute', left: 30 + i * 415, top: 100, width: 395, height: 360, boxSizing: 'border-box', padding: '12px 16px', background: '#0b0d13',
            borderRadius: 12, border: '2px solid #2a2e40', fontFamily: MONO, fontSize: 15, color: TEXT, whiteSpace: 'pre', opacity: fadeAt(frame, starts[i]),
          }}
        >
          <div style={{color: LOOK, fontWeight: 700}}>{command}</div>
          <div style={{marginTop: 10, color: MUTED, lineHeight: 1.6}}>{output}</div>
        </div>
      ))}
      <Say x={0} w={1280} y={500} from={t.ci} size={24} color={TEST} center>record once, then replay in CI with offline</Say>
    </AbsoluteFill>
  );
};

export const ARTICLE4_LIVE = narrate(PAGE, DURATIONS, NARRATION, [
  {length: INTRO.length, starts: INTRO.starts, ids: INTRO.ids, node: <Intro />},
  {length: IDEA.length, starts: IDEA.starts, ids: IDEA.ids, node: <Idea />},
  {length: FETCH.length, starts: FETCH.starts, ids: FETCH.ids, node: <Fetch />},
  {length: SEND.length, starts: SEND.starts, ids: SEND.ids, node: <Send />},
  {length: RECORD.length, starts: RECORD.starts, ids: RECORD.ids, node: <Record />},
  {length: STOPPED.length, starts: STOPPED.starts, ids: STOPPED.ids, node: <Stopped />},
  {length: COMPARE.length, starts: COMPARE.starts, ids: COMPARE.ids, node: <Compare />},
  {length: TRY.length, starts: TRY.starts, ids: TRY.ids, node: <Try />},
]);
