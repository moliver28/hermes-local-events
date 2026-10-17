"""LLM access: optional, host-provided (Hermes ctx.llm). Everything degrades to
deterministic rules when absent — the engine never requires an LLM."""

from __future__ import annotations

from collections.abc import Callable

LLMFn = Callable[[str, str], dict | None]  # (system, user) -> parsed json dict | None


def make_llm_fn(runtime) -> LLMFn | None:
    fn = getattr(runtime, "llm_json", None)
    if not callable(fn):
        return None

    def call(system: str, user: str) -> dict | None:
        try:
            return fn(system, user)
        except Exception:
            return None

    return call
