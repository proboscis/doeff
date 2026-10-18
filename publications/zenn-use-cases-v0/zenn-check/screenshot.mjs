// 書き出したZennの記事と本の組を、zenn-cliのプレビューの画面としてchromiumで開き、画面ごとに全体の写し(PNG)を撮る。
//
// 使い方(書き出し先はexport_zenn.pyとexport_zenn_book.pyの出力先。chromiumはpackage.jsonのplaywrightの版の物):
//   npm ci --prefix publications/zenn-use-cases-v0/zenn-check
//   npx --prefix publications/zenn-use-cases-v0/zenn-check playwright install chromium
//   node publications/zenn-use-cases-v0/zenn-check/screenshot.mjs <書き出し先> <写しの置き場>
// agoraの会話では、chromiumを入れずに`agora-browser node …/screenshot.mjs …`で走らせる。
//
// 記事ごとに<写しの置き場>/<slug>.png、本ごとに<本のslug>.png(表紙と設定の画面)、章ごとに
// <本のslug>--<章のslug>.png(どれも幅1280の全体)を書き、画面で数える崩れ(1つでもあれば終了コード1):
// - 検査の警告の行: プレビュー画面がzenn-modelの検査で出す警告の行(.validation-error-row)。
// - 本文のエラー: プレビューが本文の中に赤字で出すエラー([data-body-error]。無い画像はimgでなくこれになる)。
// - 読めない画像: 本文のimgと本の表紙を遅延読みせずに全部読み、読み終えても幅が0の物。
import { mkdirSync } from "node:fs";
import path from "node:path";
import { chromium } from "playwright";
import { getJson, listSlugs, startPreview, zennVersion } from "./preview.mjs";

const VIEWPORT = { width: 1280, height: 900 };

/** 章の画面の道に置く名。プレビューの画面と同じく、file名の「.」を%2Eにしてから符号化する。 */
function chapterPath(filename) {
  return encodeURIComponent(filename.replace(/\./g, "%2E"));
}

/** 記事・本・章の画面1つを開き、崩れを数えて全体を写す。readyは開き終えた印の要素。 */
async function shoot(page, origin, target, directory) {
  await page.goto(`${origin}${target.path}`, { waitUntil: "networkidle" });
  await page.waitForSelector(target.ready);
  const { warnings, bodyErrors, broken, images } = await page.evaluate(async () => {
    const all = [...document.querySelectorAll(".znc img, .book-header__cover-img")];
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
      bodyErrors: [...document.querySelectorAll(".znc [data-body-error], .error-message")].map((error) =>
        error.textContent.trim(),
      ),
      broken: all.filter((image) => image.naturalWidth === 0).map((image) => image.getAttribute("src")),
      images: all.length,
    };
  });
  const file = path.join(directory, `${target.name}.png`);
  await page.screenshot({ path: file, fullPage: true });
  const height = await page.evaluate(() => document.documentElement.scrollHeight);
  return { name: target.name, warnings, bodyErrors, broken, images, file, height };
}

/** プレビューに出る記事・本・章の画面を、写す順に並べる。 */
async function listTargets(origin) {
  const articles = (await listSlugs(origin)).map((slug) => ({
    name: slug,
    path: `/articles/${encodeURIComponent(slug)}`,
    ready: ".znc",
  }));
  const books = (await getJson(`${origin}/api/books`)).books.map((book) => book.slug).sort();
  const pages = await Promise.all(
    books.map(async (slug) => {
      const { chapters } = await getJson(`${origin}/api/books/${encodeURIComponent(slug)}/chapters`);
      return [
        { name: slug, path: `/books/${encodeURIComponent(slug)}`, ready: ".layout__main" },
        ...chapters.map((chapter) => ({
          name: `${slug}--${chapter.slug}`,
          path: `/books/${encodeURIComponent(slug)}/${chapterPath(chapter.filename)}`,
          ready: ".znc, .error-message",
        })),
      ];
    }),
  );
  return { articles: articles.length, books: books.length, targets: [...articles, ...pages.flat()] };
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
    const { articles, books, targets } = await listTargets(origin);
    for (const target of targets) {
      const result = await shoot(page, origin, target, directory);
      const problems = [
        ...result.warnings.map((warning) => `検査の警告: ${warning}`),
        ...result.bodyErrors.map((error) => `本文のエラー: ${error}`),
        ...result.broken.map((src) => `読めない画像: ${src}`),
      ];
      total += problems.length;
      console.log(
        `${result.name}: 検査の警告の行 ${result.warnings.length}・本文のエラー ${result.bodyErrors.length}・` +
          `画像 ${result.images}枚中の読めない ` +
          `${result.broken.length}・写し ${result.file}(${VIEWPORT.width}×${result.height})`,
      );
      for (const problem of problems) console.log(`  - ${problem}`);
    }
    console.log(
      `記事 ${articles}本・本 ${books}冊(画面 ${targets.length - articles})・崩れ ${total}個` +
        `(zenn-cli ${zennVersion()}・chromium ${browser.version()})`,
    );
    process.exitCode = total === 0 ? 0 : 1;
  } finally {
    await browser.close();
    child.kill();
  }
}

await main();
