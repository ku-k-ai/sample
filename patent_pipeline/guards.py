"""段階間の機械検証。意味的な正しさ・分類精度を保証するものではない。"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any, Mapping, Sequence

QUESTION_TEMPLATES = {
    "対象物・状態": "扱う対象物は何で、どのような状態・用途か。",
    "処理・出力": "対象の装置・機能は何を行い、何を出力するか。",
    "制御対象・役割": "該当する構成・制御・情報は、何を対象とし、どの動作・役割を担うか。",
    "実接続・配置": "実際の接続先・配置・適用条件として何が記載されているか。",
}
STATUSES = {"整合確認済み", "本文確認が必要", "明確に不整合", "判定対象外"}
# 最終段で選べる状態。本文確認が必要（未解決）の候補も落とさない。落とすのは確認済みの事実で外れた候補だけ
SELECTABLE_STATUSES = {"整合確認済み", "本文確認が必要"}
# 最終判定プロンプトv4で、技術担当の候補を選んでよい担当ユニット
OPEN_UNITS = {"本体共通", "限定なし"}
RESULT_PAIRS = {
    "確認済み": {"成立", "不成立"},
    "未確認": {"未解決"},
    "確認不要": {"確認不要"},
}


class ContractError(ValueError):
    """入力・出力の契約違反。候補資格を推測補正せず呼出し元で停止する。"""


def _require(ok: bool, message: str) -> None:
    if not ok:
        raise ContractError(message)


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _index(rows: Any, key: str) -> dict[str, dict[str, Any]]:
    _require(isinstance(rows, list), f"{key}: 配列が必要です")
    result = {}
    for row in rows:
        _require(isinstance(row, dict), f"{key}: オブジェクトが必要です")
        value = row.get(key)
        _require(_text(value), f"{key}: 空又は不正なIDです")
        _require(value not in result, f"{key}: 重複 {value}")
        result[value] = row
    return result


def _digest(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _citations(refs: Any, sources: Mapping[str, str], required: bool) -> None:
    _require(isinstance(refs, list), "根拠は配列が必要です")
    _require(not required or bool(refs), "確認した事実には原文引用が必要です")
    for ref in refs:
        _require(isinstance(ref, dict), "根拠はオブジェクトが必要です")
        source_id, quote = ref.get("抜粋ID"), ref.get("原文引用")
        _require(_text(source_id) and source_id in sources, "存在しない抜粋IDです")
        _require(_text(quote), "空の原文引用は認めません")
        _require(quote in sources[source_id], f"{source_id}: 引用が原文の連続文字列と一致しません")


def freeze_plan(plan: dict[str, Any], candidates: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """PLANを検証し、IDと署名を付ける。candidatesは本文確認対象だけ。"""
    _require(isinstance(plan, dict), "PLANはオブジェクトが必要です")
    candidate_map = _index(list(candidates), "候補ID")
    plans = _index(plan.get("候補計画"), "候補ID")
    _require(set(plans) == set(candidate_map), "PLANの候補集合が本文確認対象と一致しません")
    frozen = {"候補計画": []}
    for cid, candidate in candidate_map.items():
        item = deepcopy(plans[cid])
        _require(isinstance(item.get("差戻事項"), list), "差戻事項は配列が必要です")
        _require(_text(item.get("採用する範囲条件")) or bool(item["差戻事項"]), "候補全体の採用条件が必要です")
        _require(isinstance(item.get("条件整理理由"), str), "条件整理理由は文字列が必要です")
        questions = item.get("確認事項")
        _require(isinstance(questions, list), "確認事項は配列が必要です")
        seen = set()
        for question in questions:
            _require(isinstance(question, dict), "確認事項はオブジェクトが必要です")
            aspect = question.get("確認観点")
            _require(isinstance(aspect, str) and aspect in QUESTION_TEMPLATES, "未知の確認観点です")
            _require(question.get("確認事項") == QUESTION_TEMPLATES[aspect], "質問文が定型文と異なります")
            _require(_text(question.get("対象")), "確認する対象が必要です")
            _require(_text(question.get("判定する条件")), "判定する条件が必要です")
            observation = (question["対象"], aspect)
            _require(observation not in seen, "同じ対象・観点の質問を重複させないでください")
            seen.add(observation)
            refs = question.get("条件根拠")
            _require(isinstance(refs, list) and bool(refs), "条件の正式ルール原文が必要です")
            for ref in refs:
                _require(isinstance(ref, dict), "条件根拠はオブジェクトが必要です")
                field, quote = ref.get("項目"), ref.get("原文")
                _require(isinstance(field, str), "ルール項目は文字列が必要です")
                scope = candidate.get("正式担当範囲", {})
                _require(isinstance(scope, dict), "正式担当範囲はオブジェクトが必要です")
                source = candidate.get("ラベル") if field == "ラベル" else scope.get(field)
                _require(_text(source) and _text(quote) and quote in source, "条件根拠が当該候補の正式原文にありません")
                _require(quote.strip().lower() != "nan", "欠損値をルール根拠にしないでください")
        # 同じ対象・観点の並びを固定。意味が変われば署名も変わる。
        questions.sort(key=lambda q: (list(QUESTION_TEMPLATES).index(q["確認観点"]), q["対象"], q["判定する条件"]))
        for number, question in enumerate(questions, 1):
            question["確認ID"] = f"{cid}-Q{number}"
        frozen["候補計画"].append(item)
    return {"計画": frozen, "計画署名": _digest(frozen)}


def validate_review(frozen: dict[str, Any], review: dict[str, Any], sources: Mapping[str, str], *, full_text: bool) -> None:
    """部分事実は許容するが、引用なしの確定・ID欠落・勝手な確定を拒否する。"""
    _require(type(full_text) is bool, "full_textはプログラム由来のboolが必要です")
    _require(isinstance(frozen, dict) and isinstance(review, dict), "計画と結果はオブジェクトが必要です")
    _require(all(_text(k) and isinstance(v, str) for k, v in sources.items()), "出典はID→原文の辞書が必要です")
    _require(frozen.get("計画署名") == _digest(frozen.get("計画")), "固定後に確認計画が変更されています")
    plans = _index(frozen["計画"].get("候補計画"), "候補ID")
    updates = _index(review.get("候補更新"), "候補ID")
    _require(set(updates) == set(plans), "REVIEWの候補集合がPLANと一致しません")
    context = review.get("工程文脈")
    _require(isinstance(context, dict), "工程文脈が必要です")
    for key in ("工程対応", "対応理由", "実接続・配置", "未確定事項"):
        _require(isinstance(context.get(key), str), f"工程文脈の{key}が必要です")
    facts = context.get("原文事実")
    _require(isinstance(facts, list), "工程文脈の原文事実は配列が必要です")
    for fact in facts:
        _require(isinstance(fact, dict) and _text(fact.get("事実")), "原文事実が空です")
        _citations(fact.get("根拠"), sources, required=True)
    for cid, plan in plans.items():
        update = updates[cid]
        questions = _index(plan["確認事項"], "確認ID")
        answers = _index(update.get("確認結果"), "確認ID")
        _require(set(answers) == set(questions), f"{cid}: 質問の回答漏れ・追加があります")
        for answer in answers.values():
            result, condition = answer.get("結果"), answer.get("条件判定")
            _require(isinstance(result, str) and result in RESULT_PAIRS, "未知の確認結果です")
            _require(isinstance(condition, str) and condition in RESULT_PAIRS[result], "確認状態と条件判定が矛盾しています")
            fact, reason = answer.get("確認した事実"), answer.get("理由")
            _require(isinstance(fact, str) and _text(reason), "事実欄と条件対応の理由が必要です")
            _require(result != "確認済み" or _text(fact), "確定結果には確認した事実が必要です")
            _citations(answer.get("根拠"), sources, required=result == "確認済み" or bool(fact.strip()))
            alternatives = answer.get("代替成立ID")
            _require(isinstance(alternatives, list), "代替成立IDは配列が必要です")
            if result == "確認不要":
                _require(bool(alternatives), "確認不要には成立済み代替条件のIDが必要です")
                for qid in alternatives:
                    _require(isinstance(qid, str) and qid in answers, "存在しない代替成立IDです")
                    _require(answers[qid].get("条件判定") == "成立", "未成立の代替条件で確認不要にできません")
            else:
                _require(not alternatives, "代替成立IDは確認不要の回答だけに指定します")
        status = update.get("担当範囲整合")
        _require(isinstance(status, str) and status in STATUSES, "未対応の候補状態です。推測で変換しません")
        _require(_text(update.get("担当範囲整合の根拠")), "候補状態の理由が必要です")
        for field in ("不足事実", "不一致事実"):
            _require(isinstance(update.get(field), str), f"{field}は文字列が必要です")
        _require(isinstance(update.get("差戻事項"), list), "差戻事項は配列が必要です")
        if plan["差戻事項"] or update["差戻事項"]:
            _require(status == "本文確認が必要", "差戻事項を残して候補資格を確定できません")
        _require(full_text or status == "本文確認が必要", "本文提供が不完全な実行では本文確認が必要として残してください")
        if status == "整合確認済み":
            _require(any(a["条件判定"] == "成立" for a in answers.values()), "成立の根拠がない候補を確定できません")
        if status == "明確に不整合":
            _require(_text(update["不一致事実"]), "明示的な不一致事実が必要です")
            _require(any(a["条件判定"] == "不成立" for a in answers.values()), "未確認だけでは明確な不整合にできません")
        if status == "判定対象外":
            _require(full_text, "部分本文から全文確認完了として対象外にできません")
            # 見つからないことは別物である根拠にならない。確認済みの事実（成立・不成立）が1件以上必要
            _require(any(a["結果"] == "確認済み" for a in answers.values()),
                     "確認済みの事実が無いまま判定対象外にできません。未確認のままなら本文確認が必要として残してください")
        if status == "本文確認が必要":
            _require(_text(update["不足事実"]), "残る穴を明記してください")


def build_final_input(candidates: Sequence[dict[str, Any]], frozen: dict[str, Any], review: dict[str, Any], sources: Mapping[str, str], *, full_text: bool) -> dict[str, Any]:
    """STEP1スナップショットに検証済みSTEP2を結合。原文引用と未解決候補も残す。"""
    validate_review(frozen, review, sources, full_text=full_text)
    candidate_map = _index(list(candidates), "候補ID")
    updates = _index(review["候補更新"], "候補ID")
    plans = _index(frozen["計画"]["候補計画"], "候補ID")
    pending = set()
    for cid, candidate in candidate_map.items():
        _require("STEP2" not in candidate, "新実行には旧STEP2を含まないSTEP1スナップショットを渡してください")
        step1 = candidate.get("STEP1")
        _require(isinstance(step1, dict), "各候補のSTEP1結果が必要です")
        _require(step1.get("担当範囲整合") in STATUSES, "STEP1の候補状態が不明です")
        if step1["担当範囲整合"] == "本文確認が必要":
            pending.add(cid)
    _require(set(updates) == pending, "STEP1の本文確認対象とSTEP2の更新対象が一致しません")
    merged, eligible, unresolved = [], [], []
    for cid, candidate in candidate_map.items():
        item = deepcopy(candidate)
        if cid in updates:
            item["STEP2"] = deepcopy(updates[cid])
            item["確認計画"] = deepcopy(plans[cid])
        status = item.get("STEP2", item["STEP1"])["担当範囲整合"]
        if status in SELECTABLE_STATUSES:
            eligible.append(cid)
            if status == "本文確認が必要":
                unresolved.append(cid)
        merged.append(item)
    return {
        "確認計画": deepcopy(frozen),
        "工程文脈": deepcopy(review["工程文脈"]),
        "候補": merged,
        "選択可能候補ID": eligible,
        "選択不可候補ID": [cid for cid in candidate_map if cid not in eligible],
        "未解決候補ID": unresolved,
    }


def validate_final(result: dict[str, Any], handoff: dict[str, Any], all_labels: Sequence[str] | None = None) -> None:
    """最終判定プロンプトv4の出力を検査する。選択不可候補の復活、自信無の条件違反を拒否する。"""
    _require(isinstance(result, dict), "最終結果はオブジェクトが必要です")
    eligible = handoff["選択可能候補ID"]
    all_ids = [c["候補ID"] for c in handoff["候補"]]
    evaluations = _index(result.get("候補評価"), "候補ID")
    _require(set(eligible) <= set(evaluations), "選択可能候補の評価が欠けています")
    _require(set(evaluations) <= set(all_ids), "入力に無い候補を評価しています")
    for row in evaluations.values():
        _require(row.get("担当ユニットを受け持つか") in {"受け持つ", "受け持たない"}, "担当ユニットを受け持つかが不正です")
    decision = result.get("最終判定")
    _require(isinstance(decision, str) and (decision == "自信無" or decision in eligible), "選択不可候補を最終段で復活できません")
    unit = result.get("担当ユニット")
    _require(_text(unit), "担当ユニットが必要です")
    _require(result.get("請求物の種類") in {"A", "B", "C"}, "請求物の種類が不正です")
    original = result.get("本来の届け先", "")
    _require(isinstance(original, str), "本来の届け先は文字列が必要です")
    if original.strip():
        _require(decision == "自信無", "本来の届け先を書いた場合は自信無が必要です（代わりの候補を選ばない）")
        if all_labels is not None:
            _require(original in all_labels, "本来の届け先が分類ルール表のラベルと一致しません")
    if unit == "特定できない":
        _require(decision == "自信無", "担当ユニットが特定できない場合は自信無が必要です")
    if decision != "自信無" and unit not in OPEN_UNITS:
        _require(evaluations[decision]["担当ユニットを受け持つか"] == "受け持つ",
                 "担当ユニットを受け持たない候補を選んでいます（技術担当は本体共通・限定なしのときだけ）")


def suspicious_unconfirmed(review: dict[str, Any]) -> list[tuple[str, str]]:
    """確認した事実と原文引用があるのに「未確認」の回答を返す。
    部分的な事実は正当な場合もあるので例外にはしない。呼出し側で、該当の確認事項だけSTEP2を1回再実行するか、ログに残す。"""
    found = []
    for update in review.get("候補更新", []):
        for answer in update.get("確認結果", []):
            if answer.get("結果") == "未確認" and _text(answer.get("確認した事実")) and answer.get("根拠"):
                found.append((update.get("候補ID"), answer.get("確認ID")))
    return found


def render_final_prompt(template: str, *, claims: str, step0: Any, process_context: Any,
                        handoff: dict[str, Any], all_rules: Sequence[dict[str, Any]],
                        body_quotes: Sequence[dict[str, Any]] = (),
                        problem_effect: Sequence[dict[str, Any]] = ()) -> tuple[str, str]:
    """最終判定テンプレート（v4・v4.3）に値を差し込み、(system, user) を返す。
    problem_effect は sections.extract_problem_and_effect の戻り値。v4.3のテンプレートでだけ使われる。"""
    def dumps(o: Any) -> str:
        return json.dumps(o, ensure_ascii=False, indent=2)
    filled = (template
              .replace("{{INDEPENDENT_CLAIMS}}", claims)
              .replace("{{STEP0_INVENTION_PROFILE_JSON}}", dumps(step0))
              .replace("{{PROCESS_AND_DEVICE_CONTEXT_JSON}}", dumps({"前段の工程推定": process_context, "STEP2の工程文脈": handoff.get("工程文脈")}))
              .replace("{{BODY_QUOTES_JSON}}", dumps(list(body_quotes)))
              .replace("{{PROBLEM_AND_EFFECT_JSON}}", dumps(list(problem_effect)))
              .replace("{{CANDIDATES_WITH_STEP1_STEP2_JSON}}", dumps(handoff["候補"]))
              .replace("{{ALL_RULES_JSON}}", dumps(list(all_rules)))
              .replace("{{SELECTABLE_CANDIDATE_IDS_JSON}}", dumps(handoff["選択可能候補ID"]))
              .replace("{{NON_SELECTABLE_CANDIDATE_IDS_JSON}}", dumps(handoff["選択不可候補ID"])))
    _require("{{" not in filled, "差し込み漏れのプレースホルダがあります")
    system, user = filled.split("<<USER>>", 1)
    return system.replace("<<SYSTEM>>", "").strip(), user.strip()
