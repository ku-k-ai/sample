# データ形式・来歴・漏洩防止

## 保存先

推奨は社内専用ディレクトリ。リポジトリ内なら `.local_eval/` を使い、Gitへ追加しない。`plan.json` は評価用正解を含むためモデルには渡さない。runnerが試行ごとに書く `request.json` だけをアダプターへ渡す。

公開する資料は雛形と合成テストのみ。既存公開履歴にある素材を追加公開の根拠にしない。顧客の正式ルール・実特許と社内正解の対応は、公開特許由来でも機密になり得る。

## manifestの構成

`templates/study.template.json` をCodexが既存資産から埋める。ユーザーに手作業でJSONを作らせない。

- `freeze_files`：正式ルール、実際の各STEPテンプレート、schema、分類本体、評価アダプター、設定ファイル（鍵は除く）。manifestからの相対パス又は絶対パス。sourceと依存ファイルを漏らさず列挙する。ハッシュは改変検出であり完全な来歴証明ではない。
- `model_settings`：実モデル/デプロイ・API版・推論量・既存サンプリング・キャッシュ設定等。鍵は含めない。
- `cases`：case_id、family_id、kind、gold_label、gold_origin、input。
- `kind`：real / control / synthetic。対照・合成と実件を報告で区別する。元の17件と対照追加を同じ件数としてごまかさない。
- `gold_origin`：requester / user / reviewed_synthetic。ラベルは既存の出所からのみ使用する。AIの予測や新規説明をrequesterとしない。
- `input.candidates`：candidate_id、label、official_ruleの3項目。正式ルールの定義・例・振分けを原文のまま保持する。`label` は候補内で一意な正式ラベル。実装が別の安定キーを使う場合は表層表記との対応を固定する。
- `references`：IDをキーに、source_family_ids、source_case_ids、gold_origin、status、views。
- `folds`：対象case_idをキーに、Bで使うreference_idsとCのgeneralization。
- `views`：STEP1 / STEP2_PLAN / STEP2_REVIEW / FINAL_DECISIONの各段階の参照文。対象特許についての記載ではない。
- `generalization`：source_reference_ids、source_family_ids、source_case_ids、status、views。Bと同じ選択事例だけから作り、全ての出所を記載する。

`status: approved` は来歴・原文・利用条件の検証を終えたカード版を示す。発注元承認を意味しない。承認者と根拠はローカルのカード台帳に記録し、既存の確定ラベル以外を勝手に承認しない。

## familyと派生説明

同一特許・同一特許ファミリー・言い換え・反実仮想・その特許から作った一般化文を、漏洩のための同じ系統として扱う。複数特許から作ったカードは全source_family_idsを持つ。

Bの参照に対象familyがあれば `reference_excluded_known17` と `sealed_new_cases` ではprepareが止まる。Cの来歴はBと完全に一致させる。対象を除いたように見せて、その対象から作った「内部電源ならリーダー」という文だけ残すことは禁止。

機械検査は**申告された来歴**しか検査できない。semanticな類似や隠された出所は検出できないため、foldごとの作成入力ログと資料台帳を監査する。生成器へ一度全17件を見せた同じ会話でfold文を作り直さない。

## 参照選択

初回は検索器を作らない。固定した小規模のライブラリから、対象familyを機械的に除外した同じ選択方針で資料を渡す。対象の正解ラベルを見て「正解に導くカード」を選ばない。候補や独立項を用いる選択方針は事前に固定・記録する。

B/Cは同じ事例集合を使う。Bは原文付き事例、Cはその事例集合から作った抽象文。参照がないfoldは空の架空カードで埋めない。参照不足として、追加の既存確定事例を探すか、その境界の転移評価が不足すると報告する。

## 合成対照

印字→切断と切断→印字などの合成例は、条件理解を調べるために使える。しかし、後者の最終ラベルまで自動で給紙と確定してよいわけではない。最終ラベルの正解が未確定なら、最終ラベル評価のcasesには入れず別の条件判定テストとして記録する。

17件の派生対照は同じfamilyに入れる。派生対照を学習へ入れて元特許を未知扱いしない。レビュー前の合成案はapprovedにしない。

開始時の候補カタログに正解がないケースは、ツールが評価側へ `gold_absent_from_input_candidates` と記録する。正解を候補へ足して救済しない。元のパイプラインでの候補落ちとして集計・診断する。
