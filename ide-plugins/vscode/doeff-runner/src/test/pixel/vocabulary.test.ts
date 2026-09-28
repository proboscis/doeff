import * as assert from 'assert';
import * as fs from 'fs';
import * as path from 'path';
import { overlayBadge, parseGlyphSet, type GlyphSet } from '../../pixel/glyphs';
import { allGlyphs, PIXEL_DIR } from '../../pixel/build';
import {
  axisValueGlyph,
  doeStatus,
  EFFECT_GLYPHS,
  EFFECT_GLYPH_TABLE,
  effectGlyph,
  fileMood,
  gutterLines,
  kindGlyph,
  layerGlyph,
  parseGutterMode,
  ruleFamilyGlyph,
  litPixels,
  ruleIcon,
  severityLamp,
  tallyViolations,
  violationMark,
  worstMark
} from '../../pixel/vocabulary';
import { replacementGlyphs } from '../../pixel/replace';
import type { HyDefinition, HyDefinitionKind } from '../../hy/contract';
import { LINT_RULE_FAMILIES, type LintModule, type LintSeverity, type LintViolation } from '../../lint/contract';

const ROOT = path.join(__dirname, '..', '..', '..');

/** 元の定義を契約の入口で読む。 */
function glyphSet(): GlyphSet {
  const parsed = parseGlyphSet(fs.readFileSync(path.join(ROOT, PIXEL_DIR, 'glyphs.json'), 'utf8'));
  if (parsed.tag !== 'ok') {
    assert.fail(parsed.reason);
  }
  return parsed.set;
}

/** sprite の灯(L)の点の位置(大きい sprite の 32×32)— 元の定義の格子から。 */
function lampPoints(set: GlyphSet, name: string): Array<[number, number]> {
  const grid = set.glyphs.find((g) => g.name === name)?.grids[32] ?? [];
  return grid.flatMap((row, y) => [...row].flatMap((ch, x) => (ch === 'L' ? [[x, y] as [number, number]] : [])));
}

/** 行 line に始まり end 行で終わる定義(検のための最小の形)。 */
function def(name: string, kind: HyDefinitionKind, line: number, end: number, container: string | null = null): HyDefinition {
  return {
    name,
    mangled: name,
    kind,
    range: { start: { line, character: 1 }, end: { line, character: 1 + name.length } },
    fullRange: { start: { line, character: 0 }, end: { line: end, character: 10 } },
    container,
    docstring: null,
    params: [],
    bases: [],
    raw: { direct: [], via: [] },
    tags: null,
    checks: null
  };
}

/** 行 line の違反(検のための最小の形)。 */
function violation(line: number, severity: LintSeverity, extra: Partial<LintViolation> = {}): LintViolation {
  const at = { line, character: 2 };
  return {
    rule: 'DOEFF106',
    law: null,
    adr: null,
    severity,
    path: '/repo/a.hy',
    range: { start: at, end: at },
    message: 'm',
    hint: null,
    key: null,
    registered: false,
    baseSeverity: severity,
    standing: 'new',
    level: 'major',
    explanation: null,
    source: 'linter',
    probability: null,
    ...extra
  };
}

const MODULE: LintModule = {
  path: '/repo/a.hy',
  layer: 'protocol',
  service: 'kanban',
  context: null,
  role: null,
  violations: 0,
  layerReason: null
};

