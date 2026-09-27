// Runs the pure unit tests (modules that do not import `vscode`) with mocha's API.
// Why not the mocha CLI: the bundled mocha's yargs fails to load on Node >= 22 (ESM require error),
// and `vscode-test` needs a downloaded VS Code; these tests need neither.
const path = require('path');
const fs = require('fs');
const Mocha = require('mocha');

const mocha = new Mocha({ ui: 'tdd' });
const testRoot = path.join(__dirname, '..', 'out', 'test');

// Collect every compiled *.test.js below out/test.
function collect(dir) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      collect(full);
    } else if (entry.name.endsWith('.test.js')) {
      mocha.addFile(full);
    }
  }
}

collect(testRoot);
mocha.run((failures) => {
  process.exitCode = failures ? 1 : 0;
});
