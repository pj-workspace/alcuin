"""Explicit Operations Copilot example Extension."""

from .extension import (
    operations_demo_adapter,
    operations_demo_definition,
    operations_demo_manifest,
)
from .seed import seed_operations_demo

__all__ = [
    "operations_demo_adapter",
    "operations_demo_definition",
    "operations_demo_manifest",
    "seed_operations_demo",
]
