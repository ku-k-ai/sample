# 今回の実施範囲

## 実施済み

- Python 3.13.5 / Linux上で、標準ライブラリのみを使用。
- `python -m unittest discover -s tests -p "test_boundary_eval.py" -v`：33テスト通過。
- CLIのprepareと欠落結果のreport、実際のsubprocess非ゼロ終了の記録も合成入力で確認。
- 対象/familyの参照混入、Cの出所不一致、正解の推論入力混入、固定ファイル変更、出力欠落/重複、保留、退行、揺れ、モック結果、ログ改変の機械検査。
- `python -m py_compile tools/boundary_eval.py tests/test_boundary_eval.py`：通過。

テスト出力は [boundary_eval_unittest.txt](boundary_eval_unittest.txt)。

## 未実施・保証しないこと

- 会社の分類本体への接続、社内API呼出し、17件のA/B/C分類。
- 実原文からのカード作成と、正式ルールの意味整合の確認。
- Windowsでの実行（OS依存のshellや追加依存を使わない実装だが、現地確認が必要）。
- 隠された来歴の検出、正解説明の妥当性、実LLM送信の事実の自動証明。

**33テスト通過は検証器の機械的な挙動の確認であり、特許分類が改善した証拠ではない。**
旧 `tests/test_pipeline_guards.py` と `patent_pipeline/guards.py` は変更しておらず、今回の33件には含まない。

今回のSHA-256：
- tools/boundary_eval.py: `385c189262e2d92357701da484fd4d99df5f4f7b36bb4ce7d576b346be314481`
- tests/test_boundary_eval.py: `91bfa135f4269ce5bd10a3f51f83968a310496ac1900f75d11d8424c6826a366`
