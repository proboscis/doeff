// The video on the Unified LLM Effects page (docs/llm_unified_effects.md): one set of LLM effects for every
// provider, and model-based routing as plain handlers that answer or pass. Six scenes, each with headlines a
// synthetic voice reads (narration.json):
//   1. The title: doeff-llm defines the LLM effects once; the provider packages bring only handlers.
//   2. The old provider-specific effects are deprecated subclasses of LLMChat; one program, two providers.
//   3. The page's multi-provider stack: each effect travels out until a handler supports its model.
//   4. A handler's annotation filters too: a structured query skips chat_only without a call.
//   5. The order matters: OpenRouter innermost answers everything; the catch-all belongs outermost.
//   6. The outro.
// Every output, order and answer on screen is printed by a program in measure/ (its .log is next to it). The
// provider packages' mock handlers stand in for their production handlers there, so no API is called.
import React from 'react';
import {AbsoluteFill} from 'remotion';
import {fadeAt, mix, ramp} from '../../kit/motion';
import {narrate} from '../../kit/narrated';
import {CodeBlock, glow, Headlines, lineMid, Lines, Pill, startsOf} from '../../kit/parts';
import {ACCENT, f, LOOK, MONO, MUTED, PANEL, PROD, SANS, TEST, TEXT} from '../../kit/theme';
import {useSceneFrame} from '../../kit/warp';
import DURATIONS from './durations.json';

// Seen between two seconds of a scene: fades in at the first and out at the second.
const between = (frame: number, from: number, to: number, length = 0.3) => fadeAt(frame, from, length) * (1 - fadeAt(frame, to, length));

// A label in the scenes' sans font.
const Label: React.FC<{x: number; y: number; size?: number; color?: string; bold?: boolean; mono?: boolean; opacity?: number; children: React.ReactNode; center?: boolean}> = ({
  x, y, size = 20, color = MUTED, bold = false, mono = false, opacity = 1, children, center = false,
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
      position: 'absolute', left: x, top: y, width: w, height: h, boxSizing: 'border-box', borderRadius: 16, background: PANEL,
      border: `3px ${dashed ? 'dashed' : 'solid'} ${color}`, ...glow(on, color), opacity,
    }}
  >
    {children}
  </div>
);

// The handler chain of scenes 3 to 5: handlers as columns, innermost on the left. An effect leaves its yield,
// visits the columns from the inside out, passes the ones that do not take it, and the one that answers sends
// its answer back to the yield.

type Column = {name: string; sub: string};
// One effect's trip: the code line it leaves from, the row it travels on, when it leaves, how many columns it
// passes before the one that answers, what it shows on the way out and back, and the tag a passed column gets.
type Trip = {line: number; row: number; at: number; passes: number; text: string; answer: string; tag?: string};
const MOVE = 0.7;
const STOP = 0.6;
const HOP = 0.5;
const HOLD = 0.9;
const BACK = 0.8;
const arrive = (trip: Trip, i: number) => trip.at + MOVE + i * (STOP + HOP);
const answeredAt = (trip: Trip) => arrive(trip, trip.passes);
const backAt = (trip: Trip) => answeredAt(trip) + HOLD;
const doneAt = (trip: Trip) => backAt(trip) + BACK;

type ChainProps = {
  columns: Column[]; xs: number[]; w: number; top: number; bottom: number; trips: Trip[]; code: {x: number; y: number; w: number; line: number};
  fadeFrom: number;
};

