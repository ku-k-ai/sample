# sample：特許担当分類の段階維持修正

**STEP1 → STEP2_PLAN → STEP2_REVIEW → FINAL_DECISION を維持する修正版です。**
旧「1回へ統合する版」は置き換えました。本文確認の結果を捨てません。

- [段階別プロンプト全文と差し替え箇所](patent_assignment_prompt.md)
- [接続仕様・状態と証拠の検証](docs/INTEGRATION.md)
- [検証範囲・回帰確認項目](docs/VALIDATION.md)

修正するのは、確認条件の変質、質問の自由生成、事実取得と条件成立の混同、工程対応と実接続の混同、前段状態の勝手な格上げです。

## 検証

```bash
python -m unittest discover -s tests -v
```

`patent_pipeline/guards.py` は確認計画・引用・引き継ぎ・選択資格を検証する補助コードです。LLMを呼び出す実行プログラムではありません。
特許分類用の本体コードとSTEP1本体の原文はこのリポジトリにないため、実環境への接続変更は未実施です。STEP1は不足条件作成部分の差し替え指示を提供しています。
実LLMによる分類精度・複数回の安定性は未検証です。補助コードのテスト成功と分類成功を区別してください。

既存の `seleniumbase_brightdata_windows/` は変更していません。
