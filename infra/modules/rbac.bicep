@description('Name of the existing App Configuration store to grant access on.')
param configurationStoreName string

@description('Microsoft Entra object ID of the User, Group, or service principal that reads configuration. Managed identities and application identities use their service principal object ID.')
param principalId string

@description('Type of Microsoft Entra principal. Use ServicePrincipal for managed identities and application service principals, User for users, or Group for security groups.')
@allowed([
  'ServicePrincipal'
  'User'
  'Group'
])
param principalType string = 'ServicePrincipal'

// App Configuration Data Reader: read-only access to key-values, and nothing
// else. This is the least privilege the application needs.
var appConfigurationDataReaderRoleId = '516239f1-63e1-4d78-a4de-a74fb236a071'

resource store 'Microsoft.AppConfiguration/configurationStores@2024-05-01' existing = {
  name: configurationStoreName
}

resource assignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(store.id, principalId, appConfigurationDataReaderRoleId)
  scope: store
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', appConfigurationDataReaderRoleId)
    principalId: principalId
    principalType: principalType
  }
}
