// check.mjsとscreenshot.mjsが共に使う、書き出し先でzenn-cliのプレビューを起こす部品。
import { spawn } from "node:child_process";
import { readFileSync } from "node:fs";
import { createServer } from "node:net";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const zennBin = path.join(here, "node_modules/zenn-cli/dist/server/zenn.js");

/** 空いているポートを1つ借りる。 */
function freePort() {
  return new Promise((resolve, reject) => {
    const server = createServer();
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const { port } = server.address();
      server.close(() => resolve(port));
    });
  });
}

/** 書き出し先でzenn-cliのプレビューを起こし、URLを表示したらそのoriginを返す。 */
export async function startPreview(root) {
  const port = await freePort();
  const child = spawn(process.execPath, [zennBin, "preview", "--port", String(port), "--no-watch"], {
    cwd: root,
    stdio: ["ignore", "pipe", "pipe"],
  });
  const ready = new Promise((resolve, reject) => {
    let log = "";
    const watch = (chunk) => {
      log += chunk;
      if (log.includes(`localhost:${port}`)) resolve(`http://localhost:${port}`);
    };
    child.stdout.on("data", watch);
    child.stderr.on("data", watch);
    child.once("exit", (code) => reject(new Error(`zenn previewが起きずに終わりました(${code}): ${log}`)));
  });
  return { child, ready };
}

export async function getJson(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`${url}: ${response.status}`);
  return response.json();
}

/** プレビューに出る記事のslugを、名の順に返す。 */
export async function listSlugs(origin) {
  return (await getJson(`${origin}/api/articles`)).articles.map((article) => article.slug).sort();
}

export function zennVersion() {
  return JSON.parse(readFileSync(path.join(here, "node_modules/zenn-cli/package.json"), "utf8")).version;
}
