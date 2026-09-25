# 特許の届け先分類：最終判定の検査とプロンプト

**会社のCodexは [CODEX_START_v4_3.md](CODEX_START_v4_3.md) から着手する。**

| ファイル | 中身 |
|---|---|
| `prompts/final_decision_v4.txt` | 最終判定プロンプトv4（参考。本番はこれにCodexの追記がある） |
| `prompts/final_decision_v4_3.txt` | v4に3か所を加えた版（参考）。本番へは CODEX_START_v4_3.md の手順で差し込む |
| `patent_pipeline/sections.py` | 全文から【発明が解決しようとする課題】【発明の効果】を切り出す |
| `patent_pipeline/guards.py` | 段階間の検査と、プロンプトへの差し込み |
| `prompts/step2_review_patch.md` | STEP2_REVIEWの差し替え（導入済み） |
| `tests/` | 合成データのテスト。分類精度のテストではない |

公開するのは、検証コード・雛形・合成テストだけ。社内ルール表、特許の入力、送受信ログ、正解は置かない。
