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
//!
//! 待ちを重ねる意味があるのは待ちが disk の待ちの時だけ: page cache から読める間に thread を 128 本起こすと、仕事は速くならずに CPU だけが増える(`Widen`)。
//! そこで少ない本数で始め、待ちが長い仕事が続いたら呼び手が決めた本数まで増やす。

use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::{Condvar, Mutex, MutexGuard, PoisonError};
use std::thread::Scope;
use std::time::{Duration, Instant};

/// 待ちを重ねる thread の数(呼び手の thread を含む)。
pub const READERS: usize = 32;

/// repo の Hy の全部(約 2900 file・約 1200 の dir)を読む・開く時に待ちを重ねる thread の数。登録簿や名指しの源(数百 file)の READERS で足りる並べ方では、
/// 冷えた作業木の全 file の読みが 3.3 秒(中央値・agora-controllers 2839 file・Pod の load 83)かかり、128 本で 1.8 秒・256 本で 1.9 秒だった。
/// thread は disk を待っている間 CPU を使わない(stack は触った分だけ)ので、頭打ちの手前の 128 本にする。ただし起こす本数は呼び手が決める上限で、実際に動く本数は
/// 待ちが disk の待ちだと見えた時だけ増える(`WARM_READERS`・下)。
pub const WIDE_READERS: usize = 128;

/// これより少ない件数は、thread を起こす手間のほうが重いので呼び手の thread で順に読む。
const SERIAL_BELOW: usize = 4;

/// 待ちを重ねる thread は、**待ちが disk の待ちだと見えた時だけ**増やす。page cache から読めている間(1 件が数十マイクロ秒)は、thread を 128 本起こしても
/// 仕事は速くならず、起動と待ち合わせの CPU だけが増える(混んだ機体〔load 80〕で repo の Hy 4,144 file を page cache から読むのに、128 本で CPU 7〜14 秒・
/// 1 本で 0.4〜0.8 秒 — agora-redesign #1022)。そこで `WARM_READERS` 本で始め、disk を読んだ仕事が `SLOW_IN_ROW` 件になったら、呼び手が決めた本数まで
/// 1 度だけ増やす。冷えた作業木では最初の数件で増えるので、重なる待ちの数は変わらない。
///
/// disk を読んだかは、その thread が頼んだ block の読みの数(`getrusage` の RUSAGE_THREAD・ru_inblock — `thread_reads`)で見る: page cache から読めた仕事は
/// 0 のまま、冷えた dir・inode・中身の読みは増える(2026-10-23 の実測: 冷えた dir の読み 1 回で 8〜544・inode の先読みが効いた後の stat は 0)。前は 1 件の時間
/// (`SLOW_ITEM` 以上が `SLOW_IN_ROW` 件続く)で見ていたが、負荷 100 を越える Pod では CPU の順番待ちだけで 1 件が 500 マイクロ秒を越え、温まった作業木でも
/// 128 本へ増えて、repo の Hy を集める段が sys の CPU を 0.6〜4.9 秒使っていた(card ki-40c8ca380713)。数を読めない機体だけ時間で見る。
const WARM_READERS: usize = 4;
const SLOW_ITEM: Duration = Duration::from_micros(500);
const SLOW_IN_ROW: usize = 3;

/// この thread がこれまでに頼んだ block の読みの数(512 byte 単位 — `getrusage(RUSAGE_THREAD)` の ru_inblock)。読めない機体では None。
#[cfg(target_os = "linux")]
fn thread_reads() -> Option<u64> {
    let mut usage = std::mem::MaybeUninit::<libc::rusage>::zeroed();
    // SAFETY: getrusage は渡した rusage を書くだけで、ほかの memory に触らない。0 が返った時だけ書かれた値を読む(zeroed で全欄が初期化済みでもある)。
    let done = unsafe { libc::getrusage(libc::RUSAGE_THREAD, usage.as_mut_ptr()) } == 0;
    // SAFETY: 上の zeroed で全 byte が初期化されていて、rusage は全欄が整数の型。
    done.then(|| unsafe { usage.assume_init() }.ru_inblock).and_then(|reads| u64::try_from(reads).ok())
}

#[cfg(not(target_os = "linux"))]
fn thread_reads() -> Option<u64> {
    None
}

/// 仕事 1 つを始めた時の印(時刻と、その thread の block の読みの数)。
struct Began {
    at: Instant,
    reads: Option<u64>,
}

