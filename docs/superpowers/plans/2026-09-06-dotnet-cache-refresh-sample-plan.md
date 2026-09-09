# Sample 05: .NET Cache Refresh Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `samples/05-dotnet-cache-refresh/`, a real, buildable, testable .NET sample verifying the article's .NET-specific claim that applications register cached key-values for refresh with `ConfigureRefresh` and trigger the refresh by calling `TryRefreshAsync` (or App Configuration middleware), caching each tenant's `IConfiguration` object keyed by tenant id.

**Final-review addendum:** The current sample also validates tenant-ID syntax at its public
cache/loader boundaries, checks cancellation before uncached loading, and rejects malformed
endpoint input with actionable output. The original implementation snippets below are a task
record; use the current `.cs` files and [sample README](../../../samples/05-dotnet-cache-refresh/)
as the executable contract. Standard middleware does not discover separately constructed tenant roots.

**Architecture:** A small C# library (`TenantConfigurationCache` + `ITenantConfigRefresher`) implements per-tenant caching and explicit refresh, with xUnit tests using a fake refresher. `AzureConfigurationRefresher.cs` wires the real SDK (`Connect`/`Select`/`TrimKeyPrefix`/`ConfigureRefresh`/`GetRefresher`). Unit tests cover fake refresh and input guards; valid SDK loading requires a real Azure store.

**Tech Stack:** .NET 10 SDK, C#, xUnit. `Microsoft.Extensions.Configuration.AzureAppConfiguration` 8.4.0, `Azure.Identity` 1.14.0. This is a separate toolchain from the rest of the repo (Python/uv/pytest) and lives in its own directory tree.

**Spec:** `docs/superpowers/specs/2026-09-06-dotnet-cache-refresh-sample-design.md`

## Global Constraints

- Do not modify anything under `src/mtappconfig/`, `samples/01-shared-store-key-prefix/` through `samples/04-snapshot-references/`, `tests/`, `pyproject.toml`, or `conftest.py`. This plan only adds new files under `samples/05-dotnet-cache-refresh/` plus one new section each in `README.md` (repo root).
- Do not add `samples/05-dotnet-cache-refresh/` to `tests/test_pattern_contract.py` or any other Python test file. It is not a Python isolation pattern.
- Target framework for every `.csproj` in this sample is `net10.0`; use a .NET 10 SDK and runtime.
- Keep `PackageReference` versions pinned to the declared project versions for reproducibility; a populated cache is not a prerequisite.
- Restore through configured NuGet sources, without source overrides. Use `dotnet nuget list source` and the `NuGet.Config` hierarchy to inspect settings. Keep environment-specific sources and credentials out of Git and provision them locally or in CI.
- Run plain `dotnet restore`, then `dotnet test --no-restore`. Package acquisition needs access to configured sources; the restored unit tests themselves do not connect to Azure.
- Unit tests use fake refreshers and input guards. A valid `AzureConfigurationRefresher.Load` call requires a real endpoint and identity permissions; keep live execution separate from the unit-test workflow.

---

### Task 1: Core library — `TenantConfigurationCache` (TDD)

**Files:**
- Create: `samples/05-dotnet-cache-refresh/Sample05.csproj`
- Create: `samples/05-dotnet-cache-refresh/ITenantConfigRefresher.cs`
- Create: `samples/05-dotnet-cache-refresh/TenantConfigurationCache.cs`
- Create: `samples/05-dotnet-cache-refresh/Tests/Sample05.Tests.csproj`
- Create: `samples/05-dotnet-cache-refresh/Tests/TenantConfigurationCacheTests.cs`

**Interfaces:**
- Produces: `MtAppConfig.CacheRefresh.ITenantConfigRefresher` (interface, `Task<bool> TryRefreshAsync(CancellationToken cancellationToken = default)`); `MtAppConfig.CacheRefresh.TenantConfigEntry` (record, `IConfiguration Configuration`, `ITenantConfigRefresher Refresher`); `MtAppConfig.CacheRefresh.TenantConfigurationCache` (class, constructor `TenantConfigurationCache(Func<string, TenantConfigEntry> loader)`, methods `IConfiguration Get(string tenantId)` and `Task<bool> RefreshAsync(string tenantId, CancellationToken cancellationToken = default)`). Task 2 and Task 3 consume these exact names.

- [ ] **Step 1: Create `Sample05.csproj`**

