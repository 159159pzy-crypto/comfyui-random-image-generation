const test = require('node:test');
const assert = require('node:assert/strict');
const { createReadGate, createViewEpoch } = require('../static/request-state.js');

test('new reads cancel old transport and reject late completions even if abort is ignored', () => {
  const gate = createReadGate(), first = gate.next(), second = gate.next();
  assert.equal(first.signal.aborted, true); assert.equal(first.current(), false);
  assert.equal(second.signal.aborted, false); assert.equal(second.current(), true);
  gate.cancel(); assert.equal(second.current(), false); assert.equal(second.signal.aborted, true);
  const third = gate.next(); assert.equal(third.current(), true);
});
test('view tokens invalidate old UI ownership without exposing a write cancellation signal', () => {
  const view = createViewEpoch(), first = view.capture(), sameView = view.capture();
  assert.equal(first.current(), true); assert.equal(sameView.current(), true);
  assert.equal('signal' in first, false);
  view.invalidate(); view.invalidate();
  assert.equal(first.current(), false); assert.equal(view.capture().current(), true);
});
