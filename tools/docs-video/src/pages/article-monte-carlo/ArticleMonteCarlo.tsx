// The video at the head of Layer 4 of the article "Algebraic effects for Python" (docs/articles/algebraic-effects-in-python.md):
// the Monte Carlo bot of examples/effects_tour/spire.py answering one Choose. The fight that asked stays paused as k
// on the left while the handler, on the right, plays each candidate move and starts a new battle from there three
// times, each on a stack of its own; k is not touched by those runs. Then it counts the wins and resumes k, once (a k
// in doeff resumes only one time). The Choose shown is the 2nd one of
// the fight on dice(3). Every code line on screen is a line of spire.py (check_code.py checks it); every state, win
// count and total is a line of measure/monte.py's output.
import React from 'react';
import {AbsoluteFill} from 'remotion';
import {fadeAt, mix, ramp} from '../../kit/motion';
import {narrate} from '../../kit/narrated';
import {CodeBlock, Headlines, Lines, lineMid, Pill, startsOf} from '../../kit/parts';
import {ACCENT, f, LOOK, MONO, MUTED, PANEL, SANS, TEST, TEXT} from '../../kit/theme';
import {useSceneFrame} from '../../kit/warp';
import {Geometry, K, Moving, Ring, Tower} from '../article-opening/ArticleOpening';
import DURATIONS from './durations.json';
import NARRATION from './narration.json';

// The 2nd Choose of run(quiet_stats(silent(dice(3)(monte_carlo(3)(battle()))))) (measure/monte.py: "Choose 2").
const HAND: [string, string][] = [['Bash', 'cost 2 · 8'], ['Strike', 'cost 1 · 6'], ['Strike', 'cost 1 · 6'], ['Defend', 'cost 1 · 5 block']];
// Each candidate move, the enemy's hp after hit(s, card), and its wins of 3 playouts ("enemy hp after the move", "wins of 3").
const MOVES: [string, number, number][] = [['Bash', 30, 3], ['Strike', 32, 3], ['Strike', 32, 3], ['Defend', 38, 3], ['end turn', 38, 3]];
const PLAYOUTS = 3;

// ---- The paused fight, on the left.
const LEFT = {x: 30, y: 104, w: 470, h: 580};
const FIGHT_CODE = [
  'while energy > 0 and hand:',
  '    pick = yield Choose(s, hand, energy)',
  '    ...',
  '    s, dmg = hit(s, card)',
  '    yield Played(card, dmg)',
];
const CODE = {x: LEFT.x + 20, y: LEFT.y + 250, w: LEFT.w - 40, line: 30};
const CHOOSE_AT = {x: CODE.x + 330, y: lineMid(CODE.y, CODE.line, 1)};
const K_TOP = CODE.y + 12 + 2 * CODE.line;

// What the fight looks like at a design time: k parked or back, and whether Bash has been played.
type FightState = {kBand: number; band: number; played: boolean; stackGlow: boolean; still: number};

const Fight: React.FC<FightState> = ({played}) => {
  return (
    <div
      style={{
        position: 'absolute', left: LEFT.x, top: LEFT.y, width: LEFT.w, height: LEFT.h, boxSizing: 'border-box', borderRadius: 16,
        border: `2px solid ${ACCENT}`, background: PANEL,
      }}
    >
      <div style={{position: 'absolute', left: 20, top: 14, fontFamily: SANS, fontSize: 18, fontWeight: 700, color: ACCENT}}>the fight that asked: battle()</div>
      <div style={{position: 'absolute', left: 20, top: 50, fontFamily: MONO, fontSize: 21, color: TEXT, whiteSpace: 'pre'}}>
        {'T1   you 40   enemy '}
        <span style={{color: played ? LOOK : TEXT}}>{played ? '30' : '38'}</span>
      </div>
      <div style={{position: 'absolute', left: 20, top: 82, fontFamily: MONO, fontSize: 21, color: TEXT}}>
        energy <span style={{color: played ? LOOK : TEXT}}>{played ? '0' : '2'}</span>
      </div>
      <div style={{position: 'absolute', left: 20, top: 124, display: 'flex', gap: 10}}>
        {HAND.map(([card, what], i) => (
          <div
            key={i}
            style={{
              width: 98, height: 90, boxSizing: 'border-box', borderRadius: 10, border: `2px solid ${played && i === 0 ? LOOK : '#2a2e40'}`,
              background: '#0b0d13', padding: '10px 8px', opacity: played && i === 0 ? 0.9 : 1,
              transform: played && i === 0 ? 'translateY(-10px)' : 'none', boxShadow: played && i === 0 ? `0 0 18px ${LOOK}88` : 'none',
            }}
          >
            <div style={{fontFamily: MONO, fontSize: 18, fontWeight: 700, color: TEXT}}>{card}</div>
            <div style={{fontFamily: SANS, fontSize: 13, color: MUTED, marginTop: 8}}>{what}</div>
          </div>
        ))}
      </div>
      <div style={{position: 'absolute', left: 20, top: 222, fontFamily: SANS, fontSize: 16, color: MUTED}}>spire.py: battle</div>
    </div>
  );
};