const Chain: React.FC<ChainProps> = ({columns, xs, w, top, bottom, trips, code, fadeFrom}) => {
  const frame = useSceneFrame();
  const centre = (i: number) => xs[i] + w / 2;
  const at = (s: number) => frame >= f(s);
  const within = (from: number, to: number) => frame >= f(from) && frame < f(to);
  const passing = (i: number) => trips.some((trip) => i < trip.passes && within(arrive(trip, i), arrive(trip, i) + STOP));
  const answering = (i: number) => trips.some((trip) => i === trip.passes && within(answeredAt(trip), backAt(trip)));
  return (
    <>
      {columns.map((column, i) => {
        const on = answering(i);
        const flash = passing(i);
        return (
          <Box key={column.name} x={xs[i]} y={top} w={w} h={bottom - top} color={TEST} on={on || flash} opacity={fadeAt(frame, fadeFrom + i * 0.5, 0.5)}>
            <div style={{position: 'absolute', left: 0, right: 0, top: 12, textAlign: 'center', fontFamily: MONO, fontSize: 19, fontWeight: 700, color: on || flash ? LOOK : TEST}}>{column.name}</div>
            <div style={{position: 'absolute', left: 0, right: 0, top: 42, textAlign: 'center', fontFamily: MONO, fontSize: 13, color: MUTED, whiteSpace: 'pre'}}>{column.sub}</div>
          </Box>
        );
      })}
      {trips.map((trip, n) =>
        Array.from({length: trip.passes}, (_, i) => (
          <div
            key={`${n}-${i}`}
            style={{
              position: 'absolute', left: xs[i], width: w, top: trip.row + 24, textAlign: 'center', fontFamily: MONO, fontSize: 15, fontWeight: 700,
              color: within(arrive(trip, i), arrive(trip, i) + STOP) ? LOOK : MUTED, opacity: fadeAt(frame, arrive(trip, i), 0.2),
            }}
          >
            {trip.tag ?? 'Pass'}
          </div>
        )),
      )}
      {trips.map((trip, n) => (
        <div
          key={`${n}-answers`}
          style={{
            position: 'absolute', left: xs[trip.passes], width: w, top: trip.row + 24, textAlign: 'center', fontFamily: SANS, fontSize: 15, fontWeight: 700,
            color: TEST, opacity: fadeAt(frame, answeredAt(trip), 0.2),
          }}
        >
          answers
        </div>
      ))}
      {trips.map((trip, n) => {
        if (!within(trip.at, backAt(trip))) return null;
        const fromX = code.x + code.w + 10;
        const fromY = lineMid(code.y, code.line, trip.line);
        let x = centre(0);
        let y = trip.row;
        if (!at(arrive(trip, 0))) {
          const t = ramp(frame, trip.at, arrive(trip, 0));
          x = mix(fromX, centre(0), t);
          y = mix(fromY, trip.row, t);
        } else {
          for (let i = 0; i < trip.passes; i++) {
            if (at(arrive(trip, i) + STOP)) x = mix(centre(i), centre(i + 1), ramp(frame, arrive(trip, i) + STOP, arrive(trip, i + 1)));
          }
        }
        return <Pill key={n} x={x} y={y} text={trip.text} color={ACCENT} opacity={fadeAt(frame, trip.at, 0.2) * (1 - ramp(frame, backAt(trip) - 0.2, backAt(trip)))} />;
      })}
      {trips.map((trip, n) => {
        if (!within(backAt(trip), doneAt(trip) + 0.2)) return null;
        const t = ramp(frame, backAt(trip), doneAt(trip));
        const x = mix(centre(trip.passes) - 40, code.x + code.w + 10, t);
        const y = mix(trip.row, lineMid(code.y, code.line, trip.line), t);
        return <Pill key={n} x={x} y={y} left text={trip.answer} color={TEST} opacity={1 - ramp(frame, doneAt(trip), doneAt(trip) + 0.2)} />;
      })}
    </>
  );
};

// The band on the line of the code box the program is on: each trip's line from when it leaves, then the return.
const bandOf = (frame: number, trips: Trip[], last: number, end: number) => {
  if (frame < f(trips[0].at - 0.4)) return null;
  const lines = [...trips.map((trip) => [trip.at - 0.4, trip.line] as [number, number]), [end, last] as [number, number]];
  return lines.reduce((line, [from, to], i) => (i === 0 ? to : mix(line, to, ramp(frame, from, from + 0.3))), lines[0][1]);
};

// The answers of the trips as a table under the chain: model, who answered, the value.
const Answers: React.FC<{rows: {at: number; model: string; by: string; value: string}[]; y: number; x?: number}> = ({rows, y, x = 60}) => {
  const frame = useSceneFrame();
  return (
    <>
      {rows.map((row, i) => (
        <div key={row.model + i} style={{position: 'absolute', left: x, top: y + i * 32, display: 'flex', alignItems: 'center', height: 30, opacity: fadeAt(frame, row.at)}}>
          <div style={{width: 250, fontFamily: MONO, fontSize: 18, color: ACCENT}}>{row.model}</div>
          <div style={{width: 190, fontFamily: SANS, fontSize: 18, fontWeight: 700, color: TEST}}>→ {row.by}</div>
          <div style={{fontFamily: MONO, fontSize: 18, color: TEXT, whiteSpace: 'pre'}}>{row.value}</div>
        </div>
      ))}
    </>
  );
};

