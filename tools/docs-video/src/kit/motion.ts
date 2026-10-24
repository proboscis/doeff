// Small helpers for timing motion in seconds of a scene's design time.
import {Easing, interpolate} from 'remotion';
import {f} from './theme';

export const clamp = {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'} as const;
export const smooth = {...clamp, easing: Easing.inOut(Easing.cubic)};

// Opacity from 0 to 1, starting at a time in seconds.
export const fadeAt = (frame: number, seconds: number, length = 0.3) => interpolate(frame, [f(seconds), f(seconds + length)], [0, 1], clamp);
// Eased progress from 0 to 1 between two times in seconds.
export const ramp = (frame: number, from: number, to: number) => interpolate(frame, [f(from), f(to)], [0, 1], smooth);
export const mix = (a: number, b: number, t: number) => a + (b - a) * t;