const FightCode: React.FC<FightState> = ({kBand, band, stackGlow, still}) => {
  return (
    <>
      <CodeBlock lines={FIGHT_CODE} x={CODE.x} y={CODE.y} w={CODE.w} size={16} line={CODE.line} band={band} />
      {/* k: everything battle has left to do after the Choose. */}
      <div style={{position: 'absolute', left: CODE.x + 4, top: K_TOP, width: CODE.w - 8, height: 3 * CODE.line, borderRadius: 8, background: K, opacity: 0.16 * kBand}} />
      <div style={{position: 'absolute', left: CODE.x, top: CODE.y + 190, width: CODE.w, fontFamily: SANS, fontSize: 17, color: MUTED, lineHeight: '26px'}}>
        its stack: quiet_stats · silent · <span style={{fontFamily: MONO, color: stackGlow ? LOOK : TEST, fontWeight: stackGlow ? 700 : 400}}>dice(3)</span> · monte_carlo(3)
      </div>
      <div style={{position: 'absolute', left: CODE.x, top: CODE.y + 250, width: CODE.w, fontFamily: SANS, fontSize: 18, color: stackGlow ? LOOK : K, opacity: still}}>
        {stackGlow ? 'its dice(3) is not used by the runs' : 'paused at the yield: it has not moved'}
      </div>
    </>
  );
};

// ---- The handler, on the right.
const RIGHT = {x: 530, y: 104, w: 720, h: 580};
const CHOOSE_PARK = {x: RIGHT.x + 500, y: RIGHT.y + 30};
const K_PARK = {x: RIGHT.x + 660, y: RIGHT.y + 30};
const RUN_CODE = ['result, _ = run(quiet_stats(silent(dice(n)(random_bot(battle(s))))))'];

const Handler: React.FC<{children?: React.ReactNode; parked?: boolean; code?: number}> = ({children, parked = true, code = 1}) => (
  <>
    <div
      style={{
        position: 'absolute', left: RIGHT.x, top: RIGHT.y, width: RIGHT.w, height: RIGHT.h, boxSizing: 'border-box', borderRadius: 16,
        border: `3px solid ${TEST}`, background: PANEL,
      }}
    >
      <div style={{position: 'absolute', left: 20, top: 14, fontFamily: MONO, fontSize: 20, fontWeight: 700, color: TEST}}>monte_carlo(3): choose(effect, k)</div>
    </div>
    {children}
    <CodeBlock lines={RUN_CODE} x={RIGHT.x + 18} y={RIGHT.y + RIGHT.h - 66} w={RIGHT.w - 36} size={15} line={26} opacity={code} />
    {parked && <Parked />}
  </>
);

// The Choose and k as the handler holds them.
const Parked: React.FC = () => (
  <>
    <Pill x={CHOOSE_PARK.x} y={CHOOSE_PARK.y} text="Choose" color={ACCENT} />
    <Pill x={K_PARK.x} y={K_PARK.y} text="k" color={K} />
  </>
);

// 1. The fight pauses at a Choose; the handler holds k and runs battle itself.
const PAUSE_LINES: Lines = [[0, 'The fight pauses at a Choose. The handler holds the rest as k.'], [5.6, 'To answer, it runs battle itself: the same function a person plays.']];
const PAUSE = f(12);