/// 仕事ごとの印から、待ちが disk の待ちかを見分ける(全部の thread で数える)。
struct Widen {
    slow_item: Duration,
    slow_in_row: usize,
    seen_in_row: AtomicUsize,
    /// disk を読んだ仕事の数(`by_disk` の時)。
    seen_reading: AtomicUsize,
    /// thread の block の読みの数で見分けるか(偽なら 1 件の時間で見分ける)。
    by_disk: bool,
    widened: AtomicBool,
}

impl Widen {
    /// 決まりの値で見分ける — thread の block の読みの数を読める機体ではそれで、読めなければ時間(`SLOW_ITEM`・`SLOW_IN_ROW`)で。
    fn standard() -> Self {
        Widen { by_disk: thread_reads().is_some(), ..Widen::new(SLOW_ITEM, SLOW_IN_ROW) }
    }

    /// 1 件が `slow_item` 以上かかる回が `slow_in_row` 件続いたら増やす時と見る(検が時計に頼らない値を差せるよう、決まりの値と分ける)。
    fn new(slow_item: Duration, slow_in_row: usize) -> Self {
        Widen {
            slow_item,
            slow_in_row,
            seen_in_row: AtomicUsize::new(0),
            seen_reading: AtomicUsize::new(0),
            by_disk: false,
            widened: AtomicBool::new(false),
        }
    }

    /// 仕事 1 つを始める時の印。
    fn begin(&self) -> Began {
        Began { at: Instant::now(), reads: if self.by_disk { thread_reads() } else { None } }
    }

    /// `began` で始めた仕事が済んだ。増やす時の最初の 1 回だけ true。
    fn end(&self, began: Began) -> bool {
        match began.reads.zip(thread_reads()) {
            Some((before, after)) if self.by_disk => self.observe_reading(after > before),
            _ => self.observe(began.at.elapsed()),
        }
    }

    /// 仕事 1 つが `took` かかった。遅い仕事が続いた最初の 1 回だけ true(増やす時)。
    fn observe(&self, took: Duration) -> bool {
        if took < self.slow_item {
            self.seen_in_row.store(0, Ordering::Relaxed);
            return false;
        }
        if self.seen_in_row.fetch_add(1, Ordering::Relaxed) + 1 < self.slow_in_row {
            return false;
        }
        !self.widened.swap(true, Ordering::Relaxed)
    }

    /// 仕事 1 つが disk を読んだか(`read`)。disk を読んだ仕事が `slow_in_row` 件になった最初の 1 回だけ true。続いているかは問わない — 読みの数は
    /// CPU の順番待ちで増えないので、1 回だけの遅れを外す必要が無い(冷えた dir の後の stat は inode の先読みで読まないので、続けて数えると冷えた歩きでも途切れる)。
    fn observe_reading(&self, read: bool) -> bool {
        if !read || self.seen_reading.fetch_add(1, Ordering::Relaxed) + 1 < self.slow_in_row {
            return false;
        }
        !self.widened.swap(true, Ordering::Relaxed)
    }
}

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
    fan_out_tuned(readers, Widen::standard(), items, work)
}

/// `fan_out_with` の、増やす時の見分け方 `widen` を呼び手が決める形(検のため)。
fn fan_out_tuned<X, R>(readers: usize, widen: Widen, items: &[X], work: impl Fn(&X) -> R + Sync) -> Vec<R>
where
    X: Sync,
    R: Send,
{
    #[cfg(test)]
    let watch = watch::current();
    // 検の観測(`watch`)は sleep で disk の待ちを模すので、disk の読みの数でなく時間で見分ける(sleep は disk を読まない)。
    #[cfg(test)]
    let widen = if watch.is_some() { Widen { by_disk: false, ..widen } } else { widen };
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
    let spread = Spread {
        items,
        run: &run,
        next: AtomicUsize::new(0),
        slots: items.iter().map(|_| Mutex::new(None)).collect(),
        widen,
        first: workers.min(WARM_READERS),
        readers: workers,
    };
    std::thread::scope(|scope| {
        spread.add_readers(scope, spread.first - 1);
        spread.pull(scope);
    });
    spread.slots.into_iter().map(|slot| slot.into_inner().expect("fan_out の結果の置き場").expect("fan_out は全部の item の結果を置く")).collect()
}

