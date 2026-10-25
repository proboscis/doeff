// The video on the Getting Started page (docs/01-getting-started.md): the page's ideas in its order, with headlines
// a synthetic voice reads (narration.json):
//   1. A program yields an effect; a handler gets it with k, the rest of the program, and decides what it means.
//   2. The first program: counter_program's effects go out to state() and writer, and the answers come back.
//   3. Your first effect: Progress, which shrink_all yields without saying what it means.
//   4. Two handlers, two meanings: show resumes k each time; stop_after(2) drops k, so the third image is never shrunk.
//   5. Handlers that start with values: reader(env=...) answers Ask and state(initial=...) answers Get.
// Every effect, answer, order and result on screen is one that the runs in measure/ printed (the .log files).
import React from 'react';
import {AbsoluteFill, Easing, interpolate} from 'remotion';
import {fadeAt, mix, ramp} from '../../kit/motion';
import {narrate} from '../../kit/narrated';
import {CodeBlock, glow, Headlines, lineMid, Lines, PAD, Pill, startsOf} from '../../kit/parts';
import {ACCENT, f, LOOK, MONO, MUTED, PANEL, SANS, TEST, TEXT} from '../../kit/theme';
import {useSceneFrame} from '../../kit/warp';
import DURATIONS from './durations.json';

// Opacity that is 1 from `from` on and fades out over `to`..`to + 0.3`.
const until = (frame: number, from: number, to: number) => fadeAt(frame, from) * (1 - ramp(frame, to, to + 0.3));

const Label: React.FC<{x: number; y: number; text: string; color?: string; size?: number; opacity?: number; bold?: boolean; mono?: boolean}> = ({
  x, y, text, color = MUTED, size = 18, opacity = 1, bold = false, mono = false,
}) => (
  <div style={{position: 'absolute', left: x, top: y, fontFamily: mono ? MONO : SANS, fontSize: size, fontWeight: bold ? 700 : 400, color, whiteSpace: 'pre', opacity}}>
    {text}
  </div>
);

type Box = {x: number; y: number; w: number; h: number};

const Panel: React.FC<{box: Box; on: boolean; opacity: number; color?: string; fill?: boolean; children?: React.ReactNode}> = ({
  box, on, opacity, color = TEST, fill = true, children,
}) => (
  <div
    style={{
      position: 'absolute', left: box.x, top: box.y, width: box.w, height: box.h, boxSizing: 'border-box', borderRadius: 18,
      background: fill ? PANEL : 'transparent', border: `3px solid ${color}`, ...glow(on, color), opacity,
    }}
  >
    {children}
  </div>
);

// The k card: the rest of a program, held by a handler.
const KCard: React.FC<{x: number; y: number; text: string; w: number; opacity: number}> = ({x, y, text, w, opacity}) => (
  <div
    style={{
      position: 'absolute', left: x, top: y, width: w, boxSizing: 'border-box', padding: '6px 12px', borderRadius: 10, background: '#1d1a33',
      border: `3px solid ${ACCENT}`, boxShadow: `0 0 18px ${ACCENT}88`, fontFamily: MONO, fontSize: 15, lineHeight: '24px', color: TEXT,
      whiteSpace: 'pre', opacity,
    }}
  >
    {text}
  </div>
);

// Scene 1 — what the page is about: a program yields effects, and a handler gets each one with k.

const INTRO_LINES: Lines = [
  [0, 'Getting started with doeff: algebraic effects for Python.'],
  [4, 'A program yields an effect. A handler gets it, together with k: the rest of the program.'],
  [10, 'The handler decides what the effect means: resume k, throw into it, pass it on, or drop it.'],
];
const INTRO = f(16);
const INTRO_CODE = ['@do', 'def counter_program():', '    count = yield Get("counter")', '    yield Tell(f"Current count: {count}")'];
// What a handler can do with k, as the page's opening paragraph lists it.
const MEANINGS = ['resume k with an answer', 'throw an error into k', 'pass the effect on', 'drop k, end with a value'];