```xml
<Project Sdk="Microsoft.NET.Sdk">

  <PropertyGroup>
    <TargetFramework>net10.0</TargetFramework>
    <ImplicitUsings>enable</ImplicitUsings>
    <Nullable>enable</Nullable>
    <RootNamespace>MtAppConfig.CacheRefresh</RootNamespace>
  </PropertyGroup>

  <ItemGroup>
    <PackageReference Include="Microsoft.Extensions.Configuration.Abstractions" Version="8.0.0" />
  </ItemGroup>

</Project>
```

No `<OutputType>` is set, so this defaults to `Library` — there is no `Program.cs`/entry point yet (Task 3 adds one and switches this to `Exe`).

- [ ] **Step 2: Create `ITenantConfigRefresher.cs`**

```csharp
namespace MtAppConfig.CacheRefresh;

/// <summary>
/// A thin wrapper around Azure App Configuration's
/// IConfigurationRefresher.TryRefreshAsync. Tests always substitute a fake
/// implementation of this interface and never touch the real Azure SDK —
/// the same role mtappconfig.source.TenantConfigSource plays on the Python
/// side of this repo.
/// </summary>
public interface ITenantConfigRefresher
{
    Task<bool> TryRefreshAsync(CancellationToken cancellationToken = default);
}
```

- [ ] **Step 3: Create `Tests/Sample05.Tests.csproj`**

```xml
<Project Sdk="Microsoft.NET.Sdk">

  <PropertyGroup>
    <TargetFramework>net10.0</TargetFramework>
    <ImplicitUsings>enable</ImplicitUsings>
    <Nullable>enable</Nullable>
    <IsPackable>false</IsPackable>
  </PropertyGroup>

  <ItemGroup>
    <PackageReference Include="Microsoft.Extensions.Configuration" Version="8.0.0" />
    <PackageReference Include="coverlet.collector" Version="6.0.0" />
    <PackageReference Include="Microsoft.NET.Test.Sdk" Version="17.8.0" />
    <PackageReference Include="xunit" Version="2.5.3" />
    <PackageReference Include="xunit.runner.visualstudio" Version="2.5.3" />
  </ItemGroup>

  <ItemGroup>
    <Using Include="Xunit" />
  </ItemGroup>

  <ItemGroup>
    <ProjectReference Include="../Sample05.csproj" />
  </ItemGroup>

</Project>
```

- [ ] **Step 4: Write the failing tests — `Tests/TenantConfigurationCacheTests.cs`**

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
        cache.Get("tenant-a");

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

- [ ] **Step 5: Run the tests to verify they fail**

Run: `cd samples/05-dotnet-cache-refresh/Tests && dotnet restore && dotnet test --no-restore`
Expected: FAIL to build — `TenantConfigurationCache` and `TenantConfigEntry` do not exist yet.

- [ ] **Step 6: Implement `TenantConfigurationCache.cs`**

```csharp
namespace MtAppConfig.CacheRefresh;

using Microsoft.Extensions.Configuration;

public sealed record TenantConfigEntry(IConfiguration Configuration, ITenantConfigRefresher Refresher);

/// <summary>
/// Caches one IConfiguration object per tenant, keyed by tenant id, and
/// exposes an explicit refresh trigger — this is the multitenant caching
/// pattern the article describes for .NET applications
/// ("cache the tenant's IConfiguration object and use the tenant
/// identifier as the cache key"; "trigger the refresh by calling
/// TryRefreshAsync"). Compare with src/mtappconfig/cache.py on the Python
/// side of this repo, which caches TenantConfig the same way.
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

    /// <summary>Loads on first access for this tenant id; returns the cached entry's configuration afterward.</summary>
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
    /// Explicitly triggers a refresh check for an already-cached tenant by
    /// calling its refresher's TryRefreshAsync. A tenant that has never
    /// been loaded is loaded instead of refreshed (there is nothing to
    /// refresh yet) and this returns false.
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

- [ ] **Step 7: Run the tests to verify they pass**

Run: `cd samples/05-dotnet-cache-refresh/Tests && dotnet test`
Expected: `合格: 5、失敗: 0`(or the English-locale equivalent `Passed: 5, Failed: 0`) — all 5 tests pass.

- [ ] **Step 8: Commit**

```bash
git add samples/05-dotnet-cache-refresh/Sample05.csproj \
        samples/05-dotnet-cache-refresh/ITenantConfigRefresher.cs \
        samples/05-dotnet-cache-refresh/TenantConfigurationCache.cs \
        samples/05-dotnet-cache-refresh/Tests/
