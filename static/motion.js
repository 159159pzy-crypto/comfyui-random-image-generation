/* Small, interruptible motion primitives for the existing vanilla-JS UI.
 * One clock, one owner per property, and no completion callback from an old intent.
 * response is the natural period of a spring, not an input-locking duration.
 */
(() => {
  "use strict";
  const host = globalThis;
  const doc = typeof document === "undefined" ? null : document;
  const reducedQuery = typeof host.matchMedia === "function" ? host.matchMedia("(prefers-reduced-motion: reduce)") : null;
  const owners = new WeakMap(), active = new Set();
  const presences = new WeakMap(), disclosures = new WeakMap(), dialogs = new WeakMap();
  const disclosureRoots = new WeakSet(), handledClicks = new WeakSet();
  const dialogStack = [];
  let frame = null;
  const now = () => host.performance?.now?.() ?? Date.now();
  const reduced = () => !!reducedQuery?.matches;
  const immediate = options => !!options?.immediate || reduced() || !!doc?.hidden;
  const requestFrame = callback => host.requestAnimationFrame(callback);
  const cancelFrame = id => host.cancelAnimationFrame(id);
  const copy = value => ({ ...value });
  function channels(owner) {
    let map = owners.get(owner);
    if (!map) owners.set(owner, map = new Map());
    return map;
  }
  function stopClockIfIdle() {
    if (!active.size && frame !== null) { cancelFrame(frame); frame = null; }
  }
  function schedule() {
    if (active.size && frame === null) frame = requestFrame(tick);
  }
  function settlePromise(state, cancelled) {
    const resolve = state.resolve;
    state.resolve = null;
    resolve?.({ cancelled, value: copy(state.value), velocity: copy(state.velocity) });
  }
  function cancelState(state) {
    active.delete(state);
    state.running = false;
    state.version++;
    settlePromise(state, true);
    stopClockIfIdle();
  }
  function finishState(state) {
    if (!state.running) return;
    const version = state.version;
    active.delete(state);
    state.running = false;
    for (const key of Object.keys(state.target)) { state.value[key] = state.target[key]; state.velocity[key] = 0; }
    state.update?.(copy(state.value), copy(state.velocity));
    if (state.version === version) {
      settlePromise(state, false);
      state.complete?.(copy(state.value));
    }
    stopClockIfIdle();
  }
  // Exact integration of a damped oscillator. Unlike Euler steps this has the
  // same presentation value at 60 Hz, 120 Hz, and after a temporarily late frame.
  function advance(position, velocity, target, elapsed, frequency, damping) {
    const offset = position - target;
    if (damping < 1 - 1e-5) {
      const decay = damping * frequency, rate = frequency * Math.sqrt(1 - damping * damping);
      const c = Math.cos(rate * elapsed), s = Math.sin(rate * elapsed), e = Math.exp(-decay * elapsed);
      const b = (velocity + decay * offset) / rate;
      return [target + e * (offset * c + b * s), e * (velocity * c - (decay * b + rate * offset) * s)];
    }
    if (damping > 1 + 1e-5) {
      const root = Math.sqrt(damping * damping - 1);
      const a = -frequency * (damping - root), b = -frequency * (damping + root);
      const ca = (velocity - b * offset) / (a - b), cb = offset - ca;
      const ea = Math.exp(a * elapsed), eb = Math.exp(b * elapsed);
      return [target + ca * ea + cb * eb, a * ca * ea + b * cb * eb];
    }
    const e = Math.exp(-frequency * elapsed), b = velocity + frequency * offset;
    return [target + (offset + b * elapsed) * e, (velocity - frequency * b * elapsed) * e];
  }
  function tick(timestamp) {
    frame = null;
    for (const state of [...active]) {
      if (!state.running) continue;
      const version = state.version, dt = Math.max(0, (timestamp - state.time) / 1000);
      state.time = timestamp;
      let atRest = true;
      for (const key of Object.keys(state.target)) {
        const [value, velocity] = advance(state.value[key], state.velocity[key], state.target[key], dt, state.frequency, state.damping);
        state.value[key] = value; state.velocity[key] = velocity;
        const precision = state.precision[key];
        if (Math.abs(value - state.target[key]) > precision || Math.abs(velocity) > precision * 10) atRest = false;
      }
      if (atRest) finishState(state);
      else {
        state.update?.(copy(state.value), copy(state.velocity));
        if (version !== state.version) continue;
      }
    }
    schedule();
  }
  function spring(owner, channel, target, options = {}) {
    if (!owner || (typeof owner !== "object" && typeof owner !== "function")) throw new TypeError("Motion owner must be an object");
    const entries = Object.entries(target);
    if (!entries.length || entries.some(([, value]) => !Number.isFinite(value))) throw new TypeError("Motion targets must be finite numbers");
    const map = channels(owner);
    let state = map.get(channel);
    const continuing = !!state?.running;
    if (!state) {
      state = { value: {}, velocity: {}, target: {}, version: 0, running: false };
      map.set(channel, state);
    }
    if (state.resolve) settlePromise(state, true);
    for (const [key, value] of entries) {
      if (!(key in state.value) || (!continuing && options.initial && key in options.initial)) {
        state.value[key] = Number.isFinite(options.initial?.[key]) ? options.initial[key] : value;
        state.velocity[key] = Number.isFinite(options.velocity?.[key]) ? options.velocity[key] : 0;
      }
    }
    state.target = copy(target);
    // Remove obsolete keys rather than letting a previous vector leak into callbacks.
    for (const key of Object.keys(state.value)) if (!(key in target)) { delete state.value[key]; delete state.velocity[key]; }
    const gesture = options.preset === "gesture";
    state.frequency = Math.PI * 2 / Math.max(.01, options.response ?? (gesture ? .32 : .3));
    state.damping = Math.max(.01, options.damping ?? (gesture ? .85 : 1));
    // Pixel geometry and unitless opacity can share a spring without sharing
    // a visible settling threshold. A scalar keeps the existing API behavior.
    state.precision = Object.fromEntries(entries.map(([key]) => {
      const precision = typeof options.precision === "number" ? options.precision : options.precision?.[key];
      return [key, Math.max(.000001, precision ?? .001)];
    }));
    state.update = options.onUpdate;
    state.complete = options.onComplete;
    state.time = now(); state.running = true;
    const version = ++state.version;
    const finished = new Promise(resolve => { state.resolve = resolve; });
    const handle = {
      finished,
      get current() { return copy(state.value); },
      get velocity() { return copy(state.velocity); },
      get target() { return copy(state.target); },
      get active() { return state.running && state.version === version; },
      cancel() { if (state.version === version) cancelState(state); return copy(state.value); },
      finish() { if (state.version === version) finishState(state); return copy(state.value); },
    };
    active.add(state);
    const atTarget = entries.every(([key, value]) => Math.abs(state.value[key] - value) <= state.precision[key] && Math.abs(state.velocity[key]) <= state.precision[key] * 10);
    if (immediate(options) || atTarget) finishState(state);
    else {
      state.update?.(copy(state.value), copy(state.velocity));
      schedule();
    }
    return handle;
  }
  function snapshot(owner, channel) {
    const state = owners.get(owner)?.get(channel);
    return state ? { value: copy(state.value), velocity: copy(state.velocity), target: copy(state.target), active: state.running } : null;
  }
  function freeze(owner, channel) {
    const result = snapshot(owner, channel), state = owners.get(owner)?.get(channel);
    if (state) cancelState(state);
    return result;
  }
  function finish(owner, channel) { const state = owners.get(owner)?.get(channel); if (state) finishState(state); }
  function saveStyles(element, names) { return Object.fromEntries(names.map(name => [name, element.style[name] || ""])); }
  function restoreStyles(element, saved) { for (const [name, value] of Object.entries(saved)) element.style[name] = value; }
  function presence(element, visible, options = {}) {
    visible = !!visible;
    let state = presences.get(element);
    if (!state) {
      state = { target: !visible, token: 0, saved: null, handle: null, options: {}, from: null };
      presences.set(element, state);
    }
    const running = snapshot(element, "presence")?.active;
    const sameTarget = state.target === visible;
    state.options = options;
    if (visible) element.hidden = false;
    if (options.manageInert !== false) element.inert = !visible;
    if (sameTarget && running) {
      if (immediate(options)) finish(element, "presence");
      return state.handle;
    }
    const settled = element.dataset.motionState === (visible ? "open" : "closed");
    if (sameTarget && settled && !options.restart) {
      element.hidden = !visible;
      options.onComplete?.();
      return state.handle;
    }
    const token = ++state.token;
    state.target = visible;
    const from = { opacity: 0, x: 0, y: 0, scale: 1, ...state.from, ...options.from };
    state.from = from;
    if (!state.saved) state.saved = saveStyles(element, ["opacity", "transform", "willChange"]);
    element.dataset.motionState = visible ? "opening" : "closing";
    element.style.willChange = "opacity, transform";
    const current = snapshot(element, "presence");
    const initial = current ? current.value.progress : visible ? 0 : element.hidden ? 0 : 1;
    const start = options.restart && visible && sameTarget && !running ? 0 : initial;
    const render = values => {
      if (token !== state.token) return;
      const progress = values.progress;
      const value = {
        opacity: from.opacity + (1 - from.opacity) * progress,
        x: from.x * (1 - progress), y: from.y * (1 - progress),
        scale: from.scale + (1 - from.scale) * progress,
        progress,
      };
      if (state.options.render) state.options.render(value);
      else {
        element.style.opacity = String(Math.max(0, Math.min(1, value.opacity)));
        element.style.transform = `translate3d(${value.x}px, ${value.y}px, 0) scale(${value.scale})`;
      }
    };
    const handle = spring(element, "presence", { progress: visible ? 1 : 0 }, {
      ...options, initial: { progress: start }, onUpdate: render,
      onComplete() {
        if (token !== state.token) return;
        element.hidden = !visible;
        element.dataset.motionState = visible ? "open" : "closed";
        restoreStyles(element, state.saved); state.saved = null;
        state.options.onComplete?.();
      },
    });
    if (token === state.token) state.handle = handle;
    return handle;
  }
  function disclosureState(element) {
    let state = disclosures.get(element);
    if (!state) {
      state = { target: !!element.open, token: 0, saved: null, observer: null, name: element.getAttribute?.("name") || "" };
      disclosures.set(element, state);
      // Native named details collapse siblings synchronously; keep the same group
      // semantics ourselves so each sibling can finish its own exit.
      if (state.name) { element.dataset.motionDisclosureGroup = state.name; element.removeAttribute("name"); }
      element.dataset.motionDisclosure = "true";
      element.dataset.motionTargetOpen = String(state.target);
    }
    return state;
  }
  function cssNumber(style, property) { return parseFloat(style?.[property]) || 0; }
  function disclosureHeights(element) {
    const previous = element.style.height;
    element.style.height = "auto";
    const rect = element.getBoundingClientRect(), summary = element.querySelector(":scope > summary");
    const summaryRect = summary?.getBoundingClientRect();
    const css = typeof host.getComputedStyle === "function" ? host.getComputedStyle(element) : {};
    const summaryCss = summary && typeof host.getComputedStyle === "function" ? host.getComputedStyle(summary) : {};
    const closed = summaryRect ? summaryRect.bottom - rect.top + cssNumber(summaryCss, "marginBottom") + cssNumber(css, "paddingBottom") + cssNumber(css, "borderBottomWidth") : 0;
    element.style.height = previous;
    return { open: rect.height, closed };
  }
  function disclosure(element, open, options = {}) {
    const state = disclosureState(element);
    open = !!open;
    if (open && !options.groupChange) {
      const root = element.getRootNode?.() || doc;
      for (const other of root?.querySelectorAll?.("details") || []) {
        if (other === element) continue;
        const otherState = disclosureState(other);
        if (!snapshot(other, "disclosure")?.active) otherState.target = !!other.open;
        const sameGroup = state.name && state.name === otherState.name;
        if (sameGroup && otherState.target) disclosure(other, false, { ...options, groupChange: true });
      }
    }
    const wasOpen = !!element.open, rect = element.getBoundingClientRect();
    const token = ++state.token;
    state.target = open;
    element.dataset.motionTargetOpen = String(open);
    element.dataset.motionState = open ? "opening" : "closing";
    if (!state.saved) state.saved = saveStyles(element, ["height", "overflow", "boxSizing", "willChange"]);
    element.open = true;
    element.style.boxSizing = "border-box";
    const heights = disclosureHeights(element);
    const initial = rect.height || (wasOpen ? heights.open : heights.closed);
    element.style.overflow = "hidden"; element.style.willChange = "height";
    const run = () => spring(element, "disclosure", { height: open ? disclosureHeights(element).open : heights.closed }, {
      ...options, initial: { height: initial },
      onUpdate(value) { if (token === state.token) element.style.height = `${Math.max(0, value.height)}px`; },
      onComplete() {
        if (token !== state.token) return;
        element.open = open;
        element.dataset.motionState = open ? "open" : "closed";
        restoreStyles(element, state.saved); state.saved = null;
        state.observer?.disconnect(); state.observer = null;
        options.onComplete?.();
      },
    });
    state.observer?.disconnect(); state.observer = null;
    if (typeof host.ResizeObserver === "function" && !immediate(options)) {
      let measured = heights.open;
      state.observer = new host.ResizeObserver(() => {
        if (token !== state.token || !state.target || !snapshot(element, "disclosure")?.active) return;
        const next = disclosureHeights(element).open;
        if (Math.abs(next - measured) > .5) { measured = next; run(); }
      });
      for (const child of element.children) if (child.tagName !== "SUMMARY") state.observer.observe(child);
    }
    return run();
  }
  function bindDisclosures(root = doc) {
    if (!root) return;
    for (const element of root.querySelectorAll?.("details") || []) disclosureState(element);
    if (root.matches?.("details")) disclosureState(root);
    if (disclosureRoots.has(root)) return;
    disclosureRoots.add(root);
    root.addEventListener("click", event => {
      if (event.defaultPrevented || handledClicks.has(event)) return;
      const summary = event.target.closest?.("summary");
      const element = summary?.parentElement;
      if (element?.tagName !== "DETAILS" || !root.contains(element)) return;
      if (event.target !== summary && event.target.closest?.("a, button, input, select, textarea")) return;
      event.preventDefault(); handledClicks.add(event);
      const state = disclosureState(element);
      // Account for code that changes .open while the node is at rest.
      if (!snapshot(element, "disclosure")?.active) state.target = !!element.open;
      disclosure(element, !state.target);
    });
  }
  function topDialog() {
    for (let index = dialogStack.length - 1; index >= 0; index--) if (dialogStack[index].open) return dialogStack[index];
    return null;
  }
  function moveToTop(element) {
    const index = dialogStack.indexOf(element);
    if (index >= 0) dialogStack.splice(index, 1);
    dialogStack.push(element);
  }
  function canFocus(element) {
    if (!element || element.isConnected === false || element.inert || element.disabled) return false;
    const parentDialog = element.closest?.("dialog");
    return !parentDialog || parentDialog.open;
  }
  function bindDialog(element) {
    let state = dialogs.get(element);
    if (state) return state;
    state = { target: !!element.open, token: 0, opener: null, outsidePointer: false, restoreFocus: true };
    dialogs.set(element, state);
    element.addEventListener("cancel", event => {
      if (event.defaultPrevented) return;
      event.preventDefault(); dialog(element, false);
    });
    element.addEventListener("submit", event => {
      const method = event.submitter?.getAttribute?.("formmethod") || event.target.getAttribute?.("method");
      if (event.defaultPrevented || String(method).toLowerCase() !== "dialog") return;
      event.preventDefault(); dialog(element, false, { returnValue: event.submitter?.value ?? "" });
    });
    const outside = event => {
      if (event.target !== element) return false;
      const rect = element.getBoundingClientRect();
      return event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom;
    };
    element.addEventListener("pointerdown", event => { state.outsidePointer = outside(event); });
    element.addEventListener("click", event => {
      if (!event.defaultPrevented && state.outsidePointer && outside(event)) dialog(element, false);
      state.outsidePointer = false;
    });
    element.addEventListener("close", () => {
      // A queued native close event must not cancel a subsequent reopen.
      if (element.open) return;
      state.target = false; state.token++;
      const current = presences.get(element);
      // Native close can interrupt either direction (for example form.submit()).
      // Settle presence, rather than freezing an exit with temporary styles left.
      if (current) presence(element, false, { immediate: true, manageInert: false });
      else freeze(element, "presence");
      element.style.removeProperty("--motion-backdrop-opacity");
      const index = dialogStack.indexOf(element);
      if (index >= 0) dialogStack.splice(index, 1);
    });
    return state;
  }
  function dialog(element, open, options = {}) {
    const state = bindDialog(element), token = ++state.token;
    open = !!open; state.target = open;
    if (open && !element.open) state.restoreFocus = options.restoreFocus !== false;
    else if (options.restoreFocus !== undefined) state.restoreFocus = options.restoreFocus !== false;
    if (open) {
      if (!element.open) {
        state.opener = doc?.activeElement;
        element.hidden = false; element.inert = false;
        element.showModal();
      }
      moveToTop(element);
    }
    const from = { opacity: 0, y: 10, scale: .985, ...options.from };
    return presence(element, open, {
      ...options, from, manageInert: false,
      immediate: options.immediate || (!open && !element.open),
      render(value) {
        element.style.opacity = String(Math.max(0, Math.min(1, value.opacity)));
        element.style.transform = `translate3d(${value.x}px, ${value.y}px, 0) scale(${value.scale})`;
        element.style.setProperty("--motion-backdrop-opacity", String(Math.max(0, Math.min(1, value.progress))));
        options.render?.(value);
      },
      onComplete() {
        if (token !== state.token) return;
        if (!open && element.open) {
          const top = topDialog(), focused = doc?.activeElement;
          // A younger modal can have opened while this one was exiting. Closing
          // the older dialog must not let native focus restoration steal focus.
          const focusedDialog = focused?.closest?.("dialog");
          const keepFocus = (top && top !== element) || (focusedDialog?.open && focusedDialog !== element);
          if (options.returnValue !== undefined) element.close(String(options.returnValue));
          else element.close();
          const index = dialogStack.indexOf(element);
          if (index >= 0) dialogStack.splice(index, 1);
          if (keepFocus && canFocus(focused)) focused.focus({ preventScroll: true });
          else if (state.restoreFocus && canFocus(state.opener)) state.opener.focus({ preventScroll: true });
        }
        element.style.removeProperty("--motion-backdrop-opacity");
        options.onComplete?.();
      },
    });
  }
  function bindDialogs(root = doc, options = {}) {
    if (!root) return;
    const excluded = options.exclude || "#imagePreviewDialog";
    for (const element of root.querySelectorAll?.("dialog") || []) if (!element.matches?.(excluded)) bindDialog(element);
  }
  function finishAll() { for (const state of [...active]) finishState(state); }
  const preferenceChanged = () => { if (reduced()) finishAll(); };
  if (reducedQuery?.addEventListener) reducedQuery.addEventListener("change", preferenceChanged);
  else reducedQuery?.addListener?.(preferenceChanged);
  function visibilityChanged() {
    if (doc?.hidden) {
      doc.documentElement?.setAttribute("data-motion-paused", "");
      finishAll();
    } else doc?.documentElement?.removeAttribute("data-motion-paused");
  }
  doc?.addEventListener?.("visibilitychange", visibilityChanged);
  visibilityChanged();
  host.StudioMotion = Object.freeze({
    spring, presence, disclosure, bindDisclosures, dialog, bindDialogs,
    snapshot, freeze, finish, finishAll, reduced,
    target: element => dialogs.get(element)?.target ?? presences.get(element)?.target ?? disclosures.get(element)?.target,
  });
})();