// Scene 1 — the title: the shared effects, and the provider packages that only bring handlers.

const TITLE_LINES: Lines = [
  [0, 'Unified LLM Effects: doeff-llm defines the effects of talking to a model once, for every provider.'],
  [5.5, 'LLMChat, LLMStreamingChat, LLMStructuredQuery and LLMEmbedding name what a program asks for.'],
  [11, 'The provider packages bring only handlers that answer them.'],
];
const TITLE = f(17);
const EFFECTS = [
  {name: 'LLMChat', about: 'a chat completion'},
  {name: 'LLMStreamingChat', about: 'a chat, streamed'},
  {name: 'LLMStructuredQuery', about: 'a typed answer'},
  {name: 'LLMEmbedding', about: 'vectors for text'},
];
const PROVIDERS = [
  {name: 'doeff-openai', handler: 'openai_production_handler'},
  {name: 'doeff-gemini', handler: 'gemini_production_handler'},
  {name: 'doeff-openrouter', handler: 'openrouter_production_handler'},
];
const EFF = {y: 290, w: 270, h: 84, gap: 20};
const effX = (i: number) => 640 - (4 * EFF.w + 3 * EFF.gap) / 2 + i * (EFF.w + EFF.gap);
const PRO = {y: 500, w: 330, h: 92, gap: 45};
const proX = (i: number) => 640 - (3 * PRO.w + 2 * PRO.gap) / 2 + i * (PRO.w + PRO.gap);

const TitleScene: React.FC = () => {
  const frame = useSceneFrame();
  const wires = ramp(frame, 11.6, 12.6);
  return (
    <AbsoluteFill>
      <Headlines lines={TITLE_LINES} />
      <div style={{position: 'absolute', left: 0, right: 0, top: 112 + mix(16, 0, ramp(frame, 0.2, 0.8)), textAlign: 'center', opacity: fadeAt(frame, 0.2, 0.5)}}>
        <div style={{fontFamily: SANS, fontSize: 64, fontWeight: 700, color: TEXT}}>Unified LLM Effects</div>
        <div style={{fontFamily: SANS, fontSize: 26, color: ACCENT, marginTop: 4}}>
          <span style={{fontFamily: MONO}}>doeff_llm.effects</span>: one set of effects, any provider
        </div>
      </div>
      <svg width={1280} height={720} style={{position: 'absolute', left: 0, top: 0, opacity: wires * 0.5}}>
        {PROVIDERS.map((_, p) =>
          EFFECTS.map((__, e) => (
            <line key={`${p}-${e}`} x1={proX(p) + PRO.w / 2} y1={PRO.y} x2={effX(e) + EFF.w / 2} y2={EFF.y + EFF.h} stroke={PROD} strokeWidth={2} strokeDasharray="6 6" />
          )),
        )}
      </svg>
      {EFFECTS.map((effect, i) => {
        const on = frame >= f(5.8 + i * 0.6) && frame < f(6.6 + i * 0.6);
        return (
          <Box key={effect.name} x={effX(i)} y={EFF.y} w={EFF.w} h={EFF.h} color={ACCENT} on={on} opacity={fadeAt(frame, 1.4 + i * 0.3, 0.5)}>
            <div style={{position: 'absolute', left: 0, right: 0, top: 14, textAlign: 'center', fontFamily: MONO, fontSize: 21, fontWeight: 700, color: on ? LOOK : ACCENT}}>{effect.name}</div>
            <div style={{position: 'absolute', left: 0, right: 0, top: 48, textAlign: 'center', fontFamily: SANS, fontSize: 17, color: MUTED}}>{effect.about}</div>
          </Box>
        );
      })}
      <Label x={640} y={EFF.y - 36} size={18} color={ACCENT} center opacity={fadeAt(frame, 6.0)}>
        effects: plain values that say what the program asks for
      </Label>
      {PROVIDERS.map((provider, i) => {
        const on = frame >= f(12.6 + i * 0.5) && frame < f(13.4 + i * 0.5);
        return (
          <Box key={provider.name} x={proX(i)} y={PRO.y} w={PRO.w} h={PRO.h} color={PROD} on={on} opacity={fadeAt(frame, 11.2 + i * 0.3, 0.5)}>
            <div style={{position: 'absolute', left: 0, right: 0, top: 14, textAlign: 'center', fontFamily: MONO, fontSize: 21, fontWeight: 700, color: on ? LOOK : PROD}}>{provider.name}</div>
            <div style={{position: 'absolute', left: 0, right: 0, top: 52, textAlign: 'center', fontFamily: MONO, fontSize: 15, color: MUTED}}>{provider.handler}</div>
          </Box>
        );
      })}
      <Label x={640} y={PRO.y + PRO.h + 16} size={18} color={PROD} center opacity={fadeAt(frame, 13.6)}>
        provider packages: handlers only, no effects of their own
      </Label>
    </AbsoluteFill>
  );
};

