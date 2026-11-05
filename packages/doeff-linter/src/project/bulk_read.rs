//! Overlaps the waiting in places that read many small files in bulk (the first run on a cold working tree).
//!
//! A one-file run reads the registry dir (one file per key; 447 files in agora-controllers) and the sources of the modules the written file names (tens to hundreds of files).
//! Each working tree has different inodes, so on a cold working tree every file waits once on the disk. Reading in sequence makes the waits add up; in a busy Pod (load 70-100,
//! io pressure 12-30%) on a cold working tree, reading the registry alone took a median of 2.4 seconds and a maximum of 10.8 seconds (the hook deadline is 4 seconds). Waiting uses no CPU, and
//! the disk returns faster the more requests are queued in parallel, so reads are overlapped with many threads (measured on 449 cold files: serial 6.4 s / 8 threads 0.93 s / 16 threads 0.62 s /
//! 32 threads 0.37 s, all medians).
//!
//! The places that read all the repo's Hy (about 2900 files) and open about 1200 dirs (the walk of `hy_files::collect`) overlap with even more threads (`fan_out_wide`).
//!
//! The rayon pool of a one-file run is limited to 4 threads (more threads do not speed up CPU work; see `SINGLE_FILE_THREADS` in main.rs).
//! Overlapping waits is not CPU work, so threads of `std::thread::scope` are started only for the duration of the reads, separate from that pool.
//!
//! Overlapping waits is worthwhile only when the wait is a disk wait: starting 128 threads while reads can be served from the page cache does not speed the work up and only adds CPU (`Widen`).
//! So it starts with a small number, and if long-waiting work continues, it grows to the number the caller decided.

use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::{Condvar, Mutex, MutexGuard, PoisonError};
use std::thread::Scope;
use std::time::{Duration, Instant};

/// Number of threads that overlap waits (including the caller's thread).
pub const READERS: usize = 32;

/// Number of threads that overlap waits when reading and opening all of the repo's Hy (about 2900 files, about 1200 dirs). With an arrangement like READERS, which suffices for the registry and named sources (hundreds of files),
/// reading all files on a cold working tree took 3.3 seconds (median; agora-controllers 2839 files; Pod load 83), versus 1.8 seconds with 128 threads and 1.9 seconds with 256.
/// A thread uses no CPU while waiting on the disk (the stack costs only what is touched), so use 128, just short of the plateau. However, the number started is an upper bound the caller decides, and the number actually running
/// grows only when the wait looks like a disk wait (`WARM_READERS`, below).
pub const WIDE_READERS: usize = 128;

/// Fewer items than this are read in sequence on the caller's thread, since starting threads costs more.
const SERIAL_BELOW: usize = 4;

/// Threads that overlap waits grow **only when the wait looks like a disk wait**. While reads are served from the page cache (one item takes tens of microseconds), starting 128 threads
/// does not speed the work up and only adds CPU for startup and rendezvous (on a busy machine [load 80], reading the repo's 4,144 Hy files from the page cache took 7-14 seconds of CPU with 128 threads
/// and 0.4-0.8 seconds with 1). So it starts with `WARM_READERS` threads and, once work that read the disk reaches `SLOW_IN_ROW` items, grows once to the number
/// the caller decided. On a cold working tree it grows within the first few items, so the number of overlapped waits does not change.
///
/// Whether the disk was read is judged by the number of block reads that thread requested (`getrusage` RUSAGE_THREAD, ru_inblock; see `thread_reads`): work served from the page cache
/// stays at 0, while reads of a cold dir, inode, or contents increase it (measured 2026-10-23: 8-544 for one read of a cold dir; stat is 0 after the inode readahead has taken effect). It used to be judged by the time of one item
/// (`SLOW_ITEM` or more, `SLOW_IN_ROW` times in a row), but in a Pod with load over 100, waiting for a CPU turn alone made one item exceed 500 microseconds, and even on a warmed working tree
/// it grew to 128 threads, and the stage that collects the repo's Hy used 0.6-4.9 seconds of sys CPU. Only machines where the count cannot be read judge by time.
const WARM_READERS: usize = 4;
const SLOW_ITEM: Duration = Duration::from_micros(500);
const SLOW_IN_ROW: usize = 3;

