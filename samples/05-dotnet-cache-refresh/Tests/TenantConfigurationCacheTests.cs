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
