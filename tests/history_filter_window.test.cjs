const test = require('node:test');
const assert = require('node:assert/strict');
const { plan } = require('../static/history-filter-window.js');

function covers(result, index) {
  return result.ranges.some(range => range.start <= index && index < range.end);
}
function assertPartition(result, count) {
  assert.equal(result.offsets.length, count + 1);
  assert.equal(result.offsets[0], 0);
  assert.equal(result.offsets[count], result.totalHeight);
  assert.ok(Number.isFinite(result.totalHeight));
  let cursor = 0, total = 0;
  for (const segment of result.segments) {
    assert.equal(segment.start, cursor, 'no row gaps or overlapping segments');
    assert.ok(segment.end > segment.start && segment.end <= count);
    assert.equal(segment.offset, result.offsets[segment.start]);
    assert.equal(segment.height, result.offsets[segment.end] - result.offsets[segment.start]);
    assert.ok(segment.height > 0 && Number.isFinite(segment.height));
    cursor = segment.end;
    total += segment.height;
  }
  assert.equal(cursor, count);
  assert.ok(Math.abs(total - result.totalHeight) < 0.00001, 'spacers and rendered ranges preserve full height');
  for (let i = 1; i < result.ranges.length; i++) assert.ok(result.ranges[i - 1].end < result.ranges[i].start);
}

test('1963 siblings render only the viewport and eight rows on each side', () => {
  const result = plan({ heights: Array(1963).fill(44), viewportTop: 1000 * 44, viewportHeight: 440 });
  assert.deepEqual(result.ranges, [{ start: 992, end: 1018 }]);
  assert.equal(result.totalHeight, 86372);
  assert.deepEqual(result.segments.map(({ type, height }) => [type, height]), [
    ['spacer', 43648], ['range', 1144], ['spacer', 41580],
  ]);
  assertPartition(result, 1963);
});

test('exact row boundaries do not include the next row without overscan', () => {
  const heights = [20, 80, 15, 100];
  assert.deepEqual(plan({ heights, viewportTop: 20, viewportHeight: 80, overscan: 0 }).ranges, [{ start: 1, end: 2 }]);
  assert.deepEqual(plan({ heights, viewportTop: 19.5, viewportHeight: 81, overscan: 0 }).ranges, [{ start: 0, end: 3 }]);
});

test('new measured expansion heights move the visible window without fixed-height assumptions', () => {
  const heights = Array(200).fill(44);
  const before = plan({ heights, viewportTop: 4400, viewportHeight: 88, overscan: 0 });
  heights[5] = 444;
  const after = plan({ heights, viewportTop: 4400, viewportHeight: 88, overscan: 0 });
  assert.deepEqual(before.ranges, [{ start: 100, end: 102 }]);
  assert.deepEqual(after.ranges, [{ start: 90, end: 93 }]);
  assert.equal(after.totalHeight - before.totalHeight, 400);
  assertPartition(after, 200);
});

test('an expanded row taller than the viewport remains the only required visible row', () => {
  const result = plan({ heights: [44, 3000, 44, 44], viewportTop: 500, viewportHeight: 600, overscan: 0 });
  assert.deepEqual(result.ranges, [{ start: 1, end: 2 }]);
  assertPartition(result, 4);
});

test('far-away focused rows survive as separate ranges and middle spacers', () => {
  const result = plan({ heights: Array(1963).fill(44), viewportTop: 44000, viewportHeight: 440, pinned: [3, 3, 1600] });
  assert.deepEqual(result.ranges, [{ start: 3, end: 4 }, { start: 992, end: 1018 }, { start: 1600, end: 1601 }]);
  assert.equal(result.segments.filter(item => item.type === 'spacer').length, 4);
  assert.equal(result.ranges.reduce((sum, range) => sum + range.end - range.start, 0), 28);
  assertPartition(result, 1963);
});

test('pinned rows merge only when adjacent or already inside the active range', () => {
  const result = plan({ heights: Array(30).fill(10), viewportTop: 100, viewportHeight: 20, overscan: 0,
    pinned: [9, 12, 13, 11, -1, 30, NaN, 3.5, 1] });
  assert.deepEqual(result.ranges, [{ start: 1, end: 2 }, { start: 9, end: 14 }]);
  assertPartition(result, 30);
});

test('zero-height first layout keeps a bounded nearby window and never mounts all siblings', () => {
  const result = plan({ heights: Array(1963).fill(44), viewportTop: 44000, viewportHeight: 0 });
  assert.deepEqual(result.ranges, [{ start: 992, end: 1009 }]);
  assertPartition(result, 1963);
});

test('scroll beyond either edge clamps to a bounded edge window', () => {
  const heights = Array(100).fill(44);
  assert.deepEqual(plan({ heights, viewportTop: -10000, viewportHeight: 100 }).ranges, [{ start: 0, end: 9 }]);
  assert.deepEqual(plan({ heights, viewportTop: 10000, viewportHeight: 100 }).ranges, [{ start: 91, end: 100 }]);
  assert.deepEqual(plan({ heights, viewportTop: -44, viewportHeight: 88, overscan: 0 }).ranges, [{ start: 0, end: 1 }]);
});

test('missing measurements use 44px and invalid viewport values remain finite', () => {
  const result = plan({ heights: [undefined, NaN, -1, 0, Infinity, 12.5], viewportTop: NaN,
    viewportHeight: Infinity, overscan: NaN, pinned: [Infinity] });
  assert.deepEqual(result.offsets, [0, 44, 88, 132, 176, 220, 232.5]);
  assertPartition(result, 6);
  const extreme = plan({ heights: [Number.MAX_VALUE, Number.MAX_VALUE], viewportTop: Number.MAX_VALUE,
    viewportHeight: Number.MAX_VALUE });
  assertPartition(extreme, 2);
});

test('empty and single-row lists have complete well-formed partitions', () => {
  assert.deepEqual(plan(), { ranges: [], segments: [], offsets: [0], totalHeight: 0 });
  assert.deepEqual(plan({ heights: null }), plan());
  const result = plan({ heights: [90], viewportTop: 500, viewportHeight: 0 });
  assert.deepEqual(result.ranges, [{ start: 0, end: 1 }]);
  assertPartition(result, 1);
});

test('window planning does not mutate measurement or focus caches', () => {
  const heights = Object.freeze([10, 20, 40]), pinned = Object.freeze([2, 0]);
  plan({ heights, pinned, viewportTop: 15, viewportHeight: 20, overscan: 0 });
  assert.deepEqual(heights, [10, 20, 40]);
  assert.deepEqual(pinned, [2, 0]);
});

test('mixed-height scrolling preserves every visible row and the complete spacer partition', () => {
  const heights = Array.from({ length: 1963 }, (_, index) => index % 31 === 0 ? 500 : 30 + index % 25);
  let total = heights.reduce((sum, height) => sum + height, 0);
  for (let step = 0; step < 50; step++) {
    const top = (total - 600) * step / 49;
    const result = plan({ heights, viewportTop: top, viewportHeight: 600, pinned: [17, 1800] });
    let position = 0;
    for (let index = 0; index < heights.length; index++) {
      if (position < top + 600 && position + heights[index] > top) assert.ok(covers(result, index));
      position += heights[index];
    }
    assert.ok(covers(result, 17)); assert.ok(covers(result, 1800));
    assertPartition(result, heights.length);
  }
});
