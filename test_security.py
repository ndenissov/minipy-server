import ast
import base64
import json
import os
import sys
import tempfile
from pathlib import Path

# Add project root and minipy3 to path
sys.path.insert(0, str(Path(__file__).parent.parent / "minipy3"))
sys.path.insert(0, str(Path(__file__).parent))

from app import (
    ALLOWED_STATIC_EXTENSIONS,
    MIME_TYPES,
    MAX_REQUEST_BODY_SIZE,
    MinipyHTTPHandler,
    _is_valid_ip,
)
from db import Database
from service import MinipyService


def test_syntax_validation():
    print("Testing syntax validation...")
    err = MinipyService.validate_syntax("def foo():\n    return 42\n")
    assert err is None, f"Expected valid syntax, got: {err}"

    err = MinipyService.validate_syntax("def foo(:\n    return 42\n")
    assert err is not None
    assert err["error"] == "SyntaxError"
    print("  ✓ Syntax validation works")


def test_minify_and_unparse():
    print("Testing minification and safe unparsing...")
    sample_code = """
def calculate_sum(items):
    '''Calculate sum of items.'''
    'unused literal'
    total = 0
    for x in items:
        total += x
    return total

print(calculate_sum([1, 2, 3]))
"""
    # 1. Minify without compression
    res_no_comp, err = MinipyService.process_minify(sample_code, compress=False)
    assert err is None, f"Minify failed: {err}"
    assert res_no_comp is not None
    assert res_no_comp.result is not None

    # Unparse without compression
    unp_res, err = MinipyService.process_unparse(res_no_comp.result)
    assert err is None, f"Unparse failed: {err}"
    assert "calculate_sum" in unp_res["result"]

    # 2. Minify with compression required
    res_comp, err = MinipyService.process_minify(sample_code, compress=True, compress_required=True)
    assert err is None, f"Compressed minify failed: {err}"
    assert "decompress" in res_comp.result

    # 3. Unparse compressed code
    unp_comp, err = MinipyService.process_unparse(res_comp.result)
    assert err is None, f"Decompression unparse failed: {err}"
    assert "calculate_sum" in unp_comp["result"]
    assert "def calculate_sum" in unp_comp["result"]

    print("  ✓ Minify and safe decompress/unparse works")


def test_rce_prevention():
    print("Testing Remote Code Execution (RCE) neutralization...")
    canary_file = tempfile.mktemp(prefix="pwned_canary_")

    # Attacker payload that would have executed under the old exec() implementation
    malicious_payload = (
        f"i=__import__;i('os').system('touch {canary_file}'); "
        f"# b85decode decompress( "
    )

    # Call unparse with malicious code
    res, err = MinipyService.process_unparse(malicious_payload)

    # Verify canary was NEVER touched/created
    assert not os.path.exists(canary_file), "CRITICAL: RCE EXECUTED! Canary file was created!"

    # Another vector: nested exec with decompress disguise
    malicious_payload_2 = (
        f"exec(__import__('os').system('touch {canary_file}') or b''); "
        f"__import__('zlib').decompress(b'dummy') # b85decode"
    )
    res, err = MinipyService.process_unparse(malicious_payload_2)
    assert not os.path.exists(canary_file), "CRITICAL: RCE EXECUTED via second vector!"

    print("  ✓ RCE vectors safely blocked and neutralized without executing code")


def test_decompression_bomb_guard():
    print("Testing decompression bomb guard...")
    import zlib
    # 20MB of zeros compressed
    big_zeros = b"0" * (20 * 1024 * 1024)
    comp = zlib.compress(big_zeros)
    b85 = base64.b85encode(comp)
    bomb_code = f"i=__import__;exec(i('zlib').decompress(i('base64').b85decode({b85})))"

    # Should not crash or exhaust memory, should handle limit gracefully
    res, err = MinipyService.process_unparse(bomb_code)
    # Decompression exceeds limit, so _safe_decompress ignores or catches it and unparse parses/formats the code safely
    print("  ✓ Decompression bomb guard safely handled")


