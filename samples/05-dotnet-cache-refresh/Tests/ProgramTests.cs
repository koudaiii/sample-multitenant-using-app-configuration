namespace MtAppConfig.CacheRefresh.Tests;

using Xunit;

[CollectionDefinition("Program entry point", DisableParallelization = true)]
public sealed class ProgramCollection
{
}

[Collection("Program entry point")]
public class ProgramTests
{
    [Theory]
    [InlineData(null)]
    [InlineData("")]
    [InlineData("not an endpoint")]
    [InlineData("https://")]
    [InlineData("https://[broken")]
    [InlineData("http://example.azconfig.io")]
    [InlineData(" https://example.azconfig.io")]
    [InlineData("https://example.azconfig.io\n")]
    public async Task Malformed_endpoint_input_has_actionable_output_without_a_stack_trace(string? value)
    {
        var originalEndpoint = Environment.GetEnvironmentVariable("APPCONFIG_ENDPOINT");
        var originalError = Console.Error;
        using var errorOutput = new StringWriter();
        object? result;
        try
        {
            Environment.SetEnvironmentVariable("APPCONFIG_ENDPOINT", value);
            Console.SetError(errorOutput);
            result = typeof(AzureConfigurationRefresher).Assembly.EntryPoint!
                .Invoke(null, new object[] { Array.Empty<string>() });
            if (result is Task<int> task)
            {
                result = await task;
            }
        }
        finally
        {
            Environment.SetEnvironmentVariable("APPCONFIG_ENDPOINT", originalEndpoint);
            Console.SetError(originalError);
        }

        Assert.Equal(1, Assert.IsType<int>(result));
        Assert.Contains("APPCONFIG_ENDPOINT", errorOutput.ToString());
        Assert.Contains("HTTPS", errorOutput.ToString());
        Assert.DoesNotContain("UriFormatException", errorOutput.ToString());
    }
}
