from __future__ import annotations

import ast
import base64
import bz2
import gzip
import lzma
import re
import time
import zlib
from typing import Any, Callable, Dict, Optional, Tuple

import minipy3
from minipy3 import minimize

MAX_DECOMPRESSED_SIZE = 15 * 1024 * 1024  # 15 MB safety limit against decompression bombs
MAX_INPUT_CODE_SIZE = 10 * 1024 * 1024  # 10 MB limit for domain processing

DECOMPRESSORS: Dict[str, Callable[[bytes], bytes]] = {
    "lzma": lzma.decompress,
    "zlib": zlib.decompress,
    "gzip": gzip.decompress,
    "bz2": bz2.decompress,
}


class MinifyResult:
    def __init__(
        self,
        result: str,
        original_size: int,
        compressed_size: int,
        duration_ms: float,
        method: str,
        options: Dict[str, Any],
    ):
        self.result = result
        self.original_size = original_size
        self.compressed_size = compressed_size
        self.duration_ms = duration_ms
        self.bytes_saved = max(0, original_size - compressed_size)
        self.ratio = (1.0 - (compressed_size / original_size)) if original_size > 0 else 0.0
        self.ratio_percent = round(self.ratio * 100, 2)
        self.method = method
        self.options = options

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": True,
            "result": self.result,
            "original_size": self.original_size,
            "compressed_size": self.compressed_size,
            "bytes_saved": self.bytes_saved,
            "ratio_percent": self.ratio_percent,
            "duration_ms": round(self.duration_ms, 2),
            "method": self.method,
            "options": self.options,
            "engine_version": minipy3.__version__,
        }


