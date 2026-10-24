import React from 'react';
import {Composition} from 'remotion';
import {FPS, HEIGHT, WIDTH} from './kit/theme';
import {HOME} from './pages/home/Home';

// One composition per docs page, named after the page's video file in docs/assets/videos/ (home.mp4 is on
// docs/index.md). A new page's video adds its line here.
export const Root: React.FC = () => (
  <>
    <Composition id="home" component={HOME.Video} durationInFrames={HOME.length} fps={FPS} width={WIDTH} height={HEIGHT} />
  </>
);
