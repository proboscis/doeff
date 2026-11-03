// The video on the first X post: the card battle of examples/effects_tour/spire.py, one program under five layers
// (a scripted player and a screen, a random bot, record and replay, a Monte Carlo bot, 100 fights in parallel).
// Every code line on screen is a line of spire.py (check_code.py checks it); every output line and number is a line of
// `python examples/effects_tour/spire.py`, saved in measure/spire_tour.log.
import React from 'react';
import {AbsoluteFill} from 'remotion';
import {fadeAt, mix, ramp} from '../../kit/motion';
import {narrate} from '../../kit/narrated';
import {CodeBlock, Headlines, Lines, startsOf} from '../../kit/parts';
import {ACCENT, f, LOOK, MONO, MUTED, PANEL, PROD, SANS, TEST, TEXT} from '../../kit/theme';
import {useSceneFrame} from '../../kit/warp';
import DURATIONS from './durations.json';

type Row = {at: number; text: string; color?: string; big?: boolean; bar?: number};

type StageProps = {
  lines: Lines;
  code: string[];
  bands: [number, number][];
  label: string;
  rows: Row[];
  size?: number;
};

const CODE_X = 90;
const CODE_Y = 100;
const CODE_W = 1100;
const LINE = 36;

// One scene: headlines on top, the code of the layer, and below it what that code makes.
const Stage: React.FC<StageProps> = ({lines, code, bands, label, rows, size = 20}) => {
  const frame = useSceneFrame();
  const band = bands.reduce<number | null>((found, [from, i]) => (frame >= f(from) ? i : found), null);
  const codeH = 24 + code.length * LINE;
  const panelY = CODE_Y + codeH + 34;
  return (
    <AbsoluteFill>
      <Headlines lines={lines} />
      <div style={{position: 'absolute', left: CODE_X, top: CODE_Y - 30, fontFamily: SANS, fontSize: 19, fontWeight: 700, color: ACCENT, opacity: fadeAt(frame, 0.1)}}>{label}</div>
      <CodeBlock lines={code} x={CODE_X} y={CODE_Y} w={CODE_W} size={size} line={LINE} band={band} opacity={fadeAt(frame, 0.1)} />
      <div
        style={{
          position: 'absolute', left: CODE_X, top: panelY, width: CODE_W, height: 700 - panelY - 20, boxSizing: 'border-box', padding: '12px 24px',
          background: PANEL, borderRadius: 14, border: `2px solid ${TEST}`, opacity: fadeAt(frame, 0.2),
        }}
      >
        {rows.filter((row) => frame >= f(row.at)).map((row) => (
          <div key={row.text} style={{opacity: fadeAt(frame, row.at), marginBottom: 6}}>
            <div style={{fontFamily: MONO, whiteSpace: 'pre', fontSize: row.big ? 30 : 18, lineHeight: row.big ? '44px' : '29px', fontWeight: row.big ? 700 : 400, color: row.color ?? TEXT}}>
              {row.text}
            </div>
            {row.bar !== undefined && (
              <div style={{height: 14, width: 900, background: '#0b0d13', borderRadius: 7}}>
                <div style={{height: 14, width: mix(0, 900 * row.bar, ramp(frame, row.at + 0.2, row.at + 1.4)), background: row.color ?? TEST, borderRadius: 7}} />
              </div>
            )}
          </div>
        ))}
      </div>
    </AbsoluteFill>
  );
};


// 1. The battle says what it needs.
const INTRO_LINES: Lines = [[0, 'This is doeff: algebraic effects for Python.'], [3.5, 'The battle says only what it needs.'], [8, 'Each yield hands an effect outward.']];
const INTRO_CODE = [
  '@do',
  'def battle(s: Fight = Fight()):',
  '        ...',
  '            yield Show("   enemy bellows")',
  '            pick = yield Choose(s, hand, energy)',
  '            yield Played(card, dmg)',
  '        intent = yield Roll(3)',
];
const INTRO_ROWS: Row[] = [
  {at: 4.4, text: 'Show     the screen', color: TEST},
  {at: 5.6, text: 'Choose   the player\'s move', color: TEST},
  {at: 6.8, text: 'Played   a statistic', color: TEST},
  {at: 7.4, text: 'Roll     a random number', color: TEST},
  {at: 11, text: 'who rolls, who plays, who looks: not battle\'s business', color: LOOK},
];
const INTRO = f(16);

// 2. Layer one: a scripted player and a screen.
const SCRIPT_LINES: Lines = [[0, 'Layer one: a scripted player, seeded dice, a screen.'], [3.5, 'Run it, and the fight prints.'], [6, 'A win on turn six.']];
const SCRIPT_CODE = [
  '    moves = [0, 0, 0, None] * 20',
  '    print("  ->", run(quiet_stats(printed(dice(7)(scripted(moves)(battle()))))))',
];
const SCRIPT_ROWS: Row[] = [
  {at: 3.6, text: 'T1 you 40 (0 block) | enemy 44 | 3 energy | Defend Strike Defend Defend Strike'},
  {at: 3.9, text: 'T1 you 40 (5 block) | enemy 44 | 2 energy | Strike Defend Defend Strike'},
  {at: 4.2, text: 'T1 you 40 (5 block) | enemy 38 | 1 energy | Defend Defend Strike'},
  {at: 4.5, text: '   enemy hits for 11, you take 1'},
  {at: 4.8, text: 'T2 you 39 (0 block) | enemy 38 | 3 energy | Bash Strike Strike Strike Defend'},
  {at: 5.1, text: '   ...'},
  {at: 6.2, text: "-> ('win', 6)", color: LOOK, big: true},
];
const SCRIPT = f(14);

