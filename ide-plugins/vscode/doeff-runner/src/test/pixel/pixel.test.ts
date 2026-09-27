import * as assert from 'assert';
import * as fs from 'fs';
import * as path from 'path';
import * as zlib from 'zlib';
import { allGlyphs, assetFiles, flagReport, PIXEL_DIR } from '../../pixel/build';
import { chooseFlag, flagCollisions, flagGlyph } from '../../pixel/flags';
import { codepoints, FONT_PATH, iconContributions, svgFont } from '../../pixel/font';
import { lightness, parseGlyphSet, type GlyphSet } from '../../pixel/glyphs';
import { colorSvg, mergeRects, png } from '../../pixel/render';

const ROOT = path.join(__dirname, '..', '..', '..');

/** 元の定義を契約の入口で読む。 */
function glyphSet(): GlyphSet {
  const parsed = parseGlyphSet(fs.readFileSync(path.join(ROOT, PIXEL_DIR, 'glyphs.json'), 'utf8'));
  if (parsed.tag !== 'ok') {
    assert.fail(`glyphs.json を読めない: ${parsed.reason}`);
  }
  return parsed.set;
}

/** 反例を作るために書き換える欄だけの形(読み込みの検査は parseGlyphSet が持つ — ここは書き換えの道具)。 */
interface GlyphJson {
  families: Array<{ name: string; frame: unknown }>;
  glyphs: Array<{ name: string; family: string; px32: string[]; px16: string[]; picture?: string }>;
  serviceFlags: { colors: string[]; patterns: Array<{ px32: string[] }> };
}

/** 元の定義の JSON を書き換えて読ませる(反例を作るため)。 */
function mutated(change: (json: GlyphJson) => void): ReturnType<typeof parseGlyphSet> {
  const json: GlyphJson = JSON.parse(fs.readFileSync(path.join(ROOT, PIXEL_DIR, 'glyphs.json'), 'utf8'));
  change(json);
  return parseGlyphSet(JSON.stringify(json));
}

/** 読めないことと、その理由の一部を確かめる。 */
function assertInvalid(result: ReturnType<typeof parseGlyphSet>, reason: RegExp): void {
  assert.strictEqual(result.tag, 'invalid');
  if (result.tag === 'invalid') {
    assert.match(result.reason, reason);
  }
}

