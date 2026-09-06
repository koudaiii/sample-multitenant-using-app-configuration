# サンプル05「.NET 明示的キャッシュリフレッシュ」設計書

- 作成日: 2026-09-06
- 目的: `architecture-center-pr/report.md` が示す記事本文の追記対象のうち、
  Application-side caching 節の .NET 固有の記述
  (`ConfigureRefresh` で登録し、`TryRefreshAsync` またはミドルウェアで明示的に
  リフレッシュをトリガーする)を、実際にビルド・テストできる .NET コードで検証する。
- 対象記事: [Multitenancy and Azure App Configuration](https://learn.microsoft.com/en-us/azure/architecture/guide/multitenant/service/app-configuration) ―
  「Application-side caching」節
- 参照: [Configuration provider overview](https://learn.microsoft.com/azure/azure-app-configuration/configuration-provider-overview)
- 前提設計書: [[2026-08-28-multitenant-app-configuration-design]]、[[2026-09-02-snapshot-references-sample-design]]

## 1. 背景とスコープ

記事の Application-side caching 節は次の追記を含む。

> .NET applications register cached key-values for refresh by using `ConfigureRefresh`,
> and trigger the refresh by calling `TryRefreshAsync` or by using App Configuration
> middleware. ... cache the tenant's `IConfiguration` object and use the tenant
> identifier as the cache key.

この主張はサンプル01〜04が扱う **Python** の世界観の外側にある。このリポジトリの
`src/mtappconfig/cache.py` は既に「Python プロバイダーは .NET プロバイダーのように
バックグラウンドでは自動リフレッシュしない」という前提でテナント別キャッシュを実装して
おり(docstring に明記)、Python 側での対応は完了している。本設計書は、対になる **.NET側**
の主張(`ConfigureRefresh`/`TryRefreshAsync`によるテナント別`IConfiguration`キャッシュ)を、
実際に動く C# コードとテストで検証する。

### 事前検証(スパイク)で確定した事実

このリポジトリの開発環境は `nuget.org` への通信ができない(Python の PyPI 制約と同様)。
一方で `~/.nuget/packages` のグローバルパッケージキャッシュには、次のパッケージが
過去の作業により既に展開済みであることを確認した。

| パッケージ | バージョン |
| --- | --- |
| `Microsoft.Extensions.Configuration.AzureAppConfiguration` | 8.4.0 |
| `Microsoft.Extensions.Hosting` | 8.0.1 |
| `Azure.Identity` | 1.14.0 |
| `Microsoft.NET.Test.Sdk` | 17.8.0 |
| `xunit` | 2.5.3 |
| `xunit.runner.visualstudio` | 2.5.3 |
| `coverlet.collector` | 6.0.0 |

`dotnet --list-runtimes` は `Microsoft.NETCore.App 10.0.11` のみを持つため、
**プロジェクトのターゲットフレームワークは `net10.0`** とする(`Microsoft.Extensions.*`
系パッケージは `netstandard2.0`/`netstandard2.1` 配布のため互換性の問題はない)。

`dotnet restore --source ~/.nuget/packages` → `dotnet build` → `dotnet test` の一連が
このキャッシュだけで(ネットワークなしに)成功することを、スクラッチ環境で実証済み。
また、次の実際の API 呼び出し列がこのバージョンの SDK に対して**コンパイルが通る**ことも
実証済み(実行はしていない ── 本物のエンドポイントが必要なため)。

```csharp
options.Connect(new Uri("https://example.azconfig.io"), new DefaultAzureCredential());
options.Select("_shared/*");
options.Select($"{tenantId}/*");
options.TrimKeyPrefix("_shared/");
options.TrimKeyPrefix($"{tenantId}/");
options.ConfigureRefresh(refresh =>
{
    refresh.Register($"{tenantId}/Sentinel", refreshAll: true)
           .SetRefreshInterval(TimeSpan.FromSeconds(30));
});
var refresher = options.GetRefresher();   // IConfigurationRefresher
```

確認した実 API シグネチャ(`IConfigurationRefresher`):

```csharp
Task<bool> TryRefreshAsync(CancellationToken cancellationToken = default);
Task RefreshAsync(CancellationToken cancellationToken = default);
Uri AppConfigurationEndpoint { get; }
```

これはサンプル01の `KeyPrefixSource`(`select(key_filter=..., trim_prefixes=...)`)と
**選択ロジックが構造的に同一**であることを示す ── `Select` が `key_filter`、
`TrimKeyPrefix` が `trim_prefixes` に対応する。

### スコープ内

- テナント別 `IConfiguration` キャッシュと、`TryRefreshAsync` による明示的リフレッシュの
  中核ロジック(C#)。**フェイクの refresher に対して xUnit で実際にテストする。**
- `AddAzureAppConfiguration` + `ConfigureRefresh` + `GetRefresher()` の実配線
  (1ファイルに閉じ込め、実 SDK に対して**コンパイルは通す**が実行はしない)。
- `Program.cs`(実ストア接続が必要、実行はドキュメントの範囲)。
- README(既存サンプルと同じ構成)。

### スコープ外

- 実際の Azure App Configuration ストアへの接続・実行検証(接続文字列を使わず
  `DefaultAzureCredential` を使う設計だが、実ストアがないため実行できない ──
  Python 側の `azure_source.py` と同じ立ち位置)。
- ASP.NET Core ミドルウェア(`app.UseAzureAppConfiguration()`)によるリフレッシュ経路の
  実装。記事本文はこちらも代替手段として挙げているが、フルの Web アプリを追加すると
  スコープが大きくなりすぎるため、README内で概念とコード片のみ紹介し、実装はしない。
- Python 側のコード・テスト(`src/mtappconfig/`、`samples/01〜04/`)への変更。
- ルートの `tests/test_pattern_contract.py` への統合(このサンプルは isolation pattern
  ではなく、別言語での補足サンプルのため対象外)。
- `uv run pytest` からの実行(別ツールチェーンのため、`pytest` の収集対象にしない
  ── `pyproject.toml` の `testpaths` は変更しない)。

## 2. ディレクトリ構成

```
samples/05-dotnet-cache-refresh/
├── Sample05.csproj                       # ライブラリ + 実行可能ファイル
├── ITenantConfigRefresher.cs             # 自前の薄い抽象化
├── TenantConfigurationCache.cs           # テナント別キャッシュ + 明示的リフレッシュ(テスト対象)
├── AzureConfigurationRefresher.cs        # 実SDKへの配線(コンパイルのみ検証)
├── Program.cs                            # 最小コンソールアプリ(実行には実ストアが必要)
├── README.md
└── Tests/
    ├── Sample05.Tests.csproj
    └── TenantConfigurationCacheTests.cs
```

Python サンプル(`samples/01〜04/`)とは完全に独立したディレクトリツリーであり、
`pyproject.toml` の `testpaths = ["tests", "samples"]` が `samples/05-dotnet-cache-refresh/`
配下の `.py` でないファイルを誤収集することもない(pytest は `.py` ファイルしか集めない)。

## 3. コア設計

### 3.1 `ITenantConfigRefresher.cs`

```csharp
namespace MtAppConfig.CacheRefresh;

/// <summary>
/// IConfigurationRefresher.TryRefreshAsync を薄くラップする自前の抽象化。
/// テストは常にこちらの実装を差し替え、実 Azure SDK には一切触れない
/// (Python サンプルの TenantConfigSource プロトコルと同じ役割)。
/// </summary>
public interface ITenantConfigRefresher
{
    Task<bool> TryRefreshAsync(CancellationToken cancellationToken = default);
}
```

### 3.2 `TenantConfigurationCache.cs`(テスト対象の中核)

記事の主張「テナントの `IConfiguration` オブジェクトをテナント ID をキーにキャッシュする」
をそのまま実装する。ロードとリフレッシュは呼び出し元が渡す委譲に任せることで、
実 Azure SDK に依存せず xUnit でテストできるようにする。

```csharp
namespace MtAppConfig.CacheRefresh;

public sealed record TenantConfigEntry(IConfiguration Configuration, ITenantConfigRefresher Refresher);

/// <summary>
/// テナント ID をキーに IConfiguration とその refresher をキャッシュする。
/// 実際のリフレッシュ(値の更新)は Azure App Configuration プロバイダーが
/// IConfiguration を書き換える形で行われる ── ここでは「リフレッシュを
/// 明示的にトリガーする」という記事の主張だけを実装・検証する。
/// </summary>
public sealed class TenantConfigurationCache
{
    private readonly Func<string, TenantConfigEntry> _loader;
    private readonly Dictionary<string, TenantConfigEntry> _entries = new();
    private readonly object _lock = new();

    public TenantConfigurationCache(Func<string, TenantConfigEntry> loader)
    {
        _loader = loader;
    }

    /// <summary>初回アクセス時にロードし、以降はキャッシュを返す。</summary>
    public IConfiguration Get(string tenantId)
    {
        lock (_lock)
        {
            if (!_entries.TryGetValue(tenantId, out var entry))
            {
                entry = _loader(tenantId);
                _entries[tenantId] = entry;
            }
            return entry.Configuration;
        }
    }

    /// <summary>
    /// 記事が言う「TryRefreshAsync を呼んでリフレッシュをトリガーする」を
    /// テナント単位で明示的に行う。まだロードされていないテナントは
    /// まずロードするだけで、リフレッシュは呼ばない(ロード自体が最新値のため)。
    /// </summary>
    public async Task<bool> RefreshAsync(string tenantId, CancellationToken cancellationToken = default)
    {
        TenantConfigEntry entry;
        lock (_lock)
        {
            if (!_entries.TryGetValue(tenantId, out entry!))
            {
                entry = _loader(tenantId);
                _entries[tenantId] = entry;
                return false;
            }
        }
        return await entry.Refresher.TryRefreshAsync(cancellationToken);
    }
}
```

ロックは `System.Threading.Lock`(.NET 9+)ではなく、TFM に依存しない
`private readonly object _lock = new();` + `lock (_lock)` を使う(移植性のため)。

### 3.3 `AzureConfigurationRefresher.cs`(実 SDK 配線、コンパイルのみ検証)

```csharp
namespace MtAppConfig.CacheRefresh;

using Azure.Identity;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.Configuration.AzureAppConfiguration;

/// <summary>
/// サンプル01の KeyPrefixSource と選択ロジックが同一(Select + TrimKeyPrefix ==
/// select(key_filter=..., trim_prefixes=...))。この1ファイルだけが実 Azure SDK に
/// 触れる ── azure_source.py と同じ立ち位置で、コンパイルは通すが実行検証はしない。
/// </summary>
public static class AzureConfigurationRefresher
{
    private const string SharedPrefix = "_shared/";

    public static TenantConfigEntry Load(Uri endpoint, string tenantId)
    {
        IConfigurationRefresher? refresher = null;

        var configuration = new ConfigurationBuilder()
            .AddAzureAppConfiguration(options =>
            {
                options.Connect(endpoint, new DefaultAzureCredential());
                options.Select($"{SharedPrefix}*");
                options.Select($"{tenantId}/*");
                options.TrimKeyPrefix(SharedPrefix);
                options.TrimKeyPrefix($"{tenantId}/");
                options.ConfigureRefresh(refresh =>
                {
                    // サンプル04のスナップショット参照と同じ考え方:
                    // テナント配下のセンチネルキー1件だけを監視すれば、
                    // そのテナントの全キー(refreshAll: true)をリフレッシュ対象にできる。
                    refresh.Register($"{tenantId}/Sentinel", refreshAll: true)
                           .SetRefreshInterval(TimeSpan.FromSeconds(30));
                });
                refresher = options.GetRefresher();
            })
            .Build();

        return new TenantConfigEntry(configuration, new RealTenantConfigRefresher(refresher!));
    }

    private sealed class RealTenantConfigRefresher(IConfigurationRefresher refresher) : ITenantConfigRefresher
    {
        public Task<bool> TryRefreshAsync(CancellationToken cancellationToken = default) =>
            refresher.TryRefreshAsync(cancellationToken);
    }
}
```

`Load` を呼ぶと `.Build()` が実 Azure App Configuration への接続を試みるため、
テストから直接呼び出すことはしない(スコープ外の「実行検証」に該当する)。

### 3.4 `Program.cs`(最小コンソールアプリ)

```csharp
using MtAppConfig.CacheRefresh;

var endpointValue = Environment.GetEnvironmentVariable("APPCONFIG_ENDPOINT");
if (string.IsNullOrEmpty(endpointValue))
{
    Console.Error.WriteLine("Set APPCONFIG_ENDPOINT to a real App Configuration store endpoint.");
    Console.Error.WriteLine("This sample has no in-memory fake (unlike the Python samples) —");
    Console.Error.WriteLine("the real .NET provider always connects to a live store on Build().");
    return 1;
}
var endpoint = new Uri(endpointValue);

var cache = new TenantConfigurationCache(tenantId => AzureConfigurationRefresher.Load(endpoint, tenantId));

foreach (var tenantId in new[] { "tenant-a", "tenant-b" })
{
    var config = cache.Get(tenantId);
    Console.WriteLine($"[{tenantId}] LogLevel={config["LogLevel"]}");

    var refreshed = await cache.RefreshAsync(tenantId);
    Console.WriteLine($"[{tenantId}] refresh attempted: {refreshed}");
}
return 0;
```

Python サンプルと異なり、`APPCONFIG_ENDPOINT` 未設定時にフォールバックできる
インメモリフェイクは存在しない(実 Azure SDK の `IConfigurationBuilder.Build()` は
常に実ストアへの接続を試みる)。この非対称性を README の「既知の制約」に明記する。

## 4. テスト設計 — `Tests/TenantConfigurationCacheTests.cs`

`TenantConfigurationCache` を対象に、実 SDK に一切触れずテストする。

```csharp
namespace MtAppConfig.CacheRefresh.Tests;

using Microsoft.Extensions.Configuration;
using Xunit;

file sealed class FakeRefresher : ITenantConfigRefresher
{
    public int CallCount { get; private set; }
    public bool NextResult { get; set; } = true;

    public Task<bool> TryRefreshAsync(CancellationToken cancellationToken = default)
    {
        CallCount++;
        return Task.FromResult(NextResult);
    }
}

public class TenantConfigurationCacheTests
{
    private static TenantConfigEntry MakeEntry(string logLevel, out FakeRefresher refresher)
    {
        refresher = new FakeRefresher();
        var configuration = new ConfigurationBuilder()
            .AddInMemoryCollection(new Dictionary<string, string?> { ["LogLevel"] = logLevel })
            .Build();
        return new TenantConfigEntry(configuration, refresher);
    }

    [Fact]
    public void Get_caches_the_configuration_by_tenant_id()
    {
        var loadCount = 0;
        var cache = new TenantConfigurationCache(_ =>
        {
            loadCount++;
            return MakeEntry("Warning", out _);
        });

        cache.Get("tenant-a");
        cache.Get("tenant-a");

        Assert.Equal(1, loadCount);
    }

    [Fact]
    public void Get_does_not_leak_one_tenants_configuration_to_another()
    {
        var cache = new TenantConfigurationCache(tenantId =>
            MakeEntry(tenantId == "tenant-a" ? "Warning" : "Debug", out _));

        Assert.Equal("Warning", cache.Get("tenant-a")["LogLevel"]);
        Assert.Equal("Debug", cache.Get("tenant-b")["LogLevel"]);
    }

    [Fact]
    public async Task RefreshAsync_calls_through_to_the_cached_tenants_refresher()
    {
        FakeRefresher? capturedRefresher = null;
        var cache = new TenantConfigurationCache(_ =>
        {
            var entry = MakeEntry("Warning", out var refresher);
            capturedRefresher = refresher;
            return entry;
        });
        cache.Get("tenant-a"); // ロードしてキャッシュに乗せる

        var result = await cache.RefreshAsync("tenant-a");

        Assert.True(result);
        Assert.Equal(1, capturedRefresher!.CallCount);
    }

    [Fact]
    public async Task RefreshAsync_on_an_uncached_tenant_loads_instead_of_refreshing()
    {
        var loadCount = 0;
        var cache = new TenantConfigurationCache(_ =>
        {
            loadCount++;
            return MakeEntry("Warning", out _);
        });

        var result = await cache.RefreshAsync("tenant-a");

        Assert.False(result);
        Assert.Equal(1, loadCount);
    }

    [Fact]
    public async Task RefreshAsync_propagates_a_refresher_that_reports_no_change()
    {
        var cache = new TenantConfigurationCache(_ =>
        {
            var entry = MakeEntry("Warning", out var refresher);
            refresher.NextResult = false;
            return entry;
        });
        cache.Get("tenant-a");

        var result = await cache.RefreshAsync("tenant-a");

        Assert.False(result);
    }
}
```

これら5件は、記事の主張のうち以下を裏付ける。

| 主張 | テスト |
| --- | --- |
| テナント ID をキャッシュキーにする | `Get_caches_the_configuration_by_tenant_id` |
| テナント間で `IConfiguration` が混ざらない | `Get_does_not_leak_one_tenants_configuration_to_another` |
| `TryRefreshAsync` を呼んで明示的にリフレッシュする | `RefreshAsync_calls_through_to_the_cached_tenants_refresher` |
| (このリポジトリ独自の頑健性) 未ロードのテナントに対する誤ったリフレッシュ呼び出しを防ぐ | `RefreshAsync_on_an_uncached_tenant_loads_instead_of_refreshing` |
| `TryRefreshAsync` が false を返す(変化なし)場合の伝播 | `RefreshAsync_propagates_a_refresher_that_reports_no_change` |

## 5. README の内容

既存4サンプルと同じ構成(ストアの中身/中核のコード/いつ選ぶか/
メリット・デメリットとアンチパターン/動かす/既知の制約)に加え、次を明記する。

- **これは .NET のサンプルであり、01〜04(Python)とはビルド・実行系列が独立している**旨。
  `uv run pytest` の対象ではなく、`cd samples/05-dotnet-cache-refresh/Tests && dotnet test`
  で実行する。
- **実行にはフェイクストアがない**旨(Python サンプルとの非対称性を明記)。
  `AzureConfigurationRefresher.cs`/`Program.cs` は実 SDK に対してコンパイルは通るが、
  実行検証はしていない(`azure_source.py` と同じ立ち位置)。
- **この開発環境では `nuget.org` に到達できなかった**ため、`dotnet restore --source
  ~/.nuget/packages`(このリポジトリの外にあるローカルキャッシュ)を使って検証した旨。
  通常のネットワーク環境では素の `dotnet restore` / `dotnet build` / `dotnet test` で
  問題ない。
- ミドルウェア(`app.UseAzureAppConfiguration()`)による自動リフレッシュ経路についても、
  概念とコード片(`app.UseAzureAppConfiguration();` を ASP.NET Core パイプラインに
  追加するだけで、リクエストごとに自動でリフレッシュ間隔を確認する、という趣旨)を
  1段落で紹介するが、フルの Web アプリとしては実装しない旨を明記する。

## 6. ルート README の変更

- 「## クライアント直接配信(Azure Front Door、プレビュー)」節の後に、新しい節
  「## .NET: 明示的なキャッシュリフレッシュ」を追加し、05へのリンクと1〜2文の要約、
  および「このサンプルだけ .NET 製で、Python サンプルとはビルド・テスト系列が異なる」
  旨を明記する。
- 「既知の制約」に、05の検証がローカル NuGet キャッシュに依存していた旨を1文追記する。

## 7. 受け入れ条件

- `cd samples/05-dotnet-cache-refresh/Tests && dotnet test`(必要なら
  `dotnet restore --source ~/.nuget/packages` を先に実行)が、新規5件のテストを含めて
  オフラインで全件成功する。
- `dotnet build`(メインプロジェクト)が `AzureConfigurationRefresher.cs`/`Program.cs`
  を含めてエラーなくコンパイルできる(実行はしない)。
- `uv run pytest`(Python 側)の結果が本サンプル追加の前後で変化しない(既存138件+
  スキップ1件のまま)。
- サンプル05の README が、フェイクストアがないという非対称性と、ローカル NuGet
  キャッシュへの依存という開発環境固有の事情の両方を明記している。
