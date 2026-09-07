# SeleniumBase + Bright Data — Windows VM 検証用

GCP の **Windows VM** で、SeleniumBase と認証付き Bright Data プロキシを組み合わせるためのコードです。Docker、WSL、Xvfb は使いません。会社プロキシは今回の検討対象ではありません。

**Windows 実機・実際の Bright Data・業務対象サイトでの接続成功は未検証です。** 実装済みと実接続確認済みを区別してください。作成側の Linux 環境で、中継の実通信を含むオフラインテスト 28 件を実行しています。詳細は [検証記録](test-evidence/TEST_STATUS.md) にあります。

## 構成

```text
Windows VM
  run.ps1 → run.py
               ├─ 127.0.0.1 の一時ポートで認証中継を起動
               └─ browser_task.py → SeleniumBase → Chrome for Testing
                                                    ↓ 認証情報なし
                                              ローカル中継
                                                    ↓ 認証情報をここで付与
                                                Bright Data
                                                    ↓
                                                HTTPS サイト
```

Chrome の認証ダイアログを操作せず、ブラウザの認証用拡張にも依存しない方式です。認証が拒否されたときは、中継が元の `407` を記録し、Chrome には `502` を返します。認証失敗を成功扱いにする処理ではありません。

## 最初に実行する手順

前提は **64bit Python（3.12 推奨、コード上は 3.10 以上）** と Windows PowerShell 5.1 または PowerShell 7 です。GUI で確認する初回は、Windows VM の RDP デスクトップで実行してください。管理者として実行する前提ではありません。

リポジトリを取得して、このフォルダへ移動します。既に clone 済みなら、その作業コピーを更新してください。

```powershell
git clone https://github.com/ku-k-ai/sample.git
cd .\sample\seleniumbase_brightdata_windows
.\setup.ps1
notepad .env
```

`setup.ps1` は専用の `.venv`、SeleniumBase、Chrome for Testing、そのブラウザの実バージョンに一致する ChromeDriver を準備します。既存の通常 Chrome を差し替える処理ではありません。Python の場所を指定する場合は次のように実行します。

```powershell
.\setup.ps1 -PythonExe 'C:\Path\To\python.exe'
```

`.env` の次の 4 項目を、Bright Data 管理画面の**利用中の Zone のプロキシ接続情報**へ変更します。Browser API の WebSocket/CDP エンドポイントや Web Unlocker API のキーは、このコードの接続方式とは異なります。

```dotenv
BRD_PROXY_HOST=管理画面のホスト名
BRD_PROXY_PORT=管理画面のポート番号
BRD_PROXY_USER=管理画面の完全なユーザー名
BRD_PROXY_PASS=管理画面のパスワード
```

`BRD_PROXY_SCHEME=http` は「中継→Bright Data」の方式です。プロバイダーの接続仕様に合わせて `http` または `https` にします。対象サイトの `https://` とは別です。上流に HTTP を選んだ場合、その区間の Basic プロキシ認証情報は暗号化されません。HTTPS プロキシ対応エンドポイントなら `https` の利用を検討してください。

対象サイトは最初から実際の HTTPS URL に変更できます。初期値は Bright Data の公式テスト URL です。既に接続テストを済ませている場合、同じテストからやり直す必要はありません。

```dotenv
TARGET_URL=https://geo.brdtest.com/welcome.txt
EXPECT_TEXT=
EXPECT_SELECTOR=
```

設定を保存して、実行します。

```powershell
.\run.ps1
```

画面なしの実行を比較する場合は次です。Windows で Xvfb を起動するのではなく、Chrome の headless モードを使います。

```powershell
.\run.ps1 -Headless
```