git commit -m "feat: add sample 05's tenant configuration cache with explicit refresh"
```

---

### Task 2: Real SDK wiring — `AzureConfigurationRefresher.cs`

**Files:**
- Modify: `samples/05-dotnet-cache-refresh/Sample05.csproj`
- Create: `samples/05-dotnet-cache-refresh/AzureConfigurationRefresher.cs`

**Interfaces:**
- Consumes: `MtAppConfig.CacheRefresh.TenantConfigEntry`, `MtAppConfig.CacheRefresh.ITenantConfigRefresher` (Task 1).
- Produces: `MtAppConfig.CacheRefresh.AzureConfigurationRefresher.Load(Uri endpoint, string tenantId) -> TenantConfigEntry`. Task 3's `Program.cs` calls this exact signature.

- [ ] **Step 1: Add package references to `Sample05.csproj`**

Add these two lines inside the existing `<ItemGroup>` that has the `Microsoft.Extensions.Configuration.Abstractions` reference:

```xml
    <PackageReference Include="Microsoft.Extensions.Configuration" Version="8.0.0" />
    <PackageReference Include="Microsoft.Extensions.Configuration.AzureAppConfiguration" Version="8.4.0" />
    <PackageReference Include="Azure.Identity" Version="1.14.0" />
```

- [ ] **Step 2: Create `AzureConfigurationRefresher.cs`**

```csharp
namespace MtAppConfig.CacheRefresh;

using Azure.Identity;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.Configuration.AzureAppConfiguration;

/// <summary>
/// The one file in this sample that touches the real Azure App
/// Configuration SDK — the .NET equivalent of
/// src/mtappconfig/azure_source.py on the Python side of this repo. It
/// compiles against the real Microsoft.Extensions.Configuration.AzureAppConfiguration
/// package, but Load() is never called by any test in this sample: Build()
/// always attempts a live connection to the endpoint, which this
/// repository's test/CI environment cannot make.
///
/// The selection logic (Select + TrimKeyPrefix) is structurally identical
/// to sample 01's KeyPrefixSource (select(key_filter=..., trim_prefixes=...)
/// on the Python side): a shared-settings prefix merged with one
/// tenant-scoped prefix.
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
                    // One sentinel key per tenant, refreshAll: true, means
                    // watching a single key is enough to trigger a refresh
                    // of every key under that tenant's prefix — the same
                    // idea sample 04 uses for its snapshot reference key.
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

- [ ] **Step 3: Build to verify it compiles**

Run: `cd samples/05-dotnet-cache-refresh && dotnet build`
Expected: `ビルドに成功しました` (Build succeeded), 0 errors. This only compiles the code — it does not run `Load()`, so no network connection is attempted.

- [ ] **Step 4: Run the Task 1 tests again to confirm no regression**

Run: `cd samples/05-dotnet-cache-refresh/Tests && dotnet restore && dotnet test --no-restore`
Expected: still 5 passed, 0 failed (this task added no new tests — `AzureConfigurationRefresher` is not unit-testable per the Global Constraints).

- [ ] **Step 5: Commit**

```bash
git add samples/05-dotnet-cache-refresh/Sample05.csproj samples/05-dotnet-cache-refresh/AzureConfigurationRefresher.cs
git commit -m "feat: wire the real Azure App Configuration SDK for sample 05"
```

---

### Task 3: Console entrypoint — `Program.cs`

**Files:**
- Modify: `samples/05-dotnet-cache-refresh/Sample05.csproj`
- Create: `samples/05-dotnet-cache-refresh/Program.cs`

**Interfaces:**
- Consumes: `MtAppConfig.CacheRefresh.TenantConfigurationCache`, `MtAppConfig.CacheRefresh.AzureConfigurationRefresher.Load` (Tasks 1-2).

- [ ] **Step 1: Add `<OutputType>Exe</OutputType>` to `Sample05.csproj`**

In the existing `<PropertyGroup>`, add this line (anywhere inside the group, e.g. right after `<TargetFramework>net10.0</TargetFramework>`):

```xml
    <OutputType>Exe</OutputType>
```

- [ ] **Step 2: Create `Program.cs`**

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

- [ ] **Step 3: Build to verify it compiles**

Run: `cd samples/05-dotnet-cache-refresh && dotnet build`
Expected: `ビルドに成功しました`, 0 errors. Unit tests cover invalid endpoint input. For a valid `dotnet run`, supply `APPCONFIG_ENDPOINT`, a prepared store, and an identity with read access.

