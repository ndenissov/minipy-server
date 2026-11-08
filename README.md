# minipy3 Web Service

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python: >=3.9](https://img.shields.io/badge/Python->=3.9-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Engine: minipy3](https://img.shields.io/badge/Engine-minipy3%20v1.3.0-informational?logo=pypi&logoColor=white)](https://pypi.org/project/minipy3/)
[![Nix: Flake](https://img.shields.io/badge/Nix-Flake%20Ready-5277C3?logo=nixos&logoColor=white)](flake.nix)
[![Architecture: Python Stdlib](https://img.shields.io/badge/Architecture-Python%20Stdlib-2ba?logo=python&logoColor=white)](#features)
[![Dependencies: Zero Web Frameworks](https://img.shields.io/badge/Dependencies-Zero%20Web%20Frameworks-success)](#features)
[![Service Status: Active](https://img.shields.io/badge/Status-Active-brightgreen)](https://minipy.dep.ovh)

A lightweight web service and REST API for Python 3 code minimization using minipy3.

Author: Nikita Denissov

## Features

* Web interface:
  * Two-pane editor with syntax highlighting, line and byte counters, and drag-and-drop file upload.
  * Toggles for minipy3 flags: smart compression (lzma, zlib, gzip, bz2 with Base85), force compression, docstring stripping, and unused literal removal.
  * Swap action to move minified code back into the input field for testing or unparsing.
  * Unparse action to format minified or compressed code back to readable Python.
  * Keyboard shortcut: Ctrl+Enter to run minimization.
  * Quick copy and .min.py file download.
* REST API:
  * Fast JSON and raw text endpoints for CLI integration.
  * Automatic syntax error reporting with line and column numbers.
* Zero external web dependencies: runs using the Python standard library.

## Running the Service

### Using uv

```bash
uv venv
uv pip install minipy3
uv run python app.py --port 8383 --host 127.0.0.1
```

### Using Nix

```bash
# Run via flake
nix run

# Enter dev shell
nix develop
```

## CLI Usage

### Minify a Python file:

```bash
curl -s -X POST http://127.0.0.1:8383/api/minify \
  --data-binary @script.py \
  -H "Content-Type: text/plain" | jq -r .result > script.min.py
```

### Minify via JSON payload:

```bash
curl -s -X POST http://127.0.0.1:8383/api/minify \
  -H "Content-Type: application/json" \
  -d '{"code":"def f(x):\n    return x * 100000","compress":true,"remove_docstrings":true}'
```

### Decompress / unparse code:

```bash
curl -s -X POST http://127.0.0.1:8383/api/unparse \
  -H "Content-Type: application/json" \
  -d '{"code":"i=__import__;exec(i(\"zlib\").decompress(...))"}'
```

### Health check:

```bash
curl -s http://127.0.0.1:8383/health
```

### SEO endpoints:

* Sitemap: `/sitemap.xml`
* Robots: `/robots.txt`
