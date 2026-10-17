// 書き出したZennの記事の組(articles/とimages/)を、zenn-cliのプレビューとzenn-modelの検査で読み、崩れを数える。
//
// 使い方(書き出し先はexport_zenn.pyの出力先):
//   npm ci --prefix publications/zenn-use-cases-v0/zenn-check
//   node publications/zenn-use-cases-v0/zenn-check/check.mjs <書き出し先> [--online]
//
// 数える崩れ(1つでもあれば終了コード1):
// - front matterと本文の誤り: プレビューのサーバーが返す記事を、プレビュー画面と同じzenn-modelの
//   validateArticleで検査する(slug・title・emoji・type・topics・published・本文のエラー)。
// - 画像の欠け: Markdownの画像の数とimgの数を比べ、/images/...をプレビューのサーバーから読む。
// - リンク切れ: 記事内の#…は本文のidを探す。公開先から開けないパス(相対パスと/…)は切れと数える。
//   --onlineの時は外のURLを開いて状態を確かめ、GitHubのURLの#…は見出しのidを探す。
// - Markdownの崩れ: コードの外に「](」「![」「:::」「**」「`」が文字のまま残っていないか。
//   「**」は、日本語の括弧に接した太字(例 **「語」**です)をZennが太字にしない時に残る。
// - 秘密の形: zenn-cliが使うsecretlintの推奨の規則で、記事のMarkdownを読む。
import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { createEngine } from "@secretlint/node";
import { load } from "cheerio";
import zennModel from "zenn-model";
import { getJson, listSlugs, startPreview, zennVersion } from "./preview.mjs";

const markdownImage = /!\[[^\]]*\]\([^)\s]+\)/g;

/** 外のURLを開き、切れていればその訳を返す(GitHubの#…は見出しのidも探す)。 */
async function externalProblem(href, pages) {
  const url = new URL(href);
  const page = url.href.replace(/#.*$/, "");
  if (!pages.has(page)) {
    pages.set(
      page,
      fetch(page, { redirect: "follow", headers: { "user-agent": "doeff-zenn-check" } })
        .then(async (response) => ({ status: response.status, text: await response.text() }))
        .catch((error) => ({ status: 0, text: String(error) })),
    );
  }
  const { status, text } = await pages.get(page);
  if (status >= 400 || status === 0) return `${href}(状態 ${status})`;
  const anchor = decodeURIComponent(url.hash.slice(1));
  if (anchor && url.hostname === "github.com" && !text.includes(`user-content-${anchor}`)) {
    return `${href}(見出し#${anchor}が無い)`;
  }
  return null;
}

/** 記事1本を読み、崩れを種類ごとに数える。 */
async function checkArticle(origin, root, slug, options) {
  const { article } = await getJson(`${origin}/api/articles/${encodeURIComponent(slug)}`);
  const markdown = readFileSync(path.join(root, "articles", `${slug}.md`), "utf8");
  const errors = zennModel.validateArticle(article).map((error) => `${error.type}: ${error.message}`);
  const $ = load(article.bodyHtml);

  const sources = $("img").map((_, element) => $(element).attr("src")).get();
  const expectedImages = (markdown.match(markdownImage) ?? []).length;
  const imageProblems = await Promise.all(
    sources.map(async (src) => {
      if (!src.startsWith("/images/")) return `${src}(/images/の外)`;
      const response = await fetch(`${origin}${src}`);
      const type = response.headers.get("content-type") ?? "";
      return response.ok && type.startsWith("image/") ? null : `${src}(状態 ${response.status}・${type})`;
    }),
  );
  if (sources.length !== expectedImages) {
    imageProblems.push(`Markdownの画像${expectedImages}枚に対し、imgが${sources.length}個`);
  }

  // 見出しの印(header-anchor-link)は自分の見出しを指すので、本文のリンクから外す。
  const hrefs = $("a[href]")
    .not(".header-anchor-link")
    .map((_, element) => $(element).attr("href"))
    .get();
  const ids = new Set($("[id]").map((_, element) => $(element).attr("id")).get());
  const linkProblems = await Promise.all(
    hrefs.map(async (href) => {
      if (href.startsWith("#")) return ids.has(href.slice(1)) ? null : `${href}(記事内に無い)`;
      if (!/^https?:\/\//.test(href)) return `${href}(公開先から開けない)`;
      return options.online ? externalProblem(href, options.pages) : null;
    }),
  );

  $("pre, code").remove();
  const leftovers = ["](", "![", ":::", "**", "`"].filter((mark) => $.root().text().includes(mark));
  const secrets = await options.secretlint.executeOnContent({ content: markdown, filePath: `${slug}.md` });

  return {
    slug,
    title: article.title,
    published: article.published,
    errors,
    images: sources.length,
    imageProblems: imageProblems.filter(Boolean),
    links: hrefs.length,
    linkProblems: linkProblems.filter(Boolean),
    leftovers: leftovers.map((mark) => `「${mark}」が文字のまま残っている`),
    secrets: secrets.ok ? [] : [secrets.output],
  };
}

async function main() {
  const [rootArgument, ...flags] = process.argv.slice(2);
  if (!rootArgument) {
    console.error("使い方: node check.mjs <書き出し先> [--online]");
    process.exit(2);
  }
  const root = path.resolve(rootArgument);
  const files = readdirSync(path.join(root, "articles")).filter((name) => name.endsWith(".md"));
  const { child, ready } = await startPreview(root);
  try {
    const origin = await ready;
    const listed = await listSlugs(origin);
    const secretlint = await createEngine({
      configFileJSON: { rules: [{ id: "@secretlint/secretlint-rule-preset-recommend" }] },
      formatter: "compact",
      color: false,
      maskSecrets: true,
    });
    const options = { online: flags.includes("--online"), pages: new Map(), secretlint };
    const results = await Promise.all(listed.map((slug) => checkArticle(origin, root, slug, options)));
    const missing = files.filter((name) => !listed.includes(name.replace(/\.md$/, "")));

    let total = missing.length;
    if (missing.length) console.log(`プレビューに出ない記事: ${missing.join(", ")}`);
    for (const result of results) {
      const problems = [
        ...result.errors,
        ...result.imageProblems,
        ...result.linkProblems,
        ...result.leftovers,
        ...result.secrets,
      ];
      total += problems.length;
      console.log(
        `${result.slug}: published ${result.published}・検査の誤り ${result.errors.length}・` +
          `画像 ${result.images}枚中の欠け ${result.imageProblems.length}・` +
          `リンク ${result.links}個中の切れ ${result.linkProblems.length}` +
          `(外のURL ${options.online ? "開いた" : "開いていない"})・` +
          `Markdownの崩れ ${result.leftovers.length}・秘密の形 ${result.secrets.length}`,
      );
      for (const problem of problems) console.log(`  - ${problem}`);
    }
    console.log(`記事 ${results.length}本・崩れ ${total}個(zenn-cli ${zennVersion()})`);
    process.exitCode = total === 0 ? 0 : 1;
  } finally {
    child.kill();
  }
}

await main();
