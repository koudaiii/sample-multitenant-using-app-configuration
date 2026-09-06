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
