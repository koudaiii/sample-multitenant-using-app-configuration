targetScope = 'resourceGroup'

@description('Seed for independently addressable module deployment names. The default nameSuffix also derives from it; overriding nameSuffix decouples resource names from runId. Incremental deployment does not delete resources or role assignments created by earlier parameter values.')
param runId string = newGuid()

@description('Seed used to derive deterministic Azure-safe resource names. The literal value is hashed and is not embedded in resource names. Reuse it to keep resource names stable across runId values.')
param nameSuffix string = uniqueString(resourceGroup().id, runId)

@description('Azure region for the App Configuration stores and Log Analytics workspace.')
param location string = resourceGroup().location

@description('Unique tenant identifiers to provision. Values are hashed for Azure names and returned unchanged in outputs. Removing an identifier from a later incremental deployment does not delete its store or role assignment.')
param tenantIds string[] = [
  'tenant-a'
  'tenant-b'
]

@description('Pricing tier for the App Configuration stores. Review current Azure pricing and quota documentation before choosing a tier.')
@allowed([
  'free'
  'developer'
  'standard'
  'premium'
])
param skuName string = 'standard'

@description('Microsoft Entra object ID of the User, Group, or service principal that reads configuration. Managed identities and application identities use their service principal object ID. Changing it adds new assignments; incremental deployment does not remove previous assignments.')
param readerPrincipalId string

@description('Type of Microsoft Entra principal. Use ServicePrincipal for managed identities and application service principals, User for users, or Group for security groups.')
@allowed([
  'ServicePrincipal'
  'User'
  'Group'
])
param readerPrincipalType string = 'ServicePrincipal'

var validatedTenantIds = length(tenantIds) == length(union(tenantIds, tenantIds)) ? tenantIds : fail('tenantIds must contain unique values.')
var tenantNameComponents = [for tenantId in validatedTenantIds: uniqueString(tenantId)]
var runNameComponent = uniqueString(runId)
var resourceNameComponent = uniqueString(resourceGroup().id, nameSuffix)

module monitoring '../../infra/modules/monitoring.bicep' = {
  name: 'monitoring-${runNameComponent}'
  params: {
    name: 'log-mtappconfig-${resourceNameComponent}'
    location: location
  }
}

// Global settings still live in one shared store, so a global change is made
// in one place rather than once per tenant.
module sharedStore '../../infra/modules/appconfig.bicep' = {
  name: 'shared-store-${runNameComponent}'
  params: {
    name: 'appcs-shared-${resourceNameComponent}'
    location: location
    skuName: skuName
    logAnalyticsWorkspaceId: monitoring.outputs.id
  }
}

module sharedStoreRbac '../../infra/modules/rbac.bicep' = {
  name: 'shared-store-rbac-${runNameComponent}'
  params: {
    configurationStoreName: sharedStore.outputs.name
    principalId: readerPrincipalId
    principalType: readerPrincipalType
  }
}

// One store per tenant. Permissions on App Configuration are granted at the
// store level, so this is what makes per-tenant permissions possible at all.
module tenantStores '../../infra/modules/appconfig.bicep' = [
  for (tenantId, i) in validatedTenantIds: {
    name: 'tenant-store-${tenantNameComponents[i]}-${runNameComponent}'
    params: {
      name: 'appcs-${tenantNameComponents[i]}-${resourceNameComponent}'
      location: location
      skuName: skuName
      logAnalyticsWorkspaceId: monitoring.outputs.id
    }
  }
]

module tenantStoreRbac '../../infra/modules/rbac.bicep' = [
  for (tenantId, i) in validatedTenantIds: {
    name: 'tenant-rbac-${tenantNameComponents[i]}-${runNameComponent}'
    params: {
      configurationStoreName: tenantStores[i].outputs.name
      principalId: readerPrincipalId
      principalType: readerPrincipalType
    }
  }
]

output sharedEndpoint string = sharedStore.outputs.endpoint
output tenantEndpoints array = [
  for (tenantId, i) in validatedTenantIds: {
    tenantId: tenantId
    endpoint: tenantStores[i].outputs.endpoint
  }
]

output deployedRunId string = runId
output sharedStoreName string = sharedStore.outputs.name
output tenantStoreNames array = [
  for (tenantId, i) in validatedTenantIds: {
    tenantId: tenantId
    name: tenantStores[i].outputs.name
  }
]