// Scene 2 — the old names are deprecated subclasses of LLMChat; one program, answered by two providers
// (measure/shared.py).

const SHARED_LINES: Lines = [
  [0, 'Before doeff-llm, each provider package defined its own near-identical effects.'],
  [5, 'They remain as deprecated aliases: subclasses of LLMChat that warn when built.'],
  [10.5, 'Now one program yields the same LLMChat. Only the model name says who should answer.'],
  [16, 'Under the same handlers, OpenAI answers gpt-4o and Gemini answers gemini-1.5-pro.'],
];
const SHARED = f(24);
// The old names and what measure/shared.log printed for each.
const OLD = ['doeff_openai.effects.ChatCompletion', 'doeff_gemini.effects.GeminiChat', 'doeff_openrouter.effects.RouterChat'];
const OLD_BOX = {x: 60, w: 470, h: 46, y: (i: number) => 128 + i * 62};
const NEW_BOX = {x: 790, y: 172, w: 430, h: 64};
const HELLO = ['@do', 'def hello(model):', '    return (yield LLMChat(..., model=model))'];
const HC = {x: 60, y: 400, w: 560, size: 19, line: 32};
const RUNS = [
  {model: '"gpt-4o"', by: 'openai', answer: "'hi from openai'", at: 16.5},
  {model: '"gemini-1.5-pro"', by: 'gemini', answer: "'hi from gemini'", at: 19.0},
];
const runY = (i: number) => 420 + i * 82;

const SharedScene: React.FC = () => {
  const frame = useSceneFrame();
  const merge = ramp(frame, 5.4, 6.4);
  const band = frame < f(16.3) ? null : 2;
  return (
    <AbsoluteFill>
      <Headlines lines={SHARED_LINES} />
      {OLD.map((name, i) => (
        <Box key={name} x={OLD_BOX.x} y={OLD_BOX.y(i)} w={OLD_BOX.w} h={OLD_BOX.h} color={ACCENT} dashed opacity={fadeAt(frame, 0.6 + i * 0.6, 0.5) * mix(1, 0.75, merge)}>
          <div style={{position: 'absolute', left: 16, top: 10, fontFamily: MONO, fontSize: 17, color: TEXT, whiteSpace: 'nowrap'}}>{name}</div>
        </Box>
      ))}
      {OLD.map((name, i) => (
        <div
          key={`${name}-warn`}
          style={{
            position: 'absolute', left: OLD_BOX.x + OLD_BOX.w + 16, top: OLD_BOX.y(i) + 12, fontFamily: MONO, fontSize: 14, fontWeight: 700, color: PROD,
            opacity: fadeAt(frame, 7.0 + i * 0.4),
          }}
        >
          DeprecationWarning
        </div>
      ))}
      <svg width={1280} height={720} style={{position: 'absolute', left: 0, top: 0, opacity: merge}}>
        {OLD.map((name, i) => (
          <line key={name} x1={OLD_BOX.x + OLD_BOX.w + 186} y1={OLD_BOX.y(i) + OLD_BOX.h / 2} x2={NEW_BOX.x - 6} y2={NEW_BOX.y + NEW_BOX.h / 2} stroke={ACCENT} strokeWidth={2} />
        ))}
      </svg>
      <Box x={NEW_BOX.x} y={NEW_BOX.y} w={NEW_BOX.w} h={NEW_BOX.h} color={ACCENT} on={frame >= f(5.8) && frame < f(7.0)} opacity={fadeAt(frame, 5.2, 0.5)}>
        <div style={{position: 'absolute', left: 0, right: 0, top: 17, textAlign: 'center', fontFamily: MONO, fontSize: 21, fontWeight: 700, color: ACCENT}}>doeff_llm.effects.LLMChat</div>
      </Box>
      <Label x={NEW_BOX.x + NEW_BOX.w / 2} y={NEW_BOX.y + NEW_BOX.h + 10} size={17} center opacity={fadeAt(frame, 6.6)}>
        each old name is a subclass of it
      </Label>
      <CodeBlock lines={HELLO} x={HC.x} y={HC.y} w={HC.w} size={HC.size} line={HC.line} band={band} opacity={fadeAt(frame, 10.8)} />
      <Label x={HC.x} y={HC.y + 24 + HELLO.length * HC.line + 10} size={17} opacity={fadeAt(frame, 12.0)}>
        the model is a field of the effect, not a choice of class
      </Label>
      <Label x={700} y={386} size={17} mono opacity={fadeAt(frame, 13.0)}>
        run(gemini(openai(hello(model))))
        <span style={{fontFamily: SANS, marginLeft: 12}}>mock handlers</span>
      </Label>
      {RUNS.map((r, i) => {
        const out = ramp(frame, r.at, r.at + 0.8);
        const answered = frame >= f(r.at + 0.8);
        return (
          <React.Fragment key={r.model}>
            <Box x={700} y={runY(i)} w={520} h={64} color={TEST} on={answered && frame < f(r.at + 1.8)} opacity={fadeAt(frame, 13.4 + i * 0.3)}>
              <div style={{position: 'absolute', left: 16, top: 8, fontFamily: MONO, fontSize: 17, color: TEXT}}>hello({r.model})</div>
              <div style={{position: 'absolute', left: 16, top: 34, fontFamily: SANS, fontSize: 16, color: MUTED, opacity: fadeAt(frame, r.at + 0.8)}}>
                answered by <span style={{color: TEST, fontWeight: 700}}>{r.by}</span>
              </div>
              <div style={{position: 'absolute', right: 16, top: 20, fontFamily: MONO, fontSize: 19, fontWeight: 700, color: TEST, opacity: fadeAt(frame, r.at + 1.0)}}>{r.answer}</div>
            </Box>
            {frame >= f(r.at) && frame < f(r.at + 1.0) && (
              <Pill x={mix(HC.x + HC.w + 20, 700, out)} y={mix(lineMid(HC.y, HC.line, 2), runY(i) + 32, out)} left text={`LLMChat(model=${r.model})`} color={ACCENT} opacity={1 - ramp(frame, r.at + 0.5, r.at + 0.8)} />
            )}
          </React.Fragment>
        );
      })}
    </AbsoluteFill>
  );
};