suite('pixel art の当て方 — 語・kind・違反の印', () => {
  test('宣言の kind と決まった語は、元の定義にある icon に当たる(同じ語は必ず同じ icon)', () => {
    const names = new Set(allGlyphs(glyphSet()).map((g) => g.name));
    for (const kind of ['defk', 'defp', 'defhandler', 'defeffect', 'effect-clause', 'defrecord', 'deftest', 'law'] as const) {
      const glyph = kindGlyph(kind);
      assert.ok(glyph !== undefined && names.has(glyph), `${kind} の icon ${glyph} が無い`);
    }
    for (const glyph of replacementGlyphs()) {
      assert.ok(names.has(glyph), `置き換えの icon ${glyph} が無い`);
    }
    for (const family of LINT_RULE_FAMILIES) {
      assert.ok(names.has(ruleFamilyGlyph(family)), `規則の家族 ${family} の icon ${ruleFamilyGlyph(family)} が無い`);
    }
    assert.strictEqual(kindGlyph('defn'), undefined);
  });

  test('違反の欄の行の画 — sprite は規則の家族(家族ごとに違う物)、付いた灯の色が重さ、Jev の判定は組の sprite', () => {
    const set = glyphSet();
    // 家族ごとに違う sprite(Python の規則と家族の無い古い出力は ADR の決まりと同じ天秤)
    const pictures = new Set(LINT_RULE_FAMILIES.filter((f) => f !== 'python').map((f) => ruleFamilyGlyph(f)));
    assert.strictEqual(pictures.size, LINT_RULE_FAMILIES.length - 1);
    assert.strictEqual(ruleFamilyGlyph('python'), 'rule-law');
    assert.strictEqual(ruleFamilyGlyph(null), 'rule-law');
    // 灯(L の点)だけが重さの色に変わり、他の点は同じ。重さが無ければ消えた灯の色
    const lamps = lampPoints(set, 'rule-smell');
    assert.ok(lamps.length > 0, 'rule-smell に灯が無い');
    const at = (severity: LintSeverity | null): Array<number | null> => {
      const pixels = litPixels(set, ruleIcon('smell', severity, []));
      return lamps.map(([x, y]) => pixels?.[y][x] ?? null);
    };
    assert.ok(at('error').every((c) => c === severityLamp(set, 'error')));
    assert.ok(at('warning').every((c) => c === severityLamp(set, 'warning')));
    assert.ok(at('info').every((c) => c === severityLamp(set, 'info')));
    assert.ok(at(null).every((c) => c === set.lamps.off), '違反の無い行(規則の一覧)は消えた灯');
    const others = (severity: LintSeverity): string =>
      JSON.stringify(litPixels(set, ruleIcon('smell', severity, []))?.map((row, y) => row.map((c, x) => (lamps.some(([lx, ly]) => lx === x && ly === y) ? -1 : c))));
    assert.strictEqual(others('error'), others('info'));
    // Jev が判定した臭い(DOEFF205)と class(DOEFF204)は組の sprite、Jev の家族と組の無い家族はそのまま
    const jev = [violation(1, 'warning', { source: 'jev', probability: 0.8 })];
    assert.strictEqual(ruleIcon('smell', 'warning', jev).glyph, 'rule-smell-jev');
    assert.strictEqual(ruleIcon('class', 'warning', jev).glyph, 'rule-class-jev');
    assert.strictEqual(ruleIcon('jev', 'warning', jev).glyph, 'rule-jev');
    assert.strictEqual(ruleIcon('wire', 'warning', jev).glyph, 'rule-wire');
    assert.strictEqual(ruleIcon('smell', 'warning', [violation(1, 'warning')]).glyph, 'rule-smell');
  });

  test('灯 — 重さを灯で出す sprite(定義の kind・層・規則の家族と組・service の旗)は全部、灯の点を持つ', () => {
    const set = glyphSet();
    const lit = [
      ...(['defk', 'defp', 'defhandler', 'defeffect', 'defrecord', 'deftest', 'law'] as const).map((k) => kindGlyph(k) ?? k),
      ...['core', 'intent', 'protocol', 'foundation', 'entry'].map((l) => layerGlyph(l) ?? l),
      ...LINT_RULE_FAMILIES.map((f) => ruleFamilyGlyph(f)),
      'rule-smell-jev',
      'rule-class-jev',
      'defwire',
      'defsystem'
    ];
    for (const name of lit) {
      assert.ok(lampPoints(set, name).length > 0, `${name} に灯(L)が無い`);
    }
    const flag = litPixels(set, { glyph: 'service-kanban', severity: 'error', flag: null });
    assert.ok(flag?.some((row) => row.includes(severityLamp(set, 'error'))), 'service の旗の竿の先の灯が灯らない');
  });

  test('違反の印 — error は火・warning は黄色の旗・info は青い旗・登録済みは足場・Jev はふくろう', () => {
    assert.strictEqual(violationMark(violation(1, 'error')), 'lint-error');
    assert.strictEqual(violationMark(violation(1, 'warning')), 'lint-warning');
    assert.strictEqual(violationMark(violation(1, 'info')), 'lint-info');
    assert.strictEqual(violationMark(violation(1, 'warning', { registered: true })), 'lint-registered');
    assert.strictEqual(violationMark(violation(1, 'warning', { source: 'jev', probability: 0.8 })), 'jev');
  });

  test('束の最も強い印 — 火が旗より、旗がふくろうより、ふくろうが足場より先', () => {
    assert.strictEqual(worstMark([]), undefined);
    assert.strictEqual(worstMark([violation(1, 'warning', { registered: true }), violation(2, 'error')]), 'lint-error');
    assert.strictEqual(worstMark([violation(1, 'warning', { registered: true }), violation(2, 'warning', { source: 'jev' })]), 'jev');
  });

  test('doe の表情 — 違反 0 は落ち着き・error があれば驚き・他の違反だけなら困り', () => {
    assert.strictEqual(fileMood(tallyViolations([])), 'doe-calm');
    assert.strictEqual(fileMood(tallyViolations([violation(1, 'warning', { registered: true })])), 'doe-worried');
    assert.strictEqual(fileMood(tallyViolations([violation(1, 'warning'), violation(2, 'error')])), 'doe-surprised');
    const status = doeStatus(tallyViolations([violation(1, 'error'), violation(2, 'error'), violation(3, 'warning', { registered: true })]));
    assert.strictEqual(status.text, '$(doeff-doe-surprised) $(doeff-lint-error) 2 $(doeff-lint-registered) 1');
    assert.strictEqual(doeStatus(tallyViolations([])).text, '$(doeff-doe-calm) 0');
  });

  test('「タグで閲覧」の束の icon — kind・層・role・service の軸だけ、値の無い束は無し', () => {
    assert.strictEqual(axisValueGlyph({ tag: 'builtin', name: 'kind' }, 'defhandler'), 'defhandler');
    assert.strictEqual(axisValueGlyph({ tag: 'builtin', name: 'kind' }, 'nonsense'), undefined);
    assert.strictEqual(axisValueGlyph({ tag: 'builtin', name: 'layer' }, 'core'), 'layer-core');
    assert.strictEqual(axisValueGlyph({ tag: 'builtin', name: 'service' }, 'kanban'), 'service-kanban');
    assert.strictEqual(axisValueGlyph({ tag: 'builtin', name: 'service' }, '(不明)'), undefined);
    assert.strictEqual(axisValueGlyph({ tag: 'tag', key: 'owner' }, 'x'), undefined);
  });
});

