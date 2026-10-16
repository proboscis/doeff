//! 小さい file を大量に読む所の待ちを重ねる(card ki-58bfad2c13a1 の残り — 冷えた作業木の最初の 1 回)。
//!
//! 1 file の実行は、登録簿の dir(1 鍵 1 file — agora-controllers で 447 file)と、書いた file が名指す module の源(数十〜数百 file)を読む。
//! 作業木ごとに別の inode なので、冷えた作業木では 1 file ごとに disk を 1 回待つ。順に読むと待ちが足し算になり、混んだ Pod(load 70〜100・
//! io の圧迫 12〜30%)の冷えた作業木で、登録簿の読みだけが中央値 2.4 秒・最大 10.8 秒かかった(hook の期限は 4 秒)。待ちは CPU を使わず、
//! disk は並べて頼むほど速く返すので、読みを多めの thread で重ねる(冷えた 449 file の実測 — 直列 6.4 秒 / 8 本 0.93 秒 / 16 本 0.62 秒 /
//! 32 本 0.37 秒・どれも中央値)。
//!
//! repo の Hy の全部(約 2900 file)を読む所と、約 1200 の dir を開く所(`hy_files::collect` の走査)は、もっと多い本数で重ねる(`fan_out_wide`)。
//!
//! 1 file の実行の rayon の pool は 4 本に絞ってある(CPU の仕事は thread を増やしても速くならない — main.rs の `SINGLE_FILE_THREADS`)。
//! 待ちを重ねるのは CPU の仕事ではないので、その pool とは別に、読みの間だけ `std::thread::scope` の thread を起こす。

use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Condvar, Mutex, MutexGuard, PoisonError};

/// 待ちを重ねる thread の数(呼び手の thread を含む)。
pub const READERS: usize = 32;

/// repo の Hy の全部(約 2900 file・約 1200 の dir)を読む・開く時に待ちを重ねる thread の数。登録簿や名指しの源(数百 file)の READERS で足りる並べ方では、
/// 冷えた作業木の全 file の読みが 3.3 秒(中央値・agora-controllers 2839 file・Pod の load 83)かかり、128 本で 1.8 秒・256 本で 1.9 秒だった。
/// thread は待っている間 CPU を使わない(stack は触った分だけ)ので、頭打ちの手前の 128 本にする。
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

/// `fan_out` の、repo の全部を読む・開く所のための形(`WIDE_READERS` 本)。
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

/// 仕事が仕事を生む時の、待ちを重ねる道具。`seed` の仕事から始め、`step` が仕事 1 つを済ませて、続きの仕事と結果(あれば)を返す。
///
/// repo の tree を歩く(dir を開くと中の dir と file が見つかる)のと、見つけた file を読むのを、層の切れ目なしに重ねる: 続きの仕事はすぐ他の thread が拾うので、
/// 深い dir を開く待ちと、浅い所で見つかった file の読みの待ちが同時に進む。層ごとに揃えて進める形は、層の中でいちばん遅い待ちに全部が揃う。
/// 結果の順は決まらない(呼び手が並べる)。仕事は互いに独立であること。後ろに積んだ仕事から先に拾う(dir を先に開くと、並べられる仕事が早く増える)。
pub fn pipeline<T, R>(readers: usize, seed: Vec<T>, step: impl Fn(T) -> (Vec<T>, Option<R>) + Sync) -> Vec<R>
where
    T: Send,
    R: Send,
{
    let queue = Queue { state: Mutex::new(Pending { tasks: seed, active: 0 }), wake: Condvar::new() };
    let mut results = Vec::new();
    std::thread::scope(|scope| {
        let mut helpers = Vec::new();
        for _ in 1..readers.max(1) {
            // thread を起こせない機体(pid の上限など)でも止まらない — 起こせた分と呼び手の thread で全部済ませる。
            match std::thread::Builder::new().spawn_scoped(scope, || work(&queue, &step)) {
                Ok(handle) => helpers.push(handle),
                Err(_) => break,
            }
        }
        results.extend(work(&queue, &step));
        for handle in helpers {
            match handle.join() {
                Ok(part) => results.extend(part),
                Err(panic) => std::panic::resume_unwind(panic),
            }
        }
    });
    results
}