// Scene 3 — the page's multi-provider stack (measure/stack.py): each effect travels out until a handler supports
// its model; the handlers that do not yield Pass.

const STACK_LINES: Lines = [
  [0, "The page's example: one workflow, three models, three provider handlers in one stack."],
  [6, 'Each handler reads effect.model. OpenAI supports gpt-4o, so it answers the structured query.'],
  [13, "gemini-1.5-pro is not OpenAI's. Its handler yields Pass with k, and Gemini answers."],
  [20, 'mistral-large-latest passes OpenAI and Gemini, and reaches OpenRouter, the catch-all outside.'],
];
const STACK = f(29);
const WORKFLOW = [
  '@do',
  'def workflow():',
  '    analysis = yield LLMStructuredQuery(..., model="gpt-4o")',
  '    summary = yield LLMChat(..., model="gemini-1.5-pro")',
  '    fallback = yield LLMChat(..., model="mistral-large-latest")',
  '    return analysis, summary, fallback',
];
const WC = {x: 50, y: 226, w: 580, size: 14, line: 24};
const COLS = {w: 172, top: 128, bottom: 488};
const colXs = (n: number) => Array.from({length: n}, (_, i) => 664 + i * (COLS.w + 20));
const OPENAI: Column = {name: 'openai', sub: 'gpt- o1- o3- o4-'};
const GEMINI: Column = {name: 'gemini', sub: 'gemini-'};
const OPENROUTER: Column = {name: 'openrouter', sub: 'any model'};
const ROWS = [248, 338, 428];
const STACK_TRIPS: Trip[] = [
  {line: 2, row: ROWS[0], at: 6.6, passes: 0, text: 'gpt-4o', answer: 'Analysis(...)'},
  {line: 3, row: ROWS[1], at: 13.6, passes: 1, text: 'gemini-1.5-pro', answer: "'The code is clean.'"},
  {line: 4, row: ROWS[2], at: 20.6, passes: 2, text: 'mistral-large-latest', answer: "'Hello!'"},
];
// What workflow() got back for each effect (stack.log).
const STACK_ANSWERS = [
  {model: 'gpt-4o', by: 'openai', value: "Analysis(verdict='clean', score=8)"},
  {model: 'gemini-1.5-pro', by: 'gemini', value: "'The code is clean.'"},
  {model: 'mistral-large-latest', by: 'openrouter', value: "'Hello!'"},
];

