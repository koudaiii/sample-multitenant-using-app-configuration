namespace MtAppConfig.CacheRefresh;

using Azure.Identity;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.Configuration.AzureAppConfiguration;

/// <summary>
/// The one file in this sample that touches the real Azure App
/// Configuration SDK — the .NET equivalent of
/// src/mtappconfig/azure_source.py on the Python side of this repo. It
/// compiles against the real Microsoft.Extensions.Configuration.AzureAppConfiguration
/// package. Tests call only its invalid-tenant guard: Build() on valid
/// input attempts a live connection and remains unverified here.
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
        TenantId.Validate(tenantId);
        IConfigurationRefresher? refresher = null;

        var configuration = new ConfigurationBuilder()
            .AddAzureAppConfiguration(options =>
            {
                options.Connect(endpoint, new DefaultAzureCredential());
                options.Select($"{SharedPrefix}*");
                // Syntax is checked above. The caller must still resolve a
                // registered tenant and authorize access before calling Load.
                options.Select($"{tenantId}/*");
                options.TrimKeyPrefix(SharedPrefix);
                options.TrimKeyPrefix($"{tenantId}/");
                options.ConfigureRefresh(refresh =>
                {
                    // refreshAll: true means "watching this one sentinel
                    // key refreshes every key this provider instance
                    // uses" — that's everything selected above, both
                    // _shared/* and {tenantId}/*, not just the tenant's
                    // own prefix. Because each tenant has its own
                    // provider instance (Load is called once per tenant
                    // id), a change to a _shared/* key is only picked up
                    // once THIS tenant's own sentinel is also touched —
                    // see the README's デメリット for the trade-off.
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