class MinipyService:
    """Core domain logic for minifying and unparsing Python code."""

    @staticmethod
    def validate_syntax(code: str) -> Optional[Dict[str, Any]]:
        """Parses AST to check syntax before processing.
        
        Returns None if syntax is valid, or error dictionary with line and column.
        """
        try:
            ast.parse(code)
            return None
        except SyntaxError as e:
            return {
                "error": "SyntaxError",
                "message": e.msg,
                "line": e.lineno,
                "column": e.offset,
                "text": (e.text or "").strip(),
            }
        except RecursionError:
            return {
                "error": "ComplexityError",
                "message": "Code is too deeply nested to parse",
                "line": None,
                "column": None,
                "text": "",
            }
        except Exception as e:
            return {
                "error": "ParseError",
                "message": str(e),
                "line": None,
                "column": None,
                "text": "",
            }

    @classmethod
    def _safe_decompress(cls, code: str) -> Optional[str]:
        """Safely decompresses minipy3-style compression wrappers without exec/eval.
        
        Strictly inspects AST structure and literal constants to protect against RCE.
        """
        if "decompress" not in code:
            return None

        # 1. Attempt AST-based safe extraction
        try:
            tree = ast.parse(code)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue

                func = node.func
                # Look for .decompress call
                if not (isinstance(func, ast.Attribute) and func.attr == "decompress"):
                    continue

                # Identify compression module
                mod_name: Optional[str] = None
                if isinstance(func.value, ast.Call):
                    call_func = func.value.func
                    is_import = (
                        (isinstance(call_func, ast.Name) and call_func.id in ("__import__", "i"))
                        or (isinstance(call_func, ast.Attribute) and call_func.attr == "__import__")
                    )
                    if is_import and func.value.args:
                        arg0 = func.value.args[0]
                        if isinstance(arg0, ast.Constant) and isinstance(arg0.value, str):
                            mod_name = arg0.value.lower()
                elif isinstance(func.value, ast.Name):
                    mod_name = func.value.id.lower()

                if not mod_name or mod_name not in DECOMPRESSORS or not node.args:
                    continue

                payload_arg = node.args[0]
                comp_bytes: Optional[bytes] = None

                # Subcase A: base64.b85decode(b'...') or i('base64').b85decode(b'...')
                if isinstance(payload_arg, ast.Call):
                    b85_func = payload_arg.func
                    is_b85 = (
                        (isinstance(b85_func, ast.Attribute) and b85_func.attr == "b85decode")
                        or (isinstance(b85_func, ast.Name) and b85_func.id == "b85decode")
                    )
                    if is_b85 and payload_arg.args:
                        b85_data_arg = payload_arg.args[0]
                        if isinstance(b85_data_arg, ast.Constant):
                            raw_val = b85_data_arg.value
                            if isinstance(raw_val, (bytes, str)):
                                b85_input = raw_val.encode("ascii") if isinstance(raw_val, str) else raw_val
                                comp_bytes = base64.b85decode(b85_input)
                # Subcase B: raw compressed bytes constant b'...'
                elif isinstance(payload_arg, ast.Constant):
                    if isinstance(payload_arg.value, bytes):
                        comp_bytes = payload_arg.value

                if comp_bytes is not None:
                    decompressed = DECOMPRESSORS[mod_name](comp_bytes)
                    if len(decompressed) > MAX_DECOMPRESSED_SIZE:
                        raise ValueError("Decompressed payload exceeds maximum safe limit")
                    return decompressed.decode("utf-8", errors="replace")
        except Exception:
            pass

        # 2. Regex fallback for minor formatting variations (safe literal evaluation only)
        try:
            pattern_b85 = re.compile(
                r"""(?:i|__import__)\s*\(\s*['"](lzma|zlib|gzip|bz2)['"]\s*\)\s*\.decompress\s*\(\s*(?:i|__import__)\s*\(\s*['"]base64['"]\s*\)\s*\.b85decode\s*\(\s*(b['"].*?['"])\s*\)\s*\)""",
                re.DOTALL,
            )
            m = pattern_b85.search(code)
            if m:
                mod_name = m.group(1).lower()
                b85_bytes = ast.literal_eval(m.group(2))
                decompressed = DECOMPRESSORS[mod_name](base64.b85decode(b85_bytes))
                if len(decompressed) > MAX_DECOMPRESSED_SIZE:
                    raise ValueError("Decompressed payload exceeds maximum safe limit")
                return decompressed.decode("utf-8", errors="replace")

            pattern_raw = re.compile(
                r"""(?:i|__import__)\s*\(\s*['"](lzma|zlib|gzip|bz2)['"]\s*\)\s*\.decompress\s*\(\s*(b['"].*?['"])\s*\)""",
                re.DOTALL,
            )
            m = pattern_raw.search(code)
            if m:
                mod_name = m.group(1).lower()
                raw_bytes = ast.literal_eval(m.group(2))
                decompressed = DECOMPRESSORS[mod_name](raw_bytes)
                if len(decompressed) > MAX_DECOMPRESSED_SIZE:
                    raise ValueError("Decompressed payload exceeds maximum safe limit")
                return decompressed.decode("utf-8", errors="replace")
        except Exception:
            pass

        return None

    @classmethod
    def process_minify(
        cls,
        code: str,
        compress: bool = True,
        compress_required: bool = False,
        remove_docstrings: bool = False,
        remove_literals: bool = False,
    ) -> Tuple[Optional[MinifyResult], Optional[Dict[str, Any]]]:
        """Minifies python code using minipy3.
        
        Returns (MinifyResult, None) on success, or (None, error_dict) on failure.
        """
        if not code or not code.strip():
            return None, {"error": "EmptyInput", "message": "Code input cannot be empty"}

        orig_bytes = code.encode("utf-8")
        orig_size = len(orig_bytes)
        if orig_size > MAX_INPUT_CODE_SIZE:
            return None, {"error": "PayloadTooLarge", "message": "Code exceeds maximum size limit (10MB)"}

        # Check for syntax errors early for friendly feedback
        syntax_err = cls.validate_syntax(code)
        if syntax_err:
            return None, syntax_err

        options = {
            "compress": compress,
            "compress_required": compress_required,
            "remove_docstrings": remove_docstrings,
            "remove_literals": remove_literals,
        }

        start_time = time.perf_counter()
        try:
            minified = minimize(
                code,
                compress=compress,
                compress_required=compress_required,
                remove_docstrings=remove_docstrings,
                remove_literals=remove_literals,
            )
            duration_ms = (time.perf_counter() - start_time) * 1000.0

            comp_bytes = minified.encode("utf-8")
            comp_size = len(comp_bytes)

            method = "ast_minified"
            if "b85decode" in minified or "decompress(" in minified:
                if "lzma" in minified:
                    method = "lzma+base85"
                elif "zlib" in minified:
                    method = "zlib+base85"
                elif "gzip" in minified:
                    method = "gzip+base85"
                elif "bz2" in minified:
                    method = "bz2+base85"
                else:
                    method = "compressed"

            result = MinifyResult(
                result=minified,
                original_size=orig_size,
                compressed_size=comp_size,
                duration_ms=duration_ms,
                method=method,
                options=options,
            )
            return result, None
        except RecursionError:
            return None, {
                "error": "ComplexityError",
                "message": "Code is too deeply nested to minify",
            }
        except Exception as e:
            return None, {"error": "MinifyError", "message": str(e)}

    @classmethod
    def process_unparse(cls, code: str) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
        """Decompresses (if needed) and formats/unparses Python code back to standard syntax.
        
        Zero execution of untrusted code: uses safe AST parsing and decompression only.
        """
        if not code or not code.strip():
            return None, {"error": "EmptyInput", "message": "Code input cannot be empty"}

        if len(code.encode("utf-8")) > MAX_INPUT_CODE_SIZE:
            return None, {"error": "PayloadTooLarge", "message": "Code exceeds maximum size limit (10MB)"}

        start_time = time.perf_counter()
        target_code = code

        # Safely extract and decompress without calling exec/eval
        decompressed = cls._safe_decompress(code)
        if decompressed is not None:
            target_code = decompressed

        try:
            tree = ast.parse(target_code)
            unparsed = ast.unparse(tree).strip() + "\n"
            duration_ms = (time.perf_counter() - start_time) * 1000.0
            return {
                "success": True,
                "result": unparsed,
                "original_size": len(code.encode("utf-8")),
                "unparsed_size": len(unparsed.encode("utf-8")),
                "duration_ms": round(duration_ms, 2),
            }, None
        except SyntaxError as e:
            return None, {
                "error": "SyntaxError",
                "message": e.msg,
                "line": e.lineno,
                "column": e.offset,
            }
        except RecursionError:
            return None, {
                "error": "ComplexityError",
                "message": "Code is too deeply nested to unparse",
            }
        except Exception as e:
            return None, {"error": "UnparseError", "message": str(e)}

