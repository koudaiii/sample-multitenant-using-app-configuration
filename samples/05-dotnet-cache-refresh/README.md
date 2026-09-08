# 05 — .NET: 明示的なキャッシュリフレッシュ

**このサンプルだけ .NET(C#)製です。** 01〜04(Python)とはビルド・テストの系列が完全に
独立しています。`uv run pytest` の対象ではなく、`dotnet test` で実行します。

対象記事: [Multitenancy and Azure App Configuration](https://learn.microsoft.com/en-us/azure/architecture/guide/multitenant/service/app-configuration) ―
「Application-side caching」節の .NET 固有の記述
(`ConfigureRefresh` で登録し、`TryRefreshAsync` またはミドルウェアでリフレッシュを
トリガーし、テナントの `IConfiguration` オブジェクトをテナント ID をキーにキャッシュする)

## 構成

| ファイル | 役割 |
| --- | --- |
| `ITenantConfigRefresher.cs` | `IConfigurationRefresher.TryRefreshAsync` を薄くラップする自前の抽象化。テストは常にこちらを介する。 |
| `TenantConfigurationCache.cs` | テナント ID をキーに `IConfiguration` をキャッシュし、明示的な `RefreshAsync` を提供する中核ロジック。**xUnit でテスト対象。** |
| `AzureConfigurationRefresher.cs` | 実際に `AddAzureAppConfiguration` + `ConfigureRefresh` + `GetRefresher()` を配線する1ファイル。実 SDK に対してコンパイルは通すが、実行はしない(下記「既知の制約」参照)。 |
| `Program.cs` | 最小コンソールアプリ(実行には実ストアの接続情報が必要)。 |
| `Tests/TenantConfigurationCacheTests.cs` | `TenantConfigurationCache` の8つの振る舞いを検証。 |

## 中核のコード

```csharp
// AzureConfigurationRefresher.cs
options.Select($"{SharedPrefix}*");
options.Select($"{tenantId}/*");
options.TrimKeyPrefix(SharedPrefix);
options.TrimKeyPrefix($"{tenantId}/");
```

サンプル01の `KeyPrefixSource`(`select(key_filter=..., trim_prefixes=...)`)とキーの
選択条件(フィルタ)は構造的に同一です — `Select` が `key_filter`、`TrimKeyPrefix` が
`trim_prefixes` に対応します。ただし、サンプル01は共有設定とテナント設定を
`{**shared, **tenant}` で明示的にマージし、テナント側が常に勝つことをテストで
保証しています。このサンプルは2回の `Select` を同じプロバイダーに投入し、
`TrimKeyPrefix` 適用後に同名キーとなった場合の優先順位はプロバイダー内部の解決に
委ねています。この優先順位はMicrosoftのドキュメントに明記されておらず、本サンプルでは
検証していません。

```csharp
// TenantConfigurationCache.cs
public async Task<bool> RefreshAsync(string tenantId, CancellationToken cancellationToken = default)
{
    // ...
    return await entry.Refresher.TryRefreshAsync(cancellationToken);
}
```

記事が言う「`TryRefreshAsync` を呼んでリフレッシュを明示的にトリガーする」を、
テナント単位で行います。

## ミドルウェアによるリクエスト駆動のリフレッシュ(実装なし)

記事はもう1つの経路として ASP.NET Core ミドルウェアを挙げています。

```csharp
app.UseAzureAppConfiguration();
```

この1行を ASP.NET Core のリクエストパイプラインに追加すると、リクエストごとに
リフレッシュ間隔が経過していないかを自動で確認し、経過していれば
`TryRefreshAsync` 相当の処理を裏側で行います(＝明示的な呼び出しが不要になる)。
これはバックグラウンドタイマーではなく、リクエストというアプリケーション活動を
契機にした確認です。
このサンプルはフルの Web アプリを追加するとスコープが大きくなりすぎるため、
概念とコード片の紹介に留め、実装はしていません。

## いつ選ぶか

.NET でこのリポジトリの分離パターン(01〜04)を実装する場合に、テナントごとの
`IConfiguration` をどうキャッシュし、いつリフレッシュを確認するかという設計判断の
参考として。

## メリット・デメリットとアンチパターン

### メリット

- `TryRefreshAsync` はリフレッシュ間隔が経過するまでは何もせず高速に返り(no-op も
  成功扱い)、SDKが処理する通信・認証・Key Vault・キャンセル・形式エラーでは
  `false` を返してキャッシュ済みの値を使い続ける。このため、キャッシュされた
  テナントに対して安全に頻繁な呼び出しができる。想定外の例外まで常に握りつぶす
  契約ではないため、呼び出し側の通常の例外処理は別途必要。
- テナントごとに独立した `IConfigurationRefresher` を持つため、あるテナントの
  リフレッシュ失敗が他のテナントに影響しない。
- ミドルウェア経路を使えば、アプリ側でリフレッシュ呼び出しを一切書かずに済む。

### デメリット

- 実行には必ず実ストアへの接続が必要で、Python サンプルのようなインメモリ
  フェイクによるオフライン実行ができない(下記「既知の制約」参照)。
- テナント数だけproviderインスタンスと `IConfigurationRefresher` のrefresh状態を持つ
  ことになり、テナント数が多い場合は接続リソース・メモリ使用量に注意が必要。
- センチネルキーの登録を忘れると、`ConfigureRefresh` で登録した意図に反して
  リフレッシュが働かない(Register の引数を間違えるとテナント間で意図しない
  キーを監視してしまうこともある)。
- `refreshAll: true` は「このプロバイダーインスタンスが使う全キー」をリフレッシュ
  する(テナント自身のプレフィックスだけでなく `_shared/*` も含む)。しかし
  テナントごとに別々の `IConfigurationRefresher` インスタンスを持つため、共有キーを
  変更しても、そのテナント自身のセンチネルキーを更新しない限り検知されない。
  サンプル01が言う「1つの値、1つの更新箇所」という共有設定の利点が、このサンプルでは
  「1つの値、テナントの数だけセンチネルを更新」に変わる点に注意。
- このキャッシュには TTL も LRU もなく、一度ロードしたテナントの
  `IConfigurationRefresher` は解放されません。テナント数が多い長時間稼働の
  プロセスでは、provider状態と関連リソースが増え続けます。
  [`IMemoryCache`](https://learn.microsoft.com/aspnet/core/performance/caching/memory#use-setsize-size-and-sizelimit-to-limit-cache-size)
  に置き換えるだけでもメモリ圧迫時の自動回収は保証されません。本番では `SizeLimit` と
  各エントリーの `Size` を設定し、eviction callbackなどでproviderを破棄してください。
  現在のPythonサンプルは `max_entries` とTTLを明示し、expire/evict時にテナント固有
  providerを閉じます(共有providerはテナントTTLの対象外です)。
- `Get` のcold loadは全テナント共通のロック内で行います。同じテナントへの同時loadを
  1回にまとめる代わりに、遅いテナントのload中は別テナントの `Get` も待ちます。本番で
  テナント間の待ち時間まで分離するには、テナント単位のロックなどを検討してください。

### 想定シナリオ

- .NET でこのリポジトリの分離パターンを実装し、テナントごとに設定の鮮度を
  制御したい場合。

### アンチパターンシナリオ

- `TryRefreshAsync` を一度も呼ばず、`ConfigureRefresh` の登録だけして満足する。
  ミドルウェアを使わない構成では、明示的に呼ばない限りリフレッシュは発生しない。
- 全テナント共通で1つの `IConfigurationRefresher` を使い回そうとする。
  テナントごとに異なるキーフィルタ・センチネルキーを扱うには、テナントごとに
  別々の `IConfigurationRefresher`(＝別々の `IConfigurationBuilder`)が必要。

## 動かす

```bash
cd samples/05-dotnet-cache-refresh/Tests
dotnet test
```

実行(`Program.cs`)には実ストアが必要です。

```bash
export APPCONFIG_ENDPOINT=https://<your-store>.azconfig.io
cd samples/05-dotnet-cache-refresh
dotnet run
```

起動時にtenant-a/bの初期値を表示します。ストアの値と各テナントのセンチネルを更新し、
30秒以上待ってからEnterを押すと、同じプロセス内のキャッシュに対して
`RefreshAsync` を呼び、リフレッシュ後の値を表示します。

## Azure での実行(RBAC とストアのレイアウト)

`Connect(Uri, DefaultAzureCredential)` が必要とするロールは、他のサンプルと同じく
**App Configuration Data Reader** だけです。これはアプリ実行時の読み取り権限であり、
下記の `az appconfig kv set` で初期データやセンチネルを書き込む操作者には、一時的な
**App Configuration Data Owner** または同等のデータプレーン書き込み権限が別途必要です。

このサンプルはサンプル01と同じキーレイアウト(`_shared/*` と `{tenantId}/*`)を読むため、
[サンプル01用に投入済みのストア](../01-shared-store-key-prefix/#実ストアにデータを入れる)を
そのまま `APPCONFIG_ENDPOINT` に指定して使えます。

ただし、このサンプルはリフレッシュ検知のために**センチネルキー**を追加で必要とします。
最低限、次のキーを投入してください。

```bash
az appconfig kv set -n "$STORE" --auth-mode login --yes --key "tenant-a/Sentinel" --value "1"
az appconfig kv set -n "$STORE" --auth-mode login --yes --key "tenant-b/Sentinel" --value "1"
```

`RefreshAsync` を呼んだときに実際のリフレッシュを発生させるには、リフレッシュ間隔
(30秒)が経過したあとに、このセンチネルキーの値を変更してください
(`az appconfig kv set` で値を変えるだけで十分です)。

この手順は、この開発環境が実ストアに接続できないため実行検証していません
(下記「既知の制約」参照)。

## 既知の制約

- **フェイクストアがありません。** Python サンプル(01〜04)は Azure サブスクリプション
  なしでも `uv run pytest` や `flask run` が動きますが、このサンプルは実 Azure SDK
  (`Microsoft.Extensions.Configuration.AzureAppConfiguration`)を直接使うため、
  `AzureConfigurationRefresher.Load` を呼ぶと必ず実ストアへの接続を試みます。
  テストは `TenantConfigurationCache` だけを対象にし、`AzureConfigurationRefresher.cs`
  と `Program.cs` は**コンパイルのみ**検証しています(`src/mtappconfig/azure_source.py`
  と同じ立ち位置です)。
- **この開発環境では `nuget.org` に到達できませんでした**(Python の PyPI 制約と同様)。
  過去の作業でローカルの NuGet キャッシュ(`~/.nuget/packages`)に必要なパッケージが
  展開済みだったため、`dotnet restore --source ~/.nuget/packages` で検証しました。
  通常のネットワーク環境では、この `--source` オプションなしで
  `dotnet restore` / `dotnet build` / `dotnet test` が問題なく動きます。
