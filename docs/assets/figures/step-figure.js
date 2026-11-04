// Click-through figures on the docs site: tools/docs_site/hooks.py puts a figure's fragment (docs/assets/figures/<name>.html) in
// the page, and this script moves it one step per click. Each <li> of the fragment's .sf-steps is a step: its text is the caption,
// and data-state says what the stage looks like at that step. No step depends on the click that led to it, so Back and the dots
// work the same as Next, and figure-gif.mjs can record any step through figure.stepFigure.show(n).
// A token with "from" (and "via") travels in that step from those anchors to its "at", whatever the step before showed.
(() => {
  // A step lists only the values that change; every other value carries over from the steps before it.
  const statesOf = (steps) => {
    let values = {};
    return steps.map((step) => {
      const state = JSON.parse(step.dataset.state);
      values = {...values, ...state.values};
      return {...state, values};
    });
  };

  // Where a token sits for {at, place}: the centre of the anchor, or just past the end of the anchor's text with place "end".
  const pointFor = (stage, anchor, token, place) => {
    const box = stage.getBoundingClientRect();
    if (place === 'end') {
      const range = document.createRange();
      range.selectNodeContents(anchor);
      const text = range.getBoundingClientRect();
      const half = token.offsetWidth / 2;
      return {x: Math.min(text.right + 10 + half - box.left, box.width - half - 2), y: text.top + text.height / 2 - box.top};
    }
    const rect = anchor.getBoundingClientRect();
    return {x: rect.left + rect.width / 2 - box.left, y: rect.top + rect.height / 2 - box.top};
  };

  const still = window.matchMedia('(prefers-reduced-motion: reduce)');

  const build = (figure) => {
    const stage = figure.querySelector('.sf-stage');
    const list = figure.querySelector('.sf-steps');
    const steps = [...list.children];
    const states = statesOf(steps);
    const anchors = new Map([...figure.querySelectorAll('[data-anchor]')].map((node) => [node.dataset.anchor, node]));
    const slots = [...figure.querySelectorAll('[data-slot]')];
    const panels = [...figure.querySelectorAll('[data-panel]')];
    const tokens = [...figure.querySelectorAll('[data-token]')];

    const controls = document.createElement('div');
    controls.className = 'sf-controls';
    const back = document.createElement('button');
    back.type = 'button';
    back.className = 'md-button';
    back.textContent = 'Back';
    const next = document.createElement('button');
    next.type = 'button';
    next.className = 'md-button md-button--primary';
    const dots = document.createElement('div');
    dots.className = 'sf-dots';
    const dotButtons = steps.map((_, n) => {
      const dot = document.createElement('button');
      dot.type = 'button';
      dot.setAttribute('aria-label', `Step ${n + 1} of ${steps.length}`);
      dot.addEventListener('click', () => show(n));
      dots.append(dot);
      return dot;
    });
    controls.append(back, dots, next);
    figure.append(controls);

    list.setAttribute('aria-live', 'polite');
    let current = 0;
    let marked = [];
    let size = '';

    // A stop of a token's trip: the name of an anchor, or {at, place} like the token's own spec.
    const transformAt = (token, stop) => {
      const spec = typeof stop === 'string' ? {at: stop} : stop;
      const {x, y} = pointFor(stage, anchors.get(spec.at), token, spec.place);
      return `translate(${x}px, ${y}px) translate(-50%, -50%)`;
    };
    const place = (token, spec) => {
      token.style.transform = transformAt(token, spec);
    };
    // The trip of a token with "from": it shows at once where it starts, and goes through each "via" to where the step puts it.
    const travel = (token, spec) => {
      const stops = [spec.from, ...(spec.via ?? []), spec].map((stop) => transformAt(token, stop)).filter((stop, i, all) => i === 0 || stop !== all[i - 1]);
      token.getAnimations().forEach((animation) => animation.cancel());
      if (still.matches) return;
      const delay = (spec.delay ?? 0) * 1000;
      // A token that waits for its turn is not there yet: it shows when it starts to move.
      if (delay) token.animate([{opacity: 0}, {opacity: 0}], {duration: delay});
      if (stops.length > 1) {
        token.animate(stops.map((transform) => ({transform})), {
          duration: 600 * (stops.length - 1),
          delay,
          easing: 'cubic-bezier(.3, .7, .25, 1)',
          fill: 'backwards',
        });
      }
    };

    const show = (n) => {
      current = n;
      const state = states[n];
      steps.forEach((step, i) => step.classList.toggle('is-current', i === n));
      dotButtons.forEach((dot, i) => {
        dot.classList.toggle('is-current', i === n);
        dot.classList.toggle('is-past', i < n);
      });
      for (const [node, names] of marked) node.classList.remove(...names);
      marked = [
        ...(state.now ?? []).map((name) => [anchors.get(name), ['is-now']]),
        ...Object.entries(state.marks ?? {}).map(([name, mark]) => [anchors.get(name), mark.split(' ')]),
      ];
      for (const [node, names] of marked) node.classList.add(...names);
      for (const slot of slots) {
        const value = state.values[slot.dataset.slot] ?? '';
        if (slot.textContent !== value) {
          slot.textContent = value;
          slot.classList.remove('is-changed');
          void slot.offsetWidth;
          slot.classList.add('is-changed');
        }
      }
      panels.forEach((panel) => panel.classList.toggle('is-active', panel.dataset.panel === state.active));
      for (const token of tokens) {
        const spec = state.tokens?.[token.dataset.token];
        token.classList.remove('is-dropped', 'is-merging');
        if (!spec) {
          token.classList.remove('is-shown');
          continue;
        }
        if (spec.from) {
          token.classList.add('is-teleport', 'is-shown');
          place(token, spec);
          travel(token, spec);
          void token.offsetWidth;
          token.classList.remove('is-teleport');
        } else if (!token.classList.contains('is-shown')) {
          // A token that was hidden appears where it belongs; it does not fly in from where it last was.
          token.classList.add('is-teleport');
          place(token, spec);
          void token.offsetWidth;
          token.classList.remove('is-teleport');
          token.classList.add('is-shown');
        } else {
          place(token, spec);
        }
        if (spec.drop) token.classList.add('is-dropped');
        if (spec.merge) token.classList.add('is-merging');
      }
      back.disabled = n === 0;
      next.textContent = n === steps.length - 1 ? 'Replay' : 'Next';
      size = sizeOf();
    };

    const advance = () => show(current === steps.length - 1 ? 0 : current + 1);
    next.addEventListener('click', advance);
    back.addEventListener('click', () => show(Math.max(0, current - 1)));
    stage.addEventListener('click', () => {
      if (String(window.getSelection())) return;
      advance();
    });
    figure.addEventListener('keydown', (event) => {
      if (event.key === 'ArrowRight') advance();
      else if (event.key === 'ArrowLeft') show(Math.max(0, current - 1));
      else return;
      event.preventDefault();
    });

    // The layout changes the points the tokens sit on, so they are worked out again without a transition. A step that changes the
    // size of the stage has already placed its tokens, and is left to finish its moves.
    const sizeOf = () => `${stage.offsetWidth}x${stage.offsetHeight}`;
    const replace = (always) => {
      if (!always && sizeOf() === size) return;
      size = sizeOf();
      for (const token of tokens) {
        const spec = states[current].tokens?.[token.dataset.token];
        if (!spec) continue;
        token.classList.add('is-teleport');
        place(token, spec);
        void token.offsetWidth;
        token.classList.remove('is-teleport');
      }
    };
    new ResizeObserver(() => replace(false)).observe(stage);
    document.fonts?.ready.then(() => replace(true));

    figure.classList.add('is-live');
    figure.tabIndex = 0;
    figure.stepFigure = {show, length: steps.length};
    show(0);
  };

  document.querySelectorAll('[data-step-figure]').forEach(build);
})();
