# 社内パイプラインとの接続契約

## 実装担当

会社のCodexが、現在の分類本体の入口を調べ、評価用の小さなアダプターを社内ワークスペースへ追加する。この公開リポジトリにはその入口も認証情報もない。架空の関数名やSDK呼出しで「接続済み」にしない。

実行例（雛形。実在するPythonとアダプターのパスへ変更する）：

```text
python company_adapter.py --request REQUEST_JSON --response RESPONSE_JSON
```

`adapter_command` は文字列配列。shellを使用しない。`{request}` と `{response}` を各1つ含める。相対ファイルの解決はアダプター自身の場所を基準にし、作業ディレクトリ依存にしない。アダプターは既存接続のみ利用し、別モデルへ黙ってフォールバックしない。

## 受け取るJSON

`trial_id`, `case_id`, `arm`, `repeat`, `input`, `reference_by_stage`, `model_settings`, `frozen_sha256`。

`input` は `claims`（独立項原文）, `description`（全文）, `candidates`（候補ID・正式ラベル・正式ルール）だけ。正解ラベルや今回の解釈は渡さない。`case_id` を過去ログ検索・正解参照のキーに使わない。

アダプターは入力を既存本体の通常形式へ変換する。`candidates` は既存上流が受け取るカタログであり、最終選択候補を全件へ復活させる指示ではない。上流の候補抽出・STEP0が存在する場合、その処理も現行通りに使う。対象の正解が上流で落ちる場合も失敗として残す。

`reference_by_stage` は4段階それぞれの補助入力テキスト。Aはすべて空文字。B/Cは同じ位置・同じ資料区分へ追記する。元のsystem/userテンプレートと正式ルールは不変。参照例の正解は**他案件**のものとして区別する。参照例を対象特許の本文として結合しない。

**対象特許のdescriptionをSTEP1のモデル入力に含めない。** アダプターが全文を保持することと、STEP1へ見せることは別。STEP1は現行の独立項ベースの処理、STEP2だけ全文を読むという条件を守る。各STEPの呼出し直前で元テンプレート＋実データ＋補助欄がどうなったかを保存する。

## 実行する処理

1. trialごとに新しい状態で現行パイプラインを開始する。過去trialの回答・キャッシュ済み最終結果・評価正解にアクセスしない。
2. 各STEPの補助欄へ、その段階の `reference_by_stage` だけを差し込む。新しい出力schemaは要求しない。
3. 既存のSTEP1結果をSTEP2へ渡し、本文確認後の状態を最終へ渡す。正解に合わせて未確認・候補資格を修正しない。
4. STEP1の本文確認対象がゼロの場合だけ、既存STEP2スキップを維持する。全候補を本文で再調査する変更をしない。
5. 元の最終候補IDを、その案件の正式ラベルへ既存対応表で変換する。ラベル名をLLMに書き直させない。
6. 原送受信、usage、実行時間、エラーをローカルに記録する。補助欄が使われたことは文字列ハッシュと実ログで照合する。

## 返すJSON

```json
{
  "trial_id": "入力と同じ",
  "request_sha256": "tools.boundary_eval.digest(request)",
  "execution_kind": "live_llm",
  "model_settings_sha256": "tools.boundary_eval.digest(request['model_settings'])",
  "frozen_sha256": "入力と同じ",
  "status": "classified",
  "final_label": "既存候補の正式ラベル",
  "stages": [
    {
      "stage": "STEP1",
      "status": "called",
      "supplement_sha256": "補助欄文字列をUTF-8化したSHA-256（JSON化しない）",
      "request_file": "step1.request.json",
      "request_file_sha256": "ファイルのバイト列SHA-256",
      "response_file": "step1.response.json",
      "response_file_sha256": "ファイルのバイト列SHA-256"
    }
  ],
  "usage": {"llm_calls": 4, "input_tokens": 0, "output_tokens": 0}
}
```

上は構造説明であり、実測結果ではない。`stages` には `STEP1`, `STEP2_PLAN`, `STEP2_REVIEW`, `FINAL_DECISION` の4件をその順で記録する。各ファイルはresponse.jsonと同じディレクトリ以下。送受信ファイルは空にしない。非対応usage値は0で捏造せずnull又は省略する。

正常な保留は `status: "abstain", final_label: null`。APIエラー・schema失敗はプロセスの非ゼロ終了とログで残すか、失敗応答を返す。runnerはerrorとして集計する。モックは `execution_kind: "mock"`。実行証拠としては拒否される。

STEP2スキップの項目は `status: "skipped", skip_reason: "no_pending_candidates", pending_count: 0`。PLANとREVIEWは同時にスキップする。その事実はSTEP1の実出力で確認する。最終段やSTEP1はスキップできない。

## 何を機械検証し、何は監査が必要か

ツールは、入力ハッシュ、設定ハッシュ、参照欄ハッシュ、ログの存在とハッシュ、段階の件数、最終ラベルのカタログ内一致、欠落や重複を検査する。

それだけでは、アダプターが本当にそのモデルへ送ったこと、現行の候補除外を守ったこと、引用の意味が正しいことを保証しない。接続前後の実送信ログを比較する。`live_llm` という自己申告や単体テストだけを精度の証拠にしない。

評価runnerは各プロセスの時間を制限する。アダプターが子プロセスを作る設計は避け、既存APIのタイムアウトも設定する。未完了処理を裏で継続したまま次の試行へ進めない。既存リトライがある場合は上限と全試行を記録し、都合のよい回答だけを採用しない。

## コスト・公開

トークン/時間/実LLM呼出し回数は全失敗分も残す。価格は実在する社内単価がある場合だけ計算する。報告にはモデル設定と予算を付ける。

リポジトリは公開。実アダプターは内部パス・接続を含み得るため、原則として社内ローカルに置く。送受信ログ、評価plan、正解、正式ルール、カードをGitHubへpushしない。
