from __future__ import annotations

from typing import Any


def _unique_model_candidates(*model_names: str | None) -> list[str]:
    unique: list[str] = []
    for model_name in model_names:
        normalized = str(model_name or "").strip()
        if not normalized or normalized in unique:
            continue
        unique.append(normalized)
    return unique


def _recent_unique_models(recent_models: list[str], *, limit: int) -> list[str]:
    unique: list[str] = []
    for model_name in recent_models:
        normalized = str(model_name or "").strip()
        if not normalized or normalized in unique:
            continue
        unique.append(normalized)
        if len(unique) >= limit:
            break
    return unique


def _choose_coding_model(
    *,
    meta_diagnosis: dict[str, Any],
    default_model: str,
    exploratory_model: str | None,
    stagnation_model: str | None,
) -> str:
    search_mode = str(meta_diagnosis.get("search_mode") or "direct_improvement")
    status = str(meta_diagnosis.get("status") or "normal")
    observation_bundle = meta_diagnosis.get("observation_bundle") or {}
    since_last_promotion = int(observation_bundle.get("since_last_promotion") or 0)
    unfinished_started_attempts = int(observation_bundle.get("unfinished_started_attempts") or 0)
    decision_reason_histogram = observation_bundle.get("decision_reason_histogram") or {}
    validation_failed = int(decision_reason_histogram.get("validation_failed") or 0)
    recent_selected_models = [
        str(model_name).strip()
        for model_name in observation_bundle.get("recent_selected_coding_models") or []
        if str(model_name).strip()
    ]
    last_selected_model = str(observation_bundle.get("last_selected_coding_model") or "").strip() or None

    if search_mode in {"constraint_probe", "reframe"}:
        model_pool = _unique_model_candidates(exploratory_model, default_model, stagnation_model)
    else:
        model_pool = _unique_model_candidates(default_model, exploratory_model, stagnation_model)

    if model_pool and (
        status == "stagnating"
        or since_last_promotion >= 4
        or validation_failed >= 3
        or unfinished_started_attempts >= 3
    ):
        if len(model_pool) == 1:
            return model_pool[0]

        blocked_models = set(_recent_unique_models(recent_selected_models, limit=max(1, len(model_pool) - 1)))
        for model_name in model_pool:
            if model_name not in blocked_models:
                return model_name

        for model_name in model_pool:
            if model_name != last_selected_model:
                return model_name

        return model_pool[0]
    if exploratory_model and search_mode in {"constraint_probe", "reframe"}:
        return exploratory_model
    return default_model
