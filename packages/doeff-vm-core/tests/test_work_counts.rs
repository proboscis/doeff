//! Test for the process-wide accumulated counts (`memory_stats::work_counts`).
//!
//! The counts are a sum over every VM in the process, so another test running a VM in the same process would mix in its differences. Cargo builds a
//! separate binary (separate process) per integration-test file, so this file holds a single test, which runs the program twice in sequence.

use std::sync::Arc;

use doeff_vm_core::memory_stats::work_counts;
use doeff_vm_core::{
    Callable, CallableRef, DoCtrl, Fiber, Frame, IRStream, IRStreamRef, Signal, StepResult,
    StreamStep, VMError, Value, VM,
};

/// A handler that answers 1 no matter how many times the effect is performed (the Rust synchronous handler path - `call_handler` returns a DoCtrl).
#[derive(Debug)]
struct AnswerOne;

impl Callable for AnswerOne {
    fn as_any(&self) -> &dyn std::any::Any {
        self
    }
    fn call(&self, _args: Vec<Value>) -> Result<Value, VMError> {
        Err(VMError::internal("AnswerOne: use call_handler"))
    }
    fn call_handler(&self, args: Vec<Value>) -> Result<DoCtrl, VMError> {
        match args.into_iter().nth(1) {
            Some(Value::Continuation(k)) => Ok(DoCtrl::Resume {
                k,
                value: Value::Int(1),
            }),
            _ => Err(VMError::internal("AnswerOne: expected a continuation")),
        }
    }
}

/// A body that performs the effect `left` times and returns the last value answered.
#[derive(Debug)]
struct PerformTimes {
    left: u32,
}

impl IRStream for PerformTimes {
    fn resume(&mut self, value: Value) -> StreamStep {
        if self.left == 0 {
            return StreamStep::Done(value);
        }
        self.left -= 1;
        StreamStep::Instruction(DoCtrl::Perform {
            effect: Value::String("query".into()),
        })
    }
    fn throw(&mut self, error: Value) -> StreamStep {
        StreamStep::Error(error)
    }
}

/// A root that wraps the body in the handler (emits WithHandler the first time, and finishes with the body's answer the second time).
#[derive(Debug)]
struct Root {
    body: Option<Box<DoCtrl>>,
}

impl IRStream for Root {
    fn resume(&mut self, value: Value) -> StreamStep {
        match self.body.take() {
            Some(body) => StreamStep::Instruction(DoCtrl::WithHandler {
                handler: Value::Callable(Arc::new(AnswerOne) as CallableRef),
                body,
            }),
            None => StreamStep::Done(value),
        }
    }
    fn throw(&mut self, error: Value) -> StreamStep {
        StreamStep::Error(error)
    }
}

const PERFORMS: u32 = 5;

/// Run the root to completion on a fresh VM and return that VM's step count (`VM::steps`).
fn run_once() -> u64 {
    let body = Box::new(DoCtrl::Expand {
        expr: Box::new(DoCtrl::Pure {
            value: Value::Stream(IRStreamRef::new(Box::new(PerformTimes { left: PERFORMS }))),
        }),
    });
    let mut vm = VM::new();
    let mut fiber = Fiber::new(None);
    fiber.push_frame(Frame::program(
        IRStreamRef::new(Box::new(Root { body: Some(body) })),
        None,
    ));
    let fid = vm.alloc_segment(fiber);
    vm.current_segment = Some(fid);
    let mut signal = Signal::send(Value::Unit);
    loop {
        match vm.step(signal) {
            StepResult::Continue(next) => signal = next,
            StepResult::Done(value) => {
                assert!(matches!(value, Value::Int(1)), "answer: {value:?}");
                return vm.steps;
            }
            StepResult::Error { error, .. } => panic!("the program failed: {error:?}"),
            StepResult::External { .. } => panic!("unexpected external call"),
        }
    }
}

#[test]
fn the_same_program_grows_the_counts_by_the_same_amount_every_time() {
    let start = work_counts();
    let first_vm_steps = run_once();
    let middle = work_counts();
    let second_vm_steps = run_once();
    let end = work_counts();

    let first = (
        middle.steps - start.steps,
        middle.handler_calls - start.handler_calls,
    );
    let second = (
        end.steps - middle.steps,
        end.handler_calls - middle.handler_calls,
    );
    // A fixed program increases the counts by the same amount both times (unlike CPU seconds, it does not fluctuate with machine load).
    assert_eq!(first, second);
    // The accumulated step count is exactly the step count of the only VM that ran in between. The handler is called once per effect.
    assert_eq!(first.0, first_vm_steps);
    assert_eq!(second.0, second_vm_steps);
    assert_eq!(first.1, u64::from(PERFORMS));
}
