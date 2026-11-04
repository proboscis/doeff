// Click-through figures on the docs site: tools/docs_site/hooks.py puts a figure's fragment (docs/assets/figures/<name>.html) in
// the page, and this script moves it one step per click. Each <li> of the fragment's .sf-steps is a step: its text is the caption,
// and data-state says what the stage looks like at that step. No step depends on the click that led to it, so Back and the dots
// work the same as Next, and figure-gif.mjs can record any step through figure.stepFigure.show(n).
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

    const place = (token, spec) => {
      const anchor = anchors.get(spec.at);
      const {x, y} = pointFor(stage, anchor, token, spec.place);
      token.style.transform = `translate(${x}px, ${y}px) translate(-50%, -50%)`;
    };

    const show = (n) => {
      current = n;
      const state = states[n];
      steps.forEach((step, i) => step.classList.toggle('is-current', i === n));
      dotButtons.forEach((dot, i) => {
        dot.classList.toggle('is-current', i === n);
        dot.classList.toggle('is-past', i < n);
      });
      for (const anchor of anchors.values()) anchor.classList.remove('is-now', 'is-k', 'is-dead', 'is-on');
      for (const name of state.now ?? []) anchors.get(name).classList.add('is-now');
      for (const [name, mark] of Object.entries(state.marks ?? {})) anchors.get(name).classList.add(mark);
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
        // A token that was hidden appears where it belongs; it does not fly in from where it last was.
        if (!token.classList.contains('is-shown')) {
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

    // The layout changes the points the tokens sit on, so they are worked out again without a transition.
    const replace = () => {
      for (const token of tokens) {
        const spec = states[current].tokens?.[token.dataset.token];
        if (!spec) continue;
        token.classList.add('is-teleport');
        place(token, spec);
        void token.offsetWidth;
        token.classList.remove('is-teleport');
      }
    };
    new ResizeObserver(replace).observe(stage);
    document.fonts?.ready.then(replace);

    figure.classList.add('is-live');
    figure.tabIndex = 0;
    figure.stepFigure = {show, length: steps.length};
    show(0);
  };

  document.querySelectorAll('[data-step-figure]').forEach(build);
})();
