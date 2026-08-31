from __future__ import annotations

import json
import shutil
import sys
import uuid
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path(sys.argv[1]).resolve()
OUTPUT_ROOT = ROOT / "power-automate" / "Inventory_API_Import"
OUTPUT_ZIP = ROOT / "power-automate" / "Inventory_API_Import.zip"

def write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


if not (SOURCE / "manifest.json").exists():
    raise SystemExit(f"Not a Power Automate package directory: {SOURCE}")

if OUTPUT_ROOT.exists():
    shutil.rmtree(OUTPUT_ROOT)
shutil.copytree(SOURCE, OUTPUT_ROOT)

manifest_path = OUTPUT_ROOT / "manifest.json"
manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
source_flow_ids = [key for key, value in manifest["resources"].items()
                   if value.get("type") == "Microsoft.Flow/flows"]
if len(source_flow_ids) != 1:
    raise SystemExit("The source package must contain exactly one flow")
source_flow_id = source_flow_ids[0]
output_flow_id = str(uuid.uuid4())
manifest["details"].update(
    {
        "displayName": "Inventory API Import",
        "description": "Single HTTP API flow for the Excel-backed inventory application.",
        "packageTelemetryId": str(uuid.uuid4()),
    }
)
manifest["details"].pop("createdTime", None)
manifest["details"]["creator"] = "N/A"
manifest["details"]["sourceEnvironment"] = ""
flow_resource = manifest["resources"][source_flow_id]
flow_resource["suggestedCreationType"] = "New"
flow_resource["details"]["displayName"] = "Inventory - API"
flow_resource["dependsOn"] = []
# Excel workbook and Office Script references are tenant-specific. Including a
# made-up reference makes Power Automate validate it before the imported flow
# can be edited, so the import-safe package deliberately has no connector yet.
manifest["resources"] = {output_flow_id: flow_resource}
write_json(manifest_path, manifest)

flows_dir = OUTPUT_ROOT / "Microsoft.Flow" / "flows"
flow_dir = flows_dir / source_flow_id
sanitized_flow_dir = flows_dir / output_flow_id
flow_dir.rename(sanitized_flow_dir)
flow_dir = sanitized_flow_dir
write_json(flows_dir / "manifest.json", {
    "packageSchemaVersion": "1.0",
    "flowAssets": {"assetPaths": [output_flow_id]},
})
write_json(flow_dir / "apisMap.json", {})
write_json(flow_dir / "connectionsMap.json", {})

definition_path = flow_dir / "definition.json"
package_definition = json.loads(definition_path.read_text(encoding="utf-8"))
package_definition["name"] = output_flow_id
package_definition["id"] = "/providers/Microsoft.Flow/flows/" + output_flow_id
properties = package_definition["properties"]
properties["displayName"] = "Inventory - API"
properties["definition"]["metadata"] = {
    "failureAlertSubscription": True,
    "provisioningMethod": "FromDefinition",
}

request_schema = {
    "type": "object",
    "required": ["action"],
    "properties": {
        "action": {"type": "string"},
        "force": {"type": "boolean"},
        "itemId": {"type": "string"},
        "quantity": {"type": "integer"},
        "expectedCurrentSOH": {"type": "integer"},
        "fields": {"type": "object"},
    },
}

result_schema = {
    "type": "object",
    "properties": {
        "ok": {"type": "boolean"},
        "status": {"type": "integer"},
        "error": {"type": "string"},
        "message": {"type": "string"},
        "itemId": {"type": "string"},
        "old_stock": {"type": "number"},
        "stock": {"type": "number"},
        "item": {"type": "object"},
        "items": {"type": "array", "items": {"type": "object"}},
        "columns": {"type": "array", "items": {"type": "string"}},
    },
}

workflow = properties["definition"]
workflow["triggers"] = {
    "manual": {
        "metadata": {},
        "type": "Request",
        "kind": "Http",
        "inputs": {"schema": request_schema, "triggerAuthenticationType": "All"},
        "runtimeConfiguration": {"concurrency": {"runs": 1}},
    }
}
workflow["actions"] = {
    "Try": {
        "runAfter": {},
        "type": "Scope",
        "actions": {
            "Add_Run_script_here": {
                "runAfter": {},
                "type": "Compose",
                "inputs": "Add Excel Online (Business) - Run script here after import, then update Parse script result as described in POWER_AUTOMATE.md.",
            },
            "Parse_script_result": {
                "runAfter": {"Add_Run_script_here": ["Succeeded"]},
                "type": "ParseJson",
                "inputs": {
                    "content": {"ok": False, "status": 503, "error": "setup_required"},
                    "schema": result_schema,
                },
            },
            "Response": {
                "runAfter": {"Parse_script_result": ["Succeeded"]},
                "type": "Response",
                "kind": "Http",
                "operationOptions": "Asynchronous",
                "inputs": {
                    "statusCode": "@int(body('Parse_script_result')?['status'])",
                    "headers": {"Content-Type": "application/json"},
                    "body": "@body('Parse_script_result')",
                },
            },
        },
    },
    "Catch": {
        "runAfter": {"Try": ["Failed", "TimedOut"]},
        "type": "Scope",
        "actions": {
            "Service_unavailable": {
                "runAfter": {},
                "type": "Response",
                "kind": "Http",
                "operationOptions": "Asynchronous",
                "inputs": {
                    "statusCode": 503,
                    "headers": {"Content-Type": "application/json"},
                    "body": {"error": "excel_unavailable"},
                },
            }
        },
    },
}
properties["connectionReferences"] = {}
write_json(definition_path, package_definition)

if OUTPUT_ZIP.exists():
    OUTPUT_ZIP.unlink()
with zipfile.ZipFile(OUTPUT_ZIP, "w", zipfile.ZIP_DEFLATED) as archive:
    for path in sorted(OUTPUT_ROOT.rglob("*")):
        if path.is_file():
            archive.write(path, path.relative_to(OUTPUT_ROOT))

print(OUTPUT_ZIP)
