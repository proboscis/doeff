import React from 'react';
import {Composition} from 'remotion';
import {FPS, HEIGHT, WIDTH} from './kit/theme';
import {ASYNC_EFFECTS} from './pages/async-effects/AsyncEffects';
import {BASIC_EFFECTS} from './pages/basic-effects/BasicEffects';
import {CAPABILITY_CLASSES} from './pages/capability-classes/CapabilityClasses';
import {CLI_AUTO_DISCOVERY} from './pages/cli-auto-discovery/CliAutoDiscovery';
import {CORE_CONCEPTS} from './pages/core-concepts/CoreConcepts';
import {ERROR_HANDLING} from './pages/error-handling/ErrorHandling';
import {GETTING_STARTED} from './pages/getting-started/GettingStarted';
import {HOME} from './pages/home/Home';
import {KLEISLI_ARROWS} from './pages/kleisli-arrows/KleisliArrows';
import {SEMAPHORE_EFFECTS} from './pages/semaphore-effects/SemaphoreEffects';
import {RUST_SCHEDULER} from './pages/rust-scheduler/RustScheduler';
import {PATTERNS} from './pages/patterns/Patterns';
import {STATIC_TYPING} from './pages/static-typing/StaticTyping';
import {WHY_EFFECTS} from './pages/why-effects/WhyEffects';

// One composition per docs page, named after the page's video file in docs/assets/videos/ (home.mp4 is on
// docs/index.md). A new page's video adds its line here.
export const Root: React.FC = () => (
  <>
    <Composition id="home" component={HOME.Video} durationInFrames={HOME.length} fps={FPS} width={WIDTH} height={HEIGHT} />
    <Composition id="getting-started" component={GETTING_STARTED.Video} durationInFrames={GETTING_STARTED.length} fps={FPS} width={WIDTH} height={HEIGHT} />
    <Composition id="basic-effects" component={BASIC_EFFECTS.Video} durationInFrames={BASIC_EFFECTS.length} fps={FPS} width={WIDTH} height={HEIGHT} />
    <Composition id="error-handling" component={ERROR_HANDLING.Video} durationInFrames={ERROR_HANDLING.length} fps={FPS} width={WIDTH} height={HEIGHT} />
    <Composition id="async-effects" component={ASYNC_EFFECTS.Video} durationInFrames={ASYNC_EFFECTS.length} fps={FPS} width={WIDTH} height={HEIGHT} />
    <Composition id="why-effects" component={WHY_EFFECTS.Video} durationInFrames={WHY_EFFECTS.length} fps={FPS} width={WIDTH} height={HEIGHT} />
    <Composition id="capability-classes" component={CAPABILITY_CLASSES.Video} durationInFrames={CAPABILITY_CLASSES.length} fps={FPS} width={WIDTH} height={HEIGHT} />
    <Composition id="core-concepts" component={CORE_CONCEPTS.Video} durationInFrames={CORE_CONCEPTS.length} fps={FPS} width={WIDTH} height={HEIGHT} />
    <Composition id="kleisli-arrows" component={KLEISLI_ARROWS.Video} durationInFrames={KLEISLI_ARROWS.length} fps={FPS} width={WIDTH} height={HEIGHT} />
    <Composition id="cli-auto-discovery" component={CLI_AUTO_DISCOVERY.Video} durationInFrames={CLI_AUTO_DISCOVERY.length} fps={FPS} width={WIDTH} height={HEIGHT} />
    <Composition id="semaphore-effects" component={SEMAPHORE_EFFECTS.Video} durationInFrames={SEMAPHORE_EFFECTS.length} fps={FPS} width={WIDTH} height={HEIGHT} />
    <Composition id="rust-scheduler" component={RUST_SCHEDULER.Video} durationInFrames={RUST_SCHEDULER.length} fps={FPS} width={WIDTH} height={HEIGHT} />
    <Composition id="patterns" component={PATTERNS.Video} durationInFrames={PATTERNS.length} fps={FPS} width={WIDTH} height={HEIGHT} />
    <Composition id="static-typing" component={STATIC_TYPING.Video} durationInFrames={STATIC_TYPING.length} fps={FPS} width={WIDTH} height={HEIGHT} />
  </>
);
