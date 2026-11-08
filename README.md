# minipy3 Web Service

A lightweight web service and REST API for Python 3 code minimization using minipy3.

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
cd server
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
