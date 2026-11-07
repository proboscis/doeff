// The video at the top of the article "Algebraic effects for Python" (docs/articles/algebraic-effects-in-python.md):
// the card battle of examples/effects_tour/spire.py as one function, read from the top line by line (what one turn
// does), whose effects then travel out to handlers stacked around it, with k; then the stacks of the article swapped around the same function (a scripted player, a random
// bot, record and replay, a Monte Carlo bot, a hundred fights). Every code line on screen is a line of spire.py
// (check_code.py checks it); every result and count is a line of measure/stacks.py's output.
import React from 'react';
import {AbsoluteFill} from 'remotion';
import {fadeAt, mix, ramp} from '../../kit/motion';
import {narrate} from '../../kit/narrated';
import {CodeBlock, Headlines, Lines, lineMid, PAD, Pill, startsOf} from '../../kit/parts';
import {ACCENT, BG, f, FAIL, LOOK, MONO, MUTED, PANEL, PROD, SANS, TEST, TEXT} from '../../kit/theme';
import {useSceneFrame} from '../../kit/warp';
import DURATIONS from './durations.json';
import NARRATION from './narration.json';

// The rest of the function, k, once it is a value a handler holds.
export const K = '#7cc4ff';

// A pill moving from (x0, y0) to (x1, y1) between two times; hidden before `from` and after `until`.
export const Moving: React.FC<{text: string; color: string; from: number; to: number; x0: number; y0: number; x1: number; y1: number; until?: number}> = ({
  text, color, from, to, x0, y0, x1, y1, until = Infinity,
}) => {
  const frame = useSceneFrame();
  if (frame < f(from) || frame >= f(until)) return null;
  const t = ramp(frame, from, to);
  return <Pill x={mix(x0, x1, t)} y={mix(y0, y1, t)} text={text} color={color} opacity={fadeAt(frame, from, 0.2)} />;
};

// 1. The function read from the top: what one turn does, in the order of its lines; then its effects, and k.
const FN_LINES: Lines = [
  [0, 'battle: one function, a small card fight played to the end.'],
  [6.0, 'Each turn, it draws a hand of cards,'],
  [10.0, 'shows the turn on the screen,'],
  [13.0, 'lets the player choose a card, and plays it.'],
  [16.8, "Then dice decide the enemy's move."],
  [20.8, 'Until one side is out of HP. Then it returns who won.'],
  [26.6, 'Show, Choose, Played, Roll: effects, handed out to a handler.'],
  [37.0, 'The handler also gets the rest of battle: k.'],
];
const FN_CODE = [
  '@do',
  'def battle(s: Fight = Fight()):',
  '    while s.hp > 0 and s.enemy_hp > 0:',
  '        s, hand = yield draw_hand(s)',
  '        energy = 3',
  '        while energy > 0 and hand:',
  '            yield Show(f"T{s.turn} you {s.hp}',
  '            pick = yield Choose(s, hand, energy)',
  '            ...',
  '            s, dmg = hit(s, card)',
  '            yield Played(card, dmg)',
  '        ...',
  '        intent = yield Roll(3)',
  '        ...',
  '    return ("win" if s.enemy_hp <= 0 else "lose"), s.turn',
];
const FN = f(45);
const CODE_X = 60;
const CODE_Y = 120;
const CODE_W = 720;
const LINE = 30;
const CHOOSE = 7;
// The lines lit while the narration reads them: [from, the lines], each entry until the next one.
const LIT: [number, number[]][] = [
  [0.3, [0, 1]],
  [6.0, [3]],
  [10.0, [6]],
  [13.0, [CHOOSE]],
  [15.1, [CHOOSE, 9, 10]],
  [16.8, [12]],
  [20.8, [2]],
  [24.0, [2, 14]],
  // The effects, in the order the narration names them; then Choose, where battle is paused.
  [26.8, [6]],
  [27.3, [6, CHOOSE]],
  [27.8, [6, CHOOSE, 10]],
  [28.3, [6, CHOOSE, 10, 12]],
  [29.8, [CHOOSE]],
];
// What one turn does, one row per line of battle as the narration reaches it: [from, the code, what it does].
const STEPS: [number, string, string][] = [
  [6.2, 'draw_hand', 'draw 5 cards'],
  [10.2, 'Show', 'put the turn on the screen'],
  [13.2, 'Choose', 'the player picks a card'],
  [15.1, 'hit, Played', 'play it, count the damage'],
  [17.0, 'Roll(3)', "dice: the enemy's move"],
];
const PANEL_X = 820;
const PANEL_W = 410;
const HANDLER = {x: 860, y: 200, w: 360, h: 230};

