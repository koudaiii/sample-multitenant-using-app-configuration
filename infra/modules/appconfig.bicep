@description('Name of the App Configuration store.')
param name string

@description('Location for the store.')
param location string = resourceGroup().location

@description('Pricing tier for the App Configuration store. Review current Azure pricing and quota documentation before choosing a tier.')
@allowed([
  'free'
  'developer'
  'standard'
  'premium'
])
param skuName string = 'standard'

@description('Log Analytics workspace to send diagnostics to.')
param logAnalyticsWorkspaceId string

resource store 'Microsoft.AppConfiguration/configurationStores@2024-05-01' = {
  name: name
  location: location
  sku: {
    name: skuName
  }
  properties: {
    // Security: require Entra ID. Access keys and connection strings are off,
    // so a leaked key cannot be used and the app must use a managed identity.
    disableLocalAuth: true
  }
}

resource diagnostics 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  name: 'send-to-log-analytics'
  scope: store
  properties: {
    workspaceId: logAnalyticsWorkspaceId
    logs: [
      {
        categoryGroup: 'allLogs'
        enabled: true
      }
    ]
    metrics: [
      {
        category: 'AllMetrics'
        enabled: true
      }
    ]
  }
}

output id string = store.id
output name string = store.name
output endpoint string = store.properties.endpoint
