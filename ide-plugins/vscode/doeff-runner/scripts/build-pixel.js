// pixel art の icon の生成物を元の定義(resources/pixel/glyphs.json)から作る — SVG・PNG・icon 字体(woff)・拡張の icon・
// activity bar の輪郭・package.json の contributes.icons。中身は out/pixel/ の純粋な関数が作り、ここは file へ書くか、
// 書いてある物との食い違いを検める(--check)だけ。見本の HTML は --preview <path> に書く(repo には置かない)。
//
// 使い方(compile の後):
//   node scripts/build-pixel.js                 生成物を書く
//   node scripts/build-pixel.js --check         書いてある生成物が元の定義と食い違えば終了コード 1
//   node scripts/build-pixel.js --preview out/pixel/preview.html
const fs = require('fs');
const path = require('path');
const svg2ttf = require('svg2ttf');
const ttf2woff = require('ttf2woff');

const ROOT = path.join(__dirname, '..');
const { parseGlyphSet } = require(path.join(ROOT, 'out', 'pixel', 'glyphs.js'));
const { allGlyphs, assetFiles, flagReport, PIXEL_DIR } = require(path.join(ROOT, 'out', 'pixel', 'build.js'));
const { svgFont, iconContributions, FONT_PATH } = require(path.join(ROOT, 'out', 'pixel', 'font.js'));
const { previewHtml } = require(path.join(ROOT, 'out', 'pixel', 'sheet.js'));

/** 引数を読む(知らない引数は理由を出して止める)。 */
function parseArgs(argv) {
  const args = { check: false, preview: null };
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === '--check') {
      args.check = true;
    } else if (argv[i] === '--preview' && i + 1 < argv.length) {
      args.preview = argv[++i];
    } else {
      throw new Error(`知らない引数: ${argv[i]}`);
    }
  }
  return args;
}

/** SVG 字体を woff にする(時刻を 0 に固定して、同じ元から同じ byte 列にする)。 */
function woffOf(glyphs) {
  const ttf = svg2ttf(svgFont(glyphs), { ts: 0, version: '1.0', description: 'doeff-runner pixel icons' });
  return Buffer.from(ttf2woff(new Uint8Array(ttf.buffer)));
}

/** package.json の contributes.icons だけを差し替えた本文(他の欄と並びは変えない)。 */
function packageJsonWith(glyphs) {
  const file = path.join(ROOT, 'package.json');
  const pkg = JSON.parse(fs.readFileSync(file, 'utf8'));
  pkg.contributes.icons = iconContributions(glyphs);
  return `${JSON.stringify(pkg, null, 2)}\n`;
}

/** 元の定義を読み、旗の重複を検算してから、生成物を書くか食い違いを検める。 */
function main() {
  const args = parseArgs(process.argv.slice(2));
  const source = fs.readFileSync(path.join(ROOT, PIXEL_DIR, 'glyphs.json'), 'utf8');
  const parsed = parseGlyphSet(source);
  if (parsed.tag !== 'ok') {
    console.error(`glyphs.json を読めない: ${parsed.reason}`);
    process.exit(1);
  }
  const set = parsed.set;
  const glyphs = allGlyphs(set);
  const report = flagReport(set);
  console.log(`service の旗: ${report.choices.length} 本・重複 ${report.collisions.length} 組`);
  for (const c of report.choices) {
    console.log(`  ${c.service.padEnd(16)} ${c.pattern.padEnd(10)} ${c.primary.toString(16)} / ${c.secondary.toString(16)}`);
  }
  if (report.collisions.length > 0) {
    for (const c of report.collisions) {
      console.error(`  重複: ${c.services.join(' と ')}(${c.key})— glyphs.json の serviceFlags.salt を変える`);
    }
    process.exit(1);
  }
  const woff = woffOf(glyphs);
  const files = assetFiles(set);
  files.set(FONT_PATH, woff);
  files.set('package.json', packageJsonWith(glyphs));
  if (args.preview !== null) {
    fs.mkdirSync(path.dirname(path.resolve(args.preview)), { recursive: true });
    fs.writeFileSync(path.resolve(args.preview), previewHtml(set, woff));
    console.log(`見本: ${path.resolve(args.preview)}`);
  }
  const stale = [];
  for (const [relative, body] of files) {
    const target = path.join(ROOT, relative);
    const bytes = typeof body === 'string' ? Buffer.from(body, 'utf8') : body;
    const current = fs.existsSync(target) ? fs.readFileSync(target) : null;
    if (current !== null && current.equals(bytes)) {
      continue;
    }
    if (args.check) {
      stale.push(relative);
      continue;
    }
    fs.mkdirSync(path.dirname(target), { recursive: true });
    fs.writeFileSync(target, bytes);
  }
  // 元の定義から消えた icon の生成物が残っていないか(消すのは人が決める — ここでは名前を出すだけ)
  const expected = new Set([...files.keys()].map((r) => path.normalize(r)));
  const leftovers = [];
  for (const dir of ['svg/16', 'svg/8', 'png/16', 'png/8']) {
    const full = path.join(ROOT, PIXEL_DIR, dir);
    if (!fs.existsSync(full)) {
      continue;
    }
    for (const name of fs.readdirSync(full)) {
      const relative = path.normalize(path.join(PIXEL_DIR, dir, name));
      if (!expected.has(relative)) {
        leftovers.push(relative);
      }
    }
  }
  if (leftovers.length > 0) {
    console.error(`元の定義に無い生成物: ${leftovers.join(', ')}`);
  }
  if (args.check) {
    if (stale.length > 0 || leftovers.length > 0) {
      console.error(`生成物が元の定義と食い違う(${stale.length} file): ${stale.slice(0, 10).join(', ')}${stale.length > 10 ? ' …' : ''}`);
      console.error('npm run pixel で作り直す');
      process.exit(1);
    }
    console.log(`生成物は元の定義どおり(${files.size} file・icon ${glyphs.length} 個)`);
    return;
  }
  console.log(`書いた: ${files.size} file・icon ${glyphs.length} 個`);
}

main();
