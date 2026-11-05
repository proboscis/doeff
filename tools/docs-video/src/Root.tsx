import React from 'react';
import {Composition} from 'remotion';
import {EndCard} from './kit/EndCard';
import {Cue} from './kit/subtitles';
import {FPS, HEIGHT, WIDTH} from './kit/theme';

// A page's video as narrate() (kit/narrated.tsx) returns it: its id is the page's name under src/pages/ and the name
// of its video file in docs/assets/videos/ (home.mp4 is on docs/index.md). Its cues are the props of its composition:
// the video draws them as subtitles and render.mjs writes them as the subtitle files.
type PageVideo = {id: string; length: number; cues: Cue[]; Video: React.FC<{cues: Cue[]}>};

// Whether an export of a page module is the page's video.
const isVideo = (value: unknown): value is PageVideo =>
  typeof value === 'object' && value !== null && 'id' in value && 'length' in value && 'Video' in value;

// Every module under pages/, found when the bundle is built (webpack's require.context), so that a new page's video
// is a new directory and nothing here: each page adding its own line here made the pages' branches conflict.
declare const require: {context: (dir: string, deep: boolean, match: RegExp) => {keys: () => string[]; (key: string): Record<string, unknown>}};
const pages = require.context('./pages', true, /\.tsx$/);
const VIDEOS = [...new Set(pages.keys().flatMap((key) => Object.values(pages(key)).filter(isVideo)))].sort((a, b) => a.id.localeCompare(b.id));

// One composition per page's video.
export const Root: React.FC = () => (
  <>
    {VIDEOS.map((video) => (
      <Composition key={video.id} id={video.id} component={video.Video} durationInFrames={video.length} fps={FPS} width={WIDTH} height={HEIGHT} defaultProps={{cues: video.cues}} />
    ))}
    {/* The last frame of a page's GIF preview (gif.mjs renders it as a still with its own label). */}
    <Composition id="gif-end-card" component={EndCard} durationInFrames={1} fps={FPS} width={WIDTH} height={HEIGHT} defaultProps={{label: 'Watch the full video'}} />
  </>
);
