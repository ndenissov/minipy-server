from __future__ import annotations

import argparse
import email
import ipaddress
import json
import os
import posixpath
import signal
import sys
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from urllib.parse import parse_qs, unquote, urlparse

import minipy3
from db import Database
from service import MinipyService

START_TIME = time.time()
STATIC_DIR = (Path(__file__).parent / "static").resolve()
MAX_REQUEST_BODY_SIZE = 10 * 1024 * 1024  # 10 MB maximum request payload

ALLOWED_STATIC_EXTENSIONS = {
    ".html",
    ".css",
    ".js",
    ".svg",
    ".ico",
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".woff",
    ".woff2",
    ".txt",
    ".xml",
}

MIME_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".txt": "text/plain; charset=utf-8",
    ".xml": "application/xml; charset=utf-8",
}

SENSITIVE_HEADERS = {
    "authorization",
    "cookie",
    "set-cookie",
    "proxy-authorization",
    "x-api-key",
    "x-auth-token",
    "cf-access-jwt-assertion",
    "cf-access-token",
    "x-csrf-token",
    "x-xsrf-token",
}

CSP_HEADER = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self'"
)


def _is_valid_ip(ip_str: str) -> bool:
    try:
        # Disallow control characters, newlines, and spaces
        if any(c in ip_str for c in "\r\n\t\x00 "):
            return False
        ipaddress.ip_address(ip_str)
        return True
    except (ValueError, TypeError):
        return False


