//! Criterion benchmarks for doeff-vm-core internals.
//!
//! Run with:
//!     cargo bench --features python_bridge -p doeff-vm-core

use criterion::{black_box, criterion_group, criterion_main, BatchSize, Criterion};

use doeff_vm_core::ids::{ContId, DispatchId, Marker, SegmentId};
use doeff_vm_core::value::Value;
use doeff_vm_core::{Continuation, Frame, Segment, SegmentArena};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

fn make_segment() -> Segment {
    Segment::new(Marker::fresh(), None)
}

fn make_segment_with_frames(count: usize) -> Segment {
    let mut seg = make_segment();
    for _ in 0..count {
        seg.push_frame(Frame::FlatMapBindResult);
    }
    seg
}

// Dummy IRStream for Frame::Program benchmarks.
mod dummy {
    use std::sync::{Arc, Mutex};

    use doeff_vm_core::ir_stream::{IRStream, IRStreamRef, IRStreamStep};
    use doeff_vm_core::rust_store::RustStore;
    use doeff_vm_core::segment::ScopeStore;
    use doeff_vm_core::value::Value;

    #[derive(Debug)]
    pub struct DummyStream;

    impl IRStream for DummyStream {
        fn resume(
            &mut self,
            _value: Value,
            _store: &mut RustStore,
            _scope: &mut ScopeStore,
        ) -> IRStreamStep {
            IRStreamStep::Return(Value::Unit)
        }

        fn throw(
            &mut self,
            exc: doeff_vm_core::driver::PyException,
            _store: &mut RustStore,
            _scope: &mut ScopeStore,
        ) -> IRStreamStep {
            IRStreamStep::Throw(exc)
        }
    }

    pub fn make_stream_ref() -> IRStreamRef {
        Arc::new(Mutex::new(Box::new(DummyStream) as Box<dyn IRStream>))
    }
}

// ---------------------------------------------------------------------------
// ID generation benchmarks
// ---------------------------------------------------------------------------

fn bench_marker_fresh(c: &mut Criterion) {
    c.bench_function("Marker::fresh", |b| {
        b.iter(|| black_box(Marker::fresh()));
    });
}

fn bench_cont_id_fresh(c: &mut Criterion) {
    c.bench_function("ContId::fresh", |b| {
        b.iter(|| black_box(ContId::fresh()));
    });
}

fn bench_dispatch_id_fresh(c: &mut Criterion) {
    c.bench_function("DispatchId::fresh", |b| {
        b.iter(|| black_box(DispatchId::fresh()));
    });
}

// ---------------------------------------------------------------------------
// Value benchmarks
// ---------------------------------------------------------------------------

fn bench_value_from_int(c: &mut Criterion) {
    c.bench_function("Value::from(i64)", |b| {
        b.iter(|| black_box(Value::from(42i64)));
    });
}

fn bench_value_from_str(c: &mut Criterion) {
    c.bench_function("Value::from(&str)", |b| {
        b.iter(|| black_box(Value::from("hello world")));
    });
}

fn bench_value_from_bool(c: &mut Criterion) {
    c.bench_function("Value::from(bool)", |b| {
        b.iter(|| black_box(Value::from(true)));
    });
}

fn bench_value_clone_int(c: &mut Criterion) {
    let val = Value::Int(42);
    c.bench_function("Value::Int clone", |b| {
        b.iter(|| black_box(val.clone()));
    });
}

fn bench_value_clone_string(c: &mut Criterion) {
    let val = Value::String("benchmark test string".to_string());
    c.bench_function("Value::String clone", |b| {
        b.iter(|| black_box(val.clone()));
    });
}

fn bench_value_as_int(c: &mut Criterion) {
    let val = Value::Int(42);
    c.bench_function("Value::as_int", |b| {
        b.iter(|| black_box(val.as_int()));
    });
}

fn bench_value_as_str(c: &mut Criterion) {
    let val = Value::String("test".to_string());
    c.bench_function("Value::as_str", |b| {
        b.iter(|| black_box(val.as_str()));
    });
}

// ---------------------------------------------------------------------------
// Arena benchmarks
// ---------------------------------------------------------------------------

fn bench_arena_alloc(c: &mut Criterion) {
    c.bench_function("SegmentArena::alloc", |b| {
        b.iter_batched(
            || (SegmentArena::new(), make_segment()),
            |(mut arena, seg)| {
                black_box(arena.alloc(seg));
            },
            BatchSize::SmallInput,
        );
    });
}

fn bench_arena_alloc_100(c: &mut Criterion) {
    c.bench_function("SegmentArena::alloc x100", |b| {
        b.iter_batched(
            SegmentArena::new,
            |mut arena| {
                for _ in 0..100 {
                    arena.alloc(make_segment());
                }
                black_box(&arena);
            },
            BatchSize::SmallInput,
        );
    });
}

fn bench_arena_alloc_free_cycle(c: &mut Criterion) {
    c.bench_function("SegmentArena alloc+free cycle", |b| {
        let mut arena = SegmentArena::new();
        b.iter(|| {
            let id = arena.alloc(make_segment());
            arena.free(id);
            black_box(id);
        });
    });
}

fn bench_arena_reuse(c: &mut Criterion) {
    c.bench_function("SegmentArena free-list reuse x100", |b| {
        b.iter_batched(
            || {
                let mut arena = SegmentArena::new();
                let ids: Vec<_> = (0..100).map(|_| arena.alloc(make_segment())).collect();
                for id in &ids {
                    arena.free(*id);
                }
                arena
            },
            |mut arena| {
                for _ in 0..100 {
                    arena.alloc(make_segment());
                }
                black_box(&arena);
            },
            BatchSize::SmallInput,
        );
    });
}

