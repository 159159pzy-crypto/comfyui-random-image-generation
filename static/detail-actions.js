/* The detail action dock shares LoRA's measured-height spring without moving
 * the metadata scroll position or introducing another modal/focus layer. */
const DetailActionFold = (() => {
  let root = null, content = null, active = null, revision = 0, observer = null, targetHeight = 0, roomHeight = null;
  const panels = () => content?.querySelectorAll("#replayDrawer, #variantDrawer, [data-detail-panel]") || [];
  const motion = () => typeof StudioMotion !== "undefined" ? StudioMotion : null;
  const layoutHeight = element => Number.isFinite(element.offsetHeight) ? element.offsetHeight : element.getBoundingClientRect().height;
  function currentHeight() {
    if (root.hidden) return 0;
    const current = motion()?.snapshot(root, "detail-fold");
    return current?.active ? current.value.height : layoutHeight(root);
  }
  function initialize() {
    if (root?.isConnected !== false && root) return true;
    root = document.getElementById("detailActionFold");
    content = document.getElementById("detailActionFoldContent");
    if (!root || !content) return false;
    roomHeight = null;
    const dock = root.parentElement?.querySelector(".detail-primary-actions");
    dock?.addEventListener("keydown", event => {
      const trigger = event.target.closest?.("button[aria-controls]");
      if (event.key !== "ArrowUp" || !active || active.inert || trigger?.getAttribute("aria-controls") !== active.id) return;
      const first = [...active.querySelectorAll("button, input, select, textarea, [tabindex]")].find(item =>
        !item.disabled && item.tabIndex >= 0 && !item.closest("[hidden], [inert]") && item.getClientRects().length);
      if (first) { event.preventDefault(); first.focus({ preventScroll: true }); }
    });
    return true;
  }
  function conceal(panel) {
    panel.hidden = true; panel.inert = true;
    panel.dataset.motionState = "closed";
    panel.classList?.remove("closing");
  }
  function measureRoom() {
    const copy = root.parentElement, height = copy?.clientHeight;
    if (!Number.isFinite(height) || height <= 0) return;
    const header = copy.querySelector(".detail-header"), footer = copy.querySelector(".detail-primary-actions");
    // Layout sizes ignore the containing dialog's entering scale. Reserve a
    // readable metadata strip, including its padding, even on short phones.
    const room = Math.max(0, height - (header?.offsetHeight || 0) - (footer?.offsetHeight || 0) - 64);
    if (roomHeight === null || Math.abs(room - roomHeight) > .5) {
      roomHeight = room;
      root.style.setProperty("--detail-fold-room", `${room}px`);
    }
  }
  function animate(open, immediate = false, initialHeight = null) {
    const token = ++revision, current = initialHeight ?? currentHeight();
    root.hidden = false; root.inert = !open;
    root.dataset.motionState = open ? "opening" : "closing";
    if (active) active.dataset.motionState = root.dataset.motionState;
    if (open) measureRoom();
    targetHeight = open ? layoutHeight(content) : 0;
    root.style.overflow = "hidden";
    root.style.willChange = "height";
    const update = value => { if (token === revision) root.style.height = `${Math.max(0, value.height)}px`; };
    const complete = () => {
      if (token !== revision) return;
      root.hidden = !open; root.dataset.motionState = open ? "open" : "closed";
      root.style.removeProperty("height"); root.style.removeProperty("will-change"); root.style.removeProperty("overflow");
      if (open && active) active.dataset.motionState = "open";
      if (!open) { for (const panel of panels()) conceal(panel); active = null; observer?.disconnect(); observer = null; }
    };
    if (motion()) motion().spring(root, "detail-fold", { height: targetHeight }, {
      initial: { height: current }, response: .3, damping: 1, precision: .1, immediate,
      onUpdate: update, onComplete: complete,
    });
    else { update({ height: targetHeight }); complete(); }
  }
  function refresh() {
    if (!root || !active || root.inert || root.hidden) return;
    measureRoom();
    if (Math.abs(layoutHeight(content) - targetHeight) > .5) {
      const current = motion()?.snapshot(root, "detail-fold")?.active ? currentHeight() : targetHeight;
      animate(true, false, current);
    }
  }
  function set(panel, visible, immediate = false) {
    if (!panel) return;
    if (!initialize()) { panel.hidden = !visible; panel.inert = !visible; return; }
    if (!visible && panel !== active) { conceal(panel); return; }
    if (!visible) {
      panel.inert = true;
      panel.classList?.add("closing");
      observer?.disconnect(); observer = null;
      animate(false, immediate);
      return;
    }
    const changed = active !== panel, current = currentHeight();
    root.style.height = `${current}px`;
    for (const other of panels()) if (other !== panel) conceal(other);
    active = panel; panel.hidden = false; panel.inert = false; panel.classList?.remove("closing");
    if (changed) content.scrollTop = 0;
    animate(true, immediate, current);
    observer?.disconnect(); observer = null;
    if (typeof ResizeObserver !== "undefined") {
      observer = new ResizeObserver(refresh);
      for (const node of [content, root.parentElement, root.parentElement?.querySelector(".detail-header"), root.parentElement?.querySelector(".detail-primary-actions")])
        if (node) observer.observe(node);
    }
  }
  return Object.freeze({ set, refresh });
})();
