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
