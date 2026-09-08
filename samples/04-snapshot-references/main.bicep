targetScope = 'resourceGroup'

@description('A new run creates separate resources. Pass the same runId to update that run.')
param runId string = newGuid()

@description('Resource suffix. By default it is unique to the resource group and run.')
@minLength(3)
@maxLength(20)
param nameSuffix string = uniqueString(resourceGroup().id, runId)

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

// One shared store, same as samples 01 and 02: the reference key and the
// snapshots it points at both live inside it. Reading a snapshot needs no
// role beyond reading the store, so the RBAC module below is unchanged.
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

output endpoint string = sharedStore.outputs.endpoint

output deployedRunId string = runId
output sharedStoreName string = sharedStore.outputs.name