/// Number of block reads this thread has requested so far (in 512-byte units; ru_inblock of `getrusage(RUSAGE_THREAD)`). None on machines where it cannot be read.
#[cfg(target_os = "linux")]
fn thread_reads() -> Option<u64> {
    let mut usage = std::mem::MaybeUninit::<libc::rusage>::zeroed();
    // SAFETY: getrusage only writes the rusage it is given and touches no other memory. The written value is read only when 0 is returned (all fields are also initialized by zeroed).
    let done = unsafe { libc::getrusage(libc::RUSAGE_THREAD, usage.as_mut_ptr()) } == 0;
    // SAFETY: all bytes are initialized by the zeroed above, and every field of rusage is an integer type.
    done.then(|| unsafe { usage.assume_init() }.ru_inblock).and_then(|reads| u64::try_from(reads).ok())
}

#[cfg(not(target_os = "linux"))]
fn thread_reads() -> Option<u64> {
    None
}

/// The marker taken when one piece of work starts (the time, and that thread's number of block reads).
struct Began {
    at: Instant,
    reads: Option<u64>,
}

/// The verdict when one piece of work finishes.
struct Ended {
    /// That work was slow (it read the disk; when judging by time, it took `slow_item` or more).
    slow: bool,
    /// The first time, when growing the threads.
    widen: bool,
}

/// Tells from per-work markers whether the wait is a disk wait (counted across all threads).
struct Widen {
    slow_item: Duration,
    slow_in_row: usize,
    seen_in_row: AtomicUsize,
    /// Number of pieces of work that read the disk (when `by_disk`).
    seen_reading: AtomicUsize,
    /// Whether to judge by the thread's block-read count (if false, judge by the time of one item).
    by_disk: bool,
    widened: AtomicBool,
}

impl Widen {
    /// Judges by the standard values: by block-read count on machines that can read the thread's count, otherwise by time (`SLOW_ITEM`, `SLOW_IN_ROW`).
    fn standard() -> Self {
        Widen { by_disk: thread_reads().is_some(), ..Widen::new(SLOW_ITEM, SLOW_IN_ROW) }
    }

    /// Treated as the time to grow when `slow_in_row` consecutive items each take `slow_item` or more (kept separate from the standard values so that tests can supply values that do not depend on the clock).
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

    /// The marker taken when one piece of work starts.
    fn begin(&self) -> Began {
        Began { at: Instant::now(), reads: if self.by_disk { thread_reads() } else { None } }
    }

    /// The work started with `began` has finished. Whether that work was slow (read the disk, or took `slow_item` or more), and whether this is the first time, when growing.
    fn end(&self, began: Began) -> Ended {
        match began.reads.zip(thread_reads()) {
            Some((before, after)) if self.by_disk => Ended { slow: after > before, widen: self.observe_reading(after > before) },
            _ => {
                let took = began.at.elapsed();
                Ended { slow: took >= self.slow_item, widen: self.observe(took) }
            }
        }
    }

    /// One piece of work took `took`. true only the first time slow work continues (when growing).
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

    /// Whether one piece of work read the disk (`read`). true only the first time the pieces that read the disk reach `slow_in_row`. Whether they continue does not matter: the read count
    /// does not grow from waiting for a CPU turn, so there is no need to filter out a single delay (a stat after a cold dir does not read because of inode readahead, so counting consecutively would break even on a cold walk).
    fn observe_reading(&self, read: bool) -> bool {
        if !read || self.seen_reading.fetch_add(1, Ordering::Relaxed) + 1 < self.slow_in_row {
            return false;
        }
        !self.widened.swap(true, Ordering::Relaxed)
    }
}

/// Applies `work` to all of `items` and returns the results in the order of `items`. A tool for overlapping waits (file reads, stat); `work` must be independent of one another.
pub fn fan_out<X, R>(items: &[X], work: impl Fn(&X) -> R + Sync) -> Vec<R>
where
    X: Sync,
    R: Send,
{
    fan_out_with(READERS, items, work)
}