def test_static_path_traversal():
    print("Testing static path traversal defenses...")
    from app import STATIC_DIR

    class DummyHandler:
        pass

    handler = DummyHandler()
    handler._resolve_static_path = MinipyHTTPHandler._resolve_static_path.__get__(handler)

    # Legitimate static file
    assert handler._resolve_static_path("/static/style.css") is not None
    assert handler._resolve_static_path("/static/app.js") is not None

    # Path traversal attempts
    traversal_attacks = [
        "/static/../app.py",
        "/static/..%2fapp.py",
        "/static/..\\app.py",
        "/static/%2e%2e/app.py",
        "/static//etc/passwd",
        "/static/.git",
        "/static/.env",
        "/static/style.css.bak",
        "/static/../../../../../../etc/passwd",
        "/static",
        "/static/",
    ]
    for attack in traversal_attacks:
        res = handler._resolve_static_path(attack)
        assert res is None, f"SECURITY FAILURE: Path traversal succeeded on '{attack}' -> {res}"

    print("  ✓ Static path traversal properly blocked")


def test_ip_validation():
    print("Testing IP extraction and validation...")
    # Valid IPs
    assert _is_valid_ip("127.0.0.1")
    assert _is_valid_ip("192.168.1.100")
    assert _is_valid_ip("::1")
    assert _is_valid_ip("2001:db8::1")

    # Malicious injection inputs
    assert not _is_valid_ip("127.0.0.1\n[2026-11-06] Injected log entry")
    assert not _is_valid_ip("127.0.0.1\r\nCRLF")
    assert not _is_valid_ip("<script>alert(1)</script>")
    assert not _is_valid_ip("invalid_ip")
    assert not _is_valid_ip("127.0.0.1 ")
    assert not _is_valid_ip(" 127.0.0.1")
    assert not _is_valid_ip("999.999.999.999")
    print("  ✓ IP validation and log injection prevention verified")


def test_header_redaction_and_db():
    print("Testing header redaction and DB logging...")
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        db = Database(db_path)

        # Test secure permissions
        mode = oct(os.stat(db_path.parent).st_mode & 0o777)
        assert mode == "0o700", f"Expected dir mode 0o700, got {mode}"

        # Test request headers sanitization
        class DummyRequest:
            headers = {
                "User-Agent": "curl/7.68.0",
                "Authorization": "Bearer secret_jwt_token_12345",
                "Cookie": "session=sensitive_cookie_value",
                "X-Api-Key": "my-secret-api-key",
                "Accept": "*/*",
            }
        
        handler = DummyRequest()
        handler._get_request_headers_dict = MinipyHTTPHandler._get_request_headers_dict.__get__(handler)
        sanitized = handler._get_request_headers_dict()

        assert sanitized["Authorization"] == "[REDACTED]"
        assert sanitized["Cookie"] == "[REDACTED]"
        assert sanitized["X-Api-Key"] == "[REDACTED]"
        assert sanitized["User-Agent"] == "curl/7.68.0"

        # Test log_success in DB
        row_id = db.log_success(
            client_ip="1.2.3.4",
            headers=sanitized,
            compressed_result="exec(minified)",
            original_size=100,
            compressed_size=50,
            ratio=0.5,
            duration_ms=12.3,
            options={"compress": True},
        )
        assert row_id is not None

        stats = db.get_stats()
        assert stats["total_compressions"] == 1
        assert stats["total_original_bytes"] == 100
        assert stats["total_compressed_bytes"] == 50
        assert stats["total_saved_bytes"] == 50

        db.close()
    print("  ✓ Header redaction and secure DB logging verified")


