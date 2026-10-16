//! 引かれた時に 1 度だけ作る repo 全体の事実(1 file の実行 — card ki-a9959efd9cf6 の残り)。
//!
//! 失敗の型・class の frozen は、書いた file が名指さない file の宣言で決まる(逆向きの依存 — 失敗の型は別の file の defeffect の `:failure` が宣言し、
//! class の frozen は同じ名の class が repo のどこかに在るかで決まる)。書いた file が名指す module だけを読む形では答えが変わりうるので、repo 全体から
//! 作る — ただし、その事実が**引かれる file だけ**。引かれない file(match の腕が無い・型で判じる handler の引数が無い)は、作らずに済む。作る時は
//! 今までと同じ物を同じ手順で作るので、答えは変わらない(引かれる file はこれまでと同じ読み、引かれない file は読まない)。
//! 引数に渡された Program(別の file の呼び手が渡す)も逆向きの依存だが、引く呼び先が書いた file の関数と決まっているので、repo 全体を作らず、
//! 呼び先の名を字面に含む file だけから作る(`spelled_callers`)。

use std::sync::OnceLock;

/// 引かれた時に `build` で 1 度だけ作る値。作る手順は呼び手が渡す(repo 全体の事実を作る今までの関数)。
pub struct Deferred<'a, T> {
    cell: OnceLock<T>,
    build: Box<dyn Fn() -> T + Send + Sync + 'a>,
}

impl<'a, T> Deferred<'a, T> {
    pub fn new(build: impl Fn() -> T + Send + Sync + 'a) -> Self {
        Deferred { cell: OnceLock::new(), build: Box::new(build) }
    }

    /// 値(初めて引く時に作る)。
    pub fn get(&self) -> &T {
        self.cell.get_or_init(|| (self.build)())
    }

    /// もう作ったか(検のため)。
    pub fn is_built(&self) -> bool {
        self.cell.get().is_some()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicUsize, Ordering};

    #[test]
    fn the_value_is_built_once_and_only_when_asked() {
        let calls = AtomicUsize::new(0);
        let deferred = Deferred::new(|| {
            calls.fetch_add(1, Ordering::SeqCst);
            41 + 1
        });
        assert!(!deferred.is_built());
        assert_eq!(calls.load(Ordering::SeqCst), 0);
        assert_eq!((*deferred.get(), *deferred.get()), (42, 42));
        assert_eq!(calls.load(Ordering::SeqCst), 1);
    }
}
