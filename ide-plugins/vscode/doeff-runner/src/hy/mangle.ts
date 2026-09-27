// Hy の名前の照合の規則(契約の mangled と同じ規則)。索引側と同じ 1 規則で名前を比べるための module。

/**
 * Hy の名前 1 区切りを照合用の形にする — 契約どおり `-` を `_` にし、先頭に続く `-` は残す。
 */
export function mangle(name: string): string {
  const leading = /^-*/.exec(name)?.[0] ?? '';
  return leading + name.slice(leading.length).replace(/-/g, '_');
}

/** dotted の名前(`a.b-c.d`)を区切りごとに mangle して繋ぎ直す。module 名・qualifier の照合用。 */
export function mangleDotted(dotted: string): string {
  return dotted
    .split('.')
    .map((part) => mangle(part))
    .join('.');
}
