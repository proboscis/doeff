// The chrome-headless-shell Remotion renders with: DOCS_VIDEO_CHROME if set, else the one under
// PLAYWRIGHT_BROWSERS_PATH (where Playwright installs it, e.g. `npx playwright install chromium-headless-shell`).
import fs from 'node:fs';
import path from 'node:path';

export const findChrome = () => {
  if (process.env.DOCS_VIDEO_CHROME) return process.env.DOCS_VIDEO_CHROME;
  const root = process.env.PLAYWRIGHT_BROWSERS_PATH;
  if (root && fs.existsSync(root)) {
    for (const dir of fs.readdirSync(root).filter((name) => name.startsWith('chromium_headless_shell-')).sort().reverse()) {
      const shell = path.join(root, dir, 'chrome-headless-shell-linux64', 'chrome-headless-shell');
      if (fs.existsSync(shell)) return shell;
    }
  }
  throw new Error('no chrome-headless-shell: set DOCS_VIDEO_CHROME, or PLAYWRIGHT_BROWSERS_PATH to where Playwright installed it');
};
