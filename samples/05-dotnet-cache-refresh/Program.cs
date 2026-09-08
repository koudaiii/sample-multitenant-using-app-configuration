using MtAppConfig.CacheRefresh;

var endpointValue = Environment.GetEnvironmentVariable("APPCONFIG_ENDPOINT");
if (string.IsNullOrWhiteSpace(endpointValue)
    || endpointValue != endpointValue.Trim()
    || !Uri.TryCreate(endpointValue, UriKind.Absolute, out var endpoint)
    || endpoint.Scheme != Uri.UriSchemeHttps
    || string.IsNullOrEmpty(endpoint.Host))
{
    Console.Error.WriteLine("Set APPCONFIG_ENDPOINT to a non-empty HTTPS URL, e.g. https://<store>.azconfig.io.");
    Console.Error.WriteLine("This sample has no in-memory fake (unlike the Python samples) —");
    Console.Error.WriteLine("the real .NET provider always connects to a live store on Build().");
    return 1;
}

var cache = new TenantConfigurationCache(tenantId => AzureConfigurationRefresher.Load(endpoint, tenantId));
var tenantIds = new[] { "tenant-a", "tenant-b" };

foreach (var tenantId in tenantIds)
{
    var config = cache.Get(tenantId);
    Console.WriteLine($"[{tenantId}] initial LogLevel={config["LogLevel"]}");
}

Console.WriteLine("Update configuration and each tenant's Sentinel key, wait at least 30 seconds, then press Enter.");
Console.ReadLine();

foreach (var tenantId in tenantIds)
{
    var refreshed = await cache.RefreshAsync(tenantId);
    Console.WriteLine($"[{tenantId}] refresh check succeeded: {refreshed}");
    Console.WriteLine($"[{tenantId}] refreshed LogLevel={cache.Get(tenantId)["LogLevel"]}");
}
return 0;
