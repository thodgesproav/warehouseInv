"""Build a separate importable Outlook notification flow; no tenant secrets."""
import json
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parents[1]
FLOW = 'e6ca5ea9-ac96-455e-a526-415453966aa1'
API = 'c40b9a29-d7a4-499c-a5c5-7a41273b5c54'
CONNECTION = 'b1e2d13d-e36f-41b0-834d-ae17df77d606'
API_PATH = '/providers/Microsoft.PowerApps/apis/shared_office365'


def build(output):
    manifest = {'schema': '1.0', 'details': {'displayName': 'Inventory Notifications',
        'description': 'Request and availability emails for Warehouse Inventory.',
        'creator': 'Warehouse Inventory', 'sourceEnvironment': ''}, 'resources': {
        FLOW: {'type': 'Microsoft.Flow/flows', 'suggestedCreationType': 'New',
            'creationType': 'Existing, New, Update', 'details': {'displayName': 'Inventory - Notifications'},
            'configurableBy': 'User', 'hierarchy': 'Root', 'dependsOn': [API, CONNECTION]},
        API: {'id': API_PATH, 'name': 'shared_office365', 'type': 'Microsoft.PowerApps/apis',
            'suggestedCreationType': 'Existing', 'details': {'displayName': 'Office 365 Outlook'},
            'configurableBy': 'System', 'hierarchy': 'Child', 'dependsOn': []},
        CONNECTION: {'type': 'Microsoft.PowerApps/apis/connections', 'suggestedCreationType': 'Existing',
            'creationType': 'Existing', 'details': {'displayName': 'Choose the account that sends inventory emails'},
            'configurableBy': 'User', 'hierarchy': 'Child', 'dependsOn': [API]}}}
    definition = {'name': FLOW, 'id': '/providers/Microsoft.Flow/flows/' + FLOW,
        'type': 'Microsoft.Flow/flows', 'properties': {
        'apiId': '/providers/Microsoft.PowerApps/apis/shared_logicflows',
        'displayName': 'Inventory - Notifications', 'definition': {
            '$schema': 'https://schema.management.azure.com/providers/Microsoft.Logic/schemas/2016-06-01/workflowdefinition.json#',
            'contentVersion': '1.0.0.0', 'metadata': {'provisioningMethod': 'FromDefinition'},
            'parameters': {'$authentication': {'defaultValue': {}, 'type': 'SecureObject'},
                           '$connections': {'defaultValue': {}, 'type': 'Object'}},
            'triggers': {'manual': {'type': 'Request', 'kind': 'Http', 'inputs': {
                'triggerAuthenticationType': 'All', 'schema': {'type': 'object',
                    'required': ['to', 'subject', 'htmlBody', 'eventId'],
                    'properties': {key: {'type': 'string'} for key in ['to', 'subject', 'htmlBody', 'eventId']}}}}},
            'actions': {'Try': {'type': 'Scope', 'runAfter': {}, 'actions': {
                'Send_email': {'type': 'OpenApiConnection', 'runAfter': {}, 'inputs': {
                    'parameters': {'emailMessage/To': "@triggerBody()?['to']",
                                   'emailMessage/Subject': "@triggerBody()?['subject']",
                                   'emailMessage/Body': "@triggerBody()?['htmlBody']"},
                    'host': {'apiId': API_PATH, 'connectionName': 'shared_office365', 'operationId': 'SendEmailV2'},
                    'authentication': "@parameters('$authentication')", 'retryPolicy': {'type': 'none'}}},
                'Response': {'type': 'Response', 'kind': 'Http', 'runAfter': {'Send_email': ['Succeeded']},
                    'inputs': {'statusCode': 200, 'headers': {'Content-Type': 'application/json'},
                               'body': {'ok': True, 'eventId': "@triggerBody()?['eventId']"}}}}},
                'Catch': {'type': 'Scope', 'runAfter': {'Try': ['Failed', 'TimedOut']}, 'actions': {
                    'Delivery_unconfirmed': {'type': 'Response', 'kind': 'Http', 'runAfter': {},
                        'inputs': {'statusCode': 503, 'body': {'ok': False, 'error': 'delivery_unconfirmed'}}}}}}},
        'connectionReferences': {'shared_office365': {'connectionName': CONNECTION,
            'source': 'Embedded', 'id': API_PATH, 'tier': 'NotSpecified', 'apiName': 'office365',
            'isProcessSimpleApiReferenceConversionAlreadyDone': False}},
        'flowFailureAlertSubscribed': False, 'isManaged': False}}
    prefix = 'Microsoft.Flow/flows/' + FLOW + '/'
    files = {'manifest.json': manifest,
             'Microsoft.Flow/flows/manifest.json': {'packageSchemaVersion': '1.0', 'flowAssets': {'assetPaths': [FLOW]}},
             prefix + 'definition.json': definition,
             prefix + 'apisMap.json': {'shared_office365': API},
             prefix + 'connectionsMap.json': {'shared_office365': CONNECTION}}
    output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(output, 'w', ZIP_DEFLATED) as archive:
        for name, value in files.items(): archive.writestr(name, json.dumps(value, indent=2) + '\n')
    return files


if __name__ == '__main__':
    output = ROOT / 'power-automate' / 'Inventory_Notifications_Import.zip'
    build(output)
    print(output)
