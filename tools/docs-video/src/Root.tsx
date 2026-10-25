import React from 'react';
import {Composition} from 'remotion';
import {FPS, HEIGHT, WIDTH} from './kit/theme';
import {ERROR_HANDLING} from './pages/error-handling/ErrorHandling';
import {GETTING_STARTED} from './pages/getting-started/GettingStarted';
import {HOME} from './pages/home/Home';

// One composition per docs page, named after the page's video file in docs/assets/videos/ (home.mp4 is on
// docs/index.md). A new page's video adds its line here.
export const Root: React.FC = () => (
  <>
    <Composition id="home" component={HOME.Video} durationInFrames={HOME.length} fps={FPS} width={WIDTH} height={HEIGHT} />
    <Composition id="getting-started" component={GETTING_STARTED.Video} durationInFrames={GETTING_STARTED.length} fps={FPS} width={WIDTH} height={HEIGHT} />
    <Composition id="error-handling" component={ERROR_HANDLING.Video} durationInFrames={ERROR_HANDLING.length} fps={FPS} width={WIDTH} height={HEIGHT} />
  </>
);
