namespace MtAppConfig.CacheRefresh.Tests;

using Microsoft.Extensions.Configuration;
using Xunit;

sealed class FakeRefresher : ITenantConfigRefresher
{
    public int CallCount { get; private set; }

    // The value TryRefreshAsync should return: true = attempt succeeded
    // (including a no-op because the refresh interval hasn't elapsed),
    // false = attempt failed.
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
        var cache = new TenantConfigurationCache(tenantId =>
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
    public async Task RefreshAsync_only_calls_the_requested_tenants_refresher()
    {
        FakeRefresher? tenantARefresher = null;
        FakeRefresher? tenantBRefresher = null;
        var cache = new TenantConfigurationCache(tenantId =>
        {
            var entry = MakeEntry(tenantId == "tenant-a" ? "Warning" : "Debug", out var refresher);
            if (tenantId == "tenant-a")
            {
                tenantARefresher = refresher;
            }
            else
            {
                tenantBRefresher = refresher;
            }
            return entry;
        });
        cache.Get("tenant-a");
        cache.Get("tenant-b");

        await cache.RefreshAsync("tenant-a");

        Assert.Equal(1, tenantARefresher!.CallCount);
        Assert.Equal(0, tenantBRefresher!.CallCount);
    }

    [Fact]
    public async Task Concurrent_Get_for_the_same_tenant_loads_once()
    {
        var loadCount = 0;
        var cache = new TenantConfigurationCache(tenantId =>
        {
            Interlocked.Increment(ref loadCount);
            Thread.Sleep(20);
            return MakeEntry("Warning", out var unusedRefresher);
        });
        using var start = new ManualResetEventSlim(false);
        var tasks = Enumerable.Range(0, 16)
            .Select(_ => Task.Run(() =>
            {
                start.Wait();
                return cache.Get("tenant-a");
            }))
            .ToArray();

        start.Set();
        var configurations = await Task.WhenAll(tasks);

        Assert.Equal(1, loadCount);
        Assert.All(configurations, configuration => Assert.Same(configurations[0], configuration));
    }

    [Fact]
    public async Task RefreshAsync_on_an_uncached_tenant_loads_and_reports_success()
    {
        var loadCount = 0;
        var cache = new TenantConfigurationCache(tenantId =>
        {
            loadCount++;
            return MakeEntry("Warning", out _);
        });

        var result = await cache.RefreshAsync("tenant-a");

        Assert.True(result);
        Assert.Equal(1, loadCount);
    }

    [Fact]
    public async Task RefreshAsync_on_an_uncached_tenant_leaves_it_cached_for_later_calls()
    {
        var loadCount = 0;
        FakeRefresher? capturedRefresher = null;
        var cache = new TenantConfigurationCache(_ =>
        {
            loadCount++;
            var entry = MakeEntry("Warning", out var refresher);
            capturedRefresher = refresher;
            return entry;
        });

        var firstResult = await cache.RefreshAsync("tenant-a");
        var secondResult = await cache.RefreshAsync("tenant-a");

        Assert.True(firstResult);
        Assert.True(secondResult);
        Assert.Equal(1, loadCount);
        Assert.Equal(1, capturedRefresher!.CallCount);
    }

    [Fact]
    public async Task RefreshAsync_propagates_a_failed_refresh_attempt()
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
