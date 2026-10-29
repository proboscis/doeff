# moved-submodule-shim: doeff_openai.costs -> doeff_openai._costs
# moved-submodule-shim-names doeff_openai.costs:
#   CostInfo MODEL_PRICING MissingCachedPricingError ModelPricing PricingError TokenUsage
#   UnknownModelPricingError calculate_cost count_embedding_tokens count_message_tokens count_tokens
#   estimate_cost estimate_max_cost get_encoding get_model_pricing
"""Moved: ``doeff_openai.costs`` is now ``doeff_openai._costs`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_openai.costs…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_openai._costs import *  # noqa: F403  # nosemgrep: doeff-no-star-imports

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_openai._costs", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_openai._costs")