const Pause: React.FC = () => {
  const frame = useSceneFrame();
  const at = (seconds: number) => frame >= f(seconds);
  return (
    <AbsoluteFill style={{opacity: fadeAt(frame, 0.05)}}>
      <Headlines lines={PAUSE_LINES} />
      <Fight kBand={0} band={1} played={false} stackGlow={false} still={0} />
      <FightCode kBand={fadeAt(frame, 1.6, 0.4)} band={1} played={false} stackGlow={false} still={fadeAt(frame, 3.4)} />
      <Handler parked={at(3.0)} code={fadeAt(frame, 8.4)}>
        {/* The same battle, inside the handler. */}
        <div
          style={{
            position: 'absolute', left: RIGHT.x + 230, top: RIGHT.y + 190, width: 260, height: 120, boxSizing: 'border-box', borderRadius: 12,
            border: `3px solid ${ACCENT}`, background: '#0b0d13', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
            opacity: fadeAt(frame, 6.0, 0.4), transform: `scale(${mix(0.6, 1, ramp(frame, 6.0, 6.8))})`,
          }}
        >
          <div style={{fontFamily: MONO, fontSize: 28, fontWeight: 700, color: TEXT}}>battle(s)</div>
          <div style={{fontFamily: SANS, fontSize: 16, color: MUTED, marginTop: 6}}>a new run, from the state s</div>
        </div>
        <div style={{position: 'absolute', left: RIGHT.x, width: RIGHT.w, top: RIGHT.y + 340, textAlign: 'center', fontFamily: SANS, fontSize: 21, color: LOOK, opacity: fadeAt(frame, 7.2)}}>
          the real battle, not a model of the game
        </div>
      </Handler>
      <Moving text="Choose" color={ACCENT} from={1.0} to={2.4} until={3.0} x0={CHOOSE_AT.x} y0={CHOOSE_AT.y} x1={CHOOSE_PARK.x} y1={CHOOSE_PARK.y} />
      <Moving text="k" color={K} from={1.8} to={3.0} until={3.0} x0={CODE.x + CODE.w - 40} y0={K_TOP + 1.5 * CODE.line} x1={K_PARK.x} y1={K_PARK.y} />
    </AbsoluteFill>
  );
};

// The rows of candidate moves: a move, its playouts, and (from `wins`) the count of wins.
const ROW_Y = RIGHT.y + 86;
const ROW_H = 74;
const BOX_X = RIGHT.x + 200;
const BOX_W = 104;
const runAt = (move: number, n: number) => 2.8 + (move * PLAYOUTS + n) * 0.3;

const Rows: React.FC<{shown: number; ran: number; wins: number; pick: number; dim: number}> = ({shown, ran, wins, pick, dim}) => {
  const frame = useSceneFrame();
  return (
    <div style={{position: 'absolute', left: 0, top: 0, opacity: dim}}>
      <div style={{position: 'absolute', left: RIGHT.x + 20, top: ROW_Y - 30, fontFamily: SANS, fontSize: 15, color: MUTED, whiteSpace: 'nowrap'}}>move: hit(s, card)</div>
      <div style={{position: 'absolute', left: BOX_X, top: ROW_Y - 30, fontFamily: SANS, fontSize: 15, color: MUTED, whiteSpace: 'nowrap'}}>3 playouts: a new battle(s) each</div>
      <div style={{position: 'absolute', left: RIGHT.x + 560, top: ROW_Y - 30, fontFamily: SANS, fontSize: 15, color: MUTED, whiteSpace: 'nowrap', opacity: wins}}>wins</div>
      {MOVES.map(([move, enemy, won], i) => {
        const y = ROW_Y + i * ROW_H;
        const picked = pick > 0 && i === 0;
        return (
          <React.Fragment key={i}>
            <div
              style={{
                position: 'absolute', left: RIGHT.x + 20, top: y, width: 160, height: 58, boxSizing: 'border-box', borderRadius: 10, padding: '6px 12px',
                border: `2px solid ${picked ? LOOK : '#2a2e40'}`, background: '#0b0d13', opacity: fadeAt(frame, shown + i * 0.4),
                boxShadow: picked ? `0 0 20px ${LOOK}${Math.round(pick * 170).toString(16).padStart(2, '0')}` : 'none',
              }}
            >
              <div style={{fontFamily: MONO, fontSize: 18, fontWeight: 700, color: picked ? LOOK : TEXT}}>{move}</div>
              <div style={{fontFamily: SANS, fontSize: 14, color: MUTED}}>enemy {enemy}</div>
            </div>
            {Array.from({length: PLAYOUTS}, (_, n) => {
              const from = ran < 0 ? -1 : runAt(i, n);
              const done = ran < 0 || frame >= f(from + 0.35);
              return (
                <div
                  key={n}
                  style={{
                    position: 'absolute', left: BOX_X + n * (BOX_W + 10), top: y, width: BOX_W, height: 58, boxSizing: 'border-box', borderRadius: 8,
                    border: `2px solid ${done ? TEST : LOOK}`, background: '#0b0d13', display: 'flex', flexDirection: 'column', alignItems: 'center',
                    justifyContent: 'center', fontFamily: MONO, fontSize: 13, color: TEXT, opacity: ran < 0 ? 1 : fadeAt(frame, from, 0.2),
                  }}
                >
                  <div>battle(s)</div>
                  <div style={{color: done ? TEST : MUTED}}>{done ? `dice(${n}): win` : `dice(${n})`}</div>
                </div>
              );
            })}
            {/* The count of wins: a bar of 3 steps. */}
            <div style={{position: 'absolute', left: RIGHT.x + 560, top: y + 14, width: 90, height: 30, borderRadius: 6, background: '#0b0d13', border: '1px solid #2a2e40', opacity: wins}} />
            <div
              style={{
                position: 'absolute', left: RIGHT.x + 560, top: y + 14, height: 30, borderRadius: 6, background: picked ? LOOK : TEST, opacity: wins,
                width: 90 * (won / PLAYOUTS) * ramp(frame, 0.8 + i * 0.5, 1.6 + i * 0.5),
              }}
            />
            <div style={{position: 'absolute', left: RIGHT.x + 660, top: y + 14, fontFamily: MONO, fontSize: 20, fontWeight: 700, color: TEXT, opacity: wins * fadeAt(frame, 1.6 + i * 0.5)}}>
              {won}/{PLAYOUTS}
            </div>
          </React.Fragment>
        );
      })}
    </div>
  );
};

