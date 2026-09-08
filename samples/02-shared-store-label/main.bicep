targetScope = 'resourceGroup'

@description('Seed for independently addressable module deployment names. The default nameSuffix also derives from it; overriding nameSuffix decouples resource names from runId. Incremental deployment does not delete resources or role assignments created by earlier parameter values.')
param runId string = newGuid()

@description('Seed used to derive deterministic Azure-safe resource names. The literal value is hashed and is not embedded in resource names. Reuse it to keep resource names stable across runId values.')
param nameSuffix string = uniqueString(resourceGroup().id, runId)

@description('Azure region for the App Configuration store and Log Analytics workspace.')
param location string = resourceGroup().location

@description('Pricing tier for the App Configuration store. Review current Azure pricing and quota documentation before choosing a tier.')
@allowed([
  'free'
  'developer'
  'standard'
  'premium'
])
param skuName string = 'standard'

@description('Microsoft Entra object ID of the User, Group, or service principal that reads configuration. Managed identities and application identities use their service principal object ID. Changing it adds a new assignment; incremental deployment does not remove the previous assignment.')
param readerPrincipalId string

@description('Type of Microsoft Entra principal. Use ServicePrincipal for managed identities and application service principals, User for users, or Group for security groups.')
@allowed([
  'ServicePrincipal'
  'User'
  'Group'
])
param readerPrincipalType string = 'ServicePrincipal'

var runNameComponent = uniqueString(runId)
var resourceNameComponent = uniqueString(resourceGroup().id, nameSuffix)

module monitoring '../../infra/modules/monitoring.bicep' = {
  name: 'monitoring-${runNameComponent}'
  params: {
    name: 'log-mtappconfig-${resourceNameComponent}'
    location: location
  }
}

// One shared store holds every tenant's settings. Samples 01 and 02 deploy
// this file byte-for-byte identically: the two patterns differ in how the
// application queries the store, not in what gets deployed.
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

output endpoint string = sharedStore.outputs.endpoint

output deployedRunId string = runId
output sharedStoreName string = sharedStore.outputs.name
