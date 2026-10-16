//! 小さい file を大量に読む所の待ちを重ねる(card ki-58bfad2c13a1 の残り — 冷えた作業木の最初の 1 回)。
//!
//! 1 file の実行は、登録簿の dir(1 鍵 1 file — agora-controllers で 447 file)と、書いた file が名指す module の源(数十〜数百 file)を読む。
//! 作業木ごとに別の inode なので、冷えた作業木では 1 file ごとに disk を 1 回待つ。順に読むと待ちが足し算になり、混んだ Pod(load 70〜100・
//! io の圧迫 12〜30%)の冷えた作業木で、登録簿の読みだけが中央値 2.4 秒・最大 10.8 秒かかった(hook の期限は 4 秒)。待ちは CPU を使わず、
//! disk は並べて頼むほど速く返すので、読みを多めの thread で重ねる(冷えた 449 file の実測 — 直列 6.4 秒 / 8 本 0.93 秒 / 16 本 0.62 秒 /
//! 32 本 0.37 秒・どれも中央値)。
//!
//! repo の Hy の全部（約 2900 file）を読む所と、約 1200 の dir を開く所（`hy_files::collect` の走査）は、もっと多い本数で重ねる（`fan_out_wide`）。
//!
//! 1 file の実行の rayon の pool は 4 本に絞ってある(CPU の仕事は thread を増やしても速くならない — main.rs の `SINGLE_FILE_THREADS`)。
//! 待ちを重ねるのは CPU の仕事ではないので、その pool とは別に、読みの間だけ `std::thread::scope` の thread を起こす。

use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Mutex;

/// 待ちを重ねる thread の数(呼び手の thread を含む)。
pub const READERS: usize = 32;

/// repo の Hy の全部（約 2900 file・約 1200 の dir）を読む・開く時に待ちを重ねる thread の数。登録簿や名指しの源（数百 file）の READERS で足りる並べ方では、
/// 冷えた作業木の全 file の読みが 3.3 秒（中央値・agora-controllers 2839 file・Pod の load 83）かかり、128 本で 1.8 秒・256 本で 1.9 秒だった。
/// thread は待っている間 CPU を使わない（stack は触った分だけ）ので、頭打ちの手前の 128 本にする。
pub const WIDE_READERS: usize = 128;

/// これより少ない件数は、thread を起こす手間のほうが重いので呼び手の thread で順に読む。
const SERIAL_BELOW: usize = 4;

/// `items` の全部に `work` を当て、結果を `items` の順で返す。待ち(file の読み・stat)を重ねるための道具で、`work` は互いに独立であること。
pub fn fan_out<X, R>(items: &[X], work: impl Fn(&X) -> R + Sync) -> Vec<R>
where
    X: Sync,
    R: Send,
{
    fan_out_with(READERS, items, work)
}

/// `fan_out` の、repo の全部を読む・開く所のための形（`WIDE_READERS` 本）。
pub fn fan_out_wide<X, R>(items: &[X], work: impl Fn(&X) -> R + Sync) -> Vec<R>
where
    X: Sync,
    R: Send,
{
    fan_out_with(WIDE_READERS, items, work)
}

/// `fan_out` の、待ちを重ねる thread の数 `readers` を呼び手が決める形。
pub fn fan_out_with<X, R>(readers: usize, items: &[X], work: impl Fn(&X) -> R + Sync) -> Vec<R>
where
    X: Sync,
    R: Send,
{
    #[cfg(test)]
    let watch = watch::current();
    let run = |item: &X| -> R {
        #[cfg(test)]
        if let Some(watch) = &watch {
            return watch.around(|| work(item));
        }
        work(item)
    };
    let workers = readers.min(items.len());
    if workers < SERIAL_BELOW {
        return items.iter().map(run).collect();
    }
    let next = AtomicUsize::new(0);
    let slots: Vec<Mutex<Option<R>>> = items.iter().map(|_| Mutex::new(None)).collect();
    let pull = || loop {
        let index = next.fetch_add(1, Ordering::Relaxed);
        let Some(item) = items.get(index) else { break };
        let result = run(item);
        *slots[index].lock().expect("fan_out の結果の置き場") = Some(result);
    };
    std::thread::scope(|scope| {
        for _ in 1..workers {
            // thread を起こせない機体(pid の上限など)でも止まらない — 起こせた分と呼び手の thread で全部読み切る。
            if std::thread::Builder::new().spawn_scoped(scope, &pull).is_err() {
                break;
            }
        }
        pull();
    });
    slots.into_iter().map(|slot| slot.into_inner().expect("fan_out の結果の置き場").expect("fan_out は全部の item の結果を置く")).collect()
}

/// 検が置く観測 — `fan_out` の仕事が同時に何件進んだかを数える(読みが本当に重なっているかを、時計に頼らず確かめる)。
#[cfg(test)]
pub(crate) mod watch {
    use std::cell::RefCell;
    use std::sync::atomic::{AtomicUsize, Ordering};
    use std::sync::Arc;
    use std::time::Duration;

