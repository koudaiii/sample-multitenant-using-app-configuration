@description('Name of the Log Analytics workspace.')
param name string

@description('Location for the workspace.')
param location string = resourceGroup().location

@description('Retention in days.')
param retentionInDays int = 30

resource workspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: name
  location: location
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: retentionInDays
  }
}

output id string = workspace.id
output name string = workspace.name
