"""Named conductor interpreter constants backing public verbs."""


from dataclasses import dataclass


@dataclass(frozen=True)
class InterpreterConstant:
    """System-internal interpreter selection marker.

    Public author and overseer surfaces expose verbs. They do not accept these
    constants as arguments.
    """

    name: str
    purpose: str
    token_cost: str


plan_interpreter = InterpreterConstant(
    name="plan",
    purpose="record and estimate agent!/gate!/workspace! without executing them",
    token_cost="zero",
)
validation_interpreter = InterpreterConstant(
    name="validation",
    purpose="scenario-driven stub simulation for control flow and closure checks",
    token_cost="zero",
)
production_interpreter = InterpreterConstant(
    name="production",
    purpose="journaled production execution through the resolved conductor handlers",
    token_cost="real",
)
