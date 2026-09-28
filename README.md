# マルチテナント × Azure App Configuration サンプル

Azure Architecture Center の記事
[Multitenancy and Azure App Configuration](https://learn.microsoft.com/en-us/azure/architecture/guide/multitenant/service/app-configuration)
を更新したコミット
[`bef1a19`](https://github.com/MicrosoftDocs/architecture-center/commit/bef1a19651d0ea5a9bd01ede617d225b7a39f0f5)
（[PR #16561](https://github.com/MicrosoftDocs/architecture-center/pull/16561)）の変更点を、
動くコードとテストで確かめるためのサンプルです。変更点ごとに、どこで何を確かめられるかを
次の表にまとめます。大半は Azure サブスクリプションなしで、フェイクストア上のテストで
確かめられます（[動かす](#動かす)）。

## コミット bef1a19 の変更点と確かめる場所

| 記事の節 | 変更点 | 確かめる場所 | 確かめ方 |
| --- | --- | --- | --- |
| Shared stores | 「1時間あたりの最大リクエスト数」という Standard 前提の説明を、ティアごとのストレージ・リクエストクォータ・スループット上限へ一般化。Standard は geo-replication でレプリカごとにクォータを持ち、Premium はクォータなし | [01 リクエストクォータと geo-replication](samples/01-shared-store-key-prefix/#リクエストクォータと-geo-replication)、[コスト最適化](#コスト最適化) | 文書照合 |
| Shared stores | geo-replication は noisy neighbor を防がない。テナント単位のレート制限と監視が必要 | 01 の同じ節 | 文書照合 |
| Store per tenant | ストア数が無制限なのは Standard と Premium。Developer tier は SLA がなく非本番用 | [03 コスト上の注意](samples/03-store-per-tenant/#コスト上の注意) | 文書照合 |
| Store per tenant | CMK は Standard/Premium のストア単位。異なる CMK が必要なテナントごとにストアを分ける | [03 いつ選ぶか](samples/03-store-per-tenant/#いつ選ぶか) | 文書照合（Bicep は CMK を構成しない） |
| Application-side caching | 「provider は設定をキャッシュし自動で refresh する」から「キャッシュする」へ変更 | [refresh には明示的な契機が要る](#refresh-には明示的な契機が要る) | フェイクテスト（Python）、単体テスト（.NET） |
| Refresh key-values（新設） | sentinel key を全体共通にするかテナント別にするかを選ぶ。.NET は `ConfigureRefresh` で登録し、`TryRefreshAsync` かミドルウェアで refresh する | [05 .NET キャッシュリフレッシュ](samples/05-dotnet-cache-refresh/)、[refresh には明示的な契機が要る](#refresh-には明示的な契機が要る) | 単体テスト（.NET 実 SDK での値更新は未検証） |
| Configuration rollout and rollback with snapshot references（新設） | テナントスコープの参照キーを差し替えてロールアウト/ロールバックする。スコープは中身を絞らない。差し替え前に refresh の構成が必要。スナップショットのサイズ上限。アクセス制御はストア単位のまま | [04 スナップショット参照](samples/04-snapshot-references/) | フェイクテスト、オプトイン live テスト |
| Configuration delivery to client applications（新設、プレビュー） | Front Door でクライアントの読み取りを吸収する。匿名公開・sentinel key 不可・結果整合 | [Front Door 経由のクライアント配信は実装していない](#front-door-経由のクライアント配信は実装していない) | 文書照合のみ |

## 記事に残っている誤り: IMemoryCache はメモリ圧迫で自動削除しない

コミット後の記事の Refresh key-values 節は、.NET の in-memory cache について
"the cache can remove unused instances if your application is under memory pressure"
と書いています。ASP.NET Core の
[`IMemoryCache`](https://learn.microsoft.com/aspnet/core/performance/caching/memory#use-setsize-size-and-sizelimit-to-limit-cache-size)
はメモリ圧迫に応じてサイズを自動制限しません。本番では `SizeLimit` と各エントリーの `Size`
を設定し、eviction 時に provider を破棄する処理を自分で設計する必要があります。

このリポジトリでは、サンプル05のキャッシュは単純な `Dictionary` で、容量上限・TTL・破棄処理を
持ちません。Python サンプルは `max_entries` と TTL でテナントキャッシュを制限し、expire/evict 時に
テナント固有の provider を閉じます（共有 provider はテナント TTL の対象外です）。

## refresh には明示的な契機が要る

コミットは Application-side caching 節から「provider が自動で refresh する」という記述を外し、
Refresh key-values 節を新設しました。refresh はどちらの言語でもバックグラウンドだけでは進まず、
アプリケーション側の契機が必要です。

- **.NET（[05](samples/05-dotnet-cache-refresh/)）:** `ConfigureRefresh` でテナント別の sentinel key
  （`{tenantId}/Sentinel`）を登録し、`TryRefreshAsync` を明示的に呼びます。テナント別 sentinel
  なので、共有キーを変えても各テナントの sentinel を更新するまで検知されません。これが全体共通
  sentinel との trade-off です。**このサンプルだけ .NET 製で、`uv run pytest` ではなく
  `dotnet test` で実行します。**
- **Python（01〜04）:** リクエスト処理中に `provider.refresh()` を呼びます。sentinel key
  （`refresh_on`）は指定せず、provider に選択したキー全体の変更を監視させています。

## スナップショット参照でテナント単位にロールアウト/ロールバックする

[04 スナップショット参照](samples/04-snapshot-references/) は、コミットで新設された節の実装です。
01 の分離モデルはそのままに、テナントスコープの参照キーを不変スナップショットへ向け、参照先を
変えるだけでコード変更・再デプロイなしにロールアウト/ロールバックします。記事が注意する
「参照キーのスコープはスナップショットの中身を絞らない」ことは、負例のテストで示しています。

## Front Door 経由のクライアント配信は実装していない

コミットで新設されたクライアント配信の節（プレビュー、Azure パブリッククラウドのみ）は、
このリポジトリでは実装せず、文書照合だけを行っています。対象がバックエンドサーバーでテナント設定を
解決するモデルだけだからです。記事の要点は次のとおりです。

- ブラウザ・モバイル・デスクトップが設定を直接読むと、テナント数が多いほど共有ストアの
  [リクエストクォータ](samples/01-shared-store-key-prefix/#リクエストクォータと-geo-replication)
  に近づく。[Azure Front Door 経由の配信](https://learn.microsoft.com/azure/azure-app-configuration/concept-hyperscale-client-configuration)
  はエッジでキャッシュしてこの読み取りを吸収する。
- **匿名で公開アクセス可能**。秘密情報やテナント固有の非公開データを含まない専用ストアを使い、
  Front Door のルートをテナント間の認可境界として扱わない。
- [sentinel key による refresh は使えない](https://learn.microsoft.com/azure/azure-app-configuration/how-to-load-azure-front-door-configuration-provider?tabs=dotnet-maui#troubleshooting)。
  選択したすべてのキーを監視する。
- 結果整合で、Front Door のキャッシュ失効とクライアントの次回 refresh の後に反映される。
  即時反映が必要な設定には使わない。
- 採用する場合は、Front Door のマネージド ID に読み取り専用のロールだけを与える。専用ストアの
  定義・キャッシュ調整・レプリカオリジンの構成は、
  [Front Door 経由の配信の記事](https://learn.microsoft.com/azure/azure-app-configuration/concept-hyperscale-client-configuration)
  に従って別途設計する。

## 前提となる分離モデル（01〜03）

01〜03 は、記事が以前から示していた分離モデルを動かして比べるサンプルです。04・05 はこの上に
載ります。

| | [01 キープレフィックス](samples/01-shared-store-key-prefix/) | [02 ラベル](samples/02-shared-store-label/) | [03 テナント別ストア](samples/03-store-per-tenant/) |
| --- | --- | --- | --- |
| ストア数 | 共有1つ | 共有1つ | テナント数だけ + 共有1つ |
| 分け方 | キー `tenant-a/LogLevel` | ラベル `tenant-a` | ストアそのもの |
| データ分離 | 低 | 低 | 高 |
| 性能分離 | 低 | 低 | 高 |
| デプロイ/運用の複雑さ | 低 | 低 | 中〜高 |
| コスト | 低 | 低 | 中〜高 |
| ラベルの空き | 環境・バージョンに使える | テナントで占有 | 環境・バージョンに使える |

**迷ったら 01。** 記事も既定としてキープレフィックスを推奨しています。ラベルをテナント識別に
使うと、バージョニングや環境の区別にラベルを使えなくなるためです。テナントごとに異なる CMK
が必要な場合、またはテナントが設定データの分離を要求する場合にだけ 03 を選びます。
**このリポジトリの Bicep は CMK を構成しません**。CMK を有効にするには、Standard または Premium
ストア、ストア自身のマネージド ID、その ID への Key Vault キー権限（RBAC なら
**Key Vault Crypto Service Encryption User**）、ストアの
[暗号化設定](https://learn.microsoft.com/en-us/azure/azure-app-configuration/concept-customer-managed-keys)
を別途構成する必要があります。

## 動かす

Azure のサブスクリプションは不要です。既定ではメモリ上のフェイクストアが使われます。

```bash
uv sync
uv run pytest                                   # 全テスト
cd samples/01-shared-store-key-prefix
uv run flask --app app run --port 5001
```

- `http://localhost:5001/` — テナント一覧
- `http://localhost:5001/t/tenant-a/` — 解決後の設定
- `http://localhost:5001/_diagnostics/cache` — キャッシュの hit/miss/evict

### Python パッケージソース

パッケージ取得先は uv 自身の構成で管理します。uv は pip の設定を読み込みません。
ユーザー構成の `uv.toml` では `[[index]]`、プロジェクト構成の `pyproject.toml` では
`[[tool.uv.index]]` でインデックスを指定できます。必要なソースは利用者の uv 構成で設定してください。
このリポジトリには特定のインデックスを指定するプロジェクト設定は含めません。

設定値は該当する構成ファイル、解決済みパッケージの取得先は `uv.lock` で確認できます。
配布ファイルはインデックスが返すURLから取得するため、インデックス自体とホスト名が異なることがあります。
次のコマンドはリポジトリルートで実行します。

```bash
uv lock --check       # プロジェクト設定とロックの整合性
uv sync --verbose     # 使用する取得先などの詳細ログ
```

ソースを変更する場合はインデックス設定を編集して `uv lock` を実行します。
コマンドラインや `UV_INDEX` / `UV_DEFAULT_INDEX` による指定は構成ファイルより優先されます。
詳細は [uv のパッケージインデックス](https://docs.astral.sh/uv/concepts/indexes/) を参照してください。
認証情報をインデックスURLやリポジトリ内の設定へ含めないでください。

### 実ストアへの接続

実際の App Configuration に繋ぐ場合は `main.bicep` でストアを作り、環境変数を設定します。
次のコマンドは `samples/01-shared-store-key-prefix` を作業ディレクトリとして実行します。

```bash
uv pip install -r ../../requirements-azure.txt
export APPCONFIG_ENDPOINT=https://<your-store>.azconfig.io
uv run flask --app app run --port 5001
```

Azure SDK を `pyproject.toml` ではなく `requirements-azure.txt` に置いているのは意図的です。
`uv lock` は optional-dependencies も解決対象に含めるため、フェイクだけを利用する場合に
Azure SDK の依存解決や取得を不要にするための分離です。

認証は `DefaultAzureCredential` ですが、ローカル開発と Azure 上の実行では使う ID が異なります。

- **ローカル開発:** `az login` でサインインした開発者の資格情報を使います。各サンプルの手順では、
  データ投入とその後のローカル読み取りのため、その開発者に一時的な
  **App Configuration Data Owner** を付与し、テスト後に割り当て ID を指定して削除します。
- **Azure-hosted execution:** ホストするアプリにマネージド ID を設定し、そのオブジェクト ID を
  `main.bicep` の `readerPrincipalId` に渡します。Bicep はその ID に
  **App Configuration Data Reader** を付与し、Azure 上の `DefaultAzureCredential` は
  ホストのマネージド ID を使います。このリポジトリはアプリのホスティング自体は構成しません。

新しい RBAC 割り当てが App Configuration のデータプレーンで有効になるまで最大約15分かかる
ことがあります。直後の投入または読み取りが `403` になった場合は、待ってから再試行してください。

`APPCONFIG_ENDPOINT` は 01・02・04（共有ストア1つ）用です。**03 はストアが複数あるため
`APPCONFIG_SHARED_ENDPOINT` と `APPCONFIG_ENDPOINTS`（JSON）という別の環境変数**を使います。
詳細は [samples/03-store-per-tenant/README.md](samples/03-store-per-tenant/) を参照してください。

Bicep が付与するのは読み取り専用の Data Reader だけで、`disableLocalAuth: true` のため
接続文字列も使えません。アプリ本体とシードコードは実ストアへ書き込みません。各サンプルの README に、
そのパターンのレイアウトへ `az appconfig kv set` で投入する手順を載せています（ローカル手順では
自分の Entra ID に一時的な **App Configuration Data Owner** を別途付与します）。ただし、
**サンプル04のオプトインliveテストは読み取り専用ではありません**。ロールバックと復帰を検証するため
テスト内から `az appconfig kv set` を実行して参照キーを書き換えるので、実行中はその一時的な
Data Owner（または同等のデータ書き込み権限）を残す必要があります。手順を踏まずにデプロイだけ
済ませると、エラーなくストアが空のまま動いてしまうので注意してください。このローカルliveテストが
確認するのは、`DefaultAzureCredential` が選んだ active な資格情報で読み書きできることです。
選択された資格情報や実効ロールは検査しないため、成功しても Data Reader のみでの読み取りを
証明しません。Data Reader だけの読み取りを確認するには、サンプル04の README にある
ホスト/別 ID の分離実行手順に従ってください。

## 構成

ストアから値を選択・合成するロジックの差分は、各サンプルの `source_*.py` で比較できます。
共通部分（テナント ID 検証・キャッシュ・Flask のルート）は `src/mtappconfig/` に1回だけ
書かれています。ただし、エンドポイント環境変数やストア生成を扱う `app.py`、作成するストア数や
RBAC を定義する `main.bicep` など、実ストアへ接続する運用上の配線もパターンごとに異なります。

```bash
diff samples/01-shared-store-key-prefix/source_key_prefix.py \
     samples/02-shared-store-label/source_label.py
```

`tests/test_pattern_contract.py` が、**3パターンとも同じ解決結果を返す**ことを検証しています。
上の diff は値の選択方法を比較するためのもので、パターン間の差分すべてを示すものではありません。
`samples/04-snapshot-references/source_snapshot_references.py`
はこの比較の対象に意図的に含めていません(04は分離モデルの4つ目ではなく、01の上に築く
追加機能のため)。

## Well-Architected の観点

### セキュリティ

- 接続文字列・アクセスキーを使いません。`DefaultAzureCredential` のみで、Bicep は
  `disableLocalAuth: true` を設定します。
- Bicep が Azure-hosted application に付与する権限は
  **App Configuration Data Reader**（`516239f1-63e1-4d78-a4de-a74fb236a071`）だけです。
  ローカル手順の Data Owner はサインインした開発者への一時的な別割り当てで、テスト後に削除します。
- **テナント ID の検証がこのサンプルで最も重要な部分です。** URL 由来のテナント ID を検証せずに
  キーフィルタへ渡すと、`*` を指定するだけで全テナントの設定が読めてしまいます。
  `src/mtappconfig/tenants.py` のレジストリ照合と正規表現を通った ID だけがストアに到達します。
  ただし、これは不正な形式や未登録 ID を拒否する入力検証であり、**認可ではありません**。
  本番では URL で呼び出し元が選んだ値をそのまま信頼せず、認証済みのユーザーまたはワークロードの
  コンテキストからテナントを導出し、そのテナントへのアクセスを認可してからレジストリを解決します。
- Key Vault 参照の解決はこのサンプルでは実装していません。採用する場合は、App Configuration
  用とは別に Key Vault を読める資格情報または resolver をプロバイダーへ設定し、その ID に
  Key Vault の適切なデータプレーン RBAC を付与してください。

### 信頼性

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

#### タイムアウトは処理全体の締め切りではない

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

#### provider と資格情報の所有権

各 `AzureAppConfigurationStore` は1つの `DefaultAzureCredential` を遅延作成し、同じストアの
全 query provider で再利用します。`close(query)` はその provider だけを閉じ、資格情報は
閉じません。ストア全体を使い終えたら `close_all()`（または `with` 文の終了）で共有 provider
と資格情報をまとめて破棄します（`atexit` 登録済み。リクエストごとの Flask teardown では
閉じません）。

ストア全体のロックは参照・借用数の管理にのみ使い、SDK のロードや refresh 中は保持しません。
同じ query の初期化・refresh はその query のロックで直列化するため、`/readyz` が無関係な
I/O 待ちに巻き込まれることはありません。実装: [`azure_source.py`](src/mtappconfig/azure_source.py)。

#### refresh エラーの明示的な通知

SDK の `on_refresh_error` から例外を送出すると、共有 provider のバックオフが無関係な
テナントロードまで失敗させてしまいます。そこで `select()` は `ConfigValues` を返し、
**この読み取りで観測した失敗**を `refresh_errors` という別経路で通知します。warm refresh
の失敗時は直前のテナント設定を保持し、cold/TTL失効後のロードでは共有 provider の最終正常値
と新規テナント設定を使うため、共有 refresh の失敗だけでは `503` にしません。失敗は
`config.refresh.failed` として `tenant_id` 付きでログに記録します。実装:
[`cache.py`](src/mtappconfig/cache.py) の `TenantConfig.refresh_errors`。

初期ロードで使える provider がまだない場合の例外は `ConfigStoreUnavailableError` です。
不正なスナップショット参照も同じ例外に包み、cold HTTP リクエストは汎用的な `503` を返します。

### パフォーマンス効率

- テナント単位の遅延ロード。全テナント一括ロードはしません。
- TTL + LRU で外側キャッシュを有界に保ち、expire/evict 時には R1 対応として
  そのテナント固有の provider も閉じます。sample 01 と 04 は tenant prefix、02 は
  tenant label、03 は tenant 専用 store の exact provider だけを閉じ、全テナントが使う
  shared provider は閉じません。そのため shared provider の古さはテナント TTL では
  制限されない trade-off が残ります。
- リクエスト契機のリフレッシュで、毎リクエストではストアを叩きません。

### コスト最適化

| | Free | Developer | Standard | Premium |
| --- | --- | --- | --- | --- |
| ストア数上限 | 3 / リージョン / サブスクリプション | 上限なし（ストアごとに課金） | 上限なし（ストアごとに課金） | 上限なし（ストアごとに課金） |
| リクエスト | 1,000 / 日 | 6,000 / 時 | 30,000 / 時 | 制限なし |
| ストレージ | 10 MB | 500 MB | 1 GB | 4 GB |

出典: [Azure subscription and service limits](https://learn.microsoft.com/en-us/azure/azure-resource-manager/management/azure-subscription-service-limits#azure-app-configuration)

Developer tier には SLA がないため、低トラフィックの非本番用途向けです。ストア単位の SLA が
必要な本番環境では Standard または Premium を選びます。03 は共有ストアも必要なので、Free
tier で同一リージョンに作れる構成は共有1ストア + テナント専用2ストアまでです。

## 検証状況

| 検証種別 | このリポジトリでカバーする範囲 |
| --- | --- |
| フェイク実行（既定 `uv run pytest`、301 collected） | 3パターンの設定解決一致、キャッシュのclose/expiry、スナップショット参照の正例・負例 |
| 実SDKオプトイン（`--run-live`） | sample 04 の参照解決・別テナント不変・実書換後refresh・ロールバック |
| IaCコンパイル | `main.bicep` 4本の `az bicep build` |
| 文書照合のみ | geo-replication / Developer SLA / CMK / Front Door |
| 未検証 | 読み取り拒否（RBAC負例）、.NET実SDKでの値更新 |

入力ハッシュは [`docs/reviews/2026-09-07-training-inputs.json`](docs/reviews/2026-09-07-training-inputs.json) にあります。

## 既知の制約

- Python の既定テストはフェイクストアと SDK double を使い、Azure へ接続しません。
  実ストアでの動作を確認するには `requirements-azure.txt` をインストールし、ストアと権限を
  用意してオプトインliveテストを実行してください。provider の依存指定は `>=2.5.0` です。
  実接続を確認する際は、解決された SDK バージョンも記録してください。
- スナップショット参照の解決は実運用では configuration provider(SDK)側が自動的に行います。
  `samples/04-snapshot-references/` はこの解決ロジックをフェイクストア(`src/mtappconfig/fake.py`)
  内で再現しています。実接続のアダプターは解決を SDK に任せており、参照解決を独自実装しません。
  実ストアでの参照解決やロールバックの確認手順はサンプル04を参照してください。
- サンプル05(.NET)の `dotnet restore` は、その環境の NuGet 構成にあるパッケージソースを使い、
  **既存キャッシュを前提としません**。`NuGet.Config` の階層や設定したソースを確認し、
  必要に応じて利用者またはCIの設定を用意してください。ソースの一覧はリポジトリルートで
  `dotnet nuget list source` を実行すると確認できます。設定の場所や復元手順は
  [samples/05-dotnet-cache-refresh/README.md](samples/05-dotnet-cache-refresh/) を参照してください。