const Function: React.FC = () => {
  const frame = useSceneFrame();
  const at = (seconds: number) => frame >= f(seconds);
  const lit = LIT.reduce<[number, number[]] | null>((found, entry) => (at(entry[0]) ? entry : found), null);
  const kOn = at(37.4);
  const kTop = CODE_Y + PAD + (CHOOSE + 1) * LINE;
  const handlerX = HANDLER.x + HANDLER.w / 2;
  const chooseY = lineMid(CODE_Y, LINE, CHOOSE);
  // The steps of one turn give way to the handler when the effects are named.
  const steps = fadeAt(frame, 0.2) * (1 - fadeAt(frame, 26.6, 0.5));
  const latest = STEPS.reduce((found, [from], i) => (at(from) ? i : found), -1);
  return (
    <AbsoluteFill>
      <Headlines lines={FN_LINES} />
      <div style={{position: 'absolute', left: CODE_X, top: CODE_Y - 30, fontFamily: SANS, fontSize: 19, fontWeight: 700, color: ACCENT, opacity: fadeAt(frame, 0.1)}}>
        spire.py: battle
      </div>
      <CodeBlock lines={FN_CODE} x={CODE_X} y={CODE_Y} w={CODE_W} size={18} line={LINE} opacity={fadeAt(frame, 0.1)} />
      {/* The lines the narration is reading. */}
      {lit &&
        lit[1].map((i) => (
          <div
            key={`${lit[0]}-${i}`}
            style={{
              position: 'absolute', left: CODE_X + 2, top: CODE_Y + PAD + i * LINE, width: CODE_W - 4, height: LINE, background: LOOK,
              opacity: 0.22 * fadeAt(frame, lit[0], 0.25),
            }}
          />
        ))}
      {/* k: everything battle has left to do after the Choose. */}
      <div
        style={{
          position: 'absolute', left: CODE_X + 4, top: kTop, width: CODE_W - 8, height: (FN_CODE.length - CHOOSE - 1) * LINE, borderRadius: 10, background: K,
          opacity: kOn ? 0.16 * fadeAt(frame, 37.4, 0.4) : 0,
        }}
      />
      {/* One turn of battle, row by row as its lines are read. */}
      <div
        style={{
          position: 'absolute', left: PANEL_X, top: CODE_Y, width: PANEL_W, boxSizing: 'border-box', padding: '16px 22px', background: PANEL,
          borderRadius: 14, border: `2px solid #2a2e40`, opacity: steps,
        }}
      >
        <div style={{fontFamily: SANS, fontSize: 18, fontWeight: 700, color: MUTED, marginBottom: 12}}>one turn of battle</div>
        {STEPS.map(([from, code, what], i) => (
          <div key={code} style={{display: 'flex', gap: 14, alignItems: 'baseline', height: 52, opacity: fadeAt(frame, from)}}>
            <div style={{width: 24, fontFamily: SANS, fontSize: 18, color: MUTED}}>{i + 1}.</div>
            <div>
              <div style={{fontFamily: MONO, fontSize: 19, fontWeight: 700, color: i === latest && !at(20.8) ? LOOK : ACCENT}}>{code}</div>
              <div style={{fontFamily: SANS, fontSize: 17, color: TEXT}}>{what}</div>
            </div>
          </div>
        ))}
        <div style={{fontFamily: SANS, fontSize: 18, color: at(20.8) && !at(24.0) ? LOOK : MUTED, marginTop: 8, opacity: fadeAt(frame, 21.0)}}>
          again, while both sides have HP
        </div>
        <div style={{fontFamily: MONO, fontSize: 19, color: at(24.0) ? LOOK : MUTED, marginTop: 10, opacity: fadeAt(frame, 24.0)}}>
          return ("win" | "lose", turn)
        </div>
      </div>
      {/* The handler: a function of (effect, k). */}
      <div
        style={{
          position: 'absolute', left: HANDLER.x, top: HANDLER.y, width: HANDLER.w, height: HANDLER.h, boxSizing: 'border-box', borderRadius: 16,
          border: `3px solid ${TEST}`, background: PANEL, opacity: fadeAt(frame, 29.8), padding: '14px 20px',
        }}
      >
        <div style={{fontFamily: MONO, fontSize: 22, fontWeight: 700, color: TEST}}>a handler</div>
        <div style={{fontFamily: MONO, fontSize: 20, color: MUTED, marginTop: 6}}>def choose(effect, k):</div>
        <div style={{fontFamily: SANS, fontSize: 18, color: TEXT, marginTop: 70, opacity: fadeAt(frame, 41.0)}}>decides how battle goes on</div>
      </div>
      <div style={{position: 'absolute', left: HANDLER.x, top: HANDLER.y + HANDLER.h + 20, width: HANDLER.w, fontFamily: SANS, fontSize: 20, color: MUTED, opacity: fadeAt(frame, 34.0) * (1 - fadeAt(frame, 37.0, 0.4))}}>
        which handler? battle never says.
      </div>
      <Moving text="Choose" color={ACCENT} from={30.4} to={32.0} until={41.4} x0={CODE_X + 480} y0={chooseY} x1={handlerX - 70} y1={HANDLER.y + 115} />
      <Moving text="k" color={K} from={38.4} to={40.0} until={41.4} x0={CODE_X + CODE_W - 40} y0={kTop + 3 * LINE} x1={handlerX + 60} y1={HANDLER.y + 115} />
      <Moving text="Resume(k, 0)" color={TEST} from={41.4} to={43.0} until={44.6} x0={handlerX} y0={HANDLER.y + 115} x1={CODE_X + 480} y1={chooseY} />
      <div
        style={{
          position: 'absolute', left: CODE_X, top: CODE_Y + PAD * 2 + FN_CODE.length * LINE + 18, width: CODE_W, fontFamily: SANS, fontSize: 21, color: K,
          opacity: kOn ? fadeAt(frame, 38.4) : 0,
        }}
      >
        k: the rest of battle, paused at the yield, held by the handler
      </div>
    </AbsoluteFill>
  );
};

