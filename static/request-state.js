/* Read cancellation and view ownership are separate: invalidating a view must
 * never imply cancelling a write that the server may already have committed. */
(() => {
  "use strict";
  function createViewEpoch() {
    let revision = 0;
    return {
      capture() { const captured = revision; return { current: () => captured === revision }; },
      invalidate() { revision++; },
    };
  }
  function createReadGate() {
    const epoch = createViewEpoch();
    let controller;
    const cancel = () => { epoch.invalidate(); controller?.abort(); controller = null; };
    return {
      next() {
        cancel();
        controller = typeof AbortController === "undefined" ? null : new AbortController();
        return { ...epoch.capture(), signal: controller?.signal };
      },
      cancel,
    };
  }
  const api = { createViewEpoch, createReadGate };
  globalThis.StudioRequestState = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})();