suite('pixel art の icon — 元の定義', () => {
  test('読める — 家族・語の icon・service の旗・拡張の icon', () => {
    const set = glyphSet();
    assert.deepStrictEqual(
      set.families.map((f) => f.name),
      ['declaration', 'flow', 'failure', 'layer', 'lint', 'rule', 'service', 'extension']
    );
    for (const name of ['defk', 'defhandler', 'defeffect', 'defrecord', 'defwire', 'defsystem', 'deftest', 'program', 'bind', 'resume', 'finish', 'ask', 'absent', 'raise', 'unreachable', 'refused', 'conflict', 'malformed', 'layer-core', 'layer-intent', 'layer-protocol', 'layer-foundation', 'layer-entry', 'lint-error', 'lint-warning', 'lint-registered', 'jev-unjudged', 'jev', 'doe', 'doe-calm', 'doe-surprised']) {
      assert.ok(set.glyphs.some((g) => g.name === name), `${name} が無い`);
    }
    assert.strictEqual(set.serviceFlags.services.length, 17);
  });

  test('色は元の定義の色の組の表だけ — 生成した SVG に表の外の色が出ない', () => {
    const set = glyphSet();
    const palette = new Set<string>(set.palette);
    for (const [file, body] of assetFiles(set)) {
      if (!file.endsWith('.svg')) {
        continue;
      }
      for (const color of String(body).match(/#[0-9A-Fa-f]{6}/g) ?? []) {
        assert.ok(palette.has(color.toUpperCase()), `${file} に色の組の外の色 ${color}`);
      }
    }
  });

  test('反例 — 枠のある家族では、枠の中の地の外に絵の点を置くと断る(家族の枠は語が変えない)', () => {
    // 今の sprite の家族は枠を持たない(2026-09-28 に外した)。枠を戻した家族に、枠の外へ点を置いた絵を入れる
    const edge = '.'.repeat(32);
    const inner = `.${':'.repeat(30)}.`;
    const small = `.${':'.repeat(14)}.`;
    const frame = { fill: '7', px32: [edge, ...Array.from({ length: 30 }, () => inner), edge], px16: ['.'.repeat(16), ...Array.from({ length: 14 }, () => small), '.'.repeat(16)] };
    assertInvalid(
      mutated((json) => {
        const family = json.families.find((x) => x.name === 'declaration');
        const defk = json.glyphs.find((g) => g.name === 'defk');
        if (family === undefined || defk === undefined) {
          throw new Error('declaration か defk が無い');
        }
        family.frame = frame;
        defk.px32[0] = `8${defk.px32[0].slice(1)}`;
      }),
      /枠の中の地の外に点がある/
    );
  });

  test('反例 — 格子に許されない文字・行の長さ違い・知らない家族・同じ名前を断る', () => {
    assertInvalid(
      mutated((json) => {
        json.glyphs[0].px32[5] = `${json.glyphs[0].px32[5].slice(0, 31)}w`;
      }),
      /許されない文字/
    );
    assertInvalid(
      mutated((json) => {
        json.glyphs[0].px16[2] = '.'.repeat(15);
      }),
      /長さ 16 の文字列ではない/
    );
    assertInvalid(
      mutated((json) => {
        json.glyphs[0].family = 'nowhere';
      }),
      /知らない家族/
    );
    assertInvalid(
      mutated((json) => {
        json.glyphs[1].name = json.glyphs[0].name;
      }),
      /同じ名前が 2 つある/
    );
    assertInvalid(parseGlyphSet('{'), /JSON/);
  });

  test('絵の参照 — 規則の家族は既にある絵(木箱・地図・天秤 …)を枠だけ変えて使い、絵を写さない', () => {
    const set = glyphSet();
    const cls = set.glyphs.find((g) => g.name === 'rule-class');
    const record = set.glyphs.find((g) => g.name === 'defrecord');
    assert.ok(cls !== undefined && record !== undefined);
    assert.strictEqual(cls.picture, 'defrecord');
    assert.deepStrictEqual(cls.grids, record.grids);
    assert.strictEqual(record.picture, null);
  });

  test('反例 — 絵の参照が知らない名前・参照の参照・格子と参照の両方を断る', () => {
    const withPicture = (name: string) => (json: GlyphJson): void => {
      const cls = json.glyphs.find((g) => g.name === 'rule-class');
      if (cls === undefined) {
        throw new Error('rule-class が無い');
      }
      cls.picture = name;
    };
    assertInvalid(mutated(withPicture('nowhere')), /自分で絵を描いた icon ではない: nowhere/);
    assertInvalid(mutated(withPicture('rule-place')), /自分で絵を描いた icon ではない: rule-place/);
    assertInvalid(
      mutated((json) => {
        json.glyphs[0].picture = 'defrecord';
      }),
      /px32・px16\)と picture を両方は書かない/
    );
  });

  test('色の組 — 表を差し替えると全部の sprite の色が変わり、格子は番号の文字だけを持つ', () => {
    const set = glyphSet();
    const doe = allGlyphs(set).find((g) => g.name === 'defk');
    assert.ok(doe !== undefined);
    const swapped = mutated((json) => {
      const table = (json as unknown as { palette: Array<{ color: string }> }).palette;
      table[0].color = '#123456';
    });
    assert.strictEqual(swapped.tag, 'ok');
    if (swapped.tag === 'ok') {
      assert.match(colorSvg(doe.pixels[32], swapped.set.palette), /#123456/);
      assert.doesNotMatch(colorSvg(doe.pixels[32], set.palette), /#123456/);
    }
  });

  test('反例 — 色の組に無い番号・番号の順でない表・#RRGGBB でない色を断る', () => {
    const table = (json: GlyphJson): Array<{ char: string; color: string }> => (json as unknown as { palette: Array<{ char: string; color: string }> }).palette;
    assertInvalid(
      mutated((json) => {
        json.glyphs[0].px32[3] = `${json.glyphs[0].px32[3].slice(0, 31)}v`;
      }),
      /色の組\(\d+ 色\)に無い番号 "v"/
    );
    assertInvalid(
      mutated((json) => {
        table(json)[1].char = '9';
      }),
      /番号の順の文字 1 ではない/
    );
    assertInvalid(
      mutated((json) => {
        table(json)[2].color = 'red';
      }),
      /#RRGGBB ではない/
    );
  });

  test('反例 — 旗の模様が布の形と合わない・見分けられる 2 色目が無い色を断る', () => {
    assertInvalid(
      mutated((json) => {
        json.serviceFlags.patterns[0].px32[0] = 'A'.repeat(32);
      }),
      /布の点と模様の点が合わない/
    );
    assertInvalid(
      mutated((json) => {
        json.serviceFlags.colors = ['5', '9'];
      }),
      /明るさの差/
    );
  });
});

suite('pixel art の icon — service の旗', () => {
  test('並べた 17 の service の旗は重複ゼロ・2 色は明るさの差が決まり以上', () => {
    const set = glyphSet();
    const report = flagReport(set);
    assert.deepStrictEqual(report.collisions, []);
    for (const c of report.choices) {
      assert.notStrictEqual(c.primary, c.secondary);
      assert.ok(Math.abs(lightness(set.palette, c.primary) - lightness(set.palette, c.secondary)) >= set.serviceFlags.minContrast, c.service);
    }
  });

  test('同じ名前からは同じ旗 — 知らない service の名前でも拡張の実行時に同じ関数で作れる', () => {
    const set = glyphSet();
    assert.deepStrictEqual(chooseFlag(set, 'brand-new-service'), chooseFlag(set, 'brand-new-service'));
    const flag = flagGlyph(set, 'brand-new-service');
    assert.strictEqual(flag.name, 'service-brand-new-service');
    assert.strictEqual(flag.pixels[32].length, 32);
    assert.strictEqual(flag.pixels[16].length, 16);
  });

  test('重複の数え方 — 同じ模様・同じ色の組は重複', () => {
    const collisions = flagCollisions([
      { service: 'a', pattern: 'cross', primary: 8, secondary: 7 },
      { service: 'b', pattern: 'cross', primary: 8, secondary: 7 },
      { service: 'c', pattern: 'cross', primary: 7, secondary: 8 }
    ]);
    assert.deepStrictEqual(collisions, [{ key: 'cross/8/7', services: ['a', 'b'] }]);
  });
});

suite('pixel art の icon — 画と字体', () => {
  test('長方形へのまとめ — 同じ幅の連なりは縦に伸び、塗った点ちょうどを覆う', () => {
    const grid = ['##..', '##..', '..#.', '..##'];
    const rects = mergeRects(4, 4, (x, y) => grid[y][x] === '#');
    assert.deepStrictEqual(rects, [
      { x: 0, y: 0, width: 2, height: 2 },
      { x: 2, y: 2, width: 1, height: 1 },
      { x: 2, y: 3, width: 2, height: 1 }
    ]);
  });

  test('SVG は crispEdges の rect だけ・PNG は整数倍で拡大して補間しない', () => {
    const doe = allGlyphs(glyphSet()).find((g) => g.name === 'doe');
    assert.ok(doe !== undefined);
    const svg = colorSvg(doe.pixels[32], glyphSet().palette);
    assert.match(svg, /shape-rendering="crispEdges"/);
    assert.doesNotMatch(svg, /<path|<circle|filter/);
    const bytes = png(doe.pixels[32], glyphSet().palette, 4);
    assert.strictEqual(bytes.readUInt32BE(16), 128);
    assert.strictEqual(bytes.readUInt32BE(20), 128);
    // IDAT を開いて、拡大した 4×4 の正方形の中の画素が全部同じ色であることを見る(ぼかしていない)
    const idat = bytes.subarray(bytes.indexOf('IDAT') + 4, bytes.indexOf('IEND') - 8);
    const raw = zlib.inflateSync(idat);
    const pixelAt = (x: number, y: number): string => raw.subarray(y * (128 * 4 + 1) + 1 + x * 4, y * (128 * 4 + 1) + 5 + x * 4).toString('hex');
    for (let y = 0; y < 32; y++) {
      for (let x = 0; x < 32; x++) {
        const corner = pixelAt(x * 4, y * 4);
        assert.strictEqual(pixelAt(x * 4 + 3, y * 4 + 3), corner);
        assert.strictEqual(pixelAt(x * 4 + 1, y * 4 + 2), corner);
      }
    }
  });

  test('字体 — icon ごとに私用領域の文字 1 つ・package.json の contributes.icons と一致', () => {
    const glyphs = allGlyphs(glyphSet());
    const cps = codepoints(glyphs);
    assert.strictEqual(new Set(cps.map((c) => c.codepoint)).size, glyphs.length);
    const font = svgFont(glyphs);
    assert.strictEqual((font.match(/<glyph /g) ?? []).length, glyphs.length);
    const pkg = JSON.parse(fs.readFileSync(path.join(ROOT, 'package.json'), 'utf8'));
    assert.deepStrictEqual(pkg.contributes.icons, iconContributions(glyphs));
    assert.ok(fs.existsSync(path.join(ROOT, FONT_PATH)), '字体の file が無い(npm run pixel)');
  });

  test('生成物は元の定義どおり — commit した SVG・PNG・拡張の icon が食い違わない', () => {
    const stale: string[] = [];
    for (const [relative, body] of assetFiles(glyphSet())) {
      const target = path.join(ROOT, relative);
      const bytes = typeof body === 'string' ? Buffer.from(body, 'utf8') : body;
      if (!fs.existsSync(target) || !fs.readFileSync(target).equals(bytes)) {
        stale.push(relative);
      }
    }
    assert.deepStrictEqual(stale, [], 'npm run pixel で作り直す');
  });
});