// A stack of handlers drawn as rings around battle, innermost first.
export type Ring = {label: string; color?: string; glow?: boolean};
export type Geometry = {cx: number; cy: number; bw: number; bh: number; gap: number; font: number};
const BIG: Geometry = {cx: 420, cy: 410, bw: 230, bh: 110, gap: 38, font: 17};

const ringBox = (g: Geometry, i: number) => {
  const w = g.bw + 2 * g.gap * (i + 1);
  const h = g.bh + 2 * g.gap * (i + 1);
  return {left: g.cx - w / 2, top: g.cy - h / 2, w, h, right: g.cx + w / 2};
};

export const Tower: React.FC<{rings: Ring[]; g?: Geometry; opacity?: number; note?: boolean; title?: string}> = ({rings, g = BIG, opacity = 1, note = false, title = 'battle()'}) => (
  <AbsoluteFill style={{opacity}}>
    {[...rings.keys()].reverse().map((i) => {
      const box = ringBox(g, i);
      const color = rings[i].color ?? TEST;
      return (
        <div
          key={i}
          style={{
            position: 'absolute', left: box.left, top: box.top, width: box.w, height: box.h, boxSizing: 'border-box', borderRadius: 14 + 3 * i,
            border: `3px solid ${rings[i].glow ? LOOK : color}`, background: i === rings.length - 1 ? '#11141d' : 'transparent',
            boxShadow: rings[i].glow ? `0 0 22px ${LOOK}aa` : 'none',
          }}
        >
          <div style={{position: 'absolute', left: 12, top: g.gap * 0.18, fontFamily: MONO, fontSize: g.font, fontWeight: 700, color: rings[i].glow ? LOOK : color, whiteSpace: 'nowrap'}}>
            {rings[i].label}
          </div>
        </div>
      );
    })}
    <div
      style={{
        position: 'absolute', left: g.cx - g.bw / 2, top: g.cy - g.bh / 2, width: g.bw, height: g.bh, boxSizing: 'border-box', borderRadius: 12,
        border: `3px solid ${ACCENT}`, background: '#0b0d13', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
      }}
    >
      <div style={{fontFamily: MONO, fontSize: g.font * 1.55, fontWeight: 700, color: TEXT}}>{title}</div>
      {note && <div style={{fontFamily: SANS, fontSize: g.font, color: LOOK, marginTop: 4}}>0 lines changed</div>}
    </div>
  </AbsoluteFill>
);