// 2. For each move, the playouts: each a new battle(s) on a stack of its own, while k stays parked.
const RUN_LINES: Lines = [[0, 'For each move: play it, then start a new battle from there, 3 times.'], [8.6, 'Each new battle has its own stack. k is not touched.']];
const RUNS = f(16);
const OWN: Ring[] = [{label: 'random_bot'}, {label: 'dice(n)', glow: true}, {label: 'silent'}, {label: 'quiet_stats'}];
const SMALL: Geometry = {cx: RIGHT.x + RIGHT.w / 2, cy: RIGHT.y + 270, bw: 190, bh: 74, gap: 30, font: 15};

const Runs: React.FC = () => {
  const frame = useSceneFrame();
  const stack = fadeAt(frame, 9.0, 0.5);
  return (
    <AbsoluteFill>
      <Headlines lines={RUN_LINES} />
      <Fight kBand={1} band={1} played={false} stackGlow={frame >= f(10.6)} still={1} />
      <FightCode kBand={1} band={1} played={false} stackGlow={frame >= f(10.6)} still={fadeAt(frame, 10.6)} />
      <Handler>
        <Rows shown={0.4} ran={0} wins={0} pick={0} dim={1 - 0.85 * stack} />
        <Tower rings={OWN} g={SMALL} opacity={stack} title="battle(s)" />
        <div style={{position: 'absolute', left: RIGHT.x, width: RIGHT.w, top: SMALL.cy + 175, textAlign: 'center', fontFamily: SANS, fontSize: 19, color: LOOK, opacity: fadeAt(frame, 9.8)}}>
          each playout: a new battle(s) on a new stack, not k
        </div>
      </Handler>
    </AbsoluteFill>
  );
};

// 3. The wins, then Resume(k, 0), the one time k is resumed: the fight goes on with Bash.
const WIN_LINES: Lines = [[0, 'It counts the wins: every move wins 3 of 3. The paused fight has not moved.'], [7.2, 'A tie keeps the first move, Bash. It resumes k once: k is single-shot.']];
const WINS = f(14);

const Wins: React.FC = () => {
  const frame = useSceneFrame();
  const back = frame >= f(9.8);
  const fight: FightState = {kBand: back ? 0 : 1, band: back ? 3 : 1, played: frame >= f(10.2), stackGlow: false, still: back ? 0 : fadeAt(frame, 4.0)};
  return (
    <AbsoluteFill>
      <Headlines lines={WIN_LINES} />
      <Fight {...fight} />
      <FightCode {...fight} />
      <Handler parked={frame < f(8.6)}>
        <Rows shown={-5} ran={-1} wins={fadeAt(frame, 0.4)} pick={ramp(frame, 7.6, 8.2)} dim={1} />
        <div style={{position: 'absolute', left: RIGHT.x + 20, top: ROW_Y + 5 * ROW_H - 4, fontFamily: SANS, fontSize: 18, color: LOOK, opacity: fadeAt(frame, 7.8)}}>
          a tie: best stays at the first move with the most wins, 0 (Bash)
        </div>
      </Handler>
      <Moving text="Resume(k, 0)" color={TEST} from={8.6} to={9.8} until={10.4} x0={K_PARK.x - 60} y0={K_PARK.y} x1={CHOOSE_AT.x - 40} y1={CHOOSE_AT.y} />
      <div style={{position: 'absolute', left: CODE.x, top: CODE.y + 250, width: CODE.w, fontFamily: SANS, fontSize: 20, fontWeight: 700, color: LOOK, opacity: fadeAt(frame, 10.6)}}>
        k resumed once: Bash is played
      </div>
    </AbsoluteFill>
  );
};

