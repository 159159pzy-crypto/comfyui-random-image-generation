const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const app = fs.readFileSync(path.join(__dirname, '../../static/app.js'), 'utf8');

// Isolated feature tests load the shared adapters, without booting the app or
// starting its network polling. The runtime itself has a deterministic clock suite.
module.exports = function createFrontendContext(...args) {
  const context = vm.createContext(...args);
  for (const name of ['motionDialog', 'setProgressValue', 'normalizeTriggerWords', 'triggerIdentity']) {
    const start = app.indexOf(`function ${name}(`);
    vm.runInContext(app.slice(start, app.indexOf('\n}', start) + 2), context);
  }
  return context;
};
