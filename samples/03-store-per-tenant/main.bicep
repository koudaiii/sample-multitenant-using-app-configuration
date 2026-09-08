targetScope = 'resourceGroup'

@description('A new run creates separate resources. Pass the same runId to update that run.')
param runId string = newGuid()

@description('Resource suffix. By default it is unique to the resource group and run.')
@minLength(3)
@maxLength(20)
param nameSuffix string = uniqueString(resourceGroup().id, runId)

param location string = resourceGroup().location

@description('Tenants to provision a dedicated store for. Free tier store-count limits differ across Microsoft\'s own docs; see this sample\'s README before relying on a specific number.')
param tenantIds array = [
  'tenant-a'
  'tenant-b'
]

@allowed([
  'free'
  'developer'
  'standard'
  'premium'
])
param skuName string = 'standard'

@description('Object id of the managed identity that will read configuration.')
param readerPrincipalId string

@allowed([
  'ServicePrincipal'
  'User'
  'Group'
])
param readerPrincipalType string = 'ServicePrincipal'

module monitoring '../../infra/modules/monitoring.bicep' = {
  name: 'monitoring-${nameSuffix}'
  params: {
    name: 'log-mtappconfig-${nameSuffix}'
    location: location
  }
}

// Global settings still live in one shared store, so a global change is made
// in one place rather than once per tenant.
module sharedStore '../../infra/modules/appconfig.bicep' = {
  name: 'shared-store-${nameSuffix}'
  params: {
    name: 'appcs-shared-${nameSuffix}'
    location: location
    skuName: skuName
    logAnalyticsWorkspaceId: monitoring.outputs.id
  }
}

module sharedStoreRbac '../../infra/modules/rbac.bicep' = {
  name: 'shared-store-rbac-${nameSuffix}'
  params: {
    configurationStoreName: sharedStore.outputs.name
    principalId: readerPrincipalId
    principalType: readerPrincipalType
  }
}

// One store per tenant. Permissions on App Configuration are granted at the
// store level, so this is what makes per-tenant permissions possible at all.
module tenantStores '../../infra/modules/appconfig.bicep' = [
  for tenantId in tenantIds: {
    name: 'store-${tenantId}-${nameSuffix}'
    params: {
      name: 'appcs-${tenantId}-${nameSuffix}'
      location: location
      skuName: skuName
      logAnalyticsWorkspaceId: monitoring.outputs.id
    }
  }
]

module tenantStoreRbac '../../infra/modules/rbac.bicep' = [
  for (tenantId, i) in tenantIds: {
    name: 'store-rbac-${tenantId}-${nameSuffix}'
    params: {
      configurationStoreName: tenantStores[i].outputs.name
      principalId: readerPrincipalId
      principalType: readerPrincipalType
    }
  }
]

output sharedEndpoint string = sharedStore.outputs.endpoint
output tenantEndpoints array = [
  for (tenantId, i) in tenantIds: {
    tenantId: tenantId
    endpoint: tenantStores[i].outputs.endpoint
  }
]

output deployedRunId string = runId
output sharedStoreName string = sharedStore.outputs.name
output tenantStoreNames array = [
  for (tenantId, i) in tenantIds: {
    tenantId: tenantId
    name: tenantStores[i].outputs.name
  }
]
