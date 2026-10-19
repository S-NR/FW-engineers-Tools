"""Render CLI output as a terminal-style image and capture the HTML report (headless Edge/Chrome if present)."""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
OUT = os.path.join(ROOT, "docs", "images")
sys.path.insert(0, ROOT)

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from firmware_binary_tool.cli.cli import run  # noqa: E402


def terminal_image(command: str, text: str, path: str, max_lines: int = 60) -> None:
    lines = [f"> {command}", ""] + text.rstrip().splitlines()[:max_lines]
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/consola.ttf", 17)
    except OSError:
        font = ImageFont.load_default()
    line_h = 22
    width = max(int(font.getlength(line)) for line in lines) + 60
    img = Image.new("RGB", (max(width, 1100), line_h * len(lines) + 60), "#0f172a")
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, img.width, 30), fill="#1e293b")
    for i, c in enumerate(("#ef4444", "#f59e0b", "#22c55e")):
        d.ellipse((14 + i * 22, 9, 26 + i * 22, 21), fill=c)
    y = 44
    for i, line in enumerate(lines):
        color = "#93c5fd" if i == 0 else "#e2e8f0"
        if "UNEXPECTED" in line or line.strip().startswith(("MODIFIED", "REMOVED", "ADDED")):
            color = "#fca5a5"
        elif "EXPECTED" in line:
            color = "#fcd34d"
        elif line.startswith(("Result:", "Controller", "Changed functions")):
            color = "#86efac"
        d.text((24, y), line, font=font, fill=color)
        y += line_h
    img.save(path)
    print("saved", os.path.basename(path))


def main() -> None:
    os.makedirs(OUT, exist_ok=True)
    ex = os.path.join(ROOT, "examples")
    tmp = tempfile.mkdtemp(prefix="fwcmp_doc_")
    report = os.path.join(tmp, "comparison_report.html")
    args = [os.path.join(ex, "firmware", "FW_V1.2.3.bin"), os.path.join(ex, "firmware", "FW_V1.2.4.bin"),
            "--regions", os.path.join(ex, "regions_stm32.json"), "--ignore", os.path.join(ex, "expected.json"),
            "--symbols-a", os.path.join(ex, "firmware", "FW_V1.2.3.map"),
            "--symbols-b", os.path.join(ex, "firmware", "FW_V1.2.4.map"),
            "--blocks", "0", "--functions", "8", "--no-metadata", "--report", report]
    buf = io.StringIO()
    run(args, out=buf)
    terminal_image("firmware-diff FW_V1.2.3.bin FW_V1.2.4.bin --regions regions_stm32.json --ignore expected.json "
                   "--symbols-a FW_V1.2.3.map --symbols-b FW_V1.2.4.map --report comparison_report.html",
                   buf.getvalue(), os.path.join(OUT, "16_cli.png"), max_lines=70)

    browser = next((p for p in (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                                r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
                                r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                                shutil.which("msedge") or "", shutil.which("chromium") or "")
                    if p and os.path.isfile(p)), None)
    if browser is None:
        print("no headless browser found - HTML report screenshot skipped")
        return
    shot = os.path.join(OUT, "17_html_report.png")
    url = "file:///" + report.replace("\\", "/")
    subprocess.run([browser, "--headless=new", "--disable-gpu", "--hide-scrollbars", f"--screenshot={shot}",
                    "--window-size=1300,2600", url], check=False, timeout=120,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if os.path.isfile(shot):
        print("saved", os.path.basename(shot))
        shutil.copy(report, os.path.join(OUT, "..", "sample_comparison_report.html"))
    else:
        print("browser screenshot failed - skipped")


if __name__ == "__main__":
    main()