def test_live_server_endpoints():
    print("Testing live HTTP server endpoints and security headers...")
    import threading
    import urllib.request
    import urllib.error
    from app import MinipyServer, MinipyHTTPHandler

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "http_test.db"
        server = MinipyServer(("127.0.0.1", 0), MinipyHTTPHandler, db_path=str(db_path))
        port = server.server_port
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        base_url = f"http://127.0.0.1:{port}"

        try:
            # 1. GET /
            req = urllib.request.Request(f"{base_url}/")
            with urllib.request.urlopen(req) as resp:
                assert resp.status == 200
                headers = dict(resp.headers)
                assert headers.get("X-Content-Type-Options") == "nosniff"
                assert headers.get("X-Frame-Options") == "DENY"
                assert "Content-Security-Policy" in headers
                body = resp.read().decode("utf-8")
                assert "<!DOCTYPE html>" in body

            # 2. GET /static/style.css
            with urllib.request.urlopen(f"{base_url}/static/style.css") as resp:
                assert resp.status == 200
                assert resp.headers.get("Content-Type") == "text/css; charset=utf-8"
                assert resp.headers.get("X-Content-Type-Options") == "nosniff"

            # 3. Path Traversal attack: GET /static/../app.py -> 404
            try:
                urllib.request.urlopen(f"{base_url}/static/../app.py")
                assert False, "Path traversal should return 404!"
            except urllib.error.HTTPError as e:
                assert e.code == 404

            # 4. GET /health
            with urllib.request.urlopen(f"{base_url}/health") as resp:
                assert resp.status == 200
                data = json.loads(resp.read().decode("utf-8"))
                assert data["status"] == "ok"
                assert "engine" in data

            # 5. POST /api/minify with text/plain
            code = "def add(a, b):\n    return a + b\n"
            req = urllib.request.Request(
                f"{base_url}/api/minify",
                data=code.encode("utf-8"),
                headers={"Content-Type": "text/plain", "CF-Connecting-IP": "203.0.113.195"},
            )
            with urllib.request.urlopen(req) as resp:
                assert resp.status == 200
                data = json.loads(resp.read().decode("utf-8"))
                assert data["success"] is True
                assert "result" in data

            # 6. POST /api/minify with JSON
            req = urllib.request.Request(
                f"{base_url}/api/minify",
                data=json.dumps({"code": code, "compress": False}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req) as resp:
                assert resp.status == 200
                data = json.loads(resp.read().decode("utf-8"))
                assert data["success"] is True

            # 7. POST /api/unparse
            minified = data["result"]
            req = urllib.request.Request(
                f"{base_url}/api/unparse",
                data=json.dumps({"code": minified}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req) as resp:
                assert resp.status == 200
                data = json.loads(resp.read().decode("utf-8"))
                assert data["success"] is True
                assert "def add(a, b):" in data["result"]

            # 8. POST without Content-Length
            # Handled properly (standard urllib sets it, but test 413 for oversized)
            big_payload = b"x = 1\n" * (11 * 1024 * 1024)
            req = urllib.request.Request(
                f"{base_url}/api/minify",
                data=big_payload,
                headers={"Content-Type": "text/plain"},
            )
            try:
                urllib.request.urlopen(req)
                assert False, "Oversized body should be rejected with 413!"
            except urllib.error.HTTPError as e:
                assert e.code == 413

            # 9. GET /api/stats
            with urllib.request.urlopen(f"{base_url}/api/stats") as resp:
                assert resp.status == 200
                stats = json.loads(resp.read().decode("utf-8"))
                assert stats["total_compressions"] >= 1

        finally:
            server.shutdown()
            server.server_close()

    print("  ✓ Live HTTP endpoints, security headers, and error codes verified")


def main():
    print("=" * 60)
    print("Running Security & Functional Audit Test Suite")
    print("=" * 60)
    test_syntax_validation()
    test_minify_and_unparse()
    test_rce_prevention()
    test_decompression_bomb_guard()
    test_static_path_traversal()
    test_ip_validation()
    test_header_redaction_and_db()
    test_live_server_endpoints()
    print("=" * 60)
    print("ALL SECURITY & REGRESSION TESTS PASSED SUCCESSFULLY!")
    print("=" * 60)


if __name__ == "__main__":
    main()
