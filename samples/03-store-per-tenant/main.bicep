targetScope = 'resourceGroup'

@description('Suffix that keeps resource names globally unique.')
param nameSuffix string = uniqueString(resourceGroup().id)

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

module monitoring '../../infra/modules/monitoring.bicep' = {
  name: 'monitoring'
  params: {
    name: 'log-mtappconfig-${nameSuffix}'
    location: location
  }
}

// Global settings still live in one shared store, so a global change is made
// in one place rather than once per tenant.
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

// One store per tenant. Permissions on App Configuration are granted at the
// store level, so this is what makes per-tenant permissions possible at all.
module tenantStores '../../infra/modules/appconfig.bicep' = [
  for tenantId in tenantIds: {
    name: 'store-${tenantId}'
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
    name: 'store-rbac-${tenantId}'
    params: {
      configurationStoreName: tenantStores[i].outputs.name
      principalId: readerPrincipalId
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
