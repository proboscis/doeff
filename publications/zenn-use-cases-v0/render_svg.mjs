// SVGを正本にした図を、記事に載せるPNGへ描き直す。
// 使い方: playwright(1.62.1)を入れたdirで `node <このfile> <入力.svg> <出力.png> [倍率]`。
// playwrightは、このfileではなく、走らせたdirから読む。倍率の既定は2(幅600のSVGが幅1200のPNGになる)。
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";

const require = createRequire(`${process.cwd()}/`);
const { chromium } = require("playwright");
const [source, target, scaleText = "2"] = process.argv.slice(2);
if (!source || !target) {
  throw new Error("使い方: node render_svg.mjs <入力.svg> <出力.png> [倍率]");
}
const svg = readFileSync(source, "utf8");
const box = svg.match(/viewBox="0 0 (\d+) (\d+)"/);
if (!box) {
  throw new Error(`${source}: viewBox="0 0 幅 高さ" が無い`);
}
const [width, height] = [Number(box[1]), Number(box[2])];
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width, height }, deviceScaleFactor: Number(scaleText) });
await page.setContent(`<!doctype html><html><body style="margin:0;background:#fff">${svg}</body></html>`);
await page.evaluate(() => document.fonts.ready);
await page.locator("svg").screenshot({ path: target, omitBackground: false });
await browser.close();
console.log(`${target}: ${width * Number(scaleText)}x${height * Number(scaleText)}`);