// Where an effect from battle stops: on the right border of ring i, on its own row.
const stopAt = (g: Geometry, i: number) => ringBox(g, i).right;
const ROWS = [-36, -12, 12, 36].map((dy) => BIG.cy + dy * 1.6);

// The effects of one stack on their way out: [text, ring index that takes it, row, start].
type Trip = [string, number, number, number];
const Trips: React.FC<{trips: Trip[]; until: number}> = ({trips, until}) => (
  <>
    {trips.map(([text, ring, row, from]) => (
      <Moving key={`${text}${from}`} text={text} color={ACCENT} from={from} to={from + 0.9} until={until} x0={BIG.cx + BIG.bw / 2 - 30} y0={ROWS[row]} x1={stopAt(BIG, ring)} y1={ROWS[row]} />
    ))}
  </>
);

// What a stack makes, in the panel on the right.
type Row = {at: number; until?: number; text: string; color?: string; big?: boolean};
const Results: React.FC<{rows: Row[]; title: string; children?: React.ReactNode}> = ({rows, title, children}) => {
  const frame = useSceneFrame();
  return (
    <div
      style={{
        position: 'absolute', left: 800, top: 170, width: 420, height: 480, boxSizing: 'border-box', padding: '16px 22px', background: PANEL,
        borderRadius: 14, border: `2px solid ${TEST}`, opacity: fadeAt(frame, 0.2),
      }}
    >
      <div style={{fontFamily: SANS, fontSize: 18, fontWeight: 700, color: MUTED, marginBottom: 12}}>{title}</div>
      {children}
      {rows.filter((row) => frame >= f(row.at) && frame < f(row.until ?? Infinity)).map((row) => (
        <div
          key={row.text}
          style={{
            fontFamily: MONO, whiteSpace: 'pre', fontSize: row.big ? 28 : 19, lineHeight: row.big ? '44px' : '32px', fontWeight: row.big ? 700 : 400,
            color: row.color ?? TEXT, opacity: fadeAt(frame, row.at),
          }}
        >
          {row.text}
        </div>
      ))}
    </div>
  );
};

// Two stacks one after the other, cross-faded at `swap`.
const Swap: React.FC<{before: Ring[]; after: Ring[]; swap: number; noteFrom?: number}> = ({before, after, swap, noteFrom = Infinity}) => {
  const frame = useSceneFrame();
  const t = fadeAt(frame, swap, 0.5);
  return (
    <>
      <Tower rings={before} opacity={fadeAt(frame, 0.1) * (1 - t)} />
      <Tower rings={after} opacity={t} note={frame >= f(noteFrom)} />
    </>
  );
};

// 2. A stack, then the same function under a random bot and no screen.
const STACK_LINES: Lines = [[0, 'Stack handlers around it: the same fight on every run.'], [7.5, 'Swap the player and the screen. battle does not change.']];
const STACK_SECONDS = 15;
const STACK = f(STACK_SECONDS);
const SCRIPTED: Ring[] = [{label: 'scripted(moves)'}, {label: 'dice(7)'}, {label: 'printed', color: PROD}, {label: 'quiet_stats'}];
const RANDOM: Ring[] = [{label: 'random_bot', glow: true}, {label: 'dice(3)', glow: true}, {label: 'silent', glow: true}, {label: 'quiet_stats'}];
const STACK_ROWS: Row[] = [
  {at: 4.0, until: 7.5, text: "run 1  -> ('win', 6)"},
  {at: 4.5, until: 7.5, text: "run 2  -> ('win', 6)"},
  {at: 5.0, until: 7.5, text: "run 3  -> ('win', 6)"},
  {at: 5.6, until: 7.5, text: 'the same fight', color: LOOK, big: true},
  {at: 10.6, text: "-> ('win', 4)", color: LOOK, big: true},
  {at: 11.4, text: 'battle: 0 lines changed', color: TEXT},
];

