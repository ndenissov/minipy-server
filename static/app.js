/**
 * minipy3 web frontend logic (unminified source)
 * Author: Nikita Denissov
 */

(function () {
  "use strict";

  var inEl = document.getElementById("editor-input");
  var inHigh = document.getElementById("editor-input-highlight");
  var inHighPre = inHigh ? inHigh.parentElement : null;
  var outEl = document.getElementById("editor-output");
  var inMeta = document.getElementById("input-meta");
  var outMeta = document.getElementById("output-meta");
  var inName = document.getElementById("input-filename");
  var outName = document.getElementById("output-filename");
  var ratioBadge = document.getElementById("badge-ratio");

  var btnMin = document.getElementById("btn-minify");
  var btnSwap = document.getElementById("btn-swap");
  var btnUnp = document.getElementById("btn-unparse");
  var btnSam = document.getElementById("btn-sample");
  var btnClr = document.getElementById("btn-clear");
  var btnCpy = document.getElementById("btn-copy");
  var btnDwn = document.getElementById("btn-download");
  var btnUp = document.getElementById("btn-upload");
  var fileIn = document.getElementById("file-input");

  var optComp = document.getElementById("opt-compress");
  var optCompReq = document.getElementById("opt-compress-required");
  var optDoc = document.getElementById("opt-remove-docstrings");
  var optLit = document.getElementById("opt-remove-literals");

  var banner = document.getElementById("status-banner");
  var bannerTxt = document.getElementById("banner-text");
  var bannerClose = document.getElementById("banner-close");
  var dropZone = document.getElementById("drop-zone");

  var mOrig = document.getElementById("m-orig");
  var mComp = document.getElementById("m-comp");
  var mSaved = document.getElementById("m-saved");
  var mTime = document.getElementById("m-time");
  var mMethod = document.getElementById("m-method");

  var curFile = "source.py";
  var SAMPLE =
    "import math\nfrom typing import List\n\nclass DataCompressor:\n    \"\"\"Sample class for minipy3 minification.\"\"\"\n    BUFFER_SIZE = 1000000\n    \n    def __init__(self, name: str = 'main'):\n        self.name = name\n        \n    def calculate(self, items: List[int]) -> int:\n        'Calculates squared sum.'\n        'unused literal to be stripped'\n        total = 0\n        for item in items:\n            total += int(math.pow(item, 2))\n        return total\n\nif __name__ == '__main__':\n    c = DataCompressor('demo')\n    print(c.calculate([1, 2, 3, 4, 5]))\n";

  function fmtBytes(bytes) {
    if (bytes === 0) return "0 B";
    var k = 1024;
    var sizes = ["B", "KB", "MB", "GB"];
    var i = Math.floor(Math.log(bytes) / Math.log(k));
    return (bytes / Math.pow(k, i)).toFixed(i === 0 ? 0 : 1) + " " + sizes[i];
  }

  function byteLen(str) {
    return new TextEncoder().encode(str).length;
  }

  function lineCount(str) {
    return str ? str.split("\n").length : 0;
  }

  function updateInMeta() {
    var val = inEl.value;
    inMeta.textContent = fmtBytes(byteLen(val)) + " | " + lineCount(val) + " lines";
  }

  function updateOutMeta() {
    var val = outEl.textContent || "";
    if (val.startsWith("# Minified")) val = "";
    outMeta.textContent = fmtBytes(byteLen(val)) + " | " + lineCount(val) + " lines";
  }

  function showBanner(msg, isInfo) {
    bannerTxt.textContent = (isInfo ? "[INFO] " : "[ERR] ") + msg;
    banner.className = isInfo ? "status-banner info" : "status-banner";
    banner.classList.remove("hidden");
  }

  function hideBanner() {
    banner.classList.add("hidden");
  }

  function syncHighlight() {
    if (!inHigh) return;
    var code = inEl.value;
    inHigh.textContent = code.endsWith("\n") ? code + " " : code || " ";
    if (window.Prism) {
      Prism.highlightElement(inHigh);
    }
  }

  inEl.addEventListener("scroll", function () {
    if (inHighPre) {
      inHighPre.scrollTop = inEl.scrollTop;
      inHighPre.scrollLeft = inEl.scrollLeft;
    }
  });

  inEl.addEventListener("input", function () {
    updateInMeta();
    syncHighlight();
  });

  inEl.addEventListener("keydown", function (e) {
    if (e.key === "Tab") {
      e.preventDefault();
      var s = this.selectionStart;
      var en = this.selectionEnd;
      var v = this.value;
      this.value = v.substring(0, s) + "    " + v.substring(en);
      this.selectionStart = this.selectionEnd = s + 4;
      updateInMeta();
      syncHighlight();
    } else if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
      e.preventDefault();
      doMinify();
    }
  });

  async function doMinify() {
    var code = inEl.value;
    if (!code || !code.trim()) {
      showBanner("Enter or upload Python code to minify");
      return;
    }
    hideBanner();
    btnMin.disabled = true;
    btnMin.textContent = "[RUNNING...]";

    try {
      var res = await fetch("/api/minify", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          code: code,
          compress: optComp.checked,
          compress_required: optCompReq.checked,
          remove_docstrings: optDoc.checked,
          remove_literals: optLit.checked,
        }),
      });

      var data = await res.json();
      if (!res.ok || !data.success) {
        var err = data.message || "Failed to minify code";
        if (data.line) {
          err =
            "SyntaxError (line " +
            data.line +
            (data.column ? ", col " + data.column : "") +
            "): " +
            data.message +
            (data.text ? " -> " + data.text : "");
        }
        showBanner(err);
        return;
      }

      outEl.textContent = data.result;
      if (window.Prism) {
        Prism.highlightElement(outEl);
      }
      updateOutMeta();

      mOrig.textContent = fmtBytes(data.original_size);
      mComp.textContent = fmtBytes(data.compressed_size);
      mSaved.textContent = fmtBytes(data.bytes_saved) + " (-" + data.ratio_percent + "%)";
      mTime.textContent = data.duration_ms + " ms";
      mMethod.textContent = data.method;

      ratioBadge.textContent = "-" + data.ratio_percent + "%";
      ratioBadge.classList.remove("hidden");

      var stem = curFile.replace(/\.py$/i, "");
      outName.textContent = stem + ".min.py";
    } catch (err) {
      showBanner("Network error: " + err.message);
    } finally {
      btnMin.disabled = false;
      btnMin.innerHTML = '[RUN MINIFY]<span class="btn-kbd">Ctrl+Enter</span>';
    }
  }

  async function doUnparse() {
    var outText = outEl.textContent || "";
    if (outText.startsWith("# Minified")) outText = "";
    var code = inEl.value.trim() || outText.trim();
    if (!code) {
      showBanner("No code found to unparse/revert");
      return;
    }

    hideBanner();
    btnUnp.disabled = true;
    btnUnp.textContent = "[REVERTING...]";

    try {
      var res = await fetch("/api/unparse", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code: code }),
      });

      var data = await res.json();
      if (!res.ok || !data.success) {
        showBanner("Unparse error: " + (data.message || "invalid syntax"));
        return;
      }

      outEl.textContent = data.result;
      if (window.Prism) {
        Prism.highlightElement(outEl);
      }
      updateOutMeta();
      ratioBadge.classList.add("hidden");

      var stem = curFile.replace(/\.py$/i, "").replace(/\.min$/i, "");
      outName.textContent = stem + ".unparsed.py";

      mOrig.textContent = fmtBytes(data.original_size);
      mComp.textContent = fmtBytes(data.unparsed_size);
      mSaved.textContent = "reverted";
      mTime.textContent = data.duration_ms + " ms";
      mMethod.textContent = "ast_unparse";

      showBanner("Successfully unparsed/decompressed code", true);
    } catch (err) {
      showBanner("Network error: " + err.message);
    } finally {
      btnUnp.disabled = false;
      btnUnp.textContent = "[UNPARSE]";
    }
  }

  /**
   * Puts minified output directly into the input field
   * so the user can easily review, edit, or click [UNPARSE].
   */
  function doSwap() {
    var outText = outEl.textContent || "";
    if (!outText || outText.startsWith("# Minified")) {
      showBanner("No minified code to move into input");
      return;
    }

    // Move minified code to input
    inEl.value = outText;

    // Reset output pane
    outEl.textContent = "# Minified output will appear here";
    ratioBadge.classList.add("hidden");

    mOrig.textContent = "0 B";
    mComp.textContent = "0 B";
    mSaved.textContent = "0 B (0.0%)";
    mTime.textContent = "0.0 ms";
    mMethod.textContent = "-";

    updateInMeta();
    updateOutMeta();
    syncHighlight();

    showBanner("Minified code placed into input field", true);
  }

  function loadFile(file) {
    if (!file) return;
    curFile = file.name;
    inName.textContent = file.name;
    outName.textContent = file.name.replace(/\.py$/i, "") + ".min.py";

    var reader = new FileReader();
    reader.onload = function (e) {
      inEl.value = e.target.result;
      updateInMeta();
      syncHighlight();
      hideBanner();
      showBanner("Loaded " + file.name + " (" + fmtBytes(file.size) + ")", true);
    };
    reader.readAsText(file);
  }

  btnMin.addEventListener("click", doMinify);
  btnSwap.addEventListener("click", doSwap);
  btnUnp.addEventListener("click", doUnparse);

  btnSam.addEventListener("click", function () {
    curFile = "sample.py";
    inName.textContent = "sample.py";
    outName.textContent = "sample.min.py";
    inEl.value = SAMPLE;
    updateInMeta();
    syncHighlight();
    hideBanner();
  });

  btnClr.addEventListener("click", function () {
    inEl.value = "";
    outEl.textContent = "# Minified output will appear here";
    updateInMeta();
    updateOutMeta();
    syncHighlight();
    ratioBadge.classList.add("hidden");
    mOrig.textContent = "0 B";
    mComp.textContent = "0 B";
    mSaved.textContent = "0 B (0.0%)";
    mTime.textContent = "0.0 ms";
    mMethod.textContent = "-";
    hideBanner();
  });

  btnCpy.addEventListener("click", async function () {
    var text = outEl.textContent || "";
    if (!text || text.startsWith("# Minified")) {
      showBanner("Nothing to copy");
      return;
    }
    try {
      await navigator.clipboard.writeText(text);
      var orig = btnCpy.textContent;
      btnCpy.textContent = "[COPIED]";
      setTimeout(function () {
        btnCpy.textContent = orig;
      }, 1500);
    } catch (e) {
      showBanner("Clipboard access failed");
    }
  });

  btnDwn.addEventListener("click", function () {
    var text = outEl.textContent || "";
    if (!text || text.startsWith("# Minified")) {
      showBanner("Nothing to download");
      return;
    }
    var blob = new Blob([text], { type: "text/plain;charset=utf-8" });
    var url = URL.createObjectURL(blob);
    var a = document.createElement("a");
    a.href = url;
    a.download = outName.textContent || "script.min.py";
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  });

  btnUp.addEventListener("click", function () {
    fileIn.click();
  });

  fileIn.addEventListener("change", function () {
    if (this.files && this.files[0]) loadFile(this.files[0]);
  });

  bannerClose.addEventListener("click", hideBanner);

  ["dragenter", "dragover"].forEach(function (ev) {
    dropZone.addEventListener(ev, function (e) {
      e.preventDefault();
      e.stopPropagation();
      dropZone.classList.add("drag-over");
    });
  });

  ["dragleave", "drop"].forEach(function (ev) {
    dropZone.addEventListener(ev, function (e) {
      e.preventDefault();
      e.stopPropagation();
      dropZone.classList.remove("drag-over");
    });
  });

  dropZone.addEventListener("drop", function (e) {
    var dt = e.dataTransfer;
    if (dt && dt.files && dt.files.length > 0) loadFile(dt.files[0]);
  });

  document.querySelectorAll(".cli-copy").forEach(function (btn) {
    btn.addEventListener("click", async function () {
      var id = this.getAttribute("data-target");
      var el = document.getElementById(id);
      if (el) {
        await navigator.clipboard.writeText(el.textContent.trim());
        var orig = this.textContent;
        this.textContent = "[COPIED]";
        setTimeout(function () {
          btn.textContent = orig;
        }, 1500);
      }
    });
  });

  updateInMeta();
  updateOutMeta();
  syncHighlight();
})();
