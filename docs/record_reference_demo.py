"""Run the CLI and render its verified results as a compact GIF."""

import json
import os
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]


def execute(args: list[str], *, expected: int = 0) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(args, cwd=ROOT, text=True, capture_output=True, check=False)
    if result.returncode != expected:
        raise RuntimeError(f"demo command failed: {result.stderr}")
    return result


def record() -> None:
    with tempfile.TemporaryDirectory(prefix="corpora-reference-demo-") as temporary:
        directory = Path(temporary) / "demo-link"
        created = execute([sys.executable, str(ROOT / "docs/reference_demo.py"), str(directory)])
        reference = json.loads((directory / "reference.json").read_text())
        cli = [sys.executable, "-m", "corpora_cli.cli", "references"]
        checked = execute([*cli, "check", str(directory / "reference.json")])
        model = json.loads(checked.stdout)
        if model != reference:
            raise RuntimeError("checking changed the reference or its stable ID")
        retrieved = execute(
            [
                *cli,
                "retrieve",
                str(directory / "target.json"),
                "--snapshot",
                str(directory / "target-snapshot.json"),
            ]
        )
        if json.loads(retrieved.stdout) != {"text": "Love your neighbor as yourself."}:
            raise RuntimeError("retrieval did not return the exact target quote")
        stale = execute(
            [
                *cli,
                "retrieve",
                str(directory / "target.json"),
                "--snapshot",
                str(directory / "stale-snapshot.json"),
            ],
            expected=1,
        )
        if stale.stdout:
            raise RuntimeError("stale selection emitted text")
        blocks = [
            ("$ python docs/reference_demo.py demo-link", created.stdout.strip()),
            (
                "$ corpora references check demo-link/reference.json",
                "JSON summary: resolved / pending review / draft. Same ID preserved.",
            ),
            (
                "$ corpora references retrieve demo-link/target.json " + chr(92) + "\n"
                "    --snapshot demo-link/target-snapshot.json",
                retrieved.stdout.strip(),
            ),
            (
                "$ corpora references retrieve demo-link/target.json " + chr(92) + "\n"
                "    --snapshot demo-link/stale-snapshot.json",
                stale.stderr.strip() + "\nExit 1. No selected text on stdout.",
            ),
        ]
        transcript = ["Actual command results; temporary paths shown as demo-link.\n"]
        for (command, _), result in zip(blocks, (created, checked, retrieved, stale), strict=True):
            transcript.extend(
                (command, result.stdout, result.stderr, f"Exit: {result.returncode}\n")
            )
        (ROOT / "docs/reference-demo.txt").write_text("\n".join(transcript), encoding="utf-8")
        render(blocks)


def render(blocks: list[tuple[str, str]]) -> None:
    candidates = (
        os.environ.get("CORPORA_DEMO_FONT", ""),
        str(Path.home() / "Library/Fonts/GoogleSansCode-Regular.ttf"),
        "/System/Library/Fonts/Menlo.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        str(Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts/consola.ttf"),
    )
    font_path = next((path for path in candidates if path and Path(path).is_file()), None)
    font = ImageFont.truetype(font_path, 18) if font_path else ImageFont.load_default(size=18)
    frames = []
    lines: list[tuple[str, str]] = []
    for command, output in blocks:
        lines.extend((line, "#d2a24c") for line in command.splitlines())
        lines.extend((line, "#ffffff") for line in output.splitlines())
        lines.append(("", "#ffffff"))
        frame = Image.new("RGB", (1200, 680), "#262626")
        draw = ImageDraw.Draw(frame)
        for x, color in ((30, "#ff5f57"), (54, "#febc2e"), (78, "#28c840")):
            draw.ellipse((x, 20, x + 14, 34), fill=color)
        draw.text(
            (115, 18),
            "Offline reference linking | Actual results, condensed JSON",
            font=font,
            fill="#bbbbbb",
        )
        y = 66
        for line, color in lines:
            for wrapped in textwrap.wrap(line, width=99, replace_whitespace=False) or [""]:
                draw.text((30, y), wrapped, font=font, fill=color)
                y += 25
        if y > 660:
            raise RuntimeError("demo output exceeds the frame; do not truncate evidence")
        frames.append(frame)
    frames[0].save(
        ROOT / "docs/corpora-references.gif",
        save_all=True,
        append_images=frames[1:],
        duration=[3000, 3000, 4000, 6000],
        loop=0,
    )
    print("Recorded docs/corpora-references.gif and docs/reference-demo.txt")


if __name__ == "__main__":
    record()
