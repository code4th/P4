from __future__ import annotations


SYSTEM_NAMES = {
    "p2_core": {
        "formal_name": "P2自己改善カーネル",
        "short_name": "P2カーネル",
        "description": "自己改善ループを管理する最小カーネル。",
    },
    "watchdog": {
        "formal_name": "P2 watchdog プロセス",
        "short_name": "watchdog",
        "description": "loop worker と dashboard を常駐監視し、落ちたら再起動する外側の監視役。",
    },
    "dashboard": {
        "formal_name": "P2監視ダッシュボード",
        "short_name": "ダッシュボード",
        "description": "現在状態と履歴とモデル出力を可視化する画面。",
    },
}


FRAME_SYSTEM_NAMES = {
    "frame_system": {
        "formal_name": "再帰フレーム",
        "short_name": "フレーム",
        "description": "目的を局所化しながら親文脈を保持する階層実行単位。",
    },
    "current_frame": {
        "formal_name": "現在フレーム",
        "short_name": "現在フレーム",
        "description": "いま LLM が直接扱っているフレーム。",
    },
    "parent_frame": {
        "formal_name": "親フレーム",
        "short_name": "親フレーム",
        "description": "現在フレームの上位目的を持つフレーム。",
    },
    "child_frame": {
        "formal_name": "子フレーム",
        "short_name": "子フレーム",
        "description": "現在フレームをより局所化した下位フレーム。",
    },
    "frame_transition_request": {
        "formal_name": "フレーム遷移要求",
        "short_name": "遷移要求",
        "description": "LLM が continue_or_return.decision でシステムへ返す階層移動要求。",
    },
    "next_goal": {
        "formal_name": "局所ゴール",
        "short_name": "局所ゴール",
        "description": "子フレームで達成を目指す、より具体的な次の目的。",
    },
}


FRAME_DECISION_NAMES = {
    "continue_here": {
        "formal_name": "このフレームで続行",
        "short_name": "続行",
        "description": "このフレームで直接編集と検証を進める。",
    },
    "open_child_frame": {
        "formal_name": "子フレームへ降りる",
        "short_name": "子フレーム化",
        "description": "goal を局所化した局所ゴールを作り、文脈を引き継いだ子フレームへ降りる。",
    },
    "return_to_parent": {
        "formal_name": "親フレームへ戻る",
        "short_name": "親へ戻る",
        "description": "このフレームでの情報利得が尽きたため、親フレームへ戻って見直す。",
    },
    "escalate_to_top": {
        "formal_name": "最上位方針を見直す",
        "short_name": "方針見直し",
        "description": "最上位の目的設定や探索方針を見直す。",
    },
}


OBSERVABILITY_NAMES = {
    "reference_index": {
        "formal_name": "参照インデックス",
        "short_name": "参照一覧",
        "description": "LLM が追加で読める参照先の一覧。",
    },
    "selected_context": {
        "formal_name": "参照選択",
        "short_name": "参照選択",
        "description": "LLM が今回読む参照を選ぶ要求。",
    },
    "resolved_context": {
        "formal_name": "解決済み参照",
        "short_name": "参照内容",
        "description": "参照選択に対してシステムが解決して返した内容。",
    },
    "delta_context": {
        "formal_name": "局所失敗差分",
        "short_name": "失敗差分",
        "description": "直近変更と直近失敗結果の raw な対応情報。",
    },
    "reasoning_summary": {
        "formal_name": "思考要約",
        "short_name": "思考要約",
        "description": "変更理由と期待効果をまとめた構造化要約。",
    },
    "situation_report": {
        "formal_name": "状況報告",
        "short_name": "状況報告",
        "description": "known / suspected / unknown を整理した自己状況報告。",
    },
    "pre_edit_reflection": {
        "formal_name": "事前自己診断",
        "short_name": "事前診断",
        "description": "変更前に自分の探索様式を点検する自己診断。",
    },
    "post_edit_reflection": {
        "formal_name": "事後自己評価",
        "short_name": "事後評価",
        "description": "今回の変更が本当に行動変化になったかを点検する評価。",
    },
}


MEMORY_NAMES = {
    "system_skill": {
        "formal_name": "システムスキル",
        "short_name": "システムスキル",
        "description": "システムが用意する一般的な使い方のヒント。",
    },
    "self_memo": {
        "formal_name": "自己メモ",
        "short_name": "自己メモ",
        "internal_name": "self_memo",
        "description": "今回の試行から抽出した再利用候補の短い学び。",
    },
    "persistent_self_memo": {
        "formal_name": "永続自己メモ",
        "short_name": "永続自己メモ",
        "description": "次回以降にも参照できるよう保存された自己メモ。",
    },
}


RUNTIME_NAMES = {
    "active_version": {
        "formal_name": "現在版",
        "short_name": "現在版",
        "internal_name": "active_version",
        "description": "現在採用されている版。",
    },
    "candidate_version": {
        "formal_name": "候補版",
        "short_name": "候補版",
        "internal_name": "candidate_version",
        "description": "検証前の分離された候補版。",
    },
    "promotion": {
        "formal_name": "昇格",
        "short_name": "昇格",
        "description": "候補版を現在版へ採用すること。",
    },
    "rejection": {
        "formal_name": "却下",
        "short_name": "却下",
        "description": "候補版を採用せず破棄すること。",
    },
    "reset": {
        "formal_name": "初期状態リセット",
        "short_name": "リセット",
        "description": "workspace を bootstrap 直後へ戻すこと。",
    },
}


MODEL_ROLE_NAMES = {
    "thinking_model": {
        "formal_name": "思考モデル",
        "short_name": "思考モデル",
        "internal_name": "thinking_model",
    },
    "coding_model": {
        "formal_name": "コーディングモデル",
        "short_name": "コーディングモデル",
        "internal_name": "coding_model",
    },
    "exploratory_coding_model": {
        "formal_name": "探索コーディングモデル",
        "short_name": "探索コーディングモデル",
        "internal_name": "exploratory_coding_model",
    },
    "stagnation_coding_model": {
        "formal_name": "停滞打開モデル",
        "short_name": "停滞打開モデル",
        "internal_name": "stagnation_coding_model",
    },
}