/// The form of `fan_out` for places that read and open the whole repo (`WIDE_READERS` threads).
pub fn fan_out_wide<X, R>(items: &[X], work: impl Fn(&X) -> R + Sync) -> Vec<R>
where
    X: Sync,
    R: Send,
{
    fan_out_with(WIDE_READERS, items, work)
}

/// The form of `fan_out` where the caller decides the number of threads `readers` that overlap waits.
pub fn fan_out_with<X, R>(readers: usize, items: &[X], work: impl Fn(&X) -> R + Sync) -> Vec<R>
where
    X: Sync,
    R: Send,
{
    fan_out_tuned(readers, Widen::standard(), items, work)
}

/// The form of `fan_out_with` where the caller decides how to tell when to grow, `widen` (for tests).
fn fan_out_tuned<X, R>(readers: usize, widen: Widen, items: &[X], work: impl Fn(&X) -> R + Sync) -> Vec<R>
where
    X: Sync,
    R: Send,
{
    #[cfg(test)]
    let watch = watch::current();
    // The test's observation (`watch`) imitates a disk wait with sleep, so it judges by time rather than by the disk read count (sleep does not read the disk).
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
    spread.slots.into_iter().map(|slot| slot.into_inner().expect("fan_out result slot").expect("fan_out places a result for every item")).collect()
}

/// The work store of `fan_out_with`: threads draw items one by one, and when slow waits continue, more threads draw.
struct Spread<'a, X, R> {
    items: &'a [X],
    run: &'a (dyn Fn(&X) -> R + Sync),
    next: AtomicUsize,
    slots: Vec<Mutex<Option<R>>>,
    widen: Widen,
    /// Number of threads at the start (including the caller).
    first: usize,
    /// Upper bound on the number of threads after growing (including the caller).
    readers: usize,
}

impl<'a, X: Sync, R: Send> Spread<'a, X, R> {
    fn pull<'scope, 'env>(&'env self, scope: &'scope Scope<'scope, 'env>) {
        loop {
            let index = self.next.fetch_add(1, Ordering::Relaxed);
            let Some(item) = self.items.get(index) else { break };
            let began = self.widen.begin();
            let result = (self.run)(item);
            *self.slots[index].lock().expect("fan_out result slot") = Some(result);
            if self.widen.end(began).widen {
                self.add_readers(scope, self.readers - self.first);
            }
        }
    }

    fn add_readers<'scope, 'env>(&'env self, scope: &'scope Scope<'scope, 'env>, count: usize) {
        for _ in 0..count {
            // It does not stop even on machines that cannot start threads (pid limit, etc.): the threads that could be started and the other threads read everything.
            if std::thread::Builder::new().spawn_scoped(scope, move || self.pull(scope)).is_err() {
                break;
            }
        }
    }
}

/// A tool for overlapping waits when work generates work. Starting from the `seed` work, `step` finishes one piece of work and returns the follow-up work and the result (if any).
///
/// It overlaps walking the repo's tree (opening a dir finds the dirs and files inside) with reading the files found, without layer boundaries: follow-up work is picked up immediately by other threads, so
/// the wait for opening a deep dir and the wait for reading files found in a shallow place progress at the same time. Advancing layer by layer would make everything line up behind the slowest wait in the layer.
/// The order of results is not fixed (the caller sorts). Work must be independent of one another. Work pushed later is picked up first (opening a dir first makes the work that can be queued grow sooner).
pub fn pipeline<T, R>(readers: usize, seed: Vec<T>, step: impl Fn(T) -> (Vec<T>, Option<R>) + Sync) -> Vec<R>
where
    T: Send,
    R: Send,
{
    pipeline_tuned(readers, Widen::standard(), seed, step)
}

