@description('Name of the Log Analytics workspace.')
param name string

@description('Location for the workspace.')
param location string = resourceGroup().location

@description('Default workspace-level Analytics retention in days for the PerGB2018 SKU.')
@minValue(30)
@maxValue(730)
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
