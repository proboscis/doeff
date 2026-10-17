# Ok / Err / Some / Nothing / Maybe の型
from typing import assert_type

from doeff import Err, Maybe, Nothing, Ok, Some

o = Ok(1)
assert_type(o, Ok[int])
assert_type(o.value, int)
e = Err(ValueError("x"))
assert_type(e.error, BaseException)  # gap-5 want-clean (Err.error は Any)
s = Some(1)  # gap-5 want-clean[mypy] (Some に注記が無い — mypy は型の無い関数の呼びと言う)
assert_type(s, Some[int])  # gap-5 want-clean
assert_type(s.value, int)  # gap-5 want-clean
m: Maybe[int] = Nothing  # gap-5 want-clean (Maybe が型の引数を取らない)
