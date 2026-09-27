// 生の副作用(http・asyncio・時刻・乱数・file・process・環境変数・network・db・thread)の目録 — 宣言の唯一の場所。
// 判定(rawEffects.ts)はこの表だけを見る。利用者は VS Code の設定で分類ごとに名前を足せる。

/** 生の副作用の分類(閉じた集合)。 */
export const RAW_CATEGORIES = [
  'http',
  'async',
  'time',
  'random',
  'file',
  'process',
  'env',
  'network',
  'db',
  'thread'
] as const;

export type RawCategory = (typeof RAW_CATEGORIES)[number];

/** method 名だけで拾う証拠(弱い)— 誤検出を避けるため、文脈の module が見える時だけ数える。 */
export interface RawMethod {
  /** Python の method 名(`read_text`)。Hy の `read-text` は mangle して比べる */
  readonly name: string;
  /** この module(か、その中の名前)が file で import されているか、定義の中で参照されている時だけ数える。空なら常に数える */
  readonly context: readonly string[];
}

/** 分類 1 つの目録。 */
export interface RawCatalogEntry {
  readonly category: RawCategory;
  /**
   * import を通して完全な名前に直した参照と比べる dotted の名前。区切りの境目での前方一致
   * (`httpx` は `httpx.post` も拾う)。末尾の `*` は区切りの中の前方一致(`os.exec*` は `os.execv` を拾う)。
   */
  readonly patterns: readonly string[];
  /** import されていない、修飾の無い名前(組み込み)— `open`。呼び出しの頭の位置(`(open p)`)だけ数える */
  readonly builtins: readonly string[];
  readonly methods: readonly RawMethod[];
}

/** pathlib の method(Path の上の file の操作)。 */
const PATHLIB_METHODS = [
  'read_text',
  'write_text',
  'read_bytes',
  'write_bytes',
  'open',
  'mkdir',
  'unlink',
  'rmdir',
  'rename',
  'replace',
  'touch',
  'exists',
  'iterdir',
  'glob',
  'rglob',
  'stat'
];

/** 既定の目録。 */
export const DEFAULT_RAW_CATALOG: readonly RawCatalogEntry[] = [
  {
    category: 'http',
    patterns: ['httpx', 'requests', 'urllib.request', 'urllib3', 'aiohttp', 'http.client'],
    builtins: [],
    methods: []
  },
  { category: 'async', patterns: ['asyncio'], builtins: [], methods: [] },
  {
    category: 'time',
    patterns: [
      'time.time',
      'time.time_ns',
      'time.sleep',
      'time.monotonic',
      'time.perf_counter',
      'datetime.datetime.now',
      'datetime.datetime.utcnow',
      'datetime.datetime.today',
      'datetime.date.today'
    ],
    builtins: [],
    methods: []
  },
  {
    category: 'random',
    patterns: ['random', 'secrets', 'uuid.uuid1', 'uuid.uuid4', 'os.urandom'],
    builtins: [],
    methods: []
  },
  {
    category: 'file',
    patterns: [
      'shutil',
      'tempfile',
      'os.remove',
      'os.unlink',
      'os.makedirs',
      'os.mkdir',
      'os.listdir',
      'os.walk',
      'os.rename',
      'os.replace',
      'os.scandir',
      'json.load',
      'json.dump'
    ],
    builtins: ['open'],
    methods: PATHLIB_METHODS.map((name) => ({ name, context: ['pathlib'] }))
  },
  {
    category: 'process',
    patterns: ['subprocess', 'os.system', 'os.kill', 'os.fork', 'os.exec*', 'os.spawn*', 'os.popen', 'signal'],
    builtins: [],
    methods: []
  },
  { category: 'env', patterns: ['os.environ', 'os.getenv', 'os.putenv'], builtins: [], methods: [] },
  { category: 'network', patterns: ['socket', 'ssl', 'websockets'], builtins: [], methods: [] },
  {
    category: 'db',
    patterns: ['sqlite3', 'psycopg', 'psycopg2', 'psycopg_pool', 'asyncpg', 'redis'],
    builtins: [],
    methods: []
  },
  {
    category: 'thread',
    patterns: ['threading', 'multiprocessing', 'concurrent.futures'],
    builtins: [],
    methods: []
  }
];

/**
 * 目録の pattern に合っても副作用ではない名前(区切りの境目での前方一致)— 純粋な module と、小文字の例外の型。
 * 大文字で終わる例外の型(`…Error`・`…Timeout` 等)は判定の側で名前の形で除く。
 */
export const RAW_IGNORED_NAMES: readonly string[] = [
  'urllib.parse',
  'socket.timeout',
  'socket.gaierror',
  'socket.herror',
  'asyncio.iscoroutine',
  'asyncio.iscoroutinefunction'
];

/** 設定から足した結果と、読めなかった値の理由。 */
export interface CatalogMerge {
  readonly catalog: readonly RawCatalogEntry[];
  readonly problems: readonly string[];
}

/** 文字列が分類の名前かを見る。 */
export function isRawCategory(value: string): value is RawCategory {
  return (RAW_CATEGORIES as readonly string[]).includes(value);
}

/**
 * 設定(分類 → 名前の配列)を既定の目録に足す。名前の書き方: `.name` は method 名(弱い・文脈なし)、
 * `builtin:name` は組み込み、それ以外は dotted の名前(区切りの境目での前方一致・末尾 `*` 可)。
 * 知らない分類・文字列でない値は理由を返して無視する(黙って捨てない)。
 */
export function mergeRawCatalog(base: readonly RawCatalogEntry[], extra: unknown): CatalogMerge {
  if (extra === undefined || extra === null) {
    return { catalog: base, problems: [] };
  }
  if (typeof extra !== 'object' || Array.isArray(extra)) {
    return { catalog: base, problems: ['設定 rawSideEffects は「分類 → 名前の配列」の object であること'] };
  }
  const problems: string[] = [];
  const added = new Map<RawCategory, { patterns: string[]; builtins: string[]; methods: RawMethod[] }>();
  for (const [key, value] of Object.entries(extra)) {
    if (!isRawCategory(key)) {
      problems.push(`設定 rawSideEffects の分類 "${key}" は知らない(使える分類: ${RAW_CATEGORIES.join(', ')})`);
      continue;
    }
    if (!Array.isArray(value)) {
      problems.push(`設定 rawSideEffects.${key} が配列でない`);
      continue;
    }
    const bucket = added.get(key) ?? { patterns: [], builtins: [], methods: [] };
    for (const item of value) {
      if (typeof item !== 'string' || item.trim() === '') {
        problems.push(`設定 rawSideEffects.${key} に文字列でない値 ${JSON.stringify(item)}`);
        continue;
      }
      if (item.startsWith('.')) {
        bucket.methods.push({ name: item.slice(1), context: [] });
      } else if (item.startsWith('builtin:')) {
        bucket.builtins.push(item.slice('builtin:'.length));
      } else {
        bucket.patterns.push(item);
      }
    }
    added.set(key, bucket);
  }
  const catalog = base.map((entry) => {
    const more = added.get(entry.category);
    return more === undefined
      ? entry
      : {
          category: entry.category,
          patterns: [...entry.patterns, ...more.patterns],
          builtins: [...entry.builtins, ...more.builtins],
          methods: [...entry.methods, ...more.methods]
        };
  });
  return { catalog, problems };
}