struct Queue<T> {
    state: Mutex<Pending<T>>,
    wake: Condvar,
}

struct Pending<T> {
    tasks: Vec<T>,
    /// 今 `step` の中にいる thread の数。0 で `tasks` も空なら、仕事は全部済んだ。
    active: usize,
}

fn locked<T>(queue: &Queue<T>) -> MutexGuard<'_, Pending<T>> {
    queue.state.lock().unwrap_or_else(PoisonError::into_inner)
}

/// 仕事 1 つの間 `active` を数える印。終わり(`step` が panic した時も)で減らし、全部済んだなら待っている thread を起こす。
struct InStep<'a, T> {
    queue: &'a Queue<T>,
}

impl<T> Drop for InStep<'_, T> {
    fn drop(&mut self) {
        let mut state = locked(self.queue);
        state.active -= 1;
        if state.active == 0 && state.tasks.is_empty() {
            self.queue.wake.notify_all();
        }
    }
}

fn work<T, R>(queue: &Queue<T>, step: &(impl Fn(T) -> (Vec<T>, Option<R>) + Sync)) -> Vec<R> {
    let mut results = Vec::new();
    loop {
        let task = {
            let mut state = locked(queue);
            loop {
                if let Some(task) = state.tasks.pop() {
                    state.active += 1;
                    break Some(task);
                }
                if state.active == 0 {
                    break None;
                }
                state = queue.wake.wait(state).unwrap_or_else(PoisonError::into_inner);
            }
        };
        let Some(task) = task else { return results };
        let in_step = InStep { queue };
        let (more, result) = step(task);
        results.extend(result);
        if !more.is_empty() {
            // 続きの仕事を積んでから `active` を減らす(順が逆だと、仕事が残っているのに全部済んだと見える瞬間ができる)。
            let added = more.len();
            locked(queue).tasks.extend(more);
            for _ in 0..added {
                queue.wake.notify_one();
            }
        }
        drop(in_step);
    }
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

    /// 仕事が仕事を生む木(根 → 10 → 100 → 1000)を、層の切れ目なしに重ねて済ませる: 全部の仕事が 1 度ずつ済み、結果が揃い、同時に進んだ仕事が多い。
    #[test]
    fn a_pipeline_runs_every_task_once_and_overlaps_the_waits_across_levels() {
        let in_flight = AtomicUsize::new(0);
        let peak = AtomicUsize::new(0);
        let results = pipeline(WIDE_READERS, vec![(0usize, 0usize)], |(depth, id)| {
            let now = in_flight.fetch_add(1, Ordering::SeqCst) + 1;
            peak.fetch_max(now, Ordering::SeqCst);
            std::thread::sleep(Duration::from_millis(3));
            in_flight.fetch_sub(1, Ordering::SeqCst);
            let more = if depth < 3 { (0..10).map(|child| (depth + 1, id * 10 + child)).collect() } else { Vec::new() };
            (more, Some((depth, id)))
        });
        assert_eq!(results.len(), 1 + 10 + 100 + 1000);
        let mut ids: Vec<(usize, usize)> = results;
        ids.sort_unstable();
        ids.dedup();
        assert_eq!(ids.len(), 1111, "同じ仕事を 2 度済ませた");
        assert!(peak.load(Ordering::SeqCst) >= 8, "待ちが重なっていない(同時に進んだ最大 {} 件)", peak.load(Ordering::SeqCst));
        assert!(peak.load(Ordering::SeqCst) <= WIDE_READERS);
    }

    #[test]
    fn a_pipeline_without_tasks_or_with_one_reader_still_finishes() {
        assert_eq!(pipeline(WIDE_READERS, Vec::<u8>::new(), |n| (Vec::new(), Some(n))), Vec::<u8>::new());
        let mut one = pipeline(1, vec![3u8], |n| (if n > 0 { vec![n - 1] } else { Vec::new() }, Some(n)));
        one.sort_unstable();
        assert_eq!(one, vec![0, 1, 2, 3]);
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
