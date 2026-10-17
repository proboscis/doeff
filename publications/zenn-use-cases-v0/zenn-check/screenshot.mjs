// 書き出したZennの記事の組を、zenn-cliのプレビューの画面としてchromiumで開き、記事ごとに全体の写し(PNG)を撮る。
//
// 使い方(書き出し先はexport_zenn.pyの出力先。chromiumはpackage.jsonのplaywrightの版の物):
//   npm ci --prefix publications/zenn-use-cases-v0/zenn-check
//   npx --prefix publications/zenn-use-cases-v0/zenn-check playwright install chromium
//   node publications/zenn-use-cases-v0/zenn-check/screenshot.mjs <書き出し先> <写しの置き場>
// agoraの会話では、chromiumを入れずに`agora-browser node …/screenshot.mjs …`で走らせる。
//
// 記事ごとに<写しの置き場>/<slug>.png(幅1280の全体)を書き、画面で数える崩れ(1つでもあれば終了コード1):
// - 検査の警告の行: プレビュー画面がzenn-modelの検査で出す警告の行(.validation-error-row)。
// - 本文のエラー: プレビューが本文の中に赤字で出すエラー([data-body-error]。無い画像はimgでなくこれになる)。
// - 読めない画像: 本文のimgを遅延読みせずに全部読み、読み終えても幅が0の物。
import { mkdirSync } from "node:fs";
import path from "node:path";
import { chromium } from "playwright";
import { listSlugs, startPreview, zennVersion } from "./preview.mjs";

const VIEWPORT = { width: 1280, height: 900 };

/** 記事1本の画面を開き、崩れを数えて全体を写す。 */
async function shoot(page, origin, slug, directory) {
  await page.goto(`${origin}/articles/${encodeURIComponent(slug)}`, { waitUntil: "networkidle" });
  await page.waitForSelector(".znc");
  const { warnings, bodyErrors, broken, images } = await page.evaluate(async () => {
    const all = [...document.querySelectorAll(".znc img")];
    for (const image of all) image.loading = "eager";
    await Promise.all(
      all.map((image) =>
        image.complete
          ? null
          : new Promise((resolve) => {
              image.addEventListener("load", resolve, { once: true });
              image.addEventListener("error", resolve, { once: true });
            }),
      ),
    );
    await document.fonts.ready;
    return {
      warnings: [...document.querySelectorAll(".validation-error-row")].map((row) => row.textContent.trim()),
      bodyErrors: [...document.querySelectorAll(".znc [data-body-error]")].map((error) => error.textContent.trim()),
      broken: all.filter((image) => image.naturalWidth === 0).map((image) => image.getAttribute("src")),
      images: all.length,
    };
  });
  const file = path.join(directory, `${slug}.png`);
  await page.screenshot({ path: file, fullPage: true });
  const height = await page.evaluate(() => document.documentElement.scrollHeight);
  return { slug, warnings, bodyErrors, broken, images, file, height };
}

async function main() {
  const [rootArgument, directoryArgument] = process.argv.slice(2);
  if (!rootArgument || !directoryArgument) {
    console.error("使い方: node screenshot.mjs <書き出し先> <写しの置き場>");
    process.exit(2);
  }
  const directory = path.resolve(directoryArgument);
  mkdirSync(directory, { recursive: true });
  const { child, ready } = await startPreview(path.resolve(rootArgument));
  const browser = await chromium.launch();
  try {
    const origin = await ready;
    const page = await browser.newPage({ viewport: VIEWPORT });
    let total = 0;
    const slugs = await listSlugs(origin);
    for (const slug of slugs) {
      const result = await shoot(page, origin, slug, directory);
      const problems = [
        ...result.warnings.map((warning) => `検査の警告: ${warning}`),
        ...result.bodyErrors.map((error) => `本文のエラー: ${error}`),
        ...result.broken.map((src) => `読めない画像: ${src}`),
      ];
      total += problems.length;
      console.log(
        `${result.slug}: 検査の警告の行 ${result.warnings.length}・本文のエラー ${result.bodyErrors.length}・` +
          `画像 ${result.images}枚中の読めない ` +
          `${result.broken.length}・写し ${result.file}(${VIEWPORT.width}×${result.height})`,
      );
      for (const problem of problems) console.log(`  - ${problem}`);
    }
    console.log(
      `記事 ${slugs.length}本・崩れ ${total}個(zenn-cli ${zennVersion()}・chromium ${browser.version()})`,
    );
    process.exitCode = total === 0 ? 0 : 1;
  } finally {
    await browser.close();
    child.kill();
  }
}

await main();