const StackScene: React.FC = () => {
  const frame = useSceneFrame();
  const end = doneAt(STACK_TRIPS[2]) + 0.2;
  return (
    <AbsoluteFill>
      <Headlines lines={STACK_LINES} />
      <Chain columns={[OPENAI, GEMINI, OPENROUTER]} xs={colXs(3)} w={COLS.w} top={COLS.top} bottom={COLS.bottom} trips={STACK_TRIPS} code={WC} fadeFrom={1.0} />
      <CodeBlock lines={WORKFLOW} x={WC.x} y={WC.y} w={WC.w} size={WC.size} line={WC.line} band={bandOf(frame, STACK_TRIPS, 5, end)} opacity={fadeAt(frame, 0)} />
      <Label x={WC.x} y={WC.y - 34} size={16} opacity={fadeAt(frame, 2.6)}>
        inside <span style={{color: TEST}}>→</span> outside: the handlers an effect meets, in order
      </Label>
      <Label x={60} y={506} size={17} mono color={TEXT} opacity={fadeAt(frame, 2.0)}>
        run(openrouter(gemini(openai(workflow()))))
        <span style={{fontFamily: SANS, color: MUTED, marginLeft: 14}}>each package's mock handler, so no API is called</span>
      </Label>
      <Answers y={548} rows={STACK_TRIPS.map((trip, i) => ({...STACK_ANSWERS[i], at: doneAt(trip)}))} />
    </AbsoluteFill>
  );
};

// Scene 4 — a handler's annotation filters effects (measure/type_filter.py): the structured query skips
// chat_only without a call.

const FILTER_LINES: Lines = [
  [0, "A handler's annotation filters effects too. chat_only takes effect: LLMChat."],
  [5.5, 'The structured query does not match: it skips chat_only without a call, and any_model answers.'],
  [12, 'The LLMChat matches. chat_only is called, and answers it.'],
];
const FILTER = f(18.5);
const BOTH = [
  '@do',
  'def both():',
  '    analysis = yield LLMStructuredQuery(..., model="gpt-4o")',
  '    reply = yield LLMChat(..., model="gpt-4o")',
  '    return analysis, reply',
];
const FC = {x: 50, y: 236, w: 580, size: 14, line: 24};
const FCOLS = {w: 262, top: 128, bottom: 470};
const FILTER_COLUMNS: Column[] = [
  {name: 'chat_only', sub: '(effect: LLMChat, k)'},
  {name: 'any_model', sub: '(effect, k)'},
];
const FILTER_TRIPS: Trip[] = [
  {line: 2, row: 258, at: 6.0, passes: 1, text: 'LLMStructuredQuery', answer: 'Analysis(...)', tag: 'skipped: not called'},
  {line: 3, row: 368, at: 12.4, passes: 0, text: 'LLMChat', answer: "'chat answer from gpt-4o'"},
];
const FILTER_ANSWERS = [
  {model: 'analysis', by: 'any_model', value: "Analysis(verdict='clean', score=8)"},
  {model: 'reply', by: 'chat_only', value: "'chat answer from gpt-4o'"},
];

