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
                // tenantId must already be validated by the caller before
                // reaching here, the same way sample 01's KeyPrefixSource
                // requires (see src/mtappconfig/tenants.py's
                // TenantRegistry.resolve on the Python side of this repo)
                // — an unvalidated id would let a caller pass "*" and read
                // every tenant's settings at once. This sample doesn't
                // implement that validation itself since Program.cs only
                // ever passes hardcoded tenant ids.
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