suite('pixel art の effect の種類の装置', () => {
  test('effect の名 → 装置の絵 — 名の全体が先、次に頭の語、どちらにも無ければ一般の装置', () => {
    assert.strictEqual(effectGlyph('Ask'), 'effect-ask');
    assert.strictEqual(effectGlyph('GetTime'), 'effect-time', 'GetTime は頭の語 Get(読み取り)より名の全体が先');
    assert.strictEqual(effectGlyph('ReadBoard'), 'effect-read');
    assert.strictEqual(effectGlyph('FetchArtifacts'), 'effect-read');
    assert.strictEqual(effectGlyph('WriteDoneMark'), 'effect-write', '頭の語だけを見る(後ろの Mark は判子にしない)');
    assert.strictEqual(effectGlyph('PutWindow'), 'effect-write');
    assert.strictEqual(effectGlyph('SettleIntake'), 'effect-settle');
    assert.strictEqual(effectGlyph('Raise'), 'effect-raise');
    assert.strictEqual(effectGlyph('Absent'), 'effect-absent');
    assert.strictEqual(effectGlyph('ForwardHttp'), 'effect-http');
    assert.strictEqual(effectGlyph('SocketSend'), 'effect-http');
    assert.strictEqual(effectGlyph('Readme'), 'effect-device', '頭の語は大文字の区切りまで(Readme は Read ではない)');
    assert.strictEqual(effectGlyph('LaunchServer'), 'effect-launch');
    assert.strictEqual(effectGlyph('StopTurn'), 'effect-launch');
    assert.strictEqual(effectGlyph('OpenPage'), 'effect-door');
    assert.strictEqual(effectGlyph('ClosePage'), 'effect-door');
    assert.strictEqual(effectGlyph('Sleep'), 'effect-wait');
    assert.strictEqual(effectGlyph('SendText'), 'effect-send');
    assert.strictEqual(effectGlyph('Post'), 'effect-send');
    assert.strictEqual(effectGlyph('DropCard'), 'effect-delete');
    assert.strictEqual(effectGlyph('PageGoto'), 'effect-device');
    assert.strictEqual(effectGlyph('lowercase'), 'effect-device');
  });

  test('表の絵は全部元の定義にあり、表の外の絵は選ばない', () => {
    const names = new Set(allGlyphs(glyphSet()).map((g) => g.name));
    for (const glyph of EFFECT_GLYPHS) {
      assert.ok(names.has(glyph), `${glyph} が glyphs.json に無い`);
    }
    const chosen = [...Object.values(EFFECT_GLYPH_TABLE.names), ...Object.values(EFFECT_GLYPH_TABLE.verbs)];
    assert.ok(chosen.every((g) => (EFFECT_GLYPHS as readonly string[]).includes(g)));
  });
});

