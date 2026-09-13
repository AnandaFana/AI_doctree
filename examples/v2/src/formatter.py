"""Fictional label formatter used as a DocTree documentation fixture."""

from .rules.whitespace import trim_edges


def format_label(value: str) -> str:
    """Apply the current label-cleaning rule to one string."""
    return trim_edges(value)