    pub(crate) struct Watch {
        hold: Duration,
        in_flight: AtomicUsize,
        peak: AtomicUsize,
    }

    thread_local! {
        static CURRENT: RefCell<Option<Arc<Watch>>> = const { RefCell::new(None) };
    }

    /// `body` の間に呼び手の thread が始める `fan_out` を観測し、(`body` の結果, 同時に進んだ仕事の最大数)を返す。重なりが出るよう、仕事ごとに `hold` だけ待たせる。
    pub(crate) fn watching<T>(hold: Duration, body: impl FnOnce() -> T) -> (T, usize) {
        let watch = Arc::new(Watch { hold, in_flight: AtomicUsize::new(0), peak: AtomicUsize::new(0) });
        CURRENT.with(|current| *current.borrow_mut() = Some(Arc::clone(&watch)));
        let out = body();
        CURRENT.with(|current| *current.borrow_mut() = None);
        (out, watch.peak.load(Ordering::SeqCst))
    }

    pub(super) fn current() -> Option<Arc<Watch>> {
        CURRENT.with(|current| current.borrow().clone())
    }

    impl Watch {
        pub(super) fn around<R>(&self, work: impl FnOnce() -> R) -> R {
            let now = self.in_flight.fetch_add(1, Ordering::SeqCst) + 1;
            self.peak.fetch_max(now, Ordering::SeqCst);
            std::thread::sleep(self.hold);
            let out = work();
            self.in_flight.fetch_sub(1, Ordering::SeqCst);
            out
        }
    }
}

#[cfg(test)]
mod tests {
    use super::watch::watching;
    use super::*;
    use std::time::Duration;

    #[test]
    fn it_returns_the_results_in_the_order_of_the_items_and_runs_each_item_once() {
        let items: Vec<usize> = (0..200).collect();
        let calls = AtomicUsize::new(0);
        let doubled = fan_out(&items, |n| {
            calls.fetch_add(1, Ordering::SeqCst);
            n * 2
        });
        assert_eq!(doubled, items.iter().map(|n| n * 2).collect::<Vec<_>>());
        assert_eq!(calls.load(Ordering::SeqCst), 200);
    }

    #[test]
    fn it_overlaps_the_waits_of_many_items() {
        let items: Vec<usize> = (0..64).collect();
        let (out, peak) = watching(Duration::from_millis(20), || fan_out(&items, |n| *n));
        assert_eq!(out, items);
        assert!(peak >= 8, "待ちが重なっていない(同時に進んだ最大 {peak} 件 — 冷えた disk の待ちが足し算になる)");
        assert!(peak <= READERS, "起こす thread の数が上限 {READERS} を越えた({peak} 件)");
    }

    #[test]
    fn the_wide_form_overlaps_more_waits_than_the_plain_form_and_stays_within_its_limit() {
        let items: Vec<usize> = (0..400).collect();
        let (out, peak) = watching(Duration::from_millis(20), || fan_out_wide(&items, |n| *n));
        assert_eq!(out, items);
        assert!(peak > READERS, "待ちが READERS より広く重なっていない(同時に進んだ最大 {peak} 件)");
        assert!(peak <= WIDE_READERS, "起こす thread の数が上限 {WIDE_READERS} を越えた({peak} 件)");
    }

    #[test]
    fn it_reads_few_items_inline_without_starting_threads() {
        let items = [1, 2, 3];
        let (out, peak) = watching(Duration::from_millis(10), || fan_out(&items, |n| n + 1));
        assert_eq!(out, vec![2, 3, 4]);
        assert_eq!(peak, 1);
    }

    #[test]
    fn a_failing_item_is_a_result_of_its_own_and_the_others_still_run() {
        let items: Vec<usize> = (0..40).collect();
        let out = fan_out(&items, |n| if n % 7 == 3 { Err(format!("{n} を読めない")) } else { Ok(*n) });
        for (n, result) in items.iter().zip(&out) {
            assert_eq!(result, &if n % 7 == 3 { Err(format!("{n} を読めない")) } else { Ok(*n) });
        }
    }

    #[test]
    fn it_gives_an_empty_answer_for_no_items() {
        assert_eq!(fan_out(&[] as &[u8], |n| *n), Vec::<u8>::new());
    }

    /// `Mutex` を使う仕事が呼び手の thread と他の thread の両方で進んでも、結果が落ちない。
    #[test]
    fn items_that_share_a_lock_still_all_get_their_results() {
        let seen = Mutex::new(Vec::new());
        let items: Vec<usize> = (0..100).collect();
        let out = fan_out(&items, |n| {
            seen.lock().unwrap().push(*n);
            *n
        });
        let mut seen = seen.into_inner().unwrap();
        seen.sort_unstable();
        assert_eq!(seen, items);
        assert_eq!(out, items);
    }
}
