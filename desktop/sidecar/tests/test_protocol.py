import io
import json

import pytest

from desktop.sidecar.noval_sidecar.protocol import MAX_LINE_BYTES, ProtocolError, parse_request
from desktop.sidecar.noval_sidecar.server import SidecarServer


def request(method, params=None, request_id="req-1"):
    return json.dumps({
        "protocol_version": 1,
        "kind": "request",
        "request_id": request_id,
        "method": method,
        "params": params or {},
    }).encode()


def test_parse_request_rejects_invalid_and_oversized_input():
    with pytest.raises(ProtocolError, match="UTF-8 JSON"):
        parse_request(b"not-json")
    with pytest.raises(ProtocolError, match="size limit"):
        parse_request(b"x" * (MAX_LINE_BYTES + 1))


def test_hello_reports_stable_capabilities():
    parsed = parse_request(request("system.hello"))
    server = SidecarServer(io.BytesIO(), io.BytesIO())
    result = server.dispatch(parsed)
    assert result["protocol_version"] == 1
    assert "sessions" in result["capabilities"]
    assert result["core_version"]


def test_server_returns_safe_error_without_echoing_input():
    secret = "FAKE_SECRET_MUST_NOT_ECHO"
    output = io.BytesIO()
    SidecarServer(io.BytesIO((secret + "\n").encode()), output).serve()
    value = json.loads(output.getvalue())
    assert value["error"]["code"] == "invalid_json"
    assert secret not in output.getvalue().decode()


def test_workspace_must_be_selected_before_listing(tmp_path):
    server = SidecarServer(io.BytesIO(), io.BytesIO())
    server.dispatch(parse_request(request("runtime.start")))
    with pytest.raises(ValueError, match="workspace"):
        server.dispatch(parse_request(request("session.list")))
    selected = server.dispatch(parse_request(request("workspace.select", {"workdir": str(tmp_path)})))
    assert selected["workdir"] == str(tmp_path.resolve())
    assert server.dispatch(parse_request(request("session.list"))) == {"sessions": []}
    server.close()
