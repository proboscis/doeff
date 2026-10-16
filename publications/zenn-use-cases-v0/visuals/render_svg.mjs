// 手で書いたSVGの図を、Zennへ載せるPNGにする(白背景・指定した幅と高さで切り出す)。
// 使い方: playwright 1.62.1 を入れたdirで `node render_svg.mjs <svg> <png> <幅> <高さ>`。
// 2026-10-16 に time-concept と time-flow を 1200px 幅で描いた(日本語の字形は IPA ゴシック)。
import { chromium } from 'playwright';
import { readFileSync } from 'node:fs';

const [, , svgPath, pngPath, width, height] = process.argv;
const browser = await chromium.launch();
const page = await browser.newPage({
  viewport: { width: Number(width), height: Number(height) },
  deviceScaleFactor: 1,
});
const svg = readFileSync(svgPath, 'utf8');
await page.setContent(
  `<!doctype html><html><head><style>html,body{margin:0;padding:0;background:#fff}</style></head><body>${svg}</body></html>`,
);
await page.waitForTimeout(300);
await page.screenshot({ path: pngPath, clip: { x: 0, y: 0, width: Number(width), height: Number(height) } });
await browser.close();
console.log('wrote', pngPath);
