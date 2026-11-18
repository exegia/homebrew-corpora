"""Keep the draft protocol examples parseable and internally consistent."""

import json
import re
from pathlib import Path


def test_documented_protocol_examples_are_valid_and_ordered():
    document = (Path(__file__).resolve().parents[1] / "docs/sidecar-protocol-v1.md").read_text()
    blocks = re.findall(r"```json\n(.*?)\n```", document, re.DOTALL)
    assert len(blocks) == 3
    examples = [[json.loads(line) for line in block.splitlines()] for block in blocks]
    assert all(item["protocol"] == 1 for block in examples for item in block)

    sequence = examples[0]
    assert [item["type"] for item in sequence] == [
        "ready",
        "start",
        "accepted",
        "progress",
        "diagnostic",
        "progress",
        "finished",
    ]
    request, accepted = sequence[1:3]
    assert request["request_id"] == accepted["request_id"]
    assert all(item["job_id"] == accepted["job_id"] for item in sequence[3:])
    assert sequence[-1]["outcome"] == "succeeded"
    assert sequence[-1]["outputs"][0]["path"] == request["output"]["path"]

    cancel, acknowledgement, finished = examples[1]
    assert cancel["request_id"] == acknowledgement["request_id"]
    assert cancel["job_id"] == acknowledgement["job_id"] == finished["job_id"]
    assert acknowledgement["status"] == "requested"
    assert finished["outcome"] == "cancelled"
    assert finished["outputs"] == []

    error, failed, protocol_error = examples[2]
    assert error["type"] == "error" and error["code"] == "job_not_running"
    assert failed["outcome"] == "failed" and failed["outputs"] == []
    assert failed["error"]["code"] == "invalid_epub"
    assert protocol_error["type"] == "protocol_error"
