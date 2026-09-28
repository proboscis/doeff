// 1 行のチップと縦の表を切り替える閾(v6 2.1 節・2.2 節・席の既定で戻せる)— 関数の見出し(render.ts)と、型の欄(entity.ts・
// v9 の defclass / defrecord の fields)で同じ数を使うため、ここ 1 か所に置く。

/** 縦の表にする引数(欄)の数。 */
export const TALL_SIGNATURE_PARAMS = 4;
/** 縦の表にする型の文字の合計。 */
export const TALL_SIGNATURE_CHARS = 60;
/** 畳んだ 1 行で型を省いて名だけにする引数の数(関数だけ — 型の欄は型を出す・v9 の見本)。 */
export const NAMES_ONLY_PARAMS = 4;