/// The form of `pipeline` where the caller decides how to tell when to grow, `widen` (for tests).
fn pipeline_tuned<T, R>(readers: usize, widen: Widen, seed: Vec<T>, step: impl Fn(T) -> (Vec<T>, Option<R>) + Sync) -> Vec<R>
where
    T: Send,
    R: Send,
{
    let readers = readers.max(1);
    let crew = Crew {
        queue: Queue { state: Mutex::new(Pending { tasks: seed, active: 0 }), wake: Condvar::new(), waiting: AtomicUsize::new(0), locks: AtomicUsize::new(0) },
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
    #[cfg(test)]
    LAST_PIPELINE_LOCKS.with(|last| last.set(crew.queue.locks.load(Ordering::Relaxed)));
    crew.results.into_inner().unwrap_or_else(PoisonError::into_inner)
}

#[cfg(test)]
thread_local! {
    /// How many times the shared queue's lock was taken in the `pipeline` this thread ran last (for tests).
    static LAST_PIPELINE_LOCKS: std::cell::Cell<usize> = const { std::cell::Cell::new(0) };
}

/// The work store of `pipeline`: threads draw work, and when slow waits continue, more threads draw. Results are added to `results` when a thread finishes.
struct Crew<'a, T, R> {
    queue: Queue<T>,
    step: &'a (dyn Fn(T) -> (Vec<T>, Option<R>) + Sync),
    widen: Widen,
    /// Number of threads at the start (including the caller).
    first: usize,
    /// Upper bound on the number of threads after growing (including the caller).
    readers: usize,
    results: Mutex<Vec<R>>,
}

impl<'a, T: Send, R: Send> Crew<'a, T, R> {
    fn add_readers<'scope, 'env>(&'env self, scope: &'scope Scope<'scope, 'env>, count: usize) {
        for _ in 0..count {
            // It does not stop even on machines that cannot start threads (pid limit, etc.): the threads that could be started and the caller's thread finish everything.
            if std::thread::Builder::new().spawn_scoped(scope, move || self.work(scope)).is_err() {
                break;
            }
        }
    }

    /// Keeps drawing until the work runs out (no waiting work and no thread holding work remain).
    ///
    /// Follow-up work is pushed onto the local stack (`mine`) and finished by oneself; the shared queue's lock is taken only when the local stack is empty and when handing work to a waiting thread. If every piece of work
    /// went through the shared queue, then with small work that can be served from the page cache (one stat), 128 threads would fight over one lock, and the CPU of the first run on a new working tree
    /// was 10-34 seconds in sys alone. After slow work (a disk wait), if a thread is waiting, half of the local stack is handed to the shared queue,
    /// so the waits on a cold working tree overlap as before.
    fn work<'scope, 'env>(&'env self, scope: &'scope Scope<'scope, 'env>) {
        let mut results = Vec::new();
        loop {
            let Some((mut mine, holding)) = self.take() else { break };
            while let Some(task) = mine.pop() {
                let began = self.widen.begin();
                let (more, result) = (self.step)(task);
                results.extend(result);
                mine.extend(more);
                let ended = self.widen.end(began);
                // Only after slow work (a disk wait), hand half of the side picked up later (the head of the queue) to a waiting thread. Fast work is better finished locally, with less CPU and less lock use.
                // While work remains locally, `active` is still held, so it cannot appear finished before handing over.
                if ended.slow && mine.len() > 1 && self.queue.waiting.load(Ordering::Relaxed) > 0 {
                    let shared: Vec<T> = mine.drain(..mine.len() / 2).collect();
                    locked(&self.queue).tasks.extend(shared);
                    self.queue.wake.notify_one();
                }
                if ended.widen {
                    self.add_readers(scope, self.readers - self.first);
                }
            }
            drop(holding);
        }
        self.results.lock().unwrap_or_else(PoisonError::into_inner).extend(results);
    }

    /// From the shared queue, draws half of the side to be picked up next (at least 1 item) into the local stack, and returns it with the marker that counts `active` (the parallel threads split the rest; the number of lock acquisitions is
    /// decided by the number of threads and splits, not by the amount of work). Even if the queue is empty, if a thread holds work, it waits until work is handed over or everything finishes. None when everything has finished.
    fn take(&self) -> Option<(Vec<T>, Holding<'_, T>)> {
        let mut state = locked(&self.queue);
        loop {
            if !state.tasks.is_empty() {
                let keep = state.tasks.len() - (state.tasks.len() / 2).max(1);
                let mine = state.tasks.split_off(keep);
                state.active += 1;
                // If any remains, wake only one waiting thread (the woken thread draws half of the rest and wakes the next; do not wake all at once and fight over the lock).
                if !state.tasks.is_empty() {
                    self.queue.wake.notify_one();
                }
                return Some((mine, Holding { queue: &self.queue }));
            }
            if state.active == 0 {
                return None;
            }
            self.queue.waiting.fetch_add(1, Ordering::Relaxed);
            state = self.queue.wake.wait(state).unwrap_or_else(PoisonError::into_inner);
            self.queue.waiting.fetch_sub(1, Ordering::Relaxed);
        }
    }
}

