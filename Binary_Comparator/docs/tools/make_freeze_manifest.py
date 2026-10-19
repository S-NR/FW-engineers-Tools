"""Write docs/FREEZE_v1.0.0.md: baseline record with the SHA-256 of every source, test and build file."""

import datetime
import hashlib
import os

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
VERSION, FROZEN_ON = "1.0.0", datetime.date(2026, 10, 16)


def main() -> None:
    os.chdir(ROOT)
    files = []
    for top in ("firmware_binary_tool", "tests", "examples"):
        for d, _, names in os.walk(top):
            if "__pycache__" in d or d.replace("\\", "/").startswith("examples/firmware"):
                continue
            files += [os.path.join(d, f).replace("\\", "/") for f in names if f.endswith((".py", ".json"))]
    files += ["firmware_diff.py", "firmware_compare_gui.py", "pyproject.toml", "requirements.txt",
              "requirements-dev.txt", "build_windows.bat", "build_linux.sh", "README.md"]
    lines = []
    for f in sorted(files):
        with open(f, "rb") as fh:
            lines.append(f"{hashlib.sha256(fh.read()).hexdigest()}  {f}")
    with open(os.path.join("docs", f"FREEZE_v{VERSION}.md"), "w", encoding="utf-8", newline="\n") as out:
        out.write(f"""# Firmware Binary Comparison Tool — v{VERSION} frozen baseline

| Item | Value |
|---|---|
| Version | {VERSION} |
| Frozen on | {FROZEN_ON.isoformat()} |
| Status | Feature freeze — no functional changes without a new version |
| Unit tests | 84 passed (`python -m unittest discover -s tests -t .`) |
| Runtime dependencies | Python 3.11+ standard library (optional: `tkinterdnd2` for drag-and-drop) |
| Design document | `docs/Firmware_Binary_Comparison_Tool_Design_v1.0.docx` |
| Demo presentation | `docs/Firmware_Binary_Comparison_Tool_Demo_v1.0.pptx` |

## Verifying the baseline

Every source, test, configuration and build file of the frozen version is listed below with its SHA-256.
Re-compute with `sha256sum <file>` (Linux / Git Bash) or `certutil -hashfile <file> SHA256` (Windows):
any mismatch means the file changed after the freeze.

```
""" + "\n".join(lines) + "\n```\n")
    print(len(lines), "files in manifest")


if __name__ == "__main__":
    main()
