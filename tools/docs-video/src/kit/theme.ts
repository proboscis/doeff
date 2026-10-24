// The look every docs video shares: 1280x720 at 60 fps, a dark background, and one colour per role.
export const FPS = 60;
export const WIDTH = 1280;
export const HEIGHT = 720;
// Seconds of video as frames.
export const f = (seconds: number) => Math.round(seconds * FPS);

export const BG = '#0f1117';
export const PANEL = '#161925';
export const TEXT = '#e8e8ef';
export const MUTED = '#a8a8bc';
// Effects and the @do marker.
export const ACCENT = '#9d8cff';
// Test handlers, and handlers in general.
export const TEST = '#3ecfb2';
// Production handlers.
export const PROD = '#ffb86b';
// Whatever is at work right now: the "look here" ring and glow.
export const LOOK = '#ffe14d';
export const MONO = '"DejaVu Sans Mono", "Menlo", monospace';
export const SANS = '"DejaVu Sans", "Helvetica", sans-serif';