suite('pixel art の gutter', () => {
  const definitions = [def('run', 'defk', 3, 8), def('Put', 'defeffect', 10, 12), def('helper', 'defn', 14, 16), def('field', 'field', 11, 11, 'Put')];

  test('種類と違反 — 定義の行に kind の sprite(灯 = 範囲の違反の最も重い重さ)。定義の外の違反の行は印の sprite だけ', () => {
    const lines = gutterLines(definitions, [violation(5, 'warning'), violation(6, 'error'), violation(20, 'info')], MODULE, 'kind');
    assert.deepStrictEqual(lines, [
      { line: 3, icon: { glyph: 'defk', severity: 'error', flag: null } },
      { line: 6, icon: { glyph: 'lint-error', severity: null, flag: null } },
      { line: 5, icon: { glyph: 'lint-warning', severity: null, flag: null } },
      { line: 10, icon: { glyph: 'defeffect', severity: null, flag: null } },
      { line: 20, icon: { glyph: 'lint-info', severity: null, flag: null } }
    ].sort((a, b) => a.line - b.line));
  });

  test('icon の無い kind(defn)で違反も無い行・入れ物の中の定義(field)には出さない', () => {
    const lines = gutterLines(definitions, [], MODULE, 'kind');
    assert.deepStrictEqual(lines.map((l) => l.line), [3, 10]);
  });

  test('層と service — 定義の行に層の建物、右下に service の旗 / 出さない — 何も出さない', () => {
    const lines = gutterLines(definitions, [], MODULE, 'layer');
    assert.deepStrictEqual(lines[0], { line: 3, icon: { glyph: 'layer-protocol', severity: null, flag: 'service-kanban' } });
    assert.deepStrictEqual(gutterLines(definitions, [violation(5, 'error')], MODULE, 'off'), []);
  });

  test('設定の出し方 — 知らない値は undefined(呼ぶ側が理由を出す)', () => {
    assert.strictEqual(parseGutterMode('layer'), 'layer');
    assert.strictEqual(parseGutterMode('fancy'), undefined);
  });

  test('重ね合わせ — 32×32 の右下に 16×16 の印、印の周りの透明には縁の色、左上は土台のまま', () => {
    const glyphs = new Map(allGlyphs(glyphSet()).map((g) => [g.name, g]));
    const base = glyphs.get('defk')?.pixels[32];
    const badge = glyphs.get('lint-error')?.pixels[16];
    assert.ok(base !== undefined && badge !== undefined);
    const edge = glyphSet().outline[0];
    const out = overlayBadge(base, badge, edge);
    for (let y = 0; y < 16; y++) {
      for (let x = 0; x < 16; x++) {
        assert.strictEqual(out[y][x], base[y][x]);
        if (badge[y][x] !== null) {
          assert.strictEqual(out[16 + y][16 + x], badge[y][x]);
        }
      }
    }
    // 印(火)の左端の点の左隣は縁の色(元の定義の outline の先頭)
    const row = badge.findIndex((r) => r.some((c) => c !== null));
    const col = badge[row].findIndex((c) => c !== null);
    assert.strictEqual(out[16 + row][16 + col - 1], edge);
    assert.deepStrictEqual(overlayBadge(null, badge, edge)[16 + row][16 + col - 1], null);
  });
});
