from __future__ import annotations

from collections.abc import Callable

from p2_core.backend import ModelBackend


def _prepare_runtime_backends(
    *,
    model: str,
    thinking_model: str | None,
    coding_model: str | None,
    exploratory_coding_model: str | None,
    stagnation_coding_model: str | None,
    backend: ModelBackend | None,
    backend_factory: Callable[[str], ModelBackend],
) -> dict[str, ModelBackend | str]:
    normalized_thinking_model = thinking_model or model
    normalized_coding_model = coding_model or model
    normalized_exploratory_model = exploratory_coding_model or normalized_coding_model
    normalized_stagnation_model = stagnation_coding_model or normalized_thinking_model

    base_backend = backend or backend_factory(model)
    thinking_backend = base_backend if normalized_thinking_model == model else backend_factory(normalized_thinking_model)
    default_coding_backend = (
        base_backend if normalized_coding_model == model else backend_factory(normalized_coding_model)
    )
    exploratory_backend = (
        default_coding_backend
        if normalized_exploratory_model == normalized_coding_model
        else backend_factory(normalized_exploratory_model)
    )
    stagnation_backend = (
        thinking_backend
        if normalized_stagnation_model == normalized_thinking_model
        else backend_factory(normalized_stagnation_model)
    )

    return {
        "thinking_model": normalized_thinking_model,
        "coding_model": normalized_coding_model,
        "exploratory_coding_model": normalized_exploratory_model,
        "stagnation_coding_model": normalized_stagnation_model,
        "backend": base_backend,
        "thinking_backend": thinking_backend,
        "default_coding_backend": default_coding_backend,
        "exploratory_coding_backend": exploratory_backend,
        "stagnation_coding_backend": stagnation_backend,
    }