fn bench_arena_reparent(c: &mut Criterion) {
    c.bench_function("SegmentArena::reparent_children (50 segments)", |b| {
        b.iter_batched(
            || {
                let mut arena = SegmentArena::new();
                let parent = arena.alloc(make_segment());
                for _ in 0..49 {
                    let marker = Marker::fresh();
                    arena.alloc(Segment::new(marker, Some(parent)));
                }
                (arena, parent)
            },
            |(mut arena, parent)| {
                let new_parent = SegmentId::from_index(999);
                black_box(arena.reparent_children(parent, Some(new_parent)));
            },
            BatchSize::SmallInput,
        );
    });
}

// ---------------------------------------------------------------------------
// Segment and Frame benchmarks
// ---------------------------------------------------------------------------

fn bench_segment_creation(c: &mut Criterion) {
    c.bench_function("Segment::new", |b| {
        b.iter(|| {
            let marker = Marker::fresh();
            black_box(Segment::new(marker, None));
        });
    });
}

fn bench_frame_push(c: &mut Criterion) {
    c.bench_function("Segment::push_frame", |b| {
        let mut seg = make_segment();
        b.iter(|| {
            seg.push_frame(Frame::FlatMapBindResult);
            black_box(seg.frame_count());
        });
    });
}

fn bench_frame_push_pop(c: &mut Criterion) {
    c.bench_function("Segment push+pop frame cycle", |b| {
        let mut seg = make_segment();
        b.iter(|| {
            seg.push_frame(Frame::FlatMapBindResult);
            black_box(seg.pop_frame());
        });
    });
}

fn bench_frame_push_pop_program(c: &mut Criterion) {
    c.bench_function("Segment push+pop Frame::Program", |b| {
        let mut seg = make_segment();
        b.iter(|| {
            let stream = dummy::make_stream_ref();
            seg.push_frame(Frame::program(stream, None));
            black_box(seg.pop_frame());
        });
    });
}

// ---------------------------------------------------------------------------
// Continuation benchmarks
// ---------------------------------------------------------------------------

fn bench_continuation_capture_empty(c: &mut Criterion) {
    c.bench_function("Continuation::capture (0 frames)", |b| {
        let seg = make_segment();
        let seg_id = SegmentId::from_index(0);
        b.iter(|| {
            black_box(Continuation::capture(&seg, seg_id, None));
        });
    });
}

fn bench_continuation_capture_10_frames(c: &mut Criterion) {
    c.bench_function("Continuation::capture (10 frames)", |b| {
        let seg = make_segment_with_frames(10);
        let seg_id = SegmentId::from_index(0);
        b.iter(|| {
            black_box(Continuation::capture(&seg, seg_id, None));
        });
    });
}

fn bench_continuation_capture_50_frames(c: &mut Criterion) {
    c.bench_function("Continuation::capture (50 frames)", |b| {
        let seg = make_segment_with_frames(50);
        let seg_id = SegmentId::from_index(0);
        b.iter(|| {
            black_box(Continuation::capture(&seg, seg_id, None));
        });
    });
}

fn bench_continuation_clone(c: &mut Criterion) {
    c.bench_function("Continuation clone (10 frames)", |b| {
        let seg = make_segment_with_frames(10);
        let seg_id = SegmentId::from_index(0);
        let cont = Continuation::capture(&seg, seg_id, None);
        b.iter(|| {
            black_box(cont.clone());
        });
    });
}

// ---------------------------------------------------------------------------
// RustStore benchmarks
// ---------------------------------------------------------------------------

fn bench_rust_store_put_get(c: &mut Criterion) {
    use doeff_vm_core::rust_store::RustStore;

    c.bench_function("RustStore put+get cycle", |b| {
        let mut store = RustStore::new();
        b.iter(|| {
            store.put("counter".to_string(), Value::Int(42));
            black_box(store.get("counter"));
        });
    });
}

fn bench_rust_store_tell(c: &mut Criterion) {
    use doeff_vm_core::rust_store::RustStore;

    c.bench_function("RustStore::tell x100", |b| {
        b.iter_batched(
            RustStore::new,
            |mut store| {
                for i in 0..100 {
                    store.tell(Value::Int(i));
                }
                black_box(store.logs().len());
            },
            BatchSize::SmallInput,
        );
    });
}

// ---------------------------------------------------------------------------
// Groups
// ---------------------------------------------------------------------------

criterion_group!(
    id_generation,
    bench_marker_fresh,
    bench_cont_id_fresh,
    bench_dispatch_id_fresh,
);

criterion_group!(
    values,
    bench_value_from_int,
    bench_value_from_str,
    bench_value_from_bool,
    bench_value_clone_int,
    bench_value_clone_string,
    bench_value_as_int,
    bench_value_as_str,
);

criterion_group!(
    arena,
    bench_arena_alloc,
    bench_arena_alloc_100,
    bench_arena_alloc_free_cycle,
    bench_arena_reuse,
    bench_arena_reparent,
);

criterion_group!(
    segments,
    bench_segment_creation,
    bench_frame_push,
    bench_frame_push_pop,
    bench_frame_push_pop_program,
);

criterion_group!(
    continuations,
    bench_continuation_capture_empty,
    bench_continuation_capture_10_frames,
    bench_continuation_capture_50_frames,
    bench_continuation_clone,
);

criterion_group!(store, bench_rust_store_put_get, bench_rust_store_tell,);

criterion_main!(id_generation, values, arena, segments, continuations, store);
