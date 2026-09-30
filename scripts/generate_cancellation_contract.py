"""Refresh the cancellation route from its Pydantic request source."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from server.app.drop_insight.schemas import CancelDiagnosisRequest

path = ROOT / "docs/contracts/openapi.v1.json"
text = path.read_text(encoding="utf-8")
route = "/api/v2/diagnoses/{diagnosis_id}/cancel"
operation = {
    "parameters": [{"name": "diagnosis_id", "in": "path", "required": True, "schema": {"type": "string"}}],
    "post": {
        "operationId": "cancelDiagnosis",
        "description": "Operator/admin cancellation. Atomically cancels the session and owned active tasks; Agent terminates collection on heartbeat. Repeated CANCELLED requests are idempotent even with the original version. Completed history and existing evidence remain immutable.",
        "requestBody": {"required": True, "content": {"application/json": {"schema": CancelDiagnosisRequest.model_json_schema()}}},
        "responses": {"200": {"$ref": "#/components/responses/Success"},
                      "403": {"description": "Role or resource scope rejected"},
                      "404": {"description": "Diagnosis missing or archived"},
                      "409": {"description": "Version conflict or other terminal state"},
                      "422": {"description": "Invalid cancellation input"}},
    },
}
line = "    " + json.dumps(route) + ": " + json.dumps(operation, ensure_ascii=False) + ","
existing = next((row for row in text.splitlines() if row.lstrip().startswith(json.dumps(route) + ":")), None)
if existing:
    text = text.replace(existing, line, 1)
else:
    text = text.replace('  "paths": {\n', '  "paths": {\n' + line + '\n', 1)
path.write_text(text, encoding="utf-8")
