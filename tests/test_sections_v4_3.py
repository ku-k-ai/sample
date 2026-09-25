"""合成データのテスト。切り出しと、v4.3テンプレートの差分が意図どおりかを確かめる（分類精度のテストではない）。"""
from pathlib import Path
import unittest

from patent_pipeline.sections import extract_problem_and_effect
from patent_pipeline.guards import render_final_prompt

ROOT = Path(__file__).resolve().parents[1]
V4 = (ROOT / "prompts" / "final_decision_v4.txt").read_text(encoding="utf-8")
V43 = (ROOT / "prompts" / "final_decision_v4_3.txt").read_text(encoding="utf-8")

SAMPLE = """【技術分野】
【0001】本発明は、試験装置に関する。
【背景技術】
【0002】従来の試験装置は周辺の検査に用いられる。
【先行技術文献】
【特許文献】
【特許文献1】特開2000－000000号公報
【発明の概要】
【発明が解決しようとする課題】
【０００４】従来は部材Ａの開口から音が漏れる。
【0005】本発明は、開口からの音漏れを抑えることを目的とする。
【課題を解決するための手段】
【0006】本発明の試験装置は、部材Ｂとの干渉を避ける壁を備える。
【発明の効果】
【0007】本発明によれば、音漏れを抑制できる。
【図面の簡単な説明】
【0008】
【図1】斜視図である。
【発明を実施するための形態】
【0009】実施例では検査後に排出する。
"""


class SectionsTest(unittest.TestCase):
    def test_extracts_problem_and_effect_only(self):
        got = extract_problem_and_effect(SAMPLE)
        self.assertEqual([g["見出し"] for g in got], ["発明が解決しようとする課題", "発明の効果"])
        self.assertIn("音漏れを抑えることを目的とする", got[0]["原文"])
        self.assertIn("【０００４】", got[0]["原文"])  # 全角の段落番号で止まらない
        self.assertIn("【0005】", got[0]["原文"])      # 半角の段落番号で止まらない
        self.assertIn("音漏れを抑制できる", got[1]["原文"])

    def test_does_not_include_background_means_or_embodiment(self):
        text = "".join(g["原文"] for g in extract_problem_and_effect(SAMPLE))
        self.assertNotIn("周辺の検査", text)      # 背景技術
        self.assertNotIn("干渉を避ける壁", text)  # 課題を解決するための手段
        self.assertNotIn("排出", text)            # 実施例
        self.assertNotIn("斜視図", text)          # 図面の簡単な説明

    def test_missing_headings_return_empty(self):
        self.assertEqual(extract_problem_and_effect("DETAILED DESCRIPTION\nEmbodiment 1 ..."), [])
        self.assertEqual(extract_problem_and_effect(""), [])

    def test_effect_missing_returns_problem_only(self):
        text = SAMPLE.replace("【発明の効果】\n【0007】本発明によれば、音漏れを抑制できる。\n", "")
        self.assertEqual([g["見出し"] for g in extract_problem_and_effect(text)], ["発明が解決しようとする課題"])

    def test_length_is_capped(self):
        long = "【発明が解決しようとする課題】【0004】" + "あ" * 5000 + "【発明の効果】【0005】い"
        got = extract_problem_and_effect(long, max_chars=100)
        self.assertEqual(len(got[0]["原文"]), 100)


class TemplateV43Test(unittest.TestCase):
    def test_v4_3_changes_only_intended_lines(self):
        old, new = V4.splitlines(), V43.splitlines()
        removed = [l for l in old if l not in new]
        added = [l for l in new if l not in old]
        self.assertEqual(len(removed), 1)
        self.assertTrue(removed[0].startswith("- STEP2の本文確認結果・根拠引用は"))
        self.assertEqual(len([l for l in added if l.strip()]), 5)
        self.assertIn("{{PROBLEM_AND_EFFECT_JSON}}", V43)
        self.assertIn("「発明の課題と効果」に書かれた目的から答えてください", V43)
        self.assertIn("「何のための構成か」の答えには使わないでください", V43)
        self.assertIn("括弧書きのまとめ（「〜に関連する分野」など）だけを根拠に", V43)
        # 本日不採用にした文が混ざっていない
        for ng in ("直接構成または変更", "代表例（非限定）の欄に、特徴部分の仕組みそのものの名前"):
            self.assertNotIn(ng, V43)

    def test_render_fills_problem_effect(self):
        handoff = {"候補": [], "選択可能候補ID": [], "選択不可候補ID": [], "工程文脈": {}}
        pe = extract_problem_and_effect(SAMPLE)
        system, user = render_final_prompt(V43, claims="請求項1 …", step0={}, process_context={},
                                           handoff=handoff, all_rules=[], problem_effect=pe)
        self.assertIn("音漏れを抑えることを目的とする", user)
        self.assertNotIn("{{", user)

    def test_render_with_no_problem_effect_gives_empty_list(self):
        handoff = {"候補": [], "選択可能候補ID": [], "選択不可候補ID": [], "工程文脈": {}}
        _, user = render_final_prompt(V43, claims="c", step0={}, process_context={}, handoff=handoff, all_rules=[])
        self.assertIn("# 発明の課題と効果（この特許自身の記載。候補によらず毎回同じ）\n[]", user)

    def test_v4_template_still_renders(self):
        handoff = {"候補": [], "選択可能候補ID": [], "選択不可候補ID": [], "工程文脈": {}}
        _, user = render_final_prompt(V4, claims="c", step0={}, process_context={}, handoff=handoff, all_rules=[])
        self.assertNotIn("{{", user)


if __name__ == "__main__":
    unittest.main()