const Stacking: React.FC = () => (
  <AbsoluteFill>
    <Headlines lines={STACK_LINES} />
    <Swap before={SCRIPTED} after={RANDOM} swap={7.5} noteFrom={11.4} />
    <Trips trips={[['Choose', 0, 0, 1.0], ['Roll', 1, 1, 1.5], ['Show', 2, 2, 2.0], ['Played', 3, 3, 2.5]]} until={7.4} />
    <Trips trips={[['Choose', 0, 0, 8.4], ['Show', 2, 2, 9.4], ['Played', 3, 3, 9.9]]} until={STACK_SECONDS} />
    {/* random_bot yields a Roll of its own, which travels out to the dice. */}
    <Moving text="Roll" color={ACCENT} from={9.0} to={9.8} until={STACK_SECONDS} x0={stopAt(BIG, 0)} y0={ROWS[0]} x1={stopAt(BIG, 1)} y1={ROWS[1]} />
    <Results rows={STACK_ROWS} title="run(...)" />
  </AbsoluteFill>
);

// 3. Record, then replay from the tape.
const TAPE_LINES: Lines = [[0, 'Record the answers. Replay them: no dice, no player.']];
const TAPE = f(11);
const RECORDED: Ring[] = [{label: 'record(tape)', glow: true}, {label: 'random_bot'}, {label: 'dice(3)'}, {label: 'silent'}, {label: 'quiet_stats'}];
const REPLAYED: Ring[] = [{label: 'replay(tape)', glow: true}, {label: 'silent'}, {label: 'quiet_stats'}];
const TAPE_ROWS: Row[] = [
  {at: 7.4, text: "first  -> ('win', 4)"},
  {at: 7.9, text: "again  -> ('win', 4)"},
  {at: 8.6, text: 'same: True', color: LOOK, big: true},
];
const ANSWERS = 32;

const Taping: React.FC = () => {
  const frame = useSceneFrame();
  // The tape fills while the first fight runs, and empties into replay during the second.
  const kept = Math.round(ANSWERS * ramp(frame, 1.0, 5.0));
  const used = Math.round(ANSWERS * ramp(frame, 6.0, 9.0));
  return (
    <AbsoluteFill>
      <Headlines lines={TAPE_LINES} />
      <Swap before={RECORDED} after={REPLAYED} swap={5.6} />
      <Trips trips={[['Roll', 1, 1, 1.0], ['Choose', 1, 2, 1.6], ['Roll', 1, 1, 2.4], ['Choose', 1, 2, 3.0]]} until={5.4} />
      <Results rows={TAPE_ROWS} title="the tape">
        <div style={{display: 'flex', flexWrap: 'wrap', width: 376, gap: 6, marginBottom: 14}}>
          {Array.from({length: ANSWERS}, (_, i) => (
            <div key={i} style={{width: 41, height: 22, borderRadius: 5, background: i < kept ? (i < used ? '#2a2e40' : TEST) : '#0b0d13', border: '1px solid #2a2e40'}} />
          ))}
        </div>
        <div style={{fontFamily: MONO, fontSize: 20, color: TEXT, marginBottom: 10}}>tape: {kept} answers</div>
      </Results>
    </AbsoluteFill>
  );
};

// 4. A Monte Carlo bot runs battle itself while the fight that asked waits as k.
const MC_LINES: Lines = [[0, 'A Monte Carlo bot runs the fight itself. The fight that asked waits as k.']];
const MC = f(11);
const MONTE: Ring[] = [{label: 'monte_carlo(3)', glow: true}, {label: 'dice(3)'}, {label: 'silent'}, {label: 'quiet_stats'}];
const TRIES = 3;
const MOVES = 4;