/// `fan_out_with` の仕事の置き場 — thread が items を 1 つずつ引き、遅い待ちが続いたら引く thread を増やす。
struct Spread<'a, X, R> {
    items: &'a [X],
    run: &'a (dyn Fn(&X) -> R + Sync),
    next: AtomicUsize,
    slots: Vec<Mutex<Option<R>>>,
    widen: Widen,
    /// 始めの thread の数(呼び手を含む)。
    first: usize,
    /// 増やした後の thread の数の上限(呼び手を含む)。
    readers: usize,
}

impl<'a, X: Sync, R: Send> Spread<'a, X, R> {
    fn pull<'scope, 'env>(&'env self, scope: &'scope Scope<'scope, 'env>) {
        loop {
            let index = self.next.fetch_add(1, Ordering::Relaxed);
            let Some(item) = self.items.get(index) else { break };
            let began = self.widen.begin();
            let result = (self.run)(item);
            *self.slots[index].lock().expect("fan_out の結果の置き場") = Some(result);
            if self.widen.end(began) {
                self.add_readers(scope, self.readers - self.first);
            }
        }
    }

    fn add_readers<'scope, 'env>(&'env self, scope: &'scope Scope<'scope, 'env>, count: usize) {
        for _ in 0..count {
            // thread を起こせない機体(pid の上限など)でも止まらない — 起こせた分と他の thread で全部読み切る。
            if std::thread::Builder::new().spawn_scoped(scope, move || self.pull(scope)).is_err() {
                break;
            }
        }
    }
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
    pipeline_tuned(readers, Widen::standard(), seed, step)
}

/// `pipeline` の、増やす時の見分け方 `widen` を呼び手が決める形(検のため)。
fn pipeline_tuned<T, R>(readers: usize, widen: Widen, seed: Vec<T>, step: impl Fn(T) -> (Vec<T>, Option<R>) + Sync) -> Vec<R>
where
    T: Send,
    R: Send,
{
    let readers = readers.max(1);
    let crew = Crew {
        queue: Queue { state: Mutex::new(Pending { tasks: seed, active: 0 }), wake: Condvar::new() },
        step: &step,
        widen,
        first: readers.min(WARM_READERS),
        readers,
        results: Mutex::new(Vec::new()),
    };
    std::thread::scope(|scope| {
        crew.add_readers(scope, crew.first - 1);
        crew.work(scope);
    });
    crew.results.into_inner().unwrap_or_else(PoisonError::into_inner)
}

/// `pipeline` の仕事の置き場 — thread が仕事を引き、遅い待ちが続いたら引く thread を増やす。結果は thread が終わる時に `results` へ足す。
struct Crew<'a, T, R> {
    queue: Queue<T>,
    step: &'a (dyn Fn(T) -> (Vec<T>, Option<R>) + Sync),
    widen: Widen,
    /// 始めの thread の数(呼び手を含む)。
    first: usize,
    /// 増やした後の thread の数の上限(呼び手を含む)。
    readers: usize,
    results: Mutex<Vec<R>>,
}

impl<'a, T: Send, R: Send> Crew<'a, T, R> {
    fn add_readers<'scope, 'env>(&'env self, scope: &'scope Scope<'scope, 'env>, count: usize) {
        for _ in 0..count {
            // thread を起こせない機体(pid の上限など)でも止まらない — 起こせた分と呼び手の thread で全部済ませる。
            if std::thread::Builder::new().spawn_scoped(scope, move || self.work(scope)).is_err() {
                break;
            }
        }
    }

    /// 仕事が尽きる(待っている仕事も、仕事の中にいる thread も無くなる)まで引き続ける。
    fn work<'scope, 'env>(&'env self, scope: &'scope Scope<'scope, 'env>) {
        let mut mine = Vec::new();
        loop {
            let task = {
                let mut state = locked(&self.queue);
                loop {
                    if let Some(task) = state.tasks.pop() {
                        state.active += 1;
                        break Some(task);
                    }
                    if state.active == 0 {
                        break None;
                    }
                    state = self.queue.wake.wait(state).unwrap_or_else(PoisonError::into_inner);
                }
            };
            let Some(task) = task else { break };
            let in_step = InStep { queue: &self.queue };
            let began = self.widen.begin();
            let (more, result) = (self.step)(task);
            mine.extend(result);
            if !more.is_empty() {
                // 続きの仕事を積んでから `active` を減らす(順が逆だと、仕事が残っているのに全部済んだと見える瞬間ができる)。
                let added = more.len();
                locked(&self.queue).tasks.extend(more);
                for _ in 0..added {
                    self.queue.wake.notify_one();
                }
            }
            drop(in_step);
            if self.widen.end(began) {
                self.add_readers(scope, self.readers - self.first);
            }
        }
        self.results.lock().unwrap_or_else(PoisonError::into_inner).extend(mine);
    }
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

