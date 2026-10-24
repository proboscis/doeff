// A docs page's video as narrated scenes. Each scene is designed in its own frames, with headlines at the top; a
// synthetic voice reads one line per headline (tts.py writes public/<page>/<id>.wav and the page's
// durations.json). A headline's slot is stretched to fit its line instead of the voice running into the next
// headline (warp.ts), the line starts LEAD s after its headline appears, and each scene fades out over its last
// 0.4 s so the cut to the next one is soft.
import React from 'react';
import {AbsoluteFill, Audio, interpolate, Sequence, staticFile, useCurrentFrame} from 'remotion';
import {clamp} from './motion';
import {BG, f} from './theme';
import {designOf, realOf, Slot, WarpContext} from './warp';

// The seconds between a headline appearing and its line starting, and after the line before the next headline.
export const LEAD = 0.25;
export const TAIL = 0.3;

// A scene: its design length in frames, the design frames where its headlines start, the narration line id of
// each headline, and what it draws. wrap puts the drawing inside something that depends on the stretched slots,
// such as a context giving the fast-forward a stretched wait really shows.
export type Scene = {
  length: number;
  starts: number[];
  ids: string[];
  node: React.ReactNode;
  wrap?: (slots: Slot[], node: React.ReactNode) => React.ReactNode;
};

// Seconds each recorded line lasts, by line id. A line without a recording leaves its slot as designed.
export type Said = Record<string, number>;

export const slotsOf = (said: Said, {length, starts, ids}: Scene): Slot[] =>
  starts.map((from, i) => {
    const to = i + 1 < starts.length ? starts[i + 1] : length;
    const seconds = said[ids[i]];
    return {from, to, length: Math.max(to - from, seconds ? f(LEAD + seconds + TAIL) : 0)};
  });

const Faded: React.FC<{length: number; children: React.ReactNode}> = ({length, children}) => {
  const frame = useCurrentFrame();
  return <AbsoluteFill style={{opacity: interpolate(frame, [length - f(0.4), length], [1, 0], clamp)}}>{children}</AbsoluteFill>;
};

// The video of a page: its length in frames (for the Composition in Root.tsx) and the component that plays the
// scenes in order with their narration from public/<page>/.
export const narrate = (page: string, said: Said, scenes: Scene[]) => {
  const laid = scenes.map((scene) => {
    const slots = slotsOf(said, scene);
    return {scene, slots, length: Math.round(slots.reduce((sum, slot) => sum + slot.length, 0))};
  });
  const length = laid.reduce((sum, scene) => sum + scene.length, 0);
  const Video: React.FC = () => (
    <AbsoluteFill style={{backgroundColor: BG}}>
      {laid.map(({scene, slots, length: sceneLength}, i) => {
        const from = laid.slice(0, i).reduce((sum, earlier) => sum + earlier.length, 0);
        const real = realOf(slots);
        return (
          <Sequence key={i} from={from} durationInFrames={sceneLength}>
            <Faded length={sceneLength}>
              <WarpContext.Provider value={designOf(slots)}>{scene.wrap ? scene.wrap(slots, scene.node) : scene.node}</WarpContext.Provider>
            </Faded>
            {scene.ids.map((id, k) =>
              said[id] ? (
                <Sequence key={id} from={Math.round(real(scene.starts[k])) + f(LEAD)}>
                  <Audio src={staticFile(`${page}/${id}.wav`)} />
                </Sequence>
              ) : null,
            )}
          </Sequence>
        );
      })}
    </AbsoluteFill>
  );
  return {length, Video};
};