const FilterScene: React.FC = () => {
  const frame = useSceneFrame();
  const xs = [664, 664 + FCOLS.w + 30];
  const mismatch = frame >= f(arrive(FILTER_TRIPS[0], 0) - 0.2) && frame < f(arrive(FILTER_TRIPS[0], 0) + STOP + 0.4);
  return (
    <AbsoluteFill>
      <Headlines lines={FILTER_LINES} />
      <Chain columns={FILTER_COLUMNS} xs={xs} w={FCOLS.w} top={FCOLS.top} bottom={FCOLS.bottom} trips={FILTER_TRIPS} code={FC} fadeFrom={0.8} />
      <div
        style={{
          position: 'absolute', left: xs[0] + 16, top: FCOLS.top + 36, width: FCOLS.w - 32, height: 30, boxSizing: 'border-box', borderRadius: 8,
          border: `3px solid ${LOOK}`, opacity: (between(frame, 1.6, 5.2) + (mismatch ? 1 : 0)) * 0.9,
        }}
      />
      <Label x={xs[0] + FCOLS.w / 2} y={FCOLS.top + 76} size={15} bold color={LOOK} center opacity={mismatch ? 1 : 0}>
        LLMStructuredQuery is not an LLMChat
      </Label>
      <CodeBlock lines={BOTH} x={FC.x} y={FC.y} w={FC.w} size={FC.size} line={FC.line} band={bandOf(frame, FILTER_TRIPS, 4, doneAt(FILTER_TRIPS[1]) + 0.2)} opacity={fadeAt(frame, 0)} />
      <Label x={60} y={496} size={17} mono color={TEXT} opacity={fadeAt(frame, 2.0)}>
        run(any_model(chat_only(both())))
      </Label>
      <Answers y={538} rows={FILTER_TRIPS.map((trip, i) => ({...FILTER_ANSWERS[i], at: doneAt(trip)}))} />
      <Label x={60} y={620} size={17} opacity={fadeAt(frame, doneAt(FILTER_TRIPS[1]) + 0.4)}>
        chat_only was called once, for the LLMChat; any_model once, for the structured query
      </Label>
    </AbsoluteFill>
  );
};

// Scene 5 — the order matters (measure/order.py): OpenRouter innermost answers all three effects.

const ORDER_LINES: Lines = [
  [0, 'So the order of the stack matters. OpenRouter answers a chat for any model.'],
  [5, 'Install it innermost, and it answers all three effects. OpenAI and Gemini never see one.'],
  [12.5, 'The catch-all goes outermost. Each handler inside passes on the models it does not support.'],
];
const ORDER = f(19.5);
const ORDER_TRIPS: Trip[] = [
  {line: 2, row: ROWS[0], at: 5.4, passes: 0, text: 'gpt-4o', answer: 'Analysis(...)'},
  {line: 3, row: ROWS[1], at: 7.2, passes: 0, text: 'gemini-1.5-pro', answer: "'Hello!'"},
  {line: 4, row: ROWS[2], at: 9.0, passes: 0, text: 'mistral-large-latest', answer: "'Hello!'"},
];
// How many effects each mock saw in order.py (order.log): only openrouter saw any.
const SAW = [3, 0, 0];

const OrderScene: React.FC = () => {
  const frame = useSceneFrame();
  const swap = ramp(frame, 13.0, 14.4);
  const proper = colXs(3);
  // Inside out: openrouter in the innermost column; the swap moves it to the outside.
  const xs = [mix(proper[0], proper[2], swap), mix(proper[1], proper[1], swap), mix(proper[2], proper[0], swap)];
  const counted = frame >= f(doneAt(ORDER_TRIPS[2]));
  return (
    <AbsoluteFill>
      <Headlines lines={ORDER_LINES} />
      <Chain columns={[OPENROUTER, GEMINI, OPENAI]} xs={xs} w={COLS.w} top={COLS.top} bottom={COLS.bottom} trips={swap > 0 ? [] : ORDER_TRIPS} code={WC} fadeFrom={0.6} />
      {SAW.map((n, i) => (
        <Label key={i} x={xs[i] + COLS.w / 2} y={COLS.bottom - 40} size={16} bold color={n > 0 ? TEST : MUTED} center opacity={(counted ? fadeAt(frame, doneAt(ORDER_TRIPS[2])) : 0) * (1 - swap)}>
          {n > 0 ? `answered ${n}` : 'saw none'}
        </Label>
      ))}
      <CodeBlock lines={WORKFLOW} x={WC.x} y={WC.y} w={WC.w} size={WC.size} line={WC.line} band={swap > 0 ? null : bandOf(frame, ORDER_TRIPS, 5, doneAt(ORDER_TRIPS[2]) + 0.2)} opacity={fadeAt(frame, 0)} />
      <div style={{position: 'absolute', left: 60, top: 506, fontFamily: MONO, fontSize: 17}}>
        <div style={{position: 'absolute', left: 0, top: 0, whiteSpace: 'nowrap', color: TEXT, opacity: fadeAt(frame, 1.0) * (1 - swap)}}>
          run(openai(gemini(openrouter(workflow()))))<span style={{fontFamily: SANS, color: PROD, marginLeft: 14}}>inside out</span>
        </div>
        <div style={{position: 'absolute', left: 0, top: 0, whiteSpace: 'nowrap', color: TEXT, opacity: swap}}>
          run(openrouter(gemini(openai(workflow()))))<span style={{fontFamily: SANS, color: TEST, marginLeft: 14}}>catch-all outermost</span>
        </div>
      </div>
      <div style={{position: 'absolute', left: 60, top: 552, fontFamily: MONO, fontSize: 18, lineHeight: '32px', color: TEXT, opacity: (counted ? fadeAt(frame, doneAt(ORDER_TRIPS[2]) + 0.2) : 0) * (1 - swap)}}>
        <div>answered by openai: []</div>
        <div>answered by openrouter: ['gpt-4o', 'gemini-1.5-pro', 'mistral-large-latest']</div>
      </div>
      <Answers y={548} rows={STACK_ANSWERS.map((row) => ({...row, at: 14.6}))} />
    </AbsoluteFill>
  );
};

