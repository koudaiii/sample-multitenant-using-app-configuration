namespace MtAppConfig.CacheRefresh;

using System.Text.RegularExpressions;

internal static class TenantId
{
    private static readonly Regex Pattern = new(
        @"\A[a-z0-9][a-z0-9-]{1,30}[a-z0-9]\z", RegexOptions.CultureInvariant);

    internal static void Validate(string tenantId)
    {
        if (tenantId is null || !Pattern.IsMatch(tenantId))
        {
            throw new ArgumentException(
                "Tenant IDs must be 3-32 lowercase ASCII letters, digits or hyphens, "
                + "starting and ending with a letter or digit.",
                nameof(tenantId));
        }
    }
}
