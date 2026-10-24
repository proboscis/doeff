// Narration time. A scene is designed in its own frames, cut into slots at its headlines. A narrated line may need
// longer than its slot, so each slot is stretched to fit its line: the motion inside the slot slows down instead of
// the voice running over into the next headline. Scenes read their frame through useSceneFrame, which maps the
// video's frame back to the design frame; outside a warped scene the two are the same.
import React from 'react';
import {useCurrentFrame} from 'remotion';

// One slot of a scene: where it starts and ends in design frames, and how many frames it gets in the video.
export type Slot = {from: number; to: number; length: number};

// The video frame (within the scene) to the design frame.
export const designOf = (slots: Slot[]) => (real: number): number => {
  let at = 0;
  for (const slot of slots) {
    if (real < at + slot.length) return slot.from + ((real - at) * (slot.to - slot.from)) / slot.length;
    at += slot.length;
  }
  const last = slots[slots.length - 1];
  return last ? last.to + (real - at) : real;
};

// The design frame to the video frame (within the scene): the inverse of designOf.
export const realOf = (slots: Slot[]) => (design: number): number => {
  let at = 0;
  for (const slot of slots) {
    if (design < slot.to) return at + ((design - slot.from) * slot.length) / (slot.to - slot.from);
    at += slot.length;
  }
  const last = slots[slots.length - 1];
  return last ? at + (design - last.to) : design;
};

export const WarpContext = React.createContext<(real: number) => number>((real) => real);

// The current frame of the scene, in design frames.
export const useSceneFrame = (): number => {
  const real = useCurrentFrame();
  return React.useContext(WarpContext)(real);
};
