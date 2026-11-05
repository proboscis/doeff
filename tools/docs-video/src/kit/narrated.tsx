// A docs page's video as narrated scenes. Each scene is designed in its own frames, with headlines at the top; a
// synthetic voice reads one line per headline (tts.py writes public/<page>/<id>.wav and the page's
// durations.json). A headline's slot is stretched to fit its line instead of the voice running into the next
// headline (warp.ts), the line starts LEAD s after its headline appears, and each scene fades out over its last
// 0.4 s so the cut to the next one is soft. While a line is heard its text is shown at the bottom (subtitles.tsx); the
// scenes are drawn a little smaller, against the top, so the subtitle band covers none of them.
import React from 'react';
import {AbsoluteFill, Audio, interpolate, Sequence, staticFile, useCurrentFrame} from 'remotion';
import {clamp} from './motion';
import {Cue, cuesOf, BAND_TOP, Subtitles} from './subtitles';
import {BG, f, HEIGHT} from './theme';
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

// The narration of a page as narration.json writes it: the subtitles show these words.
export type Script = {id: string; text: string}[];

// How much smaller the scenes are drawn, so that they end 8 px above the subtitle band.
export const STAGE = (BAND_TOP - 8) / HEIGHT;

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

// The video of a page: its id (the page, the id of its composition in Root.tsx), its length in frames, its subtitles,
// and the component that plays the scenes in order with their narration from public/<page>/. Root.tsx hands the cues
// to the component as its props, and render.mjs writes the same cues as the page's subtitle files.
export const narrate = (page: string, said: Said, script: Script, scenes: Scene[]) => {
  const laid = scenes.map((scene) => {
    const slots = slotsOf(said, scene);
    return {scene, slots, length: Math.round(slots.reduce((sum, slot) => sum + slot.length, 0))};
  });
  const froms = laid.map((_, i) => laid.slice(0, i).reduce((sum, earlier) => sum + earlier.length, 0));
  const length = laid.reduce((sum, scene) => sum + scene.length, 0);
  // The video frame each recorded line starts on, within its scene.
  const heard = laid.map(({scene, slots}) => scene.ids.map((_, k) => Math.round(realOf(slots)(scene.starts[k])) + f(LEAD)));
  const text = Object.fromEntries(script.map((line) => [line.id, line.text]));
  const cues: Cue[] = laid.flatMap(({scene}, i) =>
    scene.ids.flatMap((id, k) => {
      if (!said[id]) return [];
      if (text[id] === undefined) throw new Error(`${page}: line ${id} is not in narration.json`);
      const from = froms[i] + heard[i][k];
      return cuesOf(id, text[id], from, from + f(said[id]));
    }),
  );
  const Video: React.FC<{cues: Cue[]}> = ({cues: shown}) => (
    <AbsoluteFill style={{backgroundColor: BG}}>
      <AbsoluteFill style={{transform: `scale(${STAGE})`, transformOrigin: 'top center'}}>
        {laid.map(({scene, slots, length: sceneLength}, i) => (
          <Sequence key={i} from={froms[i]} durationInFrames={sceneLength}>
            <Faded length={sceneLength}>
              <WarpContext.Provider value={designOf(slots)}>{scene.wrap ? scene.wrap(slots, scene.node) : scene.node}</WarpContext.Provider>
            </Faded>
          </Sequence>
        ))}
      </AbsoluteFill>
      {laid.map(({scene, length: sceneLength}, i) => (
        <Sequence key={i} from={froms[i]} durationInFrames={sceneLength}>
          {scene.ids.map((id, k) =>
            said[id] ? (
              <Sequence key={id} from={heard[i][k]}>
                <Audio src={staticFile(`${page}/${id}.wav`)} />
              </Sequence>
            ) : null,
          )}
        </Sequence>
      ))}
      <Subtitles cues={shown} />
    </AbsoluteFill>
  );
  // The frame each scene ends on: gif.mjs cuts the page's GIF preview at one of these.
  const ends = laid.map((_, i) => froms[i] + laid[i].length);
  return {id: page, length, ends, cues, Video};
};
