# 特許分類：対照事例参照の A/B/C 検証

**会社の Codex は [CODEX_START.md](CODEX_START.md) から着手する。**
これは新しい分類ルールや統合型プロンプトではない。発注元の正式ルールと、既存の `STEP1 → STEP2_PLAN → STEP2_REVIEW → FINAL_DECISION` を維持して、解釈の補助方法だけを比較する。

| 条件 | 変更範囲 |
| --- | --- |
| A | 会社で現在動いているパイプライン。そのまま。 |
| B | A ＋ 原文・正解・対照を持つ、別案件の事例参照。各段階の役割は変えない。 |
| C | A ＋ B と同じ参照事例だけから作った一般化文。具体例は入れない。 |

最初は参照する事例を固定する。検索、skills の自動選択、新モデル、FT、分類ルール改変を同時に導入しない。

## 引継ぎ一式

- [Codex の実行指示・停止条件](CODEX_START.md)
- [比較計画と合否条件](docs/EXPERIMENT_PROTOCOL.md)
- [社内パイプラインとの接続契約](docs/ADAPTER_CONTRACT.md)
- [データ形式・漏洩と公開防止](docs/DATA_CONTRACT.md)
- [事例を作る指示](prompts/BUILD_REFERENCE_CARDS.md) ／ [一般化文を作る指示](prompts/BUILD_GENERALIZATIONS.md)
- [実行マニフェストの雛形](templates/study.template.json)
- [準備・実行・集計ツール](tools/boundary_eval.py)
- [検証範囲と未実施事項](test-evidence/STATUS.md)

## ローカル実行

Python 3.10 以上、標準ライブラリのみ。Windows / PowerShell を想定し、Docker・WSL・外部ライブラリを追加しない。社内の実LLM呼出しは**既存接続を使うアダプター**へ委譲する。

```powershell
python -m unittest discover -s tests -p "test_boundary_eval.py" -v
python tools/boundary_eval.py prepare .local_eval/study.json --out .local_eval/locked.json
python tools/boundary_eval.py run .local_eval/locked.json --out .local_eval/run-001
python tools/boundary_eval.py report .local_eval/locked.json .local_eval/run-001/results.jsonl --out .local_eval/report-001.json
```

後半の3コマンドは、Codex が社内データと既存接続から `study.json` とアダプターを用意してから実行する。**この公開リポジトリには17件の原文・正解・社内分類本体・API接続はない。実分類成功はまだ確認していない。**

## 以前の修正案について

以前の段階別プロンプトと接続仕様は [archive/staged_revision/](archive/staged_revision/) に原文のまま保存した。効果がなかったというユーザー報告を受け、今回の既定の導入対象から外す。会社側で採用済みなら、それを含む現行状態を A として凍結し、勝手に差し戻さない。

`patent_pipeline/guards.py` と既存テストは残す。文字列・形式検査の通過を、ルール解釈や分類精度の改善と呼ばない。`seleniumbase_brightdata_windows/` は今回の対象外で変更しない。

**公開するのは検証コード・雛形・合成テストだけ。社内ルール、原文、正解、事例カード、モデル送受信ログは公開しない。**