    /// 時間で見分ける Widen — sleep で disk の待ちを模す test のため(sleep は disk を読まないので、決まりの見分け方では増えない)。
    fn by_time() -> Widen {
        Widen::new(SLOW_ITEM, SLOW_IN_ROW)
    }

    #[test]
    fn it_overlaps_the_waits_of_many_items() {
        let items: Vec<usize> = (0..64).collect();
        let (out, peak) = watching(Duration::from_millis(20), || fan_out_tuned(READERS, by_time(), &items, |n| *n));
        assert_eq!(out, items);
        assert!(peak >= 8, "待ちが重なっていない(同時に進んだ最大 {peak} 件 — 冷えた disk の待ちが足し算になる)");
        assert!(peak <= READERS, "起こす thread の数が上限 {READERS} を越えた({peak} 件)");
    }

    #[test]
    fn the_wide_form_overlaps_more_waits_than_the_plain_form_and_stays_within_its_limit() {
        let items: Vec<usize> = (0..400).collect();
        let (out, peak) = watching(Duration::from_millis(20), || fan_out_tuned(WIDE_READERS, by_time(), &items, |n| *n));
        assert_eq!(out, items);
        assert!(peak > READERS, "待ちが READERS より広く重なっていない(同時に進んだ最大 {peak} 件)");
        assert!(peak <= WIDE_READERS, "起こす thread の数が上限 {WIDE_READERS} を越えた({peak} 件)");
    }