- [ ] **Step 4: Run the Task 1 tests again to confirm no regression**

Run: `cd samples/05-dotnet-cache-refresh/Tests && dotnet restore && dotnet test --no-restore`
Expected: still 5 passed, 0 failed.

- [ ] **Step 5: Commit**

```bash
git add samples/05-dotnet-cache-refresh/Sample05.csproj samples/05-dotnet-cache-refresh/Program.cs
git commit -m "feat: add sample 05's console entrypoint"
```

---

### Task 4: Sample 05 README

**Files:**
- Create: `samples/05-dotnet-cache-refresh/README.md`

- [ ] **Step 1: Write the README**

```markdown
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
| `Tests/TenantConfigurationCacheTests.cs` | `TenantConfigurationCache` の5つの振る舞いを検証。 |

## 中核のコード

```csharp
// AzureConfigurationRefresher.cs
options.Select($"{SharedPrefix}*");
options.Select($"{tenantId}/*");
options.TrimKeyPrefix(SharedPrefix);
options.TrimKeyPrefix($"{tenantId}/");
```

サンプル01の `KeyPrefixSource`(`select(key_filter=..., trim_prefixes=...)`)と
選択ロジックが構造的に同一です — `Select` が `key_filter`、`TrimKeyPrefix` が
`trim_prefixes` に対応します。

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

## ミドルウェアによる自動リフレッシュ(実装なし)

記事はもう1つの経路として ASP.NET Core ミドルウェアを挙げています。

```csharp
app.UseAzureAppConfiguration();
```

この経路には、ホスト構成への `AddAzureAppConfiguration` と
`builder.Services.AddAzureAppConfiguration()` による DI 登録が必要です。
ミドルウェアは DI の `IConfigurationRefresherProvider` が持つ provider を対象にします。
このサンプルのように別の `ConfigurationBuilder` で構築してテナントキャッシュへ保存した
root は自動検出されません。認証・テナント解決・認可後に、対象テナントの
`cache.RefreshAsync(tenantId, context.RequestAborted)` を呼ぶ処理を別途配線してください。
このサンプルはフルの Web アプリを追加するとスコープが大きくなりすぎるため、
概念とコード片の紹介に留め、実装はしていません。

## いつ選ぶか

.NET でこのリポジトリの分離パターン(01〜04)を実装する場合に、テナントごとの
`IConfiguration` をどうキャッシュし、いつリフレッシュを確認するかという設計判断の
参考として。

## メリット・デメリットとアンチパターン

### メリット

- `TryRefreshAsync` は変化がなければ何もせず高速に返るため、キャッシュされた
  テナントに対して安全に頻繁な呼び出しができる。
- テナントごとに独立した `IConfigurationRefresher` を持つため、あるテナントの
  リフレッシュ失敗が他のテナントに影響しない。
- ホスト/DIに登録した provider は標準ミドルウェアで refresh できるが、独立したテナント
  root のキャッシュはテナント対応の refresh 配線を別途必要とする。

### デメリット

- 実行には必ず実ストアへの接続が必要で、Python サンプルのようなインメモリ
  フェイクによるオフライン実行ができない(下記「既知の制約」参照)。
- テナント数だけ `IConfigurationRefresher`(と背後の HTTP クライアント)を持つ
  ことになり、テナント数が多い場合はコネクション数・メモリ使用量に注意が必要。
- センチネルキーの登録を忘れると、`ConfigureRefresh` で登録した意図に反して
  リフレッシュが働かない(Register の引数を間違えるとテナント間で意図しない
  キーを監視してしまうこともある)。

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

NuGet の構成済みソースを使い、既存キャッシュを前提としません。
`dotnet nuget list source` と `NuGet.Config` の階層で設定を確認し、必要なソースや
資格情報を利用者またはCIの設定として用意してください。

```bash
cd samples/05-dotnet-cache-refresh/Tests
dotnet restore
dotnet test --no-restore
```

実行(`Program.cs`)には実ストアが必要です。

```bash
export APPCONFIG_ENDPOINT=https://<your-store>.azconfig.io
cd samples/05-dotnet-cache-refresh
dotnet run
```

## 既知の制約

- **フェイクストアがありません。** Python サンプル(01〜04)は Azure サブスクリプション
  なしでも `uv run pytest` や `flask run` が動きますが、このサンプルは実 Azure SDK
  (`Microsoft.Extensions.Configuration.AzureAppConfiguration`)を直接使うため、
  `AzureConfigurationRefresher.Load` を呼ぶと必ず実ストアへの接続を試みます。
  テストは `TenantConfigurationCache` だけを対象にし、`AzureConfigurationRefresher.cs`
  と `Program.cs` は**コンパイルのみ**検証しています(`src/mtappconfig/azure_source.py`
  と同じ立ち位置です)。
- 未取得のパッケージには構成した NuGet ソースへの通信が必要です。NuGet 復元の成功と、
  実 App Configuration ストアへの接続・RBAC の検証は別です。
```

