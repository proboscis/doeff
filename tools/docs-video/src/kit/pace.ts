// The pace of a scene whose motions come one at a time, so the eye can follow each one while its line is heard. A motion
// starts PAUSE s after the one before it has stopped; a scene's next line starts PAUSE s after both the line before it
// and its motions have stopped, and a scene holds still for STILL s (its last 0.4 s fade out) after its last line and
// its last motion. A scene with more motions than its line has time for goes on after the line.
import {LEAD, Said} from './narrated';

export const PAUSE = 0.9;
export const STILL = 1.6;

// A motion: the second it starts and the second it has stopped, in the scene's design time.
export type Step = {from: number; to: number};

// Motions end to end from a time: [name, seconds it lasts, the pause before it when it is not PAUSE].
export const chain = <K extends string>(start: number, motions: [K, number, number?][]): Record<K, Step> => {
  let at = start;
  const steps = {} as Record<K, Step>;
  motions.forEach(([name, seconds, pause = PAUSE], i) => {
    const from = i === 0 ? at : at + pause;
    steps[name] = {from, to: from + seconds};
    at = from + seconds;
  });
  return steps;
};

// The same without names: [seconds it lasts, the pause before it when it is not PAUSE].
export const sequence = (start: number, motions: [number, number?][]): Step[] => {
  let at = start;
  return motions.map(([seconds, pause = PAUSE], i) => {
    const from = i === 0 ? at : at + pause;
    at = from + seconds;
    return {from, to: at};
  });
};

// A headline is a motion too: it fades in over LINE_FADE s (Headlines in parts.tsx).
export const LINE_FADE = 0.3;

// The second the last of some motions has stopped.
export const lastOf = (steps: Step[] | Record<string, Step>) => Math.max(...Object.values(steps).map(({to}) => to));

// When a line has been heard, read from a headline at this time: heardBy(DURATIONS)(headline, id).
export const heardBy = (said: Said) => (headline: number, id: string) => headline + LEAD + said[id];

// The headline after a line and its motions: PAUSE s after both have stopped.
export const nextLine = (heard: number, steps: Record<string, Step>) => Math.max(heard, lastOf(steps)) + PAUSE;

// The length of a scene whose last line is heard by `heard`: STILL s after that line and its motions.
export const sceneEnd = (heard: number, steps: Record<string, Step>) => Math.max(heard, lastOf(steps)) + STILL;