// 4. What the bot costs on seed 3: 11 Choose effects, 165 playouts, and a fight's time against the random bot's.
const COST_LINES: Lines = [[0, 'On seed 3: 11 Chooses, 165 playouts, about 0.2 s for the fight.']];
const COST = f(9.5);
// The candidate moves of each of the 11 Choose effects (measure/monte.py: "wins of 3" per Choose); 3 playouts each.
const CANDIDATES = [6, 5, 6, 5, 4, 6, 5, 4, 6, 5, 3];
const ALL = CANDIDATES.reduce((sum, n) => sum + n, 0) * PLAYOUTS;

const Cost: React.FC = () => {
  const frame = useSceneFrame();
  const filled = Math.floor(ALL * ramp(frame, 0.8, 5.0));
  const firsts = CANDIDATES.map((_, i) => CANDIDATES.slice(0, i).reduce((sum, n) => sum + n * PLAYOUTS, 0));
  return (
    <AbsoluteFill>
      <Headlines lines={COST_LINES} />
      <div style={{position: 'absolute', left: 90, top: 110, fontFamily: SANS, fontSize: 18, color: MUTED, opacity: fadeAt(frame, 0.3)}}>
        playouts of each Choose in the fight on dice(3)
      </div>
      <div style={{position: 'absolute', left: 90, top: 146, display: 'flex', gap: 18, alignItems: 'flex-end'}}>
        {CANDIDATES.map((n, c) => (
          <div key={c} style={{display: 'flex', flexDirection: 'column', alignItems: 'center', opacity: fadeAt(frame, 0.3)}}>
            <div style={{display: 'grid', gridTemplateColumns: `repeat(${PLAYOUTS}, 22px)`, gap: 4}}>
              {Array.from({length: n * PLAYOUTS}, (_, i) => (
                <div key={i} style={{width: 22, height: 22, borderRadius: 4, border: '1px solid #2a2e40', background: firsts[c] + i < filled ? TEST : '#0b0d13'}} />
              ))}
            </div>
            <div style={{fontFamily: MONO, fontSize: 15, color: MUTED, marginTop: 8}}>{c + 1}</div>
          </div>
        ))}
      </div>
      <div style={{position: 'absolute', left: 90, top: 340, fontFamily: MONO, fontSize: 34, fontWeight: 700, color: LOOK, opacity: fadeAt(frame, 0.8)}}>
        {filled} playouts
      </div>
      {/* One fight's time (median on one machine): the Monte Carlo bot against the random bot. */}
      {[
        ['monte_carlo(3)', 'about 0.2 s', 1, 5.4],
        ['random_bot', 'about 1 ms', 1 / 180, 6.0],
      ].map(([name, took, share, from], i) => (
        <React.Fragment key={String(name)}>
          <div style={{position: 'absolute', left: 90, top: 440 + i * 66, width: 220, fontFamily: MONO, fontSize: 20, fontWeight: 700, color: TEST, opacity: fadeAt(frame, Number(from))}}>{name}</div>
          <div
            style={{
              position: 'absolute', left: 320, top: 440 + i * 66, height: 30, borderRadius: 6, background: i === 0 ? LOOK : TEST, opacity: fadeAt(frame, Number(from)),
              width: Math.max(4, 680 * Number(share) * ramp(frame, Number(from), Number(from) + 1.0)),
            }}
          />
          <div style={{position: 'absolute', left: i === 0 ? 1016 : 340, top: 440 + i * 66, fontFamily: MONO, fontSize: 20, color: TEXT, opacity: fadeAt(frame, Number(from) + 0.8)}}>{took}</div>
        </React.Fragment>
      ))}
      <div style={{position: 'absolute', left: 90, top: 580, fontFamily: SANS, fontSize: 17, color: MUTED, opacity: fadeAt(frame, 6.6)}}>
        one fight on seed 3, the median of many runs on one machine
      </div>
    </AbsoluteFill>
  );
};

export const ARTICLE_MONTE_CARLO = narrate('article-monte-carlo', DURATIONS, NARRATION, [
  {length: PAUSE, starts: startsOf(PAUSE_LINES), ids: ['m1', 'm2'], node: <Pause />},
  {length: RUNS, starts: startsOf(RUN_LINES), ids: ['m3', 'm4'], node: <Runs />},
  {length: WINS, starts: startsOf(WIN_LINES), ids: ['m5', 'm6'], node: <Wins />},
  {length: COST, starts: startsOf(COST_LINES), ids: ['m7'], node: <Cost />},
]);
