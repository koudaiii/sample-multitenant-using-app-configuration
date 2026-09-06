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

    // This call will almost always return true having done nothing: the
    // 30-second refresh interval set in AzureConfigurationRefresher.Load
    // has not elapsed since Get() just loaded this tenant moments ago.
    // Change a value in the store and wait past the interval to see an
    // actual refresh happen.
    var refreshed = await cache.RefreshAsync(tenantId);
    Console.WriteLine($"[{tenantId}] refresh check succeeded: {refreshed}");
}
return 0;
