# 検証記録 — 2026-09-08（JST）

## 実施済み

作成側環境: Linux / Python 3.13.5。

`python -m unittest discover -s tests -v`: **28 件成功**。
`python -m compileall -q .`: 成功。

内訳:
- 実際のローカル中継を使うオフライン通信テスト 10 件。認証情報の付与、特殊文字、TLS トンネル、HTTPS 上流、証明書検証、認証拒否、HTTP 拒否、接続不能、稼働中トンネルの終了等。
- ワーカー判定のモックテスト 7 件。HTTP 200 だけを内容確認成功としない、403・不明ステータス・本文不一致を失敗とする等。
- 設定・Windows コマンドのモック・TLS 設定・リダイレクトの単体テスト 11 件。

TLS テスト用依存: cryptography 46.0.4。
試験ログ: [tests.txt](tests.txt)。

## 未実施（成功を主張しない）

- Windows VM 上での setup.ps1 / run.ps1 実行。
- PowerShell の実パーサーによる構文検証。
- Windows 上での SeleniumBase + Chrome for Testing 通し実行。
- 実際の Bright Data 資格情報を使った接続。
- 業務対象サイトへのアクセス、UC モードのサイト別効果。
- Cloud Run への配置・稼働。

作成側に SeleniumBase は導入されておらず、外部パッケージ取得の DNS 接続にも失敗した。
したがって、オフラインテストの合格をブラウザの実接続成功へ読み替えないこと。
ブラウザの認証ダイアログを操作せずに済む経路を実装したもので、環境・契約・対象サイトを問わない成功保証ではない。