    /// 仕事が仕事を生む木(根 → 10 → 100 → 1000)を、層の切れ目なしに重ねて済ませる: 全部の仕事が 1 度ずつ済み、結果が揃い、同時に進んだ仕事が多い。
    #[test]
    fn a_pipeline_runs_every_task_once_and_overlaps_the_waits_across_levels() {
        let in_flight = AtomicUsize::new(0);
        let peak = AtomicUsize::new(0);
        let results = pipeline_tuned(WIDE_READERS, by_time(), vec![(0usize, 0usize)], |(depth, id)| {
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

    /// 作業した thread の数(item ごとに thread の id を集める)。
    fn threads_used<X: Sync>(items: &[X], widen: Widen, hold: Duration) -> usize {
        let seen = Mutex::new(std::collections::HashSet::new());
        fan_out_tuned(WIDE_READERS, widen, items, |_| {
            std::thread::sleep(hold);
            seen.lock().unwrap().insert(std::thread::current().id());
        });
        seen.into_inner().unwrap().len()
    }

    /// page cache から読めている間(1 件が速い)は thread を増やさない: 128 本で読むと CPU だけが増える(agora-redesign #1022)。
    #[test]
    fn quick_items_stay_within_the_warm_readers() {
        let items: Vec<usize> = (0..2000).collect();
        // 遅いと見る時間を 1 時間にして、時計の揺れで増える回を無くす。
        let used = threads_used(&items, Widen::new(Duration::from_secs(3600), SLOW_IN_ROW), Duration::ZERO);
        assert!(used <= WARM_READERS, "速い仕事で {used} 本の thread が動いた(上限 {WARM_READERS})");
    }

    /// 待ちが続けて長い(冷えた disk)なら、呼び手が決めた本数まで増える。
    #[test]
    fn slow_items_widen_to_the_readers_the_caller_chose() {
        let items: Vec<usize> = (0..400).collect();
        let used = threads_used(&items, Widen::new(Duration::ZERO, SLOW_IN_ROW), Duration::from_millis(3));
        assert!(used > WARM_READERS, "遅い仕事が続いても thread が増えない({used} 本)");
        assert!(used <= WIDE_READERS, "起こす thread の数が上限 {WIDE_READERS} を越えた({used} 本)");
    }

    /// 遅い仕事が 1 件だけ(thread が止められた遅れ)では増やさない — 続く遅れだけを待ちと見る。
    #[test]
    fn one_slow_item_among_quick_ones_does_not_widen() {
        let widen = Widen::new(Duration::from_millis(1), 3);
        assert!(!widen.observe(Duration::from_millis(5)));
        assert!(!widen.observe(Duration::from_millis(5)));
        assert!(!widen.observe(Duration::from_micros(10)), "速い仕事が挟まれば数え直す");
        assert!(!widen.observe(Duration::from_millis(5)));
        assert!(!widen.observe(Duration::from_millis(5)));
        assert!(widen.observe(Duration::from_millis(5)), "続けて 3 件遅ければ増やす");
        assert!(!widen.observe(Duration::from_millis(5)), "増やすのは 1 度だけ");
    }

    /// 決まりの見分け方では、disk を読まない仕事は 1 件が遅くても(CPU の順番待ち・lock の待ち)増やさない — 負荷 100 を越える Pod で、温まった作業木の
    /// repo の Hy の読みが 128 本へ増えて sys の CPU を 0.6〜4.9 秒使っていた(card ki-40c8ca380713)。thread の読みの数を読めない機体では確かめない。
    #[test]
    fn slow_items_that_read_nothing_from_disk_stay_within_the_warm_readers() {
        if thread_reads().is_none() {
            return;
        }
        let items: Vec<usize> = (0..200).collect();
        let used = threads_used(&items, Widen::standard(), Duration::from_millis(2));
        assert!(used <= WARM_READERS, "disk を読まない遅い仕事で {used} 本の thread が動いた(上限 {WARM_READERS})");
        let seen = Mutex::new(std::collections::HashSet::new());
        pipeline_tuned(WIDE_READERS, Widen::standard(), vec![(0usize, 0usize)], |(depth, id)| {
            std::thread::sleep(Duration::from_millis(1));
            seen.lock().unwrap().insert(std::thread::current().id());
            ((if depth < 2 { (0..10).map(|child| (depth + 1, id * 10 + child)).collect() } else { Vec::new() }), Some(()))
        });
        let used = seen.into_inner().unwrap().len();
        assert!(used <= WARM_READERS, "disk を読まない遅い歩きで {used} 本の thread が動いた(上限 {WARM_READERS})");
    }

    /// disk を読んだ仕事が `slow_in_row` 件になったら 1 度だけ増やす — 間に disk を読まない仕事が挟まっても数え直さない(冷えた dir の後の stat は
    /// inode の先読みで disk を読まないので、続けて数えると冷えた歩きでも増えない)。
    #[test]
    fn items_that_read_from_disk_widen_once_even_with_cached_items_between() {
        let widen = Widen { by_disk: true, ..Widen::new(Duration::from_secs(3600), 3) };
        assert!(!widen.observe_reading(true));
        assert!(!widen.observe_reading(false));
        assert!(!widen.observe_reading(false));
        assert!(!widen.observe_reading(true));
        assert!(!widen.observe_reading(false));
        assert!(widen.observe_reading(true), "disk を読んだ 3 件目で増やす");
        assert!(!widen.observe_reading(true), "増やすのは 1 度だけ");
    }

    /// thread の読みの数は、読める機体では thread ごとに減らない数。
    #[test]
    fn the_reads_of_a_thread_never_go_down() {
        let Some(first) = thread_reads() else { return };
        let _ = std::fs::read(std::env::current_exe().unwrap());
        assert!(thread_reads().is_some_and(|later| later >= first));
    }

    /// 仕事が仕事を生む形も、速い間は動く thread が少なく、遅い待ちが続けば増える。
    #[test]
    fn a_pipeline_widens_only_when_the_waits_are_long() {
        let tree = |widen: Widen, hold: Duration| {
            let seen = Mutex::new(std::collections::HashSet::new());
            let results = pipeline_tuned(WIDE_READERS, widen, vec![(0usize, 0usize)], |(depth, id)| {
                std::thread::sleep(hold);
                seen.lock().unwrap().insert(std::thread::current().id());
                let more = if depth < 3 { (0..10).map(|child| (depth + 1, id * 10 + child)).collect() } else { Vec::new() };
                (more, Some((depth, id)))
            });
            assert_eq!(results.len(), 1111, "全部の仕事が 1 度ずつ済む");
            seen.into_inner().unwrap().len()
        };
        let quick = tree(Widen::new(Duration::from_secs(3600), SLOW_IN_ROW), Duration::ZERO);
        assert!(quick <= WARM_READERS, "速い仕事で {quick} 本の thread が動いた(上限 {WARM_READERS})");
        let slow = tree(Widen::new(Duration::ZERO, SLOW_IN_ROW), Duration::from_millis(2));
        assert!(slow > WARM_READERS && slow <= WIDE_READERS, "遅い待ちが続いた時の thread の数 {slow}");
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