- [ ] **Step 2: Commit**

```bash
git add samples/05-dotnet-cache-refresh/README.md
git commit -m "docs: add sample 05's README"
```

---

### Task 5: Root README updates

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add a new section after the Front Door section**

Find this text in `README.md` (the last paragraph of the "## クライアント直接配信(Azure Front Door、プレビュー)" section, which ends right before `## 動かす`):

```markdown
このリポジトリはバックエンドサーバーがテナント設定を解決するモデル(01〜04)のみを対象としており、
クライアントが直接設定を読むこのパターンは実装・検証していません。Front Door 自体もローカルで
再現できないため、ここでは記事が示す注意点の要約に留めます。実装する場合は
[記事本文](https://learn.microsoft.com/azure/azure-app-configuration/concept-hyperscale-client-configuration)
に従い、専用ストアの Bicep 定義・Front Door のマネージド ID 読み取りロール・キャッシュ調整・
レプリカオリジンの構成を別途検討してください。
```

Insert a new section immediately after it (before `## 動かす`):

```markdown

## .NET: 明示的なキャッシュリフレッシュ

[05 .NET キャッシュリフレッシュ](samples/05-dotnet-cache-refresh/) は、記事の
Application-side caching 節が挙げる .NET 固有の記述(`ConfigureRefresh` で登録し
`TryRefreshAsync` またはミドルウェアでリフレッシュをトリガーする)を、実際にビルド・
テストできる C# コードで検証します。**このサンプルだけ .NET 製で、01〜04(Python)とは
ビルド・テストの系列が独立しています**(`dotnet test` で実行し、`uv run pytest` の
対象ではありません)。
```

- [ ] **Step 2: Add one bullet to "既知の制約"**

Find the end of the existing "既知の制約" section (the last bullet, about `azure_source.py` and snapshot references), and add a new bullet after it:

```markdown
- サンプル05(.NET)の通常の復元は、その環境で構成された NuGet ソースを使い、既存キャッシュを
  前提としません。社内プロキシを利用する場合は承認された `NuGet.config` を Git 管理せず、
  ローカルまたはCIで別途提供します。詳細は [samples/05-dotnet-cache-refresh/README.md](samples/05-dotnet-cache-refresh/) を参照してください。
```

- [ ] **Step 3: Run the Python suite to confirm no regression**

Run: `uv run pytest`
Expected: PASS, 138 passed, 1 skipped (unchanged — README edits don't affect Python tests).

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: link sample 05 from the root README"
```

---

### Task 6: Final verification

**Files:** none (verification only)

- [ ] **Step 1: Verify normal restore from an empty package directory, then run the .NET tests**

Run:
```bash
# From the repository root, with approved NuGet sources configured.
package_dir="$PWD/samples/05-dotnet-cache-refresh/Tests/obj/restore-validation-packages"
test ! -d "$package_dir"  # Choose a new path if a previous verification used this one.
mkdir -p "$package_dir"
dotnet restore samples/05-dotnet-cache-refresh/Tests/Sample05.Tests.csproj \
  --packages "$package_dir" --no-cache --force
dotnet test samples/05-dotnet-cache-refresh/Tests/Sample05.Tests.csproj --no-restore
```
Expected: both projects restore from configured sources and all current .NET tests pass.
Do not replace the configured sources with a machine-local cache path.

- [ ] **Step 2: Build the main project once more from a clean state**

Run: `cd samples/05-dotnet-cache-refresh && dotnet build --no-restore`
Expected: `ビルドに成功しました`, 0 errors, 0 warnings.

- [ ] **Step 3: Confirm the Python suite is untouched**

Run from the repository root: `uv run pytest`
Expected: 138 passed, 1 skipped — identical to before this plan started.

- [ ] **Step 4: Confirm `git log` shows one commit per task**

Run: `git log --oneline -6`
Expected: 5 commits from Tasks 1-5 (Task 6 makes no commit of its own).