class MinipyHTTPHandler(BaseHTTPRequestHandler):
    server_version = "minipy-service"
    sys_version = ""

    def version_string(self) -> str:
        return "minipy-service"

    @property
    def db(self) -> Database:
        return self.server.db  # type: ignore

    def _set_headers(
        self,
        status: int = 200,
        content_type: str = "application/json; charset=utf-8",
        extra_headers: Optional[Dict[str, str]] = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("X-Author", "Nikita Denissov")
        # Defense-in-depth security headers
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        self.send_header("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS, HEAD")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Requested-With")
        if extra_headers:
            for k, v in extra_headers.items():
                self.send_header(k, v)
        self.end_headers()

    def _send_json(self, data: Any, status: int = 200) -> None:
        # Minified JSON output with compact separators
        payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self._set_headers(
            status=status,
            content_type="application/json; charset=utf-8",
            extra_headers={"Cache-Control": "no-store"},
        )
        self.wfile.write(payload)

    def _send_error_json(
        self, error: str, message: str, status: int = 400, extra: Optional[Dict[str, Any]] = None
    ) -> None:
        err = {"success": False, "error": error, "message": message}
        if extra:
            err.update(extra)
        self._send_json(err, status=status)

    def _get_client_ip(self) -> str:
        """Extract and validate client IP, accounting for Cloudflare Zero Trust and proxies safely."""
        for header_name in ("CF-Connecting-IP", "True-Client-IP", "X-Real-IP"):
            val = self.headers.get(header_name)
            if val:
                cleaned = val.strip()
                if _is_valid_ip(cleaned):
                    return cleaned

        xff = self.headers.get("X-Forwarded-For")
        if xff:
            for part in xff.split(","):
                candidate = part.strip()
                if _is_valid_ip(candidate):
                    return candidate

        if self.client_address and len(self.client_address) > 0:
            candidate = str(self.client_address[0]).strip()
            if _is_valid_ip(candidate):
                return candidate

        return "127.0.0.1"

    def _get_request_headers_dict(self) -> Dict[str, str]:
        """Convert incoming HTTP headers into a sanitized dictionary safe for audit storage.
        
        Redacts sensitive credentials/cookies and caps entry count and lengths to prevent DB bloat.
        """
        sanitized: Dict[str, str] = {}
        for idx, (k, v) in enumerate(self.headers.items()):
            if idx >= 40:
                sanitized["_truncated"] = "true"
                break
            clean_key = str(k)[:100]
            if clean_key.lower() in SENSITIVE_HEADERS:
                sanitized[clean_key] = "[REDACTED]"
            else:
                sanitized[clean_key] = str(v)[:500]
        return sanitized

    def _resolve_static_path(self, raw_path: str) -> Optional[Tuple[Path, str]]:
        """Safely validates and resolves static file requests within STATIC_DIR.
        
        Guarantees protection against directory traversal, path prefix overlap, and hidden files.
        Returns (Path, content_type) on success, or None on failure.
        """
        normalized = posixpath.normpath(raw_path)
        if not normalized.startswith("/static/"):
            return None

        rel_str = normalized[len("/static/"):].lstrip("/")
        rel_str = unquote(rel_str)

        if not rel_str or rel_str.startswith(".") or "/." in rel_str or "\\." in rel_str:
            return None

        try:
            target_path = (STATIC_DIR / rel_str).resolve()
            if not target_path.is_relative_to(STATIC_DIR):
                return None
        except (ValueError, Exception):
            return None

        if not target_path.is_file():
            return None

        suffix = target_path.suffix.lower()
        if suffix not in ALLOWED_STATIC_EXTENSIONS:
            return None

        content_type = MIME_TYPES.get(suffix, "application/octet-stream")
        return target_path, content_type

    def do_OPTIONS(self) -> None:
        self._set_headers(204)

    def do_HEAD(self) -> None:
        parsed_url = urlparse(self.path)
        path = posixpath.normpath(parsed_url.path)

        if path in ("/", "/index.html"):
            index_path = STATIC_DIR / "index.html"
            if index_path.is_file():
                self._set_headers(
                    200,
                    "text/html; charset=utf-8",
                    {
                        "Cache-Control": "no-cache",
                        "Content-Security-Policy": CSP_HEADER,
                    },
                )
            else:
                self._set_headers(404, "application/json; charset=utf-8")
            return

        if path in ("/robots.txt", "/sitemap.xml"):
            c_type = "text/plain; charset=utf-8" if path == "/robots.txt" else "application/xml; charset=utf-8"
            self._set_headers(200, c_type, {"Cache-Control": "public, max-age=86400"})
            return

        if path.startswith("/static/"):
            resolved = self._resolve_static_path(path)
            if resolved is not None:
                _, content_type = resolved
                self._set_headers(
                    200,
                    content_type,
                    {"Cache-Control": "public, max-age=31536000, immutable"},
                )
                return
            self._set_headers(404, "application/json; charset=utf-8")
            return

        if path in ("/health", "/api/health", "/api/stats"):
            self._set_headers(200, "application/json; charset=utf-8")
            return

        self._set_headers(404, "application/json; charset=utf-8")

    def do_GET(self) -> None:
        parsed_url = urlparse(self.path)
        path = posixpath.normpath(parsed_url.path)

        if path in ("/", "/index.html"):
            index_path = STATIC_DIR / "index.html"
            if index_path.is_file():
                content = index_path.read_bytes()
                self._set_headers(
                    200,
                    "text/html; charset=utf-8",
                    {
                        "Cache-Control": "no-cache",
                        "Content-Security-Policy": CSP_HEADER,
                    },
                )
                self.wfile.write(content)
            else:
                self._send_error_json("NotFound", "index.html not found", 404)
            return

        if path == "/robots.txt":
            robots_path = STATIC_DIR / "robots.txt"
            if robots_path.is_file():
                content = robots_path.read_bytes()
                self._set_headers(200, "text/plain; charset=utf-8", {"Cache-Control": "public, max-age=86400"})
                self.wfile.write(content)
            else:
                self._send_error_json("NotFound", "robots.txt not found", 404)
            return

        if path == "/sitemap.xml":
            sitemap_path = STATIC_DIR / "sitemap.xml"
            if sitemap_path.is_file():
                content = sitemap_path.read_bytes()
                self._set_headers(200, "application/xml; charset=utf-8", {"Cache-Control": "public, max-age=86400"})
                self.wfile.write(content)
            else:
                self._send_error_json("NotFound", "sitemap.xml not found", 404)
            return

        if path.startswith("/static/"):
            resolved = self._resolve_static_path(path)
            if resolved is not None:
                target_path, content_type = resolved
                self._set_headers(
                    200,
                    content_type,
                    {"Cache-Control": "public, max-age=31536000, immutable"},
                )
                self.wfile.write(target_path.read_bytes())
                return
            self._send_error_json("NotFound", "Static asset not found", 404)
            return

        if path in ("/health", "/api/health"):
            self._send_json({
                "status": "ok",
                "uptime_seconds": round(time.time() - START_TIME, 1),
                "engine": "minipy3",
                "version": minipy3.__version__,
                "author": "Nikita Denissov",
            })
            return

        if path == "/api/stats":
            stats = self.db.get_stats()
            stats["engine_version"] = minipy3.__version__
            stats["uptime_seconds"] = round(time.time() - START_TIME, 1)
            stats["author"] = "Nikita Denissov"
            self._send_json(stats)
            return

        # 404 fallback
        self._send_error_json("NotFound", f"Endpoint {path} not found", 404)

    def do_POST(self) -> None:
        parsed_url = urlparse(self.path)
        path = posixpath.normpath(parsed_url.path)

        if path not in ("/api/minify", "/api/unparse"):
            self._send_error_json("NotFound", f"Endpoint {path} not found", 404)
            return

        content_length_header = self.headers.get("Content-Length")
        if content_length_header is None:
            self._send_error_json("LengthRequired", "Content-Length header required", 411)
            return

        try:
            content_length = int(content_length_header)
        except ValueError:
            self._send_error_json("BadRequest", "Invalid Content-Length header", 400)
            return

        if content_length < 0:
            self._send_error_json("BadRequest", "Negative Content-Length is invalid", 400)
            return

        if content_length > MAX_REQUEST_BODY_SIZE:
            self.close_connection = True
            self._send_error_json(
                "PayloadTooLarge",
                f"Max request body size is {MAX_REQUEST_BODY_SIZE // (1024 * 1024)}MB",
                413,
                extra={"headers": {"Connection": "close"}},
            )
            return

        if content_length == 0:
            self._send_error_json("EmptyInput", "No Python code provided in request body", 400)
            return

        try:
            body = self.rfile.read(content_length)
        except Exception as e:
            self._send_error_json("BadRequest", f"Failed to read request body: {e}", 400)
            return

        content_type = self.headers.get("Content-Type", "")
        query_params = parse_qs(parsed_url.query)

        code, options = self._parse_request_body(body, content_type, query_params)

        if not code or not code.strip():
            self._send_error_json("EmptyInput", "No Python code provided in request body", 400)
            return

        if path == "/api/minify":
            self._handle_minify(code, options)
        elif path == "/api/unparse":
            self._handle_unparse(code)

    def _parse_request_body(
        self, body: bytes, content_type: str, query_params: Dict[str, list]
    ) -> Tuple[str, Dict[str, Any]]:
        """Parses JSON, multipart/form-data, or plain text bodies."""
        options: Dict[str, Any] = {
            "compress": True,
            "compress_required": False,
            "remove_docstrings": False,
            "remove_literals": False,
        }

        if "compress" in query_params and query_params["compress"]:
            options["compress"] = query_params["compress"][0].lower() not in ("0", "false", "no")
        if "compress_required" in query_params and query_params["compress_required"]:
            options["compress_required"] = query_params["compress_required"][0].lower() in ("1", "true", "yes")
        if "remove_docstrings" in query_params or "no_docstrings" in query_params:
            k = "remove_docstrings" if "remove_docstrings" in query_params else "no_docstrings"
            if query_params[k]:
                options["remove_docstrings"] = query_params[k][0].lower() in ("1", "true", "yes")
        if "remove_literals" in query_params and query_params["remove_literals"]:
            options["remove_literals"] = query_params["remove_literals"][0].lower() in ("1", "true", "yes")

        code = ""

        if "application/json" in content_type:
            try:
                data = json.loads(body.decode("utf-8", errors="replace"))
                if isinstance(data, dict):
                    raw_code = data.get("code", "")
                    if isinstance(raw_code, str):
                        code = raw_code
                    for opt_key in ("compress", "compress_required", "remove_docstrings", "remove_literals"):
                        if opt_key in data:
                            options[opt_key] = bool(data[opt_key])
            except Exception:
                code = ""
        elif "multipart/form-data" in content_type:
            try:
                msg_bytes = b"Content-Type: " + content_type.encode("latin-1") + b"\r\n\r\n" + body
                msg = email.message_from_bytes(msg_bytes)
                if msg.is_multipart():
                    for part in msg.walk():
                        if part.get_content_disposition() == "form-data":
                            name = part.get_param("name", header="content-disposition")
                            if name in ("file", "code"):
                                payload = part.get_payload(decode=True)
                                if payload and isinstance(payload, bytes):
                                    code = payload.decode("utf-8", errors="replace")
                            elif name in options:
                                payload = part.get_payload(decode=True)
                                if isinstance(payload, bytes):
                                    val_str = payload.decode("utf-8", errors="replace").strip().lower()
                                else:
                                    val_str = str(part.get_payload() or "").strip().lower()
                                options[name] = val_str in ("1", "true", "yes", "on")
            except Exception as e:
                pass
        else:
            code = body.decode("utf-8", errors="replace")

        return code, options

    def _handle_minify(self, code: str, options: Dict[str, Any]) -> None:
        result, error = MinipyService.process_minify(
            code=code,
            compress=options["compress"],
            compress_required=options["compress_required"],
            remove_docstrings=options["remove_docstrings"],
            remove_literals=options["remove_literals"],
        )

        if error:
            status_code = 413 if error.get("error") == "PayloadTooLarge" else 400
            self._send_error_json(
                error=error.get("error", "MinifyError"),
                message=error.get("message", "Failed to minify code"),
                status=status_code,
                extra=error,
            )
            return

        client_ip = self._get_client_ip()
        headers = self._get_request_headers_dict()

        # Database logging: stores ONLY minified result, NEVER raw original code
        self.db.log_success(
            client_ip=client_ip,
            headers=headers,
            compressed_result=result.result,
            original_size=result.original_size,
            compressed_size=result.compressed_size,
            ratio=result.ratio,
            duration_ms=result.duration_ms,
            options=result.options,
        )

        self._send_json(result.to_dict())

    def _handle_unparse(self, code: str) -> None:
        res, error = MinipyService.process_unparse(code)
        if error:
            status_code = 413 if error.get("error") == "PayloadTooLarge" else 400
            self._send_error_json(
                error=error.get("error", "UnparseError"),
                message=error.get("message", "Failed to unparse code"),
                status=status_code,
                extra=error,
            )
            return
        self._send_json(res)

    def log_message(self, format: str, *args: Any) -> None:
        client_ip = self._get_client_ip()
        try:
            msg = format % args
        except Exception:
            msg = str(format)
        # Prevent log injection / carriage return log splitting
        clean_msg = msg.replace("\r", " ").replace("\n", " ").strip()
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        sys.stderr.write(f"[{timestamp}] {client_ip} - {clean_msg}\n")


class MinipyServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    timeout = 30.0  # Close stalled or slowloris idle sockets after 30 seconds

    def __init__(self, server_address, RequestHandlerClass, db_path: str):
        self.db = Database(db_path)
        super().__init__(server_address, RequestHandlerClass)


def run_server(host: str = "127.0.0.1", port: int = 8383, db_path: str = "./data/minipy.db") -> None:
    server_address = (host, port)
    httpd = MinipyServer(server_address, MinipyHTTPHandler, db_path=db_path)

    def shutdown_handler(signum, frame):
        print("\n[INFO] Gracefully shutting down minipy service...")
        httpd.db.close()
        httpd.shutdown()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown_handler)
    signal.signal(signal.SIGTERM, shutdown_handler)

    print("=" * 60)
    print(f"  MINIPY3 WEB SERVICE  [Engine: v{minipy3.__version__}]")
    print(f"  Author:       Nikita Denissov")
    print(f"  Listening on: http://{host}:{port}")
    print(f"  Database:     {Path(db_path).resolve()}")
    print("=" * 60)

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.db.close()


def main():
    parser = argparse.ArgumentParser(description="Minipy3 Lightweight Web Service")
    parser.add_argument(
        "--host",
        default=os.environ.get("MINIPY_HOST", "127.0.0.1"),
        help="Bind address (default: 127.0.0.1 or env MINIPY_HOST)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("MINIPY_PORT", "8383")),
        help="Port to listen on (default: 8383 or env MINIPY_PORT)",
    )
    parser.add_argument(
        "--db",
        default=os.environ.get("MINIPY_DB_PATH", "./data/minipy.db"),
        help="SQLite database path (default: ./data/minipy.db or env MINIPY_DB_PATH)",
    )

    args = parser.parse_args()
    run_server(host=args.host, port=args.port, db_path=args.db)


if __name__ == "__main__":
    main()
