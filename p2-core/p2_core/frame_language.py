from __future__ import annotations

from typing import Any

from p2_core.terminology import FRAME_DECISION_NAMES, FRAME_SYSTEM_NAMES


FRAME_DECISION_ORDER = tuple(FRAME_DECISION_NAMES.keys())

FRAME_DECISION_LABELS = {
    code: payload["formal_name"] for code, payload in FRAME_DECISION_NAMES.items()
}

FRAME_DECISION_MEANINGS = {
    code: payload["description"] for code, payload in FRAME_DECISION_NAMES.items()
}

FRAME_TERMINOLOGY = {
    "frame": FRAME_SYSTEM_NAMES["frame_system"]["short_name"],
    "parent_frame": FRAME_SYSTEM_NAMES["parent_frame"]["short_name"],
    "child_frame": FRAME_SYSTEM_NAMES["child_frame"]["short_name"],
    "next_goal": FRAME_SYSTEM_NAMES["next_goal"]["formal_name"],
}


def frame_execution_policy_payload(*, depth: int, max_depth: int, has_parent_frame: bool) -> dict[str, Any]:
    can_open_child_frame = depth < max_depth
    return {
        "core_rule": "現在フレームでは 1 つの作業単位だけをやり切る。",
        "continue_here_when": [
            "現在の文脈だけで 1 回の編集と 1 回の検証まで見通せる。",
            "未解決の下位問題が編集前に残っていない。",
        ],
        "open_child_frame_when": (
            [
                "原因切り分け、差分レビュー、失敗箇所近傍の確認など、下位問題の解決が先に必要。",
                "goal や commitment が広く、1 回の編集と 1 回の検証で閉じない。",
                "同じ失敗が続いていて、まず局所ゴールへ分解した方がよい。",
            ]
            if can_open_child_frame
            else ["最大深度に達しているため、このフレームでは子フレームへ降りられない。"]
        ),
        "return_to_parent_when": (
            [
                "このフレームで得た観測や局所結論を親へ返した方がよい。",
                "このフレームだけでは前進せず、親で目的や切り方を見直すべき。",
            ]
            if has_parent_frame
            else ["親フレームがないため、このフレームからは上位へ戻れない。"]
        ),
        "escalate_to_top_when": [
            "最上位の目的設定や探索方針そのものが不適切そう。",
        ],
        "anti_patterns": [
            "原因が曖昧なまま広い goal を抱えてコードを書き始める。",
            "次の attempt で考えると言いながら、今の階層では分解しない。",
            "本当は分解が必要なのに、無理に推測変更を作る。",
        ],
        "delegation_note": (
            "open_child_frame を選ぶときは next_goal に最初の子フレーム局所ゴールを書き、"
            " child_goals に分解した局所ゴール群を優先順で書く。"
            " revised_file_content は無理に推測変更せず、現在のファイル内容をそのまま返してもよい。"
        ),
    }


def frame_affordances_payload(*, depth: int, max_depth: int, has_parent_frame: bool) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "current_depth": depth,
        "max_depth": max_depth,
        "can_open_child_frame": depth < max_depth,
        "can_return_to_parent": has_parent_frame,
        "can_escalate_to_top": True,
        "allowed_decisions": list(FRAME_DECISION_ORDER),
        "decision_labels": dict(FRAME_DECISION_LABELS),
        "execution_policy": frame_execution_policy_payload(
            depth=depth,
            max_depth=max_depth,
            has_parent_frame=has_parent_frame,
        ),
    }
    for code, meaning in FRAME_DECISION_MEANINGS.items():
        payload[f"{code}_meaning"] = meaning
    return payload


def frame_transition_capabilities_payload() -> dict[str, str]:
    return {code: FRAME_DECISION_MEANINGS[code] for code in FRAME_DECISION_ORDER}


def frame_work_unit_prompt_guide() -> str:
    return (
        "question_to_answer は『次の編集を始める前に答えるべき 1 問』にしてください。"
        "commitment は『このフレームでやり切る 1 単位』にしてください。"
        "1 回の編集と 1 回の検証で閉じないなら、そのまま広く抱えず、子フレームへ渡す局所ゴールに分解してください。"
    )


def frame_decision_prompt_guide() -> str:
    parts = [
        "decision は continue_here, open_child_frame, return_to_parent, escalate_to_top のいずれかにしてください。",
        "現在フレームでは 1 つの作業単位だけをやり切ってください。",
        "continue_here は、現在の文脈だけで 1 回の編集と 1 回の検証まで見通せる場合のみ選んでください。",
        "open_child_frame は、原因切り分け・差分レビュー・失敗箇所近傍の確認・複数仮説の分離など、未解決の下位問題が編集前に残っている場合に選んでください。",
        "goal が広い、原因が曖昧、同じ失敗が続く場合は、次の attempt へ送らず今この段階で子フレームへ降りてください。",
        "open_child_frame を選ぶときは next_goal に最初の子フレーム局所ゴールを書き、child_goals に分解した局所ゴール群を優先順で書いてください。",
        "child_goals は親が順番に実行する計画です。必要なら各子フレーム内でさらに child_goals へ分解して構いません。",
        "open_child_frame を選ぶ場合、revised_file_content は無理に推測変更せず、現在のファイル内容をそのまま返してもかまいません。",
        "return_to_parent は、局所情報は得たがこのフレームでは前進しきれない場合に選んでください。",
    ]
    for code in FRAME_DECISION_ORDER:
        parts.append(f"{code} は {FRAME_DECISION_LABELS[code]} です。{FRAME_DECISION_MEANINGS[code]}")
    return "".join(parts)