PowerShell スクリプトを許可されていない環境では、組織の実行ルールに従ってください。Python を直接使う場合の同等の最低限のセットアップは次です。ドライバの解決・ダウンロードは SeleniumBase に任せます。

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\sbase.exe get cft
Copy-Item .env.example .env
# .env は必ずローカルで編集する。
.\.venv\Scripts\python.exe .\run.py --env-file .env
```

## モードと成功条件

既定の `BROWSER_MODE=standard` から始めます。`uc` へ変更した比較も可能ですが、この版では UC を接続したまま利用し、CDP モードへの切替や CAPTCHA 自動処理は行いません。UC 固有のステルス動作や対象サイトの許可を保証するものではありません。

**HTTP 200 だけでは、目的のページが取得できたとは判定しません。** 対象ページ特有の本文か、表示される要素を指定してください。両方指定した場合は両方を確認します。

```dotenv
EXPECT_TEXT=目的のページに固有の文字列
EXPECT_SELECTOR=#main-content
```

| 結果 | 意味 |
|---|---|
| `PASS_CONTENT_ASSERTION` | HTTPS・HTTP 2xx と指定した本文／要素を確認した |
| `PASS_HTTP_ONLY_CONTENT_UNVERIFIED` | HTTPS・HTTP 2xx のみ確認した。ページ内容は未確認 |
| `ok: false` または非ゼロ終了コード | 起動、接続、ステータス、本文、終了処理等で失敗した |

期待条件は実際の業務ページを識別できるものにしてください。`body` の存在などの汎用条件だけでは、ブロック画面を除外できません。

## 失敗した場合に確認するファイル

結果は `artifacts\<UTC日時>\` に保存されます。認証情報や業務ログを公開リポジトリへ送信する処理はありません。

| ファイル | 内容 |
|---|---|
| `run.json` | 全体結果、タイムアウト、ワーカー終了コード |
| `result.json` | 失敗段階、ブラウザ／ドライバ／SeleniumBase のバージョン、HTTP ステータス、Chrome エラーコード |
| `relay.jsonl` | Bright Data が返した CONNECT ステータス、数値エラー識別子 |
| `worker.log` | ライブラリ出力と詳細な例外。**URL 等の業務情報が入る可能性があるため、そのまま公開しないこと** |

`SAVE_PAGE_ARTIFACTS=1` にした場合だけ、`page.png` と `page.html` も保存を試みます。これらも業務情報を含み得ます。

`phase: browser_start` はブラウザ起動側、`upstream_connect/status: 407` は上流プロキシ認証側です。`407` を Chrome へ転送しないため、ブラウザ側では `ERR_TUNNEL_CONNECTION_FAILED` 等になり得ます。両方のログを合わせて判断してください。

`RUN_TIMEOUT` の既定値は 180 秒です。時間切れ／Ctrl+C 時には、この検証ワーカーの PID と子プロセスを対象に `taskkill /T /F` を実行します。VM 全体の `chrome.exe` を一括終了する処理ではありません。通常終了は SeleniumBase のブラウザ終了処理に任せます。

## 証明書について

中継は TLS を復号しません。SeleniumBase 4.51.12 の Chrome options 生成処理に小さなアダプターを置き、暗黙の証明書エラー無視の起動引数を除去し、`acceptInsecureCerts=False` にしています。この部分は内部 API に依存するため、SeleniumBase を更新する場合は再検証してください。

利用する Bright Data 製品・設定で同社 CA が必要な場合は、同社公式から取得し、出所・フィンガープリント・組織の許可を確認した証明書だけを Windows の適切な信頼ストアへ登録してください。スクリプトは証明書を自動インストールしません。`BRD_PROXY_CA_FILE` は Python 中継から HTTPS プロキシへ接続する際の追加 CA であり、**Chrome の対象サイト用信頼ストアを変更する設定ではありません**。

## 制約・バージョン・機密情報

- **対象は HTTPS / CONNECT のみ**です。平文 HTTP、HTTP へ戻るリダイレクト、平文 HTTP に依存するページは対象外です。一般用途のフル機能プロキシではありません。
- `.env` はローカル専用です。`@ : # $ % & + =` などは原則そのまま記入し、URL エンコードしません。インラインコメントや変数展開は行いません。OS の同名環境変数があれば `.env` より優先します。
- SeleniumBase は `4.51.12` に固定しています。ブラウザはセットアップ時に取得した版です。`installed-browser.json` と `installed-requirements.txt` をローカル保存します。完全に同じ条件へ揃えるには同じ Python と依存パッケージも必要です。
- 再現時に Chrome の版を指定するには `setup.ps1 -ChromeVersion '<取得済みのバージョン>'` を使います。
- `.gitignore` で `.env`、実行ログ、HTML、画像、証明書等の作業生成物を除外しています。`git add -f` などで強制追加しないでください。Git ignore は暗号化でも OS のアクセス制御でもありません。VM 上のファイル権限は別途管理してください。
- この Windows 版に Cloud Run 用 Dockerfile は含めていません。将来の Linux コンテナ＋仮想画面での検証は別工程です。

## オフラインテスト

Bright Data の資格情報も外部サイトへのアクセスも不要です。TLS 用証明書は一時ディレクトリで生成・破棄します。Windows に `openssl.exe` を導入する必要はありません。

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-test.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Windows 固有プロセス終了コマンドのテストはモックです。テストが通ったことだけをもって、Windows 上の Chrome や Bright Data 接続の成功と解釈しないでください。

## 参照した一次情報

- [SeleniumBase: browser binary / 実行オプション](https://seleniumbase.io/help_docs/customizing_test_runs/)
- [SeleniumBase: ブラウザ・ドライバの取得](https://seleniumbase.io/help_docs/webdriver_installation/)
- [SeleniumBase v4.51.12: Chrome options 実装](https://github.com/seleniumbase/SeleniumBase/blob/v4.51.12/seleniumbase/core/browser_launcher.py)
- [SeleniumBase 4.51.12 公開パッケージ](https://pypi.org/project/seleniumbase/4.51.12/)
- [Bright Data: Selenium 連携とテスト URL](https://docs.brightdata.com/integrations/selenium)
- [Bright Data: ローカル中継側で認証する公式の構成例](https://docs.brightdata.com/proxy-networks/proxy-manager/integration)

ここで実装した `relay.py` は独自の小さな CONNECT 中継であり、Bright Data 公式 Proxy Manager 自体ではありません。
