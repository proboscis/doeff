from datetime import datetime  # 期限の型に使う(ここでは時刻を読まない)。

from doeff import EffectGenerator, do  # 戻り値の型と、Programにする印を使う。
from doeff_time import GetTime  # 「今の日時が欲しい」という依頼。答えるのは外側の時計のハンドラ。


@do  # 呼んだだけでは動かないProgramにする。
def is_overdue(due: datetime) -> EffectGenerator[bool]:  # 期限を過ぎたかをboolで返す。
    now = yield GetTime()  # 時刻はハンドラに頼む。datetime.now()は呼ばない(ADRのR1)。
    return now > due  # 期限より後ならTrue。
