"""Query Understanding & Routing."""

from .validation_module import validate_query

__all__ = ["validate_query", "process_query"]


def __getattr__(name):
    # Lazy: pipeline imports stage_2, which the other submodules do not need.
    if name == "process_query":
        from .pipeline import process_query

        return process_query
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