// The outro.

const OUTRO_LINES: Lines = [[0, 'One effect says what the program asks for. Handlers decide who answers: they handle it, or pass it on with k.']];
const OUTRO = f(9);
const OUTRO_CHAIN = ['openai', 'gemini', 'openrouter'];

const OutroScene: React.FC = () => {
  const frame = useSceneFrame();
  return (
    <AbsoluteFill>
      <Headlines lines={OUTRO_LINES} />
      <div style={{position: 'absolute', left: 0, right: 0, top: 170 + mix(16, 0, ramp(frame, 0.2, 0.8)), textAlign: 'center', opacity: fadeAt(frame, 0.2, 0.5)}}>
        <div style={{fontFamily: SANS, fontSize: 64, fontWeight: 700, color: TEXT}}>doeff</div>
        <div style={{fontFamily: SANS, fontSize: 30, color: ACCENT, marginTop: 4}}>Algebraic effects for Python</div>
      </div>
      <Pill x={150} y={430} left text="LLMChat(model=...)" color={ACCENT} opacity={fadeAt(frame, 1.0)} />
      <div style={{position: 'absolute', left: 384, top: 430, width: 40, height: 2, background: MUTED, opacity: fadeAt(frame, 1.4)}} />
      {OUTRO_CHAIN.map((name, i) => (
        <React.Fragment key={name}>
          <div style={{position: 'absolute', left: 430 + i * 250, top: 406, width: 180, height: 50, boxSizing: 'border-box', borderRadius: 12, border: `2px solid ${TEST}`, background: PANEL, fontFamily: MONO, fontSize: 20, fontWeight: 700, color: TEST, lineHeight: '46px', textAlign: 'center', opacity: fadeAt(frame, 1.4 + i * 0.3)}}>
            {name}
          </div>
          {i < OUTRO_CHAIN.length - 1 && (
            <Label x={430 + i * 250 + 215} y={384} size={15} bold color={MUTED} center opacity={fadeAt(frame, 1.7 + i * 0.3)}>
              Pass
            </Label>
          )}
          {i < OUTRO_CHAIN.length - 1 && <div style={{position: 'absolute', left: 430 + i * 250 + 190, top: 430, width: 50, height: 2, background: MUTED, opacity: fadeAt(frame, 1.7 + i * 0.3)}} />}
        </React.Fragment>
      ))}
      <Label x={640} y={520} size={22} color={TEXT} center opacity={fadeAt(frame, 3.0)}>
        Routing by model is handler composition: each handler answers, or yields Pass.
      </Label>
    </AbsoluteFill>
  );
};

// The scenes in order; Root.tsx registers LLM_UNIFIED_EFFECTS.length frames of LLM_UNIFIED_EFFECTS.Video.
export const LLM_UNIFIED_EFFECTS = narrate('llm-unified-effects', DURATIONS, [
  {length: TITLE, starts: startsOf(TITLE_LINES), ids: ['t1', 't2', 't3'], node: <TitleScene />},
  {length: SHARED, starts: startsOf(SHARED_LINES), ids: ['s1', 's2', 's3', 's4'], node: <SharedScene />},
  {length: STACK, starts: startsOf(STACK_LINES), ids: ['k1', 'k2', 'k3', 'k4'], node: <StackScene />},
  {length: FILTER, starts: startsOf(FILTER_LINES), ids: ['f1', 'f2', 'f3'], node: <FilterScene />},
  {length: ORDER, starts: startsOf(ORDER_LINES), ids: ['o1', 'o2', 'o3'], node: <OrderScene />},
  {length: OUTRO, starts: [0], ids: ['z1'], node: <OutroScene />},
]);
