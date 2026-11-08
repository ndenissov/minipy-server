from __future__ import annotations

import json
import os
import sqlite3
import threading
from pathlib import Path
from typing import Any, Dict, Optional


class Database:
    """Lightweight SQLite database manager for Minipy3 service.
    
    Stores successful minification requests:
    - Client IP (accounting for Cloudflare / proxies)
    - Request Headers (JSON, sensitive headers redacted)
    - ONLY the compressed result (never original code)
    - Compression statistics (original size, minified size, ratio, duration)
    """

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            # Secure directory permissions (owner-only access)
            os.chmod(self.db_path.parent, 0o700)
        except OSError:
            pass

        self._local = threading.local()
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn") or self._local.conn is None:
            is_new_db = not self.db_path.exists()
            conn = sqlite3.connect(
                str(self.db_path),
                timeout=15.0,
                check_same_thread=False
            )
            conn.row_factory = sqlite3.Row
            # Secure file permissions on newly created database file
            if is_new_db:
                try:
                    os.chmod(self.db_path, 0o600)
                except OSError:
                    pass

            # WAL mode for superior concurrency and low overhead
            conn.execute("PRAGMA journal_mode = WAL;")
            conn.execute("PRAGMA synchronous = NORMAL;")
            conn.execute("PRAGMA busy_timeout = 5000;")
            self._local.conn = conn
        return self._local.conn

    def _init_db(self) -> None:
        conn = self._get_connection()
        with conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS minify_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    client_ip TEXT NOT NULL,
                    headers TEXT NOT NULL,
                    compressed_result TEXT NOT NULL,
                    original_size INTEGER NOT NULL,
                    compressed_size INTEGER NOT NULL,
                    ratio REAL NOT NULL,
                    duration_ms REAL NOT NULL,
                    options TEXT NOT NULL
                );
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_minify_logs_created 
                ON minify_logs(created_at);
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_minify_logs_ip 
                ON minify_logs(client_ip);
            """)

    def log_success(
        self,
        client_ip: str,
        headers: Dict[str, str],
        compressed_result: str,
        original_size: int,
        compressed_size: int,
        ratio: float,
        duration_ms: float,
        options: Dict[str, Any],
    ) -> Optional[int]:
        """Saves successful compression info to SQLite.
        
        Guarantees that raw source code is NEVER stored.
        """
        try:
            conn = self._get_connection()
            clean_ip = str(client_ip).strip()[:64]
            # Defense against extreme log bloat
            safe_result = compressed_result if len(compressed_result) <= 2 * 1024 * 1024 else compressed_result[: 2 * 1024 * 1024] + " ... [TRUNCATED]"
            headers_json = json.dumps(headers, ensure_ascii=False)
            options_json = json.dumps(options, ensure_ascii=False)
            with conn:
                cursor = conn.execute(
                    """
                    INSERT INTO minify_logs (
                        client_ip, headers, compressed_result,
                        original_size, compressed_size, ratio,
                        duration_ms, options
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        clean_ip,
                        headers_json,
                        safe_result,
                        max(0, int(original_size)),
                        max(0, int(compressed_size)),
                        round(float(ratio), 4),
                        round(float(duration_ms), 2),
                        options_json,
                    ),
                )
                return cursor.lastrowid
        except Exception as e:
            # Database logging shouldn't crash client response
            print(f"[DB ERROR] Failed to log minify request: {e}")
            return None

    def get_stats(self) -> Dict[str, Any]:
        """Aggregated service statistics."""
        try:
            conn = self._get_connection()
            cursor = conn.execute("""
                SELECT 
                    COUNT(*) as total_requests,
                    TOTAL(original_size) as total_orig_bytes,
                    TOTAL(compressed_size) as total_comp_bytes,
                    AVG(ratio) as avg_ratio,
                    AVG(duration_ms) as avg_duration_ms,
                    MAX(created_at) as last_request_at
                FROM minify_logs
            """)
            row = cursor.fetchone()
            if row and row["total_requests"] > 0:
                total_reqs = row["total_requests"]
                orig_bytes = int(row["total_orig_bytes"])
                comp_bytes = int(row["total_comp_bytes"])
                saved_bytes = max(0, orig_bytes - comp_bytes)
                return {
                    "total_compressions": total_reqs,
                    "total_original_bytes": orig_bytes,
                    "total_compressed_bytes": comp_bytes,
                    "total_saved_bytes": saved_bytes,
                    "average_ratio_percent": round(float(row["avg_ratio"] or 0.0) * 100, 2),
                    "average_duration_ms": round(float(row["avg_duration_ms"] or 0.0), 2),
                    "last_request_at": row["last_request_at"],
                }
            return {
                "total_compressions": 0,
                "total_original_bytes": 0,
                "total_compressed_bytes": 0,
                "total_saved_bytes": 0,
                "average_ratio_percent": 0.0,
                "average_duration_ms": 0.0,
                "last_request_at": None,
            }
        except Exception as e:
            print(f"[DB ERROR] Failed to fetch stats: {e}")
            return {"error": str(e)}

    def close(self) -> None:
        if hasattr(self._local, "conn") and self._local.conn is not None:
            try:
                self._local.conn.close()
            except Exception:
                pass
            self._local.conn = None