const IntroScene: React.FC = () => {
  const frame = useSceneFrame();
  const code = {x: 70, y: 180, w: 620, size: 21, line: 38};
  const box = {x: 760, y: 150, w: 450, h: 230};
  const kFrom = {x: code.x + 24, y: code.y + PAD + 3 * code.line};
  const kSlot = {x: box.x + 70, y: box.y + 158};
  const kGo = ramp(frame, 6.4, 7.4);
  const restDim = interpolate(frame, [f(6.4), f(6.9)], [1, 0.3], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  const mechanism = fadeAt(frame, 4.2);
  return (
    <AbsoluteFill>
      <Headlines lines={INTRO_LINES} />
      <div style={{position: 'absolute', left: 0, right: 0, top: 170 + mix(16, 0, ramp(frame, 0.2, 0.8)), textAlign: 'center', opacity: until(frame, 0.2, 3.6)}}>
        <div style={{fontFamily: SANS, fontSize: 30, color: MUTED}}>Getting Started</div>
        <div style={{fontFamily: SANS, fontSize: 84, fontWeight: 700, color: TEXT, marginTop: 6}}>doeff</div>
        <div style={{fontFamily: SANS, fontSize: 32, color: ACCENT, marginTop: 4}}>Algebraic effects for Python</div>
        <div style={{fontFamily: MONO, fontSize: 28, color: TEST, marginTop: 44, opacity: fadeAt(frame, 1.2)}}>pip install doeff</div>
      </div>
      <Label x={code.x} y={code.y - 34} text="a program" bold size={20} opacity={mechanism} />
      <CodeBlock lines={INTRO_CODE} {...code} band={frame < f(4.8) ? null : 2} dim={(i) => (i === 3 ? restDim : 1)} opacity={mechanism} />
      <Panel box={box} on={frame >= f(5.6) && frame < f(10)} opacity={fadeAt(frame, 4.4)}>
        <Label x={20} y={14} text="a handler" color={TEST} bold size={24} />
        <Label x={20} y={98} text="effect" />
        <Label x={20} y={166} text="k" />
      </Panel>
      {frame >= f(4.8) && (
        <Pill
          x={mix(code.x + code.w + 20, box.x + 90, ramp(frame, 4.8, 5.6))}
          y={mix(lineMid(code.y, code.line, 2), box.y + 110, ramp(frame, 4.8, 5.6))}
          text={'Get("counter")'}
          color={ACCENT}
          left
        />
      )}
      {frame >= f(6.0) && (
        <KCard x={mix(kFrom.x, kSlot.x, kGo)} y={mix(kFrom.y, kSlot.y, kGo)} w={370} text={'yield Tell(f"Current count: {count}")'} opacity={fadeAt(frame, 6.0)} />
      )}
      <Label x={code.x} y={code.y + 190} text="k = the rest of the program, paused at the yield" color={ACCENT} bold size={20} opacity={fadeAt(frame, 7.4)} />
      {MEANINGS.map((meaning, i) => (
        <div
          key={meaning}
          style={{
            position: 'absolute', left: 70 + (i % 2) * 580, top: 470 + Math.floor(i / 2) * 64, width: 560, height: 50, boxSizing: 'border-box',
            borderRadius: 12, border: `2px solid ${TEST}`, fontFamily: SANS, fontSize: 21, fontWeight: 700, color: TEXT, lineHeight: '46px',
            paddingLeft: 18, opacity: fadeAt(frame, 10.4 + i * 0.7),
          }}
        >
          {meaning}
        </div>
      ))}
    </AbsoluteFill>
  );
};

// Scene 2 — the first program: each effect goes out to the handler that answers it, and the answer comes back.

const PROGRAM_LINES: Lines = [
  [0, 'Your first program: counter_program yields Put, Tell and Get.'],
  [4.5, 'Each effect goes out to the handler that answers it: state() for Get and Put, writer for Tell.'],
  [14.2, 'Each answer comes back to its yield, and the code goes on. The run returns 1.'],
];
const PROGRAM = f(19);
const COUNTER = [
  '@do',
  'def counter_program():',
  '    yield Put("counter", 0)',
  '    yield Tell("Counter initialized")',
  '    count = yield Get("counter")',
  '    yield Tell(f"Current count: {count}")',
  '    yield Put("counter", count + 1)',
  '    final_count = yield Get("counter")',
  '    yield Tell(f"Final count: {final_count}")',
  '    return final_count',
];
// The effects counter_program yielded, in order, with the answers that came back (measure/first_program.log).
type Step = {line: number; effect: string; answer: string; to: 'writer' | 'state'; log?: string; store?: number};
const STEPS: Step[] = [
  {line: 2, effect: "Put('counter', 0)", answer: 'None', to: 'state', store: 0},
  {line: 3, effect: "Tell('Counter initialized')", answer: 'None', to: 'writer', log: 'Counter initialized'},
  {line: 4, effect: "Get('counter')", answer: '0', to: 'state'},
  {line: 5, effect: "Tell('Current count: 0')", answer: 'None', to: 'writer', log: 'Current count: 0'},
  {line: 6, effect: "Put('counter', 1)", answer: 'None', to: 'state', store: 1},
  {line: 7, effect: "Get('counter')", answer: '1', to: 'state'},
  {line: 8, effect: "Tell('Final count: 1')", answer: 'None', to: 'writer', log: 'Final count: 1'},
];
const STEP_FROM = 5.0;
const STEP_EVERY = 1.3;
const leaves = (i: number) => STEP_FROM + i * STEP_EVERY;
const STATE_BOX = {x: 650, y: 110, w: 580, h: 330};
const WRITER_BOX = {x: 672, y: 162, w: 270, h: 258};
const LANDS = {writer: {x: WRITER_BOX.x + 150, y: 245}, state: {x: 1100, y: 245}};

const ProgramScene: React.FC = () => {
  const frame = useSceneFrame();
  const code = {x: 50, y: 110, w: 560, size: 19, line: 30};
  const reached = STEPS.reduce((found, _, i) => (frame >= f(leaves(i)) ? i : found), -1);
  const band =
    frame >= f(13.9) ? mix(8, 9, ramp(frame, 13.9, 14.2)) : reached < 0 ? null : mix(STEPS[Math.max(reached - 1, 0)].line, STEPS[reached].line, ramp(frame, leaves(reached), leaves(reached) + 0.2));
  const done = STEPS.filter((_, i) => frame >= f(leaves(i) + 0.5));
  const log = done.filter((s) => s.log).map((s) => s.log as string);
  const stores = done.filter((s) => s.store !== undefined);
  const working = (to: 'writer' | 'state') => STEPS.some((s, i) => s.to === to && frame >= f(leaves(i) + 0.5) && frame < f(leaves(i) + 0.75));
  const startX = code.x + code.w + 14;
  return (
    <AbsoluteFill>
      <Headlines lines={PROGRAM_LINES} />
      <CodeBlock lines={COUNTER} {...code} band={band} opacity={fadeAt(frame, 0)} />
      <CodeBlock lines={['result = run(state()(writer(counter_program())))']} x={code.x} y={452} w={code.w} size={17} line={28} opacity={fadeAt(frame, 1.6)} />
      <Label x={code.x} y={528} text="Result: 1" color={ACCENT} bold mono size={26} opacity={fadeAt(frame, 14.6)} />
      <Panel box={STATE_BOX} on={working('state')} opacity={fadeAt(frame, 1.0)} fill={false}>
        <Label x={18} y={10} text="state()" color={working('state') ? LOOK : TEST} bold mono size={21} />
        <Label x={124} y={13} text="handles Get · Put" size={17} />
        <Label x={312} y={168} text="store" size={17} />
        <Label x={312} y={196} text={stores.length ? `counter = ${stores[stores.length - 1].store}` : '(empty)'} color={stores.length ? TEXT : MUTED} mono size={21} />
      </Panel>
      <Panel box={WRITER_BOX} on={working('writer')} opacity={fadeAt(frame, 1.8)}>
        <Label x={16} y={10} text="writer" color={working('writer') ? LOOK : TEST} bold mono size={21} />
        <Label x={16} y={40} text="handles Tell" size={17} />
        <Label x={16} y={116} text="log" size={17} />
        {log.map((line, i) => (
          <Label key={line} x={16} y={144 + i * 28} text={line} color={TEXT} mono size={15} />
        ))}
      </Panel>
      <Label x={STATE_BOX.x} y={STATE_BOX.y + STATE_BOX.h + 12} text="writer inside, state() outside: state()(writer(...))" size={17} opacity={fadeAt(frame, 2.6)} />
      <Label x={STATE_BOX.x} y={500} text="effects in order, with their answers" size={17} opacity={fadeAt(frame, 4.6)} />
      {done.map((step, i) => (
        <div key={i} style={{position: 'absolute', left: STATE_BOX.x, top: 530 + i * 25, fontFamily: MONO, fontSize: 16, color: TEXT, whiteSpace: 'pre'}}>
          <span style={{color: ACCENT}}>{step.effect}</span>
          <span style={{color: MUTED}}>{'  →  '}</span>
          <span style={{color: TEST}}>{step.answer}</span>
        </div>
      ))}
      {STEPS.map((step, i) => {
        const leave = leaves(i);
        const y = lineMid(code.y, code.line, step.line);
        const land = LANDS[step.to];
        const out = frame >= f(leave) && frame < f(leave + 0.55);
        const back = frame >= f(leave + 0.7) && frame < f(leave + 1.15);
        const shortName = step.effect.split('(')[0];
        return (
          <React.Fragment key={i}>
            {out && <Pill x={mix(startX, land.x, ramp(frame, leave, leave + 0.5))} y={mix(y, land.y, ramp(frame, leave, leave + 0.5))} text={shortName} color={ACCENT} opacity={1 - ramp(frame, leave + 0.45, leave + 0.55)} />}
            {back && <Pill x={mix(land.x, startX, ramp(frame, leave + 0.7, leave + 1.1))} y={mix(land.y, y, ramp(frame, leave + 0.7, leave + 1.1))} text={step.answer} color={TEST} opacity={1 - ramp(frame, leave + 1.05, leave + 1.15)} />}
          </React.Fragment>
        );
      })}
    </AbsoluteFill>
  );
};

// Scene 3 — an effect of your own: shrink_all reports Progress and leaves its meaning to a handler.

const EFFECT_LINES: Lines = [
  [0, 'Your first effect of your own: Progress, a frozen dataclass under EffectBase.'],
  [5, 'shrink_all yields Progress after each image. What that report means is up to a handler.'],
];
const EFFECT = f(12);
const SHRINK = [
  '@dataclass(frozen=True)',
  'class Progress(EffectBase):',
  '    done: int',
  '    total: int',
  ' ',
  '@do',
  'def shrink_all(images):',
  '    sizes = []',
  '    for width, height in images:',
  '        sizes.append((width // 2, height // 2))',
  '        yield Progress(len(sizes), len(images))',
  '    return sizes',
];

const EffectScene: React.FC = () => {
  const frame = useSceneFrame();
  const code = {x: 50, y: 110, w: 620, size: 19, line: 30};
  const box = {x: 790, y: 300, w: 430, h: 180};
  const out = ramp(frame, 6.2, 7.0);
  return (
    <AbsoluteFill>
      <Headlines lines={EFFECT_LINES} />
      <CodeBlock lines={SHRINK} {...code} band={frame < f(5.6) ? null : 10} opacity={fadeAt(frame, 0)} />
      <div
        style={{
          position: 'absolute', left: code.x + 8, top: code.y + PAD - 2, width: code.w - 16, height: 4 * code.line + 4, boxSizing: 'border-box',
          borderRadius: 8, border: `3px solid ${LOOK}`, boxShadow: `0 0 16px ${LOOK}88`, opacity: until(frame, 0.8, 4.6),
        }}
      />
      <Label x={790} y={140} text={'your own effect:\na value that names an operation'} color={ACCENT} bold size={22} opacity={until(frame, 1.4, 4.6)} />
      <Panel box={box} on={frame >= f(7.0)} opacity={fadeAt(frame, 5.4)}>
        <Label x={20} y={14} text="a handler" color={frame >= f(7.0) ? LOOK : TEST} bold size={24} />
        <Label x={20} y={60} text={'installed around shrink_all,\ndecides what Progress means'} size={19} />
      </Panel>
      {frame >= f(6.2) && (
        <Pill x={mix(code.x + code.w + 20, box.x + 60, out)} y={mix(lineMid(code.y, code.line, 10), box.y - 30, out)} text="Progress(1, 3)" color={ACCENT} left />
      )}
      <Label x={code.x} y={530} text="No cancel flag, no callback parameter: shrink_all only reports." color={MUTED} size={22} opacity={fadeAt(frame, 8.0)} />
    </AbsoluteFill>
  );
};

// Scene 4 — the same program under two handlers: show resumes k each time, stop_after(2) drops k.

const TWO_LINES: Lines = [
  [0, 'show notes each Progress and resumes k. All three images are shrunk.'],
  [7.0, 'stop_after(2) returns at the second report, without resuming k.'],
  [12.0, 'The rest is dropped: the third image is never shrunk. Same program, another meaning.'],
];
const TWO = f(18);
// The three images and their halves (measure/first_handler.log), drawn at 0.17 of their size.
const IMAGES = [
  {w: 640, h: 480, x: 60},
  {w: 1024, h: 768, x: 205},
  {w: 800, h: 600, x: 415},
];
const SCALE = 0.17;
const LANES = {show: 104, stop: 384} as const;
// When each lane's i-th image is shrunk; its Progress leaves 0.4 s later.
const stepAt = (lane: 'show' | 'stop', i: number) => (lane === 'show' ? 1.0 : 7.6) + i * 1.7;

const Lane: React.FC<{lane: 'show' | 'stop'}> = ({lane}) => {
  const frame = useSceneFrame();
  const y0 = LANES[lane];
  const steps = lane === 'show' ? 3 : 2;
  const base = y0 + 182;
  const box = {x: 760, y: y0 + 30, w: 460, h: 170};
  const seen = Array.from({length: steps}, (_, i) => i).filter((i) => frame >= f(stepAt(lane, i) + 0.9));
  const working = Array.from({length: steps}, (_, i) => i).some((i) => frame >= f(stepAt(lane, i) + 0.9) && frame < f(stepAt(lane, i) + 1.15));
  const stopAt = stepAt('stop', 1) + 0.9;
  const dropped = lane === 'stop' && frame >= f(12.3);
  const handlerCode = lane === 'show' ? 'shown.append(...)\nreturn (yield Resume(k, None))' : 'if effect.done >= limit:\n    return f"stopped after ..."\nreturn (yield Resume(k, None))';
  return (
    <>
      <Label x={60} y={y0} text={lane === 'show' ? 'run(show(shrink_all(images)))' : 'run(stop_after(2)(shrink_all(images)))'} color={TEXT} mono size={19} opacity={fadeAt(frame, lane === 'show' ? 0.2 : 7.0)} />
      {IMAGES.map((image, i) => {
        const shrinkAt = stepAt(lane, i);
        const shrunk = i < steps ? ramp(frame, shrinkAt, shrinkAt + 0.4) : 0;
        const w = image.w * SCALE * mix(1, 0.5, shrunk);
        const h = image.h * SCALE * mix(1, 0.5, shrunk);
        const busy = i < steps && frame >= f(shrinkAt) && frame < f(shrinkAt + 0.5);
        const never = lane === 'stop' && i === 2;
        const size = shrunk > 0.5 ? `${image.w / 2}×${image.h / 2}` : `${image.w}×${image.h}`;
        return (
          <React.Fragment key={i}>
            <div
              style={{
                position: 'absolute', left: image.x, top: base - h, width: w, height: h, boxSizing: 'border-box', borderRadius: 6,
                background: `linear-gradient(160deg, #26304a, ${PANEL})`, border: `2px solid ${busy ? LOOK : '#3a3f55'}`,
                boxShadow: busy ? `0 0 16px ${LOOK}aa` : 'none', opacity: fadeAt(frame, lane === 'show' ? 0.4 : 7.2) * (never && dropped ? 0.35 : 1),
              }}
            />
            <Label x={image.x} y={base + 6} text={size} color={shrunk > 0.5 ? TEST : MUTED} mono size={15} opacity={fadeAt(frame, lane === 'show' ? 0.4 : 7.2) * (never && dropped ? 0.35 : 1)} />
            {never && <Label x={image.x} y={base + 28} text="never shrunk" color={LOOK} bold size={17} opacity={fadeAt(frame, 12.5)} />}
          </React.Fragment>
        );
      })}
      <Panel box={box} on={working || (lane === 'stop' && frame >= f(stopAt) && frame < f(11.4))} opacity={fadeAt(frame, lane === 'show' ? 0.3 : 7.1)}>
        <Label x={18} y={10} text={lane === 'show' ? 'show' : 'stop_after(2)'} color={TEST} bold mono size={21} />
        <Label x={18} y={44} text={handlerCode} color={MUTED} mono size={14} />
        <Label x={18} y={128} text={`seen: ${seen.map((i) => `${i + 1}/3`).join('  ')}`} color={TEXT} mono size={17} />
      </Panel>
      {Array.from({length: steps}, (_, i) => {
        const s = stepAt(lane, i);
        const image = IMAGES[i];
        const from = {x: image.x + (image.w * SCALE) / 2 + 20, y: base - (image.h * SCALE) / 2 - 10};
        const to = {x: box.x - 10, y: box.y + 60};
        const resumes = lane === 'show' || i < 1;
        return (
          <React.Fragment key={i}>
            {frame >= f(s + 0.4) && frame < f(s + 0.95) && (
              <Pill x={mix(from.x, to.x, ramp(frame, s + 0.4, s + 0.9))} y={mix(from.y, to.y, ramp(frame, s + 0.4, s + 0.9))} text={`Progress(${i + 1}, 3)`} color={ACCENT} opacity={1 - ramp(frame, s + 0.85, s + 0.95)} />
            )}
            {resumes && frame >= f(s + 1.1) && frame < f(s + 1.6) && (
              <Pill x={mix(to.x, from.x + 120, ramp(frame, s + 1.1, s + 1.5))} y={to.y + 40} text="Resume(k, None)" color={TEST} opacity={1 - ramp(frame, s + 1.45, s + 1.6)} />
            )}
          </React.Fragment>
        );
      })}
      {lane === 'stop' && frame >= f(stopAt + 0.3) && (
        <>
          <KCard x={box.x + 200} y={box.y + 118} w={240} text="k: rest of shrink_all" opacity={fadeAt(frame, stopAt + 0.3) * (1 - 0.7 * ramp(frame, 11.0, 11.6))} />
          <Label x={box.x + 200} y={box.y + 176} text="dropped, never resumed" color={MUTED} bold size={16} opacity={fadeAt(frame, 11.3)} />
        </>
      )}
      <Label
        x={60}
        y={y0 + 222}
        text={lane === 'show' ? '→ [(320, 240), (512, 384), (400, 300)]' : '→ stopped after 2 of 3'}
        color={ACCENT}
        bold
        mono
        size={19}
        opacity={fadeAt(frame, lane === 'show' ? 6.0 : 10.8)}
      />
    </>
  );
};

const TwoScene: React.FC = () => {
  const frame = useSceneFrame();
  return (
    <AbsoluteFill>
      <Headlines lines={TWO_LINES} />
      <Lane lane="show" />
      <div style={{position: 'absolute', left: 60, right: 60, top: 366, height: 2, background: '#2a2e40', opacity: fadeAt(frame, 6.6)}} />
      <Lane lane="stop" />
    </AbsoluteFill>
  );
};

// Scene 5 — handlers that start with values: reader(env=...) answers Ask, state(initial=...) answers Get.

const START_LINES: Lines = [
  [0, 'Handlers can start with values: reader(env=...) and state(initial=...).'],
  [4.5, 'Ask is answered from the environment, Get from the initial state.'],
  [10.5, 'Start state with another user_id, and the same program connects as user 7.'],
];
const START = f(16);
const CONNECT = [
  '@do',
  'def my_program():',
  '    db_url = yield Ask("database_url")',
  '    user_id = yield Get("user_id")',
  '    return f"Connected to {db_url} as user {user_id}"',
];
const URL = 'postgresql://localhost/mydb';
// The results of the two runs (measure/initial_state.log).
const RESULTS = [
  {user: 123, text: `Connected to ${URL} as user 123`, at: 9.2},
  {user: 7, text: `Connected to ${URL} as user 7`, at: 13.2},
];

const StartScene: React.FC = () => {
  const frame = useSceneFrame();
  const code = {x: 60, y: 270, w: 640, size: 18, line: 34};
  const readerBox = {x: 60, y: 110, w: 560, h: 110};
  const stateBox = {x: 660, y: 110, w: 560, h: 110};
  const user = frame >= f(11.0) ? 7 : 123;
  const band = frame < f(5.0) ? null : frame < f(6.9) ? 2 : frame < f(8.8) ? mix(2, 3, ramp(frame, 6.9, 7.2)) : frame < f(11.4) ? mix(3, 4, ramp(frame, 8.8, 9.1)) : frame < f(12.7) ? 3 : mix(3, 4, ramp(frame, 12.7, 13.0));
  const lineEnd = code.x + code.w - 120;
  // One trip: an effect from its line up to a handler, and the answer down to the right of the code.
  const trip = (key: string, line: number, effect: string, answer: string, target: Box, leave: number) => {
    const y = lineMid(code.y, code.line, line);
    const top = {x: target.x + target.w / 2, y: target.y + target.h + 10};
    return (
      <React.Fragment key={key}>
        {frame >= f(leave) && frame < f(leave + 0.75) && (
          <Pill x={mix(lineEnd, top.x, ramp(frame, leave, leave + 0.7))} y={mix(y, top.y, ramp(frame, leave, leave + 0.7))} text={effect} color={ACCENT} opacity={1 - ramp(frame, leave + 0.65, leave + 0.75)} />
        )}
        {frame >= f(leave + 1.0) && frame < f(leave + 1.75) && (
          <Pill x={mix(top.x, code.x + code.w + 30 + answer.length * 6, ramp(frame, leave + 1.0, leave + 1.7))} y={mix(top.y, y, ramp(frame, leave + 1.0, leave + 1.7))} text={answer} color={TEST} opacity={1 - ramp(frame, leave + 1.65, leave + 1.75)} />
        )}
      </React.Fragment>
    );
  };
  const working = (leave: number) => frame >= f(leave + 0.7) && frame < f(leave + 1.0);
  return (
    <AbsoluteFill>
      <Headlines lines={START_LINES} />
      <Panel box={readerBox} on={working(5.0)} opacity={fadeAt(frame, 0.6)}>
        <Label x={18} y={12} text="reader(env=...)" color={TEST} bold mono size={21} />
        <Label x={18} y={58} text={`database_url = "${URL}"`} color={TEXT} mono size={16} />
      </Panel>
      <Panel box={stateBox} on={working(7.0) || working(11.4) || (frame >= f(11.0) && frame < f(11.4))} opacity={fadeAt(frame, 1.0)}>
        <Label x={18} y={12} text="state(initial=...)" color={TEST} bold mono size={21} />
        <Label x={18} y={58} text={`user_id = ${user}`} color={TEXT} mono size={16} />
      </Panel>
      <CodeBlock lines={CONNECT} {...code} band={band} opacity={fadeAt(frame, 0)} />
      <CodeBlock
        lines={[`prog = state(initial={"user_id": ${user}})(my_program())`, `prog = reader(env={"database_url": "${URL}"})(prog)`]}
        x={60} y={490} w={760} size={16} line={28} opacity={fadeAt(frame, 1.6)}
      />
      {trip('ask', 2, 'Ask("database_url")', `"${URL}"`, readerBox, 5.0)}
      {trip('get', 3, 'Get("user_id")', '123', stateBox, 7.0)}
      {trip('get7', 3, 'Get("user_id")', '7', stateBox, 11.4)}
      {RESULTS.map((result, i) => (
        <div key={result.user} style={{position: 'absolute', left: 60, top: 600 + i * 40, fontFamily: MONO, fontSize: 19, fontWeight: 700, color: ACCENT, whiteSpace: 'pre', opacity: fadeAt(frame, result.at)}}>
          <span style={{color: MUTED, fontWeight: 400}}>{`user_id=${result.user}  → `}</span>
          {result.text}
        </div>
      ))}
    </AbsoluteFill>
  );
};

// The outro: the page's idea in one sentence, where to read on, and how to install.

const OUTRO = f(7);

const Outro: React.FC = () => {
  const frame = useSceneFrame();
  const rise = interpolate(frame, [0, f(0.67)], [20, 0], {extrapolateRight: 'clamp', easing: Easing.out(Easing.cubic)});
  return (
    <AbsoluteFill style={{padding: 90, justifyContent: 'center', transform: `translateY(${rise}px)`}}>
      <div style={{fontFamily: SANS, fontSize: 56, fontWeight: 700, color: TEXT, opacity: fadeAt(frame, 0, 0.4)}}>doeff</div>
      <div style={{fontFamily: SANS, fontSize: 30, color: ACCENT, marginTop: 6, opacity: fadeAt(frame, 0.2, 0.4)}}>Algebraic effects for Python</div>
      <div style={{fontFamily: SANS, fontSize: 28, lineHeight: '42px', color: TEXT, marginTop: 34, width: 1050, opacity: fadeAt(frame, 0.6, 0.4)}}>
        Programs yield effects. Handlers get each one with k, the rest of the program, and decide what it means.
      </div>
      <div style={{fontFamily: SANS, fontSize: 24, color: MUTED, marginTop: 24, opacity: fadeAt(frame, 1.0, 0.4)}}>Next: Core Concepts, the handler contract.</div>
      <div style={{fontFamily: MONO, fontSize: 28, color: TEST, marginTop: 40, opacity: fadeAt(frame, 1.4, 0.4)}}>pip install doeff</div>
    </AbsoluteFill>
  );
};

// The scenes in order; Root.tsx registers GETTING_STARTED.length frames of GETTING_STARTED.Video.
export const GETTING_STARTED = narrate('getting-started', DURATIONS, [
  {length: INTRO, starts: startsOf(INTRO_LINES), ids: ['g1', 'g2', 'g3'], node: <IntroScene />},
  {length: PROGRAM, starts: startsOf(PROGRAM_LINES), ids: ['p1', 'p2', 'p3'], node: <ProgramScene />},
  {length: EFFECT, starts: startsOf(EFFECT_LINES), ids: ['e1', 'e2'], node: <EffectScene />},
  {length: TWO, starts: startsOf(TWO_LINES), ids: ['t1', 't2', 't3'], node: <TwoScene />},
  {length: START, starts: startsOf(START_LINES), ids: ['s1', 's2', 's3'], node: <StartScene />},
  {length: OUTRO, starts: [0], ids: ['o1'], node: <Outro />},
]);
