# 実装メモ: セキュリティと障害時の挙動

このリポジトリのサンプルが、テナント ID の扱い・ストア障害・タイムアウト・provider の寿命を
どう実装しているかの記録です。記事の変更点との対応は [ルート README](../README.md) を参照してください。

## セキュリティ

- 接続文字列・アクセスキーを使いません。`DefaultAzureCredential` のみで、Bicep は
  `disableLocalAuth: true` を設定します。
- Bicep が Azure-hosted application に付与する権限は
  **App Configuration Data Reader**（`516239f1-63e1-4d78-a4de-a74fb236a071`）だけです。
  ローカル手順の Data Owner はサインインした開発者への一時的な別割り当てで、テスト後に削除します。
- **テナント ID の検証がこのサンプルで最も重要な部分です。** URL 由来のテナント ID を検証せずに
  キーフィルタへ渡すと、`*` を指定するだけで全テナントの設定が読めてしまいます。
  [`src/mtappconfig/tenants.py`](../src/mtappconfig/tenants.py) のレジストリ照合と正規表現を通った ID だけがストアに到達します。
  ただし、これは不正な形式や未登録 ID を拒否する入力検証であり、**認可ではありません**。
  本番では URL で呼び出し元が選んだ値をそのまま信頼せず、認証済みのユーザーまたはワークロードの
  コンテキストからテナントを導出し、そのテナントへのアクセスを認可してからレジストリを解決します。
- Key Vault 参照の解決はこのサンプルでは実装していません。採用する場合は、App Configuration
  用とは別に Key Vault を読める資格情報または resolver をプロバイダーへ設定し、その ID に
  Key Vault の適切なデータプレーン RBAC を付与してください。

## 信頼性

- **すでにキャッシュ済みのテナント**は、ストア障害時も直近の値で応答を続けます
  （refresh 失敗はログに記録するだけです）。TTL 失効時はテナント provider を `close()` して
  次回読み込みで作り直すため、**古さの上限はこの close/recreate が与えます**（独立した
  `max_staleness_seconds` はありません）。
- **まだキャッシュされていないテナント**（初回リクエスト、または TTL 失効直後の再ロード自体が
  失敗した場合）は `503 Service Unavailable` を `Retry-After` 付きで返します。パターン03 では
  `/readyz` が共有ストアの到達性だけを見るため、1テナントのストア障害で他テナントがロード
  バランサから外れることはありません（意図的設計）。
- キャッシュは**全テナント共通のロック**をロード中も保持します。障害テナントの新規ロード
  （既定100秒の再試行予算、上限ではありません）には他テナントのリクエストも待たされ得るため、
  本番ではテナント単位のロックを検討してください。
- 503 応答の `detail` は常に汎用文言です。実際のエラー内容はサーバーログにのみ出力し、
  未認証で到達できるエンドポイントに内部情報を漏らしません。`/healthz` は依存先を叩かず、
  `/readyz` はストア到達性を含みます。

### タイムアウトは処理全体の締め切りではない

`startup_timeout_seconds`（既定100秒）と `probe_timeout_seconds`（既定5秒）は、SDK の
`startup_timeout` に渡す**再試行予算**であり、**ハードなレイテンシ上限ではありません**。
進行中の資格情報取得や HTTP 呼び出しは中断できません。

| SDK オプション | 通常の provider | readiness probe | 意味 |
| --- | --- | --- | --- |
| `connection_timeout` | 5秒 | 2秒 | 個々の接続待ち |
| `read_timeout` | 5秒 | 2秒 | 個々の読み取り待ち |
| `timeout` | 30秒 | 5秒 | 個々の SDK 操作の retry policy 予算 |
| `retry_total` | 2 | 0 | 個々の HTTP 要求に対する再試行回数 |
| `retry_backoff_max` | 1秒 | 1秒 | 指数バックオフの上限 |

資格情報チェーン・複数ページ・DNS・ロック待ちを含めた応答時間全体は、この表から算出できません。
厳密なリクエスト締め切りが必要な運用では、別途キャンセル可能な実行を設計してください。
実装参照（provider 2.5.0）:
[`_load_all` / `refresh`](https://github.com/Azure/azure-sdk-for-python/blob/azure-appconfiguration-provider_2.5.0/sdk/appconfiguration/azure-appconfiguration-provider/azure/appconfiguration/provider/_azureappconfigurationprovider.py)。

### provider と資格情報の所有権

各 `AzureAppConfigurationStore` は1つの `DefaultAzureCredential` を遅延作成し、同じストアの
全 query provider で再利用します。`close(query)` はその provider だけを閉じ、資格情報は
閉じません。ストア全体を使い終えたら `close_all()`（または `with` 文の終了）で共有 provider
と資格情報をまとめて破棄します（`atexit` 登録済み。リクエストごとの Flask teardown では
閉じません）。

ストア全体のロックは参照・借用数の管理にのみ使い、SDK のロードや refresh 中は保持しません。
同じ query の初期化・refresh はその query のロックで直列化するため、`/readyz` が無関係な
I/O 待ちに巻き込まれることはありません。実装: [`azure_source.py`](../src/mtappconfig/azure_source.py)。

### refresh エラーの明示的な通知

SDK の `on_refresh_error` から例外を送出すると、共有 provider のバックオフが無関係な
テナントロードまで失敗させてしまいます。そこで `select()` は `ConfigValues` を返し、
**この読み取りで観測した失敗**を `refresh_errors` という別経路で通知します。warm refresh
の失敗時は直前のテナント設定を保持し、cold/TTL失効後のロードでは共有 provider の最終正常値
と新規テナント設定を使うため、共有 refresh の失敗だけでは `503` にしません。失敗は
`config.refresh.failed` として `tenant_id` 付きでログに記録します。実装:
[`cache.py`](../src/mtappconfig/cache.py) の `TenantConfig.refresh_errors`。

初期ロードで使える provider がまだない場合の例外は `ConfigStoreUnavailableError` です。
不正なスナップショット参照も同じ例外に包み、cold HTTP リクエストは汎用的な `503` を返します。
