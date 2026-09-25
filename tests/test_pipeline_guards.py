"""すべて合成データによる機械契約テスト。実特許の分類精度テストではない。"""
from copy import deepcopy
import unittest

from pathlib import Path

from patent_pipeline.guards import (
    ContractError, QUESTION_TEMPLATES, build_final_input, freeze_plan,
    render_final_prompt, suspicious_unconfirmed, validate_final, validate_review,
)

TEMPLATE = (Path(__file__).resolve().parents[1] / "prompts" / "final_decision_v4.txt").read_text(encoding="utf-8")
TEMPLATE_V4_2 = (Path(__file__).resolve().parents[1] / "prompts" / "final_decision_v4_2.txt").read_text(encoding="utf-8")
V4_2_LINE = "代表例（非限定）の欄に、特徴部分の仕組みそのものの名前"


class PipelineGuardsTest(unittest.TestCase):
    def setUp(self):
        self.sources = {
            "D1": "試験用装置は印字済みのカードを順番に整列する。",
            "D2": "制御回路は起動中に記録情報を送信する。",
        }
        self.candidates = [
            {"候補ID": "C1", "ラベル": "整列装置", "正式担当範囲": {"担当範囲の定義": "対象物の整列と整列装置の制御。"}, "STEP1": {"担当範囲整合": "本文確認が必要", "不足事実": "具体的な処理内容"}},
            {"候補ID": "C2", "ラベル": "電源回路", "正式担当範囲": {"担当範囲の定義": "電源回路の動作制御。"}, "STEP1": {"担当範囲整合": "本文確認が必要", "不足事実": "電源回路との関係"}},
            {"候補ID": "C3", "ラベル": "媒体搬送", "正式担当範囲": {"担当範囲の定義": "媒体の搬送。"}, "STEP1": {"担当範囲整合": "整合確認済み", "対応箇所": ["合成の請求項断片"]}},
        ]
        self.plan = {"候補計画": []}
        for candidate, aspect, target in zip(self.candidates[:2], ["処理・出力", "制御対象・役割"], ["試験用装置", "記録情報の送信制御"]):
            rule = candidate["正式担当範囲"]["担当範囲の定義"]
            self.plan["候補計画"].append({
                "候補ID": candidate["候補ID"], "採用する範囲条件": rule, "条件整理理由": "", "差戻事項": [],
                "確認事項": [{"確認観点": aspect, "対象": target, "確認事項": QUESTION_TEMPLATES[aspect],
                            "判定する条件": rule, "条件根拠": [{"項目": "担当範囲の定義", "原文": rule}]}],
            })
        self.frozen = freeze_plan(self.plan, self.candidates[:2])
        self.review = {
            "工程文脈": {
                "原文事実": [{"事実": "印字済みカードを整列する", "根拠": [{"抜粋ID": "D1", "原文引用": self.sources["D1"]}]}],
                "工程対応": "印字後の整列", "対応理由": "D1の用途からの技術的解釈",
                "実接続・配置": "記載からは不明", "未確定事項": "",
            },
            "候補更新": [
                {"候補ID": "C1", "確認結果": [{"確認ID": "C1-Q1", "結果": "確認済み", "確認した事実": "カードを整列する", "条件判定": "成立", "理由": "対象の整列機能が対応する", "根拠": [{"抜粋ID": "D1", "原文引用": self.sources["D1"]}], "代替成立ID": []}], "担当範囲整合": "整合確認済み", "担当範囲整合の根拠": "C1-Q1で機能を確認", "不足事実": "", "不一致事実": "", "差戻事項": []},
                {"候補ID": "C2", "確認結果": [{"確認ID": "C2-Q1", "結果": "未確認", "確認した事実": "制御回路が記録情報を送信する", "条件判定": "未解決", "理由": "電源回路そのものの制御との関係は未解決", "根拠": [{"抜粋ID": "D2", "原文引用": self.sources["D2"]}], "代替成立ID": []}], "担当範囲整合": "本文確認が必要", "担当範囲整合の根拠": "制御対象の同一性が不足", "不足事実": "電源回路の動作との関係", "不一致事実": "", "差戻事項": []},
            ],
        }

    def check(self):
        validate_review(self.frozen, self.review, self.sources, full_text=True)

    def handoff(self):
        return build_final_input(self.candidates, self.frozen, self.review, self.sources, full_text=True)

    def final(self):
        return {
            "請求物": "試験用装置", "請求物の種類": "A", "特徴部分": "カードの整列", "何のための構成か": "",
            "担当ユニット": "整列装置", "担当ユニットの根拠": "D1",
            "候補評価": [
                {"候補ID": "C1", "ラベル": "整列装置", "担当領域": "ユニット・工程", "担当ユニットを受け持つか": "受け持つ", "特徴部分との関係": "整列"},
                {"候補ID": "C2", "ラベル": "電源回路", "担当領域": "技術", "担当ユニットを受け持つか": "受け持たない", "特徴部分との関係": "なし"},
                {"候補ID": "C3", "ラベル": "媒体搬送", "担当領域": "ユニット・工程", "担当ユニットを受け持つか": "受け持たない", "特徴部分との関係": "なし"},
            ],
            "最も競合する候補": "媒体搬送", "競合候補を選ばない理由": "", "本来の届け先": "", "最終判定": "C1", "理由": "",
        }

    def test_valid_review(self):
        self.check()

    def test_plan_does_not_mutate_input(self):
        before = deepcopy(self.plan)
        freeze_plan(self.plan, self.candidates[:2])
        self.assertEqual(before, self.plan)

    def test_plan_ids_and_signature_are_repeatable(self):
        self.plan["候補計画"].reverse()
        self.assertEqual(self.frozen, freeze_plan(self.plan, self.candidates[:2]))

    def test_free_question_is_rejected(self):
        self.plan["候補計画"][0]["確認事項"][0]["確認事項"] = "プリンタに直結していますか。"
        with self.assertRaises(ContractError):
            freeze_plan(self.plan, self.candidates[:2])

    def test_rule_quote_from_other_candidate_is_rejected(self):
        self.plan["候補計画"][0]["確認事項"][0]["条件根拠"][0]["原文"] = "電源回路の動作制御。"
        with self.assertRaises(ContractError):
            freeze_plan(self.plan, self.candidates[:2])

    def test_missing_candidate_in_plan_is_rejected(self):
        self.plan["候補計画"].pop()
        with self.assertRaises(ContractError):
            freeze_plan(self.plan, self.candidates[:2])

    def test_duplicate_observation_is_rejected(self):
        questions = self.plan["候補計画"][0]["確認事項"]
        questions.append(deepcopy(questions[0]))
        with self.assertRaises(ContractError):
            freeze_plan(self.plan, self.candidates[:2])

    def test_frozen_condition_change_is_rejected(self):
        self.frozen["計画"]["候補計画"][0]["確認事項"][0]["判定する条件"] = "別の条件"
        with self.assertRaises(ContractError):
            self.check()

    def test_missing_answer_is_rejected(self):
        self.review["候補更新"][0]["確認結果"] = []
        with self.assertRaises(ContractError):
            self.check()

    def test_duplicate_answer_is_rejected(self):
        answers = self.review["候補更新"][0]["確認結果"]
        answers.append(deepcopy(answers[0]))
        with self.assertRaises(ContractError):
            self.check()

    def test_fabricated_quote_is_rejected(self):
        self.review["候補更新"][0]["確認結果"][0]["根拠"][0]["原文引用"] = "存在しない文章"
        with self.assertRaises(ContractError):
            self.check()

    def test_wrong_source_id_is_rejected(self):
        self.review["候補更新"][0]["確認結果"][0]["根拠"][0]["抜粋ID"] = "D2"
        with self.assertRaises(ContractError):
            self.check()

    def test_confirmed_without_evidence_is_rejected(self):
        self.review["候補更新"][0]["確認結果"][0]["根拠"] = []
        with self.assertRaises(ContractError):
            self.check()

    def test_partial_fact_without_quote_is_rejected(self):
        self.review["候補更新"][1]["確認結果"][0]["根拠"] = []
        with self.assertRaises(ContractError):
            self.check()

    def test_assertive_text_does_not_promote_unknown(self):
        answer = self.review["候補更新"][1]["確認結果"][0]
        answer["条件判定"] = "成立"
        with self.assertRaises(ContractError):
            self.check()

    def test_unknown_candidate_cannot_be_marked_consistent(self):
        self.review["候補更新"][1]["担当範囲整合"] = "整合確認済み"
        with self.assertRaises(ContractError):
            self.check()

    def test_unknown_is_not_explicit_conflict(self):
        item = self.review["候補更新"][1]
        item["担当範囲整合"] = "明確に不整合"
        item["不一致事実"] = "記載がなかった"
        with self.assertRaises(ContractError):
            self.check()

    def test_out_of_scope_without_confirmed_fact_is_rejected(self):
        # 「全文を読んだが根拠が取れなかった」だけで正解の候補を落とした実例がある
        item = self.review["候補更新"][1]
        item["担当範囲整合"] = "判定対象外"
        item["担当範囲整合の根拠"] = "全文を確認したが電源回路との具体的関係は得られなかった。"
        item["不足事実"] = ""
        with self.assertRaises(ContractError):
            self.check()

    def test_out_of_scope_with_confirmed_fact_is_allowed(self):
        item = self.review["候補更新"][1]
        answer = item["確認結果"][0]
        answer["結果"], answer["条件判定"] = "確認済み", "不成立"
        answer["理由"] = "送信しているのは制御回路であり、電源回路の動作ではないと原文から言える"
        item["担当範囲整合"] = "判定対象外"
        item["担当範囲整合の根拠"] = "C2-Q1で、対象が電源回路の動作ではないと確認"
        item["不足事実"] = ""
        self.check()
        self.assertEqual(self.handoff()["選択可能候補ID"], ["C1", "C3"])

    def test_partial_document_cannot_finish_out_of_scope(self):
        self.review["候補更新"][1]["担当範囲整合"] = "判定対象外"
        with self.assertRaises(ContractError):
            validate_review(self.frozen, self.review, self.sources, full_text=False)

    def test_unnecessary_without_alternative_is_rejected(self):
        item = self.review["候補更新"][1]["確認結果"][0]
        item["結果"], item["条件判定"] = "確認不要", "確認不要"
        with self.assertRaises(ContractError):
            self.check()

    def test_return_to_plan_cannot_be_ignored(self):
        self.review["候補更新"][0]["差戻事項"] = ["条件の意味が曖昧"]
        with self.assertRaises(ContractError):
            self.check()

    def test_partial_facts_and_step1_survive_handoff(self):
        handoff = self.handoff()
        self.assertEqual(handoff["候補"][1]["STEP2"], self.review["候補更新"][1])
        self.assertEqual(handoff["候補"][2]["STEP1"], self.candidates[2]["STEP1"])
        self.assertNotIn("STEP2", handoff["候補"][2])
        self.assertEqual(handoff["工程文脈"], self.review["工程文脈"])
        # 本文確認が必要（未解決）のC2も最終段で選べる。落とすのは確認済みの事実で外れた候補だけ
        self.assertEqual(handoff["選択可能候補ID"], ["C1", "C2", "C3"])
        self.assertEqual(handoff["選択不可候補ID"], [])
        self.assertEqual(handoff["未解決候補ID"], ["C2"])

    def test_adopted_condition_and_plan_survive_handoff(self):
        handoff = self.handoff()
        self.assertEqual(handoff["候補"][0]["確認計画"], self.frozen["計画"]["候補計画"][0])

    def test_handoff_does_not_mutate_step1_or_review(self):
        before = deepcopy((self.candidates, self.review))
        handoff = self.handoff()
        handoff["候補"][0]["STEP1"]["不足事実"] = "changed"
        self.assertEqual(before, (self.candidates, self.review))

    def test_pending_candidate_cannot_be_skipped(self):
        self.candidates[2]["STEP1"]["担当範囲整合"] = "本文確認が必要"
        with self.assertRaises(ContractError):
            self.handoff()

    def test_old_step2_is_not_silently_reused(self):
        self.candidates[2]["STEP2"] = {"担当範囲整合": "整合確認済み"}
        with self.assertRaises(ContractError):
            self.handoff()

    def test_valid_final(self):
        validate_final(self.final(), self.handoff())

    def test_excluded_candidate_cannot_be_resurrected(self):
        self.candidates[2]["STEP1"]["担当範囲整合"] = "明確に不整合"
        result = self.final()
        result["最終判定"] = "C3"
        with self.assertRaises(ContractError):
            validate_final(result, self.handoff())

    def test_original_destination_requires_abstention(self):
        result = self.final()
        result["本来の届け先"] = "後処理"
        with self.assertRaises(ContractError):
            validate_final(result, self.handoff(), all_labels=["整列装置", "電源回路", "媒体搬送", "後処理"])
        result["最終判定"] = "自信無"
        validate_final(result, self.handoff(), all_labels=["整列装置", "電源回路", "媒体搬送", "後処理"])

    def test_original_destination_must_be_a_rule_label(self):
        result = self.final()
        result["最終判定"], result["本来の届け先"] = "自信無", "存在しない担当"
        with self.assertRaises(ContractError):
            validate_final(result, self.handoff(), all_labels=["整列装置", "電源回路", "媒体搬送"])

    def test_non_owner_cannot_be_chosen_for_a_unit(self):
        # 担当ユニットが決まっているのに、受け持たない技術担当を選ぶ（CISで低圧電源を選んだ形）
        result = self.final()
        result["最終判定"] = "C2"
        with self.assertRaises(ContractError):
            validate_final(result, self.handoff())

    def test_tech_candidate_allowed_when_unit_is_common(self):
        result = self.final()
        result["担当ユニット"], result["最終判定"] = "本体共通", "C2"
        validate_final(result, self.handoff())

    def test_unknown_unit_requires_abstention(self):
        result = self.final()
        result["担当ユニット"] = "特定できない"
        with self.assertRaises(ContractError):
            validate_final(result, self.handoff())

    def test_missing_evaluation_of_selectable_is_rejected(self):
        result = self.final()
        result["候補評価"] = result["候補評価"][:1]
        with self.assertRaises(ContractError):
            validate_final(result, self.handoff())

    def test_suspicious_unconfirmed_is_reported(self):
        # C2-Q1は、事実と引用があるのに未確認。部分事実の可能性もあるので例外ではなく報告
        self.assertEqual(suspicious_unconfirmed(self.review), [("C2", "C2-Q1")])

    def test_render_final_prompt_fills_all_placeholders(self):
        system, user = render_final_prompt(
            TEMPLATE, claims="請求項1 …", step0={}, process_context={}, handoff=self.handoff(),
            all_rules=[{"ラベル": "整列装置", "担当範囲の定義": "…"}])
        self.assertIn("請求された物を設計・変更するのは誰か", system)
        self.assertNotIn("{{", user)

    def test_v4_2_differs_from_v4_by_one_line_only(self):
        old, new = TEMPLATE.splitlines(), TEMPLATE_V4_2.splitlines()
        added = [line for line in new if line not in old]
        self.assertEqual(len(new), len(old) + 1)
        self.assertEqual(len(added), 1)
        self.assertIn(V4_2_LINE, added[0])
        self.assertNotIn("直接構成または変更", TEMPLATE_V4_2)  # 不採用のBの段落が混入していない

    def test_render_v4_2_fills_all_placeholders(self):
        system, user = render_final_prompt(
            TEMPLATE_V4_2, claims="請求項1 …", step0={}, process_context={}, handoff=self.handoff(),
            all_rules=[{"ラベル": "整列装置", "担当範囲の定義": "…", "代表例（非限定）": "整列機構"}])
        self.assertIn(V4_2_LINE, user)
        self.assertNotIn("{{", user)


if __name__ == "__main__":
    unittest.main()
