import React from 'react';
import {Composition} from 'remotion';
import {FPS, HEIGHT, WIDTH} from './kit/theme';

// A page's video as narrate() (kit/narrated.tsx) returns it: its id is the page's name under src/pages/ and the name
// of its video file in docs/assets/videos/ (home.mp4 is on docs/index.md).
type PageVideo = {id: string; length: number; Video: React.FC};

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
      <Composition key={video.id} id={video.id} component={video.Video} durationInFrames={video.length} fps={FPS} width={WIDTH} height={HEIGHT} />
    ))}
  </>
);
