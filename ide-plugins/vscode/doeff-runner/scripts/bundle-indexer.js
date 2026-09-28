// vsix を作る前に、同梱の doeff-indexer を bin/ に置く(vscode:prepublish から呼ぶ)。
//
// 何が壊れていたか: bin/ の binary は git に入っておらず(CI が置く)、手元で `vsce package` すると
// 前に誰かが手で置いた binary があればそれを、無ければ binary の無い vsix を黙って作っていた。
// 2026-09-28 の 0.6.24 は binary の無い vsix で、Hy の索引が作れず「タグで閲覧」が空になった。
//
// 手元: 同じ checkout の packages/doeff-indexer を cargo で組み、この機体の名前で bin/ に置く
//       (前に置いた binary を使い回さない — 拡張と道具は同じ commit から作る)。
// CI(GITHUB_ACTIONS): 全部の platform の binary が既に置かれていることだけを確かめる。
// どちらでも、置けない・足りない時は失敗で止める(binary の無い vsix を作らない)。

const { execFileSync } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');

const EXTENSION_DIR = path.join(__dirname, '..');
const BIN_DIR = path.join(EXTENSION_DIR, 'bin');
const INDEXER_DIR = path.join(EXTENSION_DIR, '..', '..', '..', 'packages', 'doeff-indexer');
const ALL_BINARIES = [
  'doeff-indexer-darwin-x64',
  'doeff-indexer-darwin-arm64',
  'doeff-indexer-linux-x64',
  'doeff-indexer-linux-arm64',
  'doeff-indexer-windows-x64.exe'
];

/** この機体の binary の名(extension.ts の getBundledIndexerPath と同じ対応)。 */
function hostBinaryName() {
  const arm = process.arch === 'arm64';
  switch (process.platform) {
    case 'darwin':
      return arm ? 'doeff-indexer-darwin-arm64' : 'doeff-indexer-darwin-x64';
    case 'linux':
      return arm ? 'doeff-indexer-linux-arm64' : 'doeff-indexer-linux-x64';
    case 'win32':
      return 'doeff-indexer-windows-x64.exe';
    default:
      throw new Error(`同梱の doeff-indexer の無い platform: ${process.platform}/${process.arch}`);
  }
}

/** binary が hy-index を知っていることを確かめる(知らなければ投げる)。 */
function verifyHyIndex(binary) {
  execFileSync(binary, ['hy-index', '--help'], { stdio: 'ignore' });
}

/** 拡張が読む索引の契約の版(src/hy/contract.ts の HY_INDEX_CONTRACT_VERSION — 正本はそこ 1 か所)。 */
function expectedContractVersion() {
  const source = fs.readFileSync(path.join(EXTENSION_DIR, 'src', 'hy', 'contract.ts'), 'utf8');
  const found = /export const HY_INDEX_CONTRACT_VERSION = (\d+);/.exec(source);
  if (found === null) {
    throw new Error('src/hy/contract.ts に HY_INDEX_CONTRACT_VERSION が無い');
  }
  return Number(found[1]);
}

/**
 * 同梱する binary の索引の契約の版が、拡張の読む版と同じことを確かめる — 版が違うと拡張は索引を丸ごと捨て、読む面・「タグで閲覧」・
 * 移動が空になる(版 4 の U7・版 5 の U8 で、binary と拡張は同時に更新が要る)。小さな Hy の file を 1 つ索引にして版を読む。
 */
function verifyContractVersion(binary) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'bundle-indexer-'));
  try {
    fs.writeFileSync(path.join(root, 'probe.hy'), '(defn probe [] 1)\n');
    const out = execFileSync(binary, ['hy-index', '--root', root], { encoding: 'utf8', maxBuffer: 16 * 1024 * 1024 });
    const version = JSON.parse(out).version;
    const expected = expectedContractVersion();
    if (version !== expected) {
      throw new Error(`同梱の doeff-indexer の索引の契約は版 ${version}、拡張が読むのは版 ${expected}(同じ commit から組むこと)`);
    }
    console.log(`[bundle-indexer] 索引の契約の版 ${version} が拡張と合っている`);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
}

function verifyPrebuilt() {
  const missing = ALL_BINARIES.filter((name) => !fs.existsSync(path.join(BIN_DIR, name)));
  if (missing.length > 0) {
    throw new Error(`CI の vsix に同梱の doeff-indexer が足りない: ${missing.join(', ')}`);
  }
  // この機体で走る binary だけは版まで確かめる(他の platform の binary は走らせられない)
  const host = path.join(BIN_DIR, hostBinaryName());
  if (fs.existsSync(host)) {
    verifyContractVersion(host);
  }
  console.log(`[bundle-indexer] CI: 同梱の doeff-indexer ${ALL_BINARIES.length} 本を確かめた`);
}

function buildHost() {
  if (!fs.existsSync(path.join(INDEXER_DIR, 'Cargo.toml'))) {
    throw new Error(`doeff-indexer の source が無い: ${INDEXER_DIR}`);
  }
  console.log(`[bundle-indexer] cargo build --release --no-default-features (${INDEXER_DIR})`);
  execFileSync('cargo', ['build', '--release', '--no-default-features'], { cwd: INDEXER_DIR, stdio: 'inherit' });
  const exe = process.platform === 'win32' ? 'doeff-indexer.exe' : 'doeff-indexer';
  const built = path.join(INDEXER_DIR, 'target', 'release', exe);
  verifyHyIndex(built);
  const dest = path.join(BIN_DIR, hostBinaryName());
  fs.mkdirSync(BIN_DIR, { recursive: true });
  // 同じ dir の一時 file へ写してから rename(途中で止まっても壊れた binary を残さない)
  const tmp = `${dest}.tmp-${process.pid}`;
  fs.copyFileSync(built, tmp);
  fs.chmodSync(tmp, 0o755);
  fs.renameSync(tmp, dest);
  verifyHyIndex(dest);
  verifyContractVersion(dest);
  console.log(`[bundle-indexer] 同梱の doeff-indexer を置いた: ${dest}`);
}

try {
  if (process.env.GITHUB_ACTIONS === 'true') {
    verifyPrebuilt();
  } else {
    buildHost();
  }
} catch (error) {
  console.error(`[bundle-indexer] 失敗 — doeff-indexer の無い vsix は作らない: ${error instanceof Error ? error.message : String(error)}`);
  process.exit(1);
}
