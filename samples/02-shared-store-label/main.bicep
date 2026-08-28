targetScope = 'resourceGroup'

@description('Suffix that keeps resource names globally unique.')
param nameSuffix string = uniqueString(resourceGroup().id)

param location string = resourceGroup().location

@allowed([
  'free'
  'developer'
  'standard'
  'premium'
])
param skuName string = 'standard'

@description('Object id of the managed identity that will read configuration.')
param readerPrincipalId string

module monitoring '../../infra/modules/monitoring.bicep' = {
  name: 'monitoring'
  params: {
    name: 'log-mtappconfig-${nameSuffix}'
    location: location
  }
}

// One shared store holds every tenant's settings, told apart by key prefix.
module sharedStore '../../infra/modules/appconfig.bicep' = {
  name: 'shared-store'
  params: {
    name: 'appcs-shared-${nameSuffix}'
    location: location
    skuName: skuName
    logAnalyticsWorkspaceId: monitoring.outputs.id
  }
}

module sharedStoreRbac '../../infra/modules/rbac.bicep' = {
  name: 'shared-store-rbac'
  params: {
    configurationStoreName: sharedStore.outputs.name
    principalId: readerPrincipalId
  }
}

output endpoint string = sharedStore.outputs.endpoint
