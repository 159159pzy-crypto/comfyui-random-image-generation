/* Pure measured-row window planning. The drawer owns DOM measurement, scroll
 * anchoring and the >80-row threshold; this module never reads layout. */
(() => {
  "use strict";
  const DEFAULT_HEIGHT = 44;
  const finite = (value, fallback) => typeof value === "number" && Number.isFinite(value) ? value : fallback;

  function lowerBound(values, target) {
    let start = 0, end = values.length;
    while (start < end) {
      const middle = start + Math.floor((end - start) / 2);
      if (values[middle] < target) start = middle + 1;
      else end = middle;
    }
    return start;
  }
  function upperBound(values, target) {
    let start = 0, end = values.length;
    while (start < end) {
      const middle = start + Math.floor((end - start) / 2);
      if (values[middle] <= target) start = middle + 1;
      else end = middle;
    }
    return start;
  }

  function plan({ heights = [], viewportTop = 0, viewportHeight = 0, overscan = 8, pinned = [] } = {}) {
    const rows = Array.isArray(heights) ? heights : [];
    const count = rows.length, offsets = new Array(count + 1);
    offsets[0] = 0;
    // Browser measurements are small, but invalid caller data must not create
    // infinite spacers or nonmonotonic offsets that break the binary searches.
    const maximumHeight = Number.MAX_SAFE_INTEGER / Math.max(1, count);
    for (let index = 0; index < count; index++) {
      const value = finite(rows[index], DEFAULT_HEIGHT);
      offsets[index + 1] = offsets[index] + Math.min(maximumHeight, value > 0 ? value : DEFAULT_HEIGHT);
    }
    const totalHeight = offsets[count];
    if (!count) return { ranges: [], segments: [], totalHeight, offsets };

    const top = finite(viewportTop, 0), height = Math.max(0, finite(viewportHeight, 0));
    const clippedTop = Math.min(totalHeight, Math.max(0, top));
    const clippedEnd = Math.min(totalHeight, Math.max(0, top + height));
    const first = Math.min(count - 1, Math.max(0, upperBound(offsets, clippedTop) - 1));
    // Keep the anchor row even before the drawer has a measurable viewport or
    // while the entire list lies outside it. Never render the entire tree as a
    // zero-height fallback.
    const last = Math.min(count, Math.max(first + 1, lowerBound(offsets, clippedEnd)));
    const extra = Math.max(0, Math.min(count, Math.floor(finite(overscan, 8))));
    const candidates = [{ start: Math.max(0, first - extra), end: Math.min(count, last + extra) }];
    for (const index of Array.isArray(pinned) ? pinned : []) {
      if (Number.isInteger(index) && index >= 0 && index < count) candidates.push({ start: index, end: index + 1 });
    }
    candidates.sort((a, b) => a.start - b.start || a.end - b.end);
    const ranges = [];
    for (const candidate of candidates) {
      const previous = ranges.at(-1);
      if (previous && candidate.start <= previous.end) previous.end = Math.max(previous.end, candidate.end);
      else ranges.push({ ...candidate });
    }

    const segments = [];
    let cursor = 0;
    const append = (type, start, end) => {
      if (start < end) segments.push({ type, start, end, offset: offsets[start], height: offsets[end] - offsets[start] });
    };
    for (const range of ranges) {
      append("spacer", cursor, range.start);
      append("range", range.start, range.end);
      cursor = range.end;
    }
    append("spacer", cursor, count);
    return { ranges, segments, totalHeight, offsets };
  }

  const api = { plan };
  globalThis.HistoryFilterWindow = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})();