struct Queue<T> {
    state: Mutex<Pending<T>>,
    wake: Condvar,
    /// Number of threads waiting with the shared queue empty (decides without taking the lock whether a thread holding work should hand over its local work).
    waiting: AtomicUsize,
    /// Number of times the shared queue's lock was taken (a test checks that it is far fewer than the amount of work).
    locks: AtomicUsize,
}

struct Pending<T> {
    tasks: Vec<T>,
    /// Number of threads that currently hold work (they have drawn from the shared queue and are finishing local work). If 0 and `tasks` is also empty, all the work is finished.
    active: usize,
}

fn locked<T>(queue: &Queue<T>) -> MutexGuard<'_, Pending<T>> {
    queue.locks.fetch_add(1, Ordering::Relaxed);
    queue.state.lock().unwrap_or_else(PoisonError::into_inner)
}

/// A marker that counts `active` while a thread holds work. It decrements when the local work runs out (also when `step` panics), and wakes waiting threads if everything is finished.
struct Holding<'a, T> {
    queue: &'a Queue<T>,
}

impl<T> Drop for Holding<'_, T> {
    fn drop(&mut self) {
        let mut state = locked(self.queue);
        state.active -= 1;
        if state.active == 0 && state.tasks.is_empty() {
            self.queue.wake.notify_all();
        }
    }
}

