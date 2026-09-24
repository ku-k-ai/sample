# 会社のCodexへの作業指示（2026-09-24）

社内パイプライン（STEP1 → STEP2_PLAN → STEP2_REVIEW → FINAL_DECISION）の構成は変えない。変えるのは次の4点だけ。

## 1. 検査コードを差し替える

`patent_pipeline/guards.py` をこのリポジトリの版にする。変更点：

| 関数 | 変更 |
|---|---|
| `validate_review` | 「判定対象外」は、確認済み（成立・不成立）の回答が1件以上あるときだけ認める。確認済み0件なら例外。 |
| `build_final_input` | 選択可能を「整合確認済み」「本文確認が必要」の両方にする。落とすのは「明確に不整合」「判定対象外」だけ。`未解決候補ID` を追加。 |
| `validate_final` | 最終判定プロンプトv4の出力形式で検査する（下記）。旧形式（主な届け先との関係・前段不整合）は廃止。 |
| `suspicious_unconfirmed`（新） | 確認した事実と原文引用があるのに「未確認」の回答を返す。 |
| `render_final_prompt`（新） | v4プロンプトに値を差し込む。 |

`suspicious_unconfirmed` が1件以上返したら、その確認事項だけでSTEP2_REVIEWを1回再実行する。2回目も未確認ならそのまま進める（候補は選択可能に残るので、最終段で判断される）。

`validate_final` が見ること：
- 選択可能候補がすべて評価されている。入力に無い候補を評価していない。
- 最終判定は選択可能候補か「自信無」。
- 本来の届け先を書いたら自信無（代わりを選ばない）。本来の届け先は分類ルール表のラベル。
- 担当ユニットが「特定できない」なら自信無。
- 担当ユニットが本体共通・限定なし以外なのに、「受け持たない」候補を選んでいない。

## 2. STEP2_REVIEWのプロンプトを2か所差し替える

`prompts/step2_review_patch.md` のとおり。「確認済み」の定義（字面でなく中身で判定）と、「判定対象外」の定義（見つからないだけでは落とさない）。

## 3. 最終判定プロンプトを差し替える

`prompts/final_decision_v4.txt` に置き換える。判定はLLMが最後まで行う（ルールベースの順位付けコードは使わない）。

```python
from patent_pipeline.guards import build_final_input, render_final_prompt, validate_final

handoff = build_final_input(candidates, frozen_plan, review, sources, full_text=True)
system, user = render_final_prompt(
    template=open("prompts/final_decision_v4.txt", encoding="utf-8").read(),
    claims=independent_claims,            # 今と同じ
    step0=step0_invention_profile,        # 今と同じ
    process_context=process_and_device_context,  # 今と同じ
    handoff=handoff,                      # STEP1/2の表。外さない
    all_rules=all_rule_rows,              # 追加：分類ルール表の全行（ラベル・定義・代表例・備考）。列は足さない
    body_quotes=[],                       # 任意：表に無い本文引用があれば
)
result = call_existing_llm(system, user)  # 既存の接続・モデル設定のまま
validate_final(result, handoff, all_labels=[r["ラベル"] for r in all_rule_rows])
```

出力の `最終判定` は候補IDか「自信無」。`本来の届け先` が空でなければ人手確認へ回す。

## 4. テスト

```powershell
python -m unittest discover -s tests -p "test_pipeline_guards.py" -v
```

37本。すべて合成データで、分類精度のテストではない。

## 5. 17件で確認する

1〜4を入れたら17件を流し、各件の `請求物の種類`・`担当ユニット`・`最終判定`・`本来の届け先` と、STEP2で「判定対象外」「明確に不整合」になった候補を一覧にする。外れた件は次のどれかに分けて報告する。

- 正解の候補がSTEP1/2で落ちている（最終段の問題ではない）
- 正解の候補は残っているが、担当ユニットの読みが違う
- 担当ユニットは合っているが、受け持つ候補の対応が違う

事前確認（Sonnet、3件×2回）の内容は、社内ローカルの資料（v4_eval）にある。正解の候補がSTEP2で判定対象外になっている表のままでは、最終段は「自信無・本来の届け先あり」までしか出せない。1と2を入れて初めて正解に届く。

## やってはいけないこと

- 17件の正解に合わせて、プロンプトに特定の特許の対象物の名前を入れない。
- 正式ルール表に列を足さない。
- このリポジトリ（公開）に、社内ルール表・特許のSTEP1/2の表・送受信ログ・正解を置かない。
