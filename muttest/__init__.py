"""muttest -- a standard-library-only mutation testing framework."""
from .mutator import Mutant, apply_mutant, collect_mutants
from .runner import (INCOMPETENT, KILLED, SURVIVED, TIMEOUT, MutantResult,
                     RunSummary, run_suite)

__all__ = ["Mutant", "apply_mutant", "collect_mutants", "run_suite",
           "MutantResult", "RunSummary", "KILLED", "SURVIVED", "TIMEOUT",
           "INCOMPETENT"]
__version__ = "1.0.0"