// 3. Layers two and three: a random bot, record and replay.
const TAPE_LINES: Lines = [[0, 'Layer two: a random bot.'], [3.5, 'Layer three: record.'], [8, 'Replay: no dice, no player.']];
const TAPE_CODE = [
  '    print("  ->", run(quiet_stats(silent(dice(3)(random_bot(battle()))))))',
  '    first = run(quiet_stats(silent(dice(3)(random_bot(record(tape)(battle()))))))',
  '    again = run(quiet_stats(silent(replay(tape)(battle()))))',
];
const TAPE_ROWS: Row[] = [
  {at: 1.0, text: "  -> ('win', 4)", color: TEST, big: true},
  {at: 5.0, text: "  -> ('win', 4) ('win', 4) same: True tape: 32 answers", color: LOOK, big: true},
];
const TAPE = f(16);

// 4. Layer four: a Monte Carlo bot, whose handler runs battle itself.
const MC_LINES: Lines = [[0, 'Layer four: a Monte Carlo bot.'], [3.5, 'Its handler runs battle itself.'], [8, 'A program is a value.']];
const MC_CODE = [
  '    def choose(effect: Choose, k):',
  '        ...',
  '            for n in range(playouts):',
  '        ...',
  '                result, _ = run(quiet_stats(silent(dice(n)(random_bot(battle(s))))))',
  '                wins += result == "win"',
  '    print("  ->", run(quiet_stats(silent(dice(3)(monte_carlo(PLAYOUTS)(battle()))))))',
];
const MC_ROWS: Row[] = [{at: 9.5, text: "  -> ('win', 4)", color: LOOK, big: true}];
const MC = f(16);

// 5. Layer five: 100 fights in parallel.
const PAR_LINES: Lines = [[0, 'Layer five: 100 fights in parallel.'], [4, 'The random bot wins 73 of 100.'], [7.5, 'The Monte Carlo bot wins all 100.']];
const PAR_CODE = [
  '    for seed in range(fights):',
  '        tasks.append((yield Spawn(silent(dice(seed)(player(battle()))))))',
  '    results = yield Gather(*tasks)',
  '    return sum(r == "win" for r, _ in results) / fights',
];
const PAR_ROWS: Row[] = [
  {at: 4.2, text: '  -> random bot wins 0.73', color: PROD, bar: 0.73},
  {at: 7.7, text: '  -> monte carlo wins 1.0', color: TEST, bar: 1.0},
];
const PAR = f(16);

const OUTRO_LINES: Lines = [[0, 'doeff: algebraic effects for Python. pip install doeff']];
const OUTRO = f(14);

const Outro: React.FC = () => {
  const frame = useSceneFrame();
  return (
    <AbsoluteFill>
      <Headlines lines={OUTRO_LINES} />
      <div style={{position: 'absolute', left: 0, right: 0, top: 210, textAlign: 'center', opacity: fadeAt(frame, 0.2)}}>
        <div style={{fontFamily: SANS, fontSize: 96, fontWeight: 700, color: TEXT}}>doeff</div>
        <div style={{fontFamily: SANS, fontSize: 34, color: ACCENT, marginTop: 6}}>Algebraic effects for Python</div>
        <div style={{fontFamily: MONO, fontSize: 30, color: TEST, marginTop: 40}}>pip install doeff</div>
      </div>
    </AbsoluteFill>
  );
};

export const X_FIRST_POST = narrate('x-first-post', DURATIONS, [
  {length: INTRO, starts: startsOf(INTRO_LINES), ids: ['i1', 'i2', 'i3'], node: <Stage lines={INTRO_LINES} code={INTRO_CODE} bands={[[4.4, 3], [5.6, 4], [6.8, 5], [7.4, 6]]} label="the program: battle" rows={INTRO_ROWS} />},
  {length: SCRIPT, starts: startsOf(SCRIPT_LINES), ids: ['p1', 'p2', 'p3'], node: <Stage lines={SCRIPT_LINES} code={SCRIPT_CODE} bands={[[0.5, 1]]} label="layer 1: a scripted player and a screen" rows={SCRIPT_ROWS} />},
  {length: TAPE, starts: startsOf(TAPE_LINES), ids: ['r1', 'r2', 'r3'], node: <Stage lines={TAPE_LINES} code={TAPE_CODE} bands={[[0.5, 0], [3.7, 1], [8.2, 2]]} label="layers 2 and 3: a random bot, record and replay" rows={TAPE_ROWS} />},
  {length: MC, starts: startsOf(MC_LINES), ids: ['m1', 'm2', 'm3'], node: <Stage lines={MC_LINES} code={MC_CODE} bands={[[0.5, 6], [3.7, 4], [8.2, 6]]} label="layer 4: a Monte Carlo bot" rows={MC_ROWS} size={19} />},
  {length: PAR, starts: startsOf(PAR_LINES), ids: ['w1', 'w2', 'w3'], node: <Stage lines={PAR_LINES} code={PAR_CODE} bands={[[0.5, 1], [4, 3], [7.5, 3]]} label="layer 5: 100 fights, one task per seed" rows={PAR_ROWS} />},
  {length: OUTRO, starts: [0], ids: ['o1'], node: <Outro />},
]);
