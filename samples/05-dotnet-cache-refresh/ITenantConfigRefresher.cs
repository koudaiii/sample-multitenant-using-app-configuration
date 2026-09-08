namespace MtAppConfig.CacheRefresh;

/// <summary>
/// A thin wrapper around Azure App Configuration's
/// IConfigurationRefresher.TryRefreshAsync. Tests always substitute a fake
/// implementation of this interface and never touch the real Azure SDK —
/// the same role mtappconfig.source.TenantConfigSource plays on the Python
/// side of this repo.
///
/// The real SDK's contract: the returned bool means the refresh *attempt*
/// succeeded, not that any value changed. It is true both when a refresh
/// was performed successfully and when it was skipped as a no-op because
/// the configured refresh interval has not yet elapsed. It is false only
/// for failures the SDK handles, including request, authentication, Key
/// Vault, cancellation, and formatting failures; it logs those failures
/// and continues serving the last known good values. Unexpected exceptions
/// are not guaranteed to be swallowed.
/// </summary>
public interface ITenantConfigRefresher
{
    Task<bool> TryRefreshAsync(CancellationToken cancellationToken = default);
}
