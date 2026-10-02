//! process 全体の積み上げの数(`memory_stats::work_counts` — agora-redesign #2851)の検。
//!
//! 数は process の全部の VM を通した和なので、同じ process で他の検が VM を回すと差が混ざる。cargo は統合の検の file ごとに
//! 別の binary(別の process)を作るので、この file には検を 1 本だけ置き、その中で program を順に 2 回走らせる。

use std::sync::Arc;

use doeff_vm_core::memory_stats::work_counts;
use doeff_vm_core::{
    Callable, CallableRef, DoCtrl, Fiber, Frame, IRStream, IRStreamRef, Signal, StepResult,
    StreamStep, VMError, Value, VM,
};

/// 何回 effect を撃っても 1 で答える handler(Rust の同期の handler の道 — `call_handler` が DoCtrl を返す)。
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

/// effect を `left` 回撃ち、最後に答えた値を返す本体。
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

/// 本体を handler で包む根(1 回目に WithHandler を出し、2 回目に本体の答えで終わる)。
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

/// 新しい VM で根を最後まで走らせ、その VM の歩数(`VM::steps`)を返す。
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
    // 決まった program は、2 回とも同じだけ数を増やす(CPU 秒と違い、機体の負荷で揺れない)。
    assert_eq!(first, second);
    // 歩数の積み上げは、その間に走った唯一の VM の歩数そのもの。handler は effect の数だけ呼ばれる。
    assert_eq!(first.0, first_vm_steps);
    assert_eq!(second.0, second_vm_steps);
    assert_eq!(first.1, u64::from(PERFORMS));
}