const Searching: React.FC = () => {
  const frame = useSceneFrame();
  const held = stopAt(BIG, 0);
  return (
    <AbsoluteFill>
      <Headlines lines={MC_LINES} />
      <Tower rings={MONTE} opacity={fadeAt(frame, 0.1)} />
      <Moving text="Choose" color={ACCENT} from={0.6} to={1.4} until={8.8} x0={BIG.cx + BIG.bw / 2 - 30} y0={ROWS[0]} x1={held} y1={ROWS[0]} />
      <Moving text="k" color={K} from={0.9} to={1.7} until={8.0} x0={BIG.cx + BIG.bw / 2 - 30} y0={ROWS[2]} x1={held} y1={ROWS[2]} />
      <Moving text="Resume(k, best)" color={TEST} from={8.0} to={9.0} until={10.0} x0={held} y0={ROWS[2]} x1={BIG.cx + 40} y1={BIG.cy} />
      <Results rows={[{at: 8.4, text: '144 playouts in one fight', color: LOOK}]} title="inside monte_carlo: battle(s), again">
        {Array.from({length: MOVES}, (_, move) => (
          <div key={move} style={{display: 'flex', gap: 10, marginBottom: 12, alignItems: 'center'}}>
            <div style={{width: 92, fontFamily: SANS, fontSize: 16, color: MUTED}}>{move < MOVES - 1 ? `move ${move + 1}` : 'end turn'}</div>
            {Array.from({length: TRIES}, (_, n) => {
              const from = 2.0 + (move * TRIES + n) * 0.45;
              return (
                <div
                  key={n}
                  style={{
                    width: 82, height: 46, boxSizing: 'border-box', borderRadius: 8, border: `2px solid ${frame >= f(from + 0.4) ? TEST : '#2a2e40'}`,
                    background: '#0b0d13', fontFamily: MONO, fontSize: 13, color: TEXT, display: 'flex', flexDirection: 'column', alignItems: 'center',
                    justifyContent: 'center', opacity: fadeAt(frame, from, 0.25),
                  }}
                >
                  <div>battle(s)</div>
                  <div style={{color: MUTED}}>dice({n})</div>
                </div>
              );
            })}
          </div>
        ))}
      </Results>
    </AbsoluteFill>
  );
};

// 5. A hundred fights at once, one task per seed.
const HUNDRED_LINES: Lines = [[0, 'A hundred fights at once, each with its own dice.'], [5.5, 'Random bot: 73 wins. Monte Carlo bot: all 100.']];
const HUNDRED = f(13);
// The random bot's outcome on seeds 0 to 99 (measure/stacks.py: "random bot wins by seed").
const RANDOM_WINS = 'WWWWW.WWW.WWWWWWW.WWW.WW.W.W.WWWWWWWW.WW.WWW.WW.WW.WW.WW..W.WWWWWW.WWWWWWWW.WWW.WW.W.WWW..W...W.WWWW';

const Grid: React.FC<{x: number; label: string; wins: string; done: number; title: string}> = ({x, label, wins, done, title}) => {
  const frame = useSceneFrame();
  const shown = ramp(frame, 0.6, 3.0) * 100;
  const settled = ramp(frame, done, done + 2.4) * 100;
  const count = [...wins].slice(0, Math.floor(settled)).filter((w) => w === 'W').length;
  return (
    <div style={{position: 'absolute', left: x, top: 120, width: 520}}>
      <div style={{fontFamily: MONO, fontSize: 21, fontWeight: 700, color: TEST}}>{label}</div>
      <div style={{display: 'flex', flexWrap: 'wrap', width: 400, gap: 6, marginTop: 14}}>
        {[...wins].map((w, i) => (
          <div
            key={i}
            style={{
              width: 34, height: 34, borderRadius: 6, boxSizing: 'border-box', opacity: i < shown ? 1 : 0,
              border: `2px solid ${i < settled ? (w === 'W' ? TEST : FAIL) : ACCENT}`, background: i < settled ? (w === 'W' ? `${TEST}55` : `${FAIL}33`) : '#0b0d13',
            }}
          />
        ))}
      </div>
      <div style={{fontFamily: MONO, fontSize: 30, fontWeight: 700, color: LOOK, marginTop: 16, opacity: fadeAt(frame, done)}}>
        {count} / 100 {title}
      </div>
    </div>
  );
};

