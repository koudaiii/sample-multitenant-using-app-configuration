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