/// Observation placed by tests: counts how many pieces of `fan_out` work progressed at once (to check, without relying on the clock, that reads really overlap).
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

    /// Observes the `fan_out` that the caller's thread starts during `body`, and returns (the result of `body`, the maximum number of pieces of work in progress at once). Each piece of work waits `hold` so that overlap appears.
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

    /// A Widen that judges by time, for tests that imitate a disk wait with sleep (sleep does not read the disk, so the standard way of telling would not grow).
    fn by_time() -> Widen {
        Widen::new(SLOW_ITEM, SLOW_IN_ROW)
    }

    #[test]
    fn it_overlaps_the_waits_of_many_items() {
        let items: Vec<usize> = (0..64).collect();
        let (out, peak) = watching(Duration::from_millis(20), || fan_out_tuned(READERS, by_time(), &items, |n| *n));
        assert_eq!(out, items);
        assert!(peak >= 8, "waits are not overlapping (maximum in progress at once: {peak}; the waits on a cold disk would add up)");
        assert!(peak <= READERS, "the number of threads started exceeded the upper bound {READERS} ({peak})");
    }

    #[test]
    fn the_wide_form_overlaps_more_waits_than_the_plain_form_and_stays_within_its_limit() {
        let items: Vec<usize> = (0..400).collect();
        let (out, peak) = watching(Duration::from_millis(20), || fan_out_tuned(WIDE_READERS, by_time(), &items, |n| *n));
        assert_eq!(out, items);
        assert!(peak > READERS, "waits are not overlapping more widely than READERS (maximum in progress at once: {peak})");
        assert!(peak <= WIDE_READERS, "the number of threads started exceeded the upper bound {WIDE_READERS} ({peak})");
    }

    /// A tree where work generates work (root -> 10 -> 100 -> 1000) is finished with overlap and no layer boundaries: every piece of work is finished exactly once, the results are complete, and many pieces are in progress at once.
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
        assert_eq!(ids.len(), 1111, "the same work was finished twice");
        assert!(peak.load(Ordering::SeqCst) >= 8, "waits are not overlapping (maximum in progress at once: {})", peak.load(Ordering::SeqCst));
        assert!(peak.load(Ordering::SeqCst) <= WIDE_READERS);
    }

    /// A tree of fast work (1111 items) finishes with far fewer shared-queue lock acquisitions than the amount of work. The arrangement where every piece of work fights over the lock, on the first run on a new working tree,
    /// had 128 threads cycling through work the size of one stat and used 10-34 seconds of sys CPU. The same holds when threads grew because of slow waits.
    #[test]
    fn a_pipeline_takes_the_shared_queue_lock_far_fewer_times_than_there_are_tasks() {
        let tree = |widen: Widen, hold: Duration| {
            let results = pipeline_tuned(WIDE_READERS, widen, vec![(0usize, 0usize)], |(depth, id)| {
                std::thread::sleep(hold);
                let more = if depth < 3 { (0..10).map(|child| (depth + 1, id * 10 + child)).collect() } else { Vec::new() };
                (more, Some((depth, id)))
            });
            assert_eq!(results.len(), 1111, "all work is finished exactly once");
            LAST_PIPELINE_LOCKS.with(std::cell::Cell::get)
        };
        let quick = tree(Widen::new(Duration::from_secs(3600), SLOW_IN_ROW), Duration::ZERO);
        assert!(quick < 1111 / 4, "took the shared queue's lock {quick} times for 1111 fast items");
        let slow = tree(Widen::new(Duration::ZERO, SLOW_IN_ROW), Duration::from_micros(300));
        assert!(slow < 1111 * 2, "took the shared queue's lock {slow} times for 1111 slow items with grown threads");
    }

    #[test]
    fn a_pipeline_without_tasks_or_with_one_reader_still_finishes() {
        assert_eq!(pipeline(WIDE_READERS, Vec::<u8>::new(), |n| (Vec::new(), Some(n))), Vec::<u8>::new());
        let mut one = pipeline(1, vec![3u8], |n| (if n > 0 { vec![n - 1] } else { Vec::new() }, Some(n)));
        one.sort_unstable();
        assert_eq!(one, vec![0, 1, 2, 3]);
    }

    /// Number of threads that worked (collects the thread id per item).
    fn threads_used<X: Sync>(items: &[X], widen: Widen, hold: Duration) -> usize {
        let seen = Mutex::new(std::collections::HashSet::new());
        fan_out_tuned(WIDE_READERS, widen, items, |_| {
            std::thread::sleep(hold);
            seen.lock().unwrap().insert(std::thread::current().id());
        });
        seen.into_inner().unwrap().len()
    }

    /// While reads are served from the page cache (one item is fast), threads do not grow: reading with 128 threads only adds CPU.
    #[test]
    fn quick_items_stay_within_the_warm_readers() {
        let items: Vec<usize> = (0..2000).collect();
        // Set the time regarded as slow to 1 hour, to eliminate runs that grow because of clock jitter.
        let used = threads_used(&items, Widen::new(Duration::from_secs(3600), SLOW_IN_ROW), Duration::ZERO);
        assert!(used <= WARM_READERS, "{used} threads ran on fast work (upper bound {WARM_READERS})");
    }

    /// If waits are consistently long (a cold disk), the threads grow up to the number the caller decided.
    #[test]
    fn slow_items_widen_to_the_readers_the_caller_chose() {
        let items: Vec<usize> = (0..400).collect();
        let used = threads_used(&items, Widen::new(Duration::ZERO, SLOW_IN_ROW), Duration::from_millis(3));
        assert!(used > WARM_READERS, "threads do not grow even though slow work continues ({used} threads)");
        assert!(used <= WIDE_READERS, "the number of threads started exceeded the upper bound {WIDE_READERS} ({used} threads)");
    }

    /// A single slow item (a delay caused by a thread being stopped) does not grow it: only continuing delays are regarded as waits.
    #[test]
    fn one_slow_item_among_quick_ones_does_not_widen() {
        let widen = Widen::new(Duration::from_millis(1), 3);
        assert!(!widen.observe(Duration::from_millis(5)));
        assert!(!widen.observe(Duration::from_millis(5)));
        assert!(!widen.observe(Duration::from_micros(10)), "if a fast item is interleaved, the count starts over");
        assert!(!widen.observe(Duration::from_millis(5)));
        assert!(!widen.observe(Duration::from_millis(5)));
        assert!(widen.observe(Duration::from_millis(5)), "grow if 3 consecutive items are slow");
        assert!(!widen.observe(Duration::from_millis(5)), "grow only once");
    }

    /// With the standard way of telling, work that does not read the disk does not grow the threads even if one item is slow (waiting for a CPU turn, waiting on a lock): in a Pod with load over 100, the warmed working tree's
    /// reading of the repo's Hy grew to 128 threads and used 0.6-4.9 seconds of sys CPU. Not checked on machines that cannot read the thread's read count.
    #[test]
    fn slow_items_that_read_nothing_from_disk_stay_within_the_warm_readers() {
        if thread_reads().is_none() {
            return;
        }
        let items: Vec<usize> = (0..200).collect();
        let used = threads_used(&items, Widen::standard(), Duration::from_millis(2));
        assert!(used <= WARM_READERS, "{used} threads ran on slow work that does not read the disk (upper bound {WARM_READERS})");
        let seen = Mutex::new(std::collections::HashSet::new());
        pipeline_tuned(WIDE_READERS, Widen::standard(), vec![(0usize, 0usize)], |(depth, id)| {
            std::thread::sleep(Duration::from_millis(1));
            seen.lock().unwrap().insert(std::thread::current().id());
            ((if depth < 2 { (0..10).map(|child| (depth + 1, id * 10 + child)).collect() } else { Vec::new() }), Some(()))
        });
        let used = seen.into_inner().unwrap().len();
        assert!(used <= WARM_READERS, "{used} threads ran on a slow walk that does not read the disk (upper bound {WARM_READERS})");
    }

    /// Grows only once when work that read the disk reaches `slow_in_row` items: it does not start over even if work that does not read the disk is interleaved (a stat after a cold dir does not read the disk because of
    /// inode readahead, so counting consecutively would not grow it even on a cold walk).
    #[test]
    fn items_that_read_from_disk_widen_once_even_with_cached_items_between() {
        let widen = Widen { by_disk: true, ..Widen::new(Duration::from_secs(3600), 3) };
        assert!(!widen.observe_reading(true));
        assert!(!widen.observe_reading(false));
        assert!(!widen.observe_reading(false));
        assert!(!widen.observe_reading(true));
        assert!(!widen.observe_reading(false));
        assert!(widen.observe_reading(true), "grow at the 3rd item that read the disk");
        assert!(!widen.observe_reading(true), "grow only once");
    }

    /// The thread's read count is a number that does not decrease per thread, on machines where it can be read.
    #[test]
    fn the_reads_of_a_thread_never_go_down() {
        let Some(first) = thread_reads() else { return };
        let _ = std::fs::read(std::env::current_exe().unwrap());
        assert!(thread_reads().is_some_and(|later| later >= first));
    }

    /// The form where work generates work also uses few threads while work is fast, and grows when slow waits continue.
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
            assert_eq!(results.len(), 1111, "all work is finished exactly once");
            seen.into_inner().unwrap().len()
        };
        let quick = tree(Widen::new(Duration::from_secs(3600), SLOW_IN_ROW), Duration::ZERO);
        assert!(quick <= WARM_READERS, "{quick} threads ran on fast work (upper bound {WARM_READERS})");
        let slow = tree(Widen::new(Duration::ZERO, SLOW_IN_ROW), Duration::from_millis(2));
        assert!(slow > WARM_READERS && slow <= WIDE_READERS, "number of threads when slow waits continued {slow}");
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
        let out = fan_out(&items, |n| if n % 7 == 3 { Err(format!("cannot read {n}")) } else { Ok(*n) });
        for (n, result) in items.iter().zip(&out) {
        assert_eq!(result, &if n % 7 == 3 { Err(format!("cannot read {n}")) } else { Ok(*n) });
        }
    }

    #[test]
    fn it_gives_an_empty_answer_for_no_items() {
        assert_eq!(fan_out(&[] as &[u8], |n| *n), Vec::<u8>::new());
    }

    /// Even if work using a `Mutex` progresses on both the caller's thread and other threads, no results are lost.
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