const Hundred: React.FC = () => {
  const frame = useSceneFrame();
  return (
    <AbsoluteFill>
      <Headlines lines={HUNDRED_LINES} />
      <Grid x={150} label="win_rate(random_bot, 100)" wins={RANDOM_WINS} done={3.4} title="wins" />
      <Grid x={740} label="win_rate(monte_carlo(3), 100)" wins={'W'.repeat(100)} done={6.6} title="wins" />
      <div style={{position: 'absolute', left: 0, right: 0, top: 650, textAlign: 'center', fontFamily: SANS, fontSize: 20, color: MUTED, opacity: fadeAt(frame, 1.0)}}>
        one task per seed under <span style={{fontFamily: MONO, color: TEST}}>scheduled</span>, each with its own <span style={{fontFamily: MONO, color: TEST}}>dice(seed)</span>
      </div>
    </AbsoluteFill>
  );
};

// 6. Every stack, the same battle in the middle.
const OUTRO_LINES: Lines = [[0, 'One function, unchanged, in every stack.'], [4.2, 'Algebraic effects in Python, with doeff.']];
const OUTRO = f(10);
const SMALL = (cx: number): Geometry => ({cx, cy: 300, bw: 112, bh: 56, gap: 16, font: 10});
const STACKS: [string, Ring[]][] = [
  ['scripted', SCRIPTED],
  ['random bot', RANDOM.map(({label}) => ({label}))],
  ['replayed', REPLAYED.map(({label}) => ({label}))],
  ['Monte Carlo', MONTE.map(({label}) => ({label}))],
  ['production', [{label: 'human', color: PROD}, {label: 'dice(seed)'}, {label: 'printed', color: PROD}, {label: 'quiet_stats'}]],
];

const Outro: React.FC = () => {
  const frame = useSceneFrame();
  return (
    <AbsoluteFill>
      <Headlines lines={OUTRO_LINES} />
      {STACKS.map(([name, rings], i) => {
        const cx = 140 + i * 250;
        return (
          <React.Fragment key={name}>
            <Tower rings={rings} g={SMALL(cx)} opacity={fadeAt(frame, 0.3 + i * 0.35)} />
            <div style={{position: 'absolute', left: cx - 110, width: 220, top: 440, textAlign: 'center', fontFamily: SANS, fontSize: 18, color: MUTED, opacity: fadeAt(frame, 0.3 + i * 0.35)}}>
              {name}
            </div>
          </React.Fragment>
        );
      })}
      <div style={{position: 'absolute', left: 0, right: 0, top: 500, textAlign: 'center', opacity: fadeAt(frame, 4.4)}}>
        <div style={{fontFamily: SANS, fontSize: 64, fontWeight: 700, color: TEXT}}>doeff</div>
        <div style={{fontFamily: SANS, fontSize: 26, color: ACCENT, marginTop: 2}}>Algebraic effects for Python</div>
        <div style={{fontFamily: MONO, fontSize: 24, color: TEST, marginTop: 14}}>pip install doeff</div>
      </div>
    </AbsoluteFill>
  );
};

export const ARTICLE_OPENING = narrate('article-opening', DURATIONS, NARRATION, [
  {length: FN, starts: startsOf(FN_LINES), ids: ['o1', 'o2', 'o3', 'o4', 'o5', 'o6', 'o7', 'o8'], node: <Function />},
  {length: STACK, starts: startsOf(STACK_LINES), ids: ['o9', 'o10'], node: <Stacking />},
  {length: TAPE, starts: startsOf(TAPE_LINES), ids: ['o11'], node: <Taping />},
  {length: MC, starts: startsOf(MC_LINES), ids: ['o12'], node: <Searching />},
  {length: HUNDRED, starts: startsOf(HUNDRED_LINES), ids: ['o13'], node: <Hundred />},
  {length: OUTRO, starts: startsOf(OUTRO_LINES), ids: ['o14'], node: <Outro />},
]);
