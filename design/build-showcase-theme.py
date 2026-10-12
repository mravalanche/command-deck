"""Generate browser-ready theme variables from the canonical daisyUI theme source."""

import re
from pathlib import Path

HERE = Path(__file__).parent
SOURCE = HERE / "themes.css"
OUTPUT = HERE / "showcase-themes.generated.css"


def build() -> str:
    source = SOURCE.read_text(encoding="utf-8")
    blocks: list[str] = [
        "/* Generated from design/themes.css; run python3 design/build-showcase-theme.py. */"
    ]
    for match in re.finditer(r'@plugin "daisyui/theme" \{(.*?)^\}', source, re.M | re.S):
        body = match.group(1)
        name_match = re.search(r'name:\s*"([^"]+)"', body)
        if name_match is None:
            raise ValueError("Theme block has no name")
        declarations = [
            line.strip()
            for line in body.splitlines()
            if line.strip().startswith(("--", "color-scheme:"))
        ]
        blocks.append(
            f'[data-theme="{name_match.group(1)}"] {{\n'
            + "\n".join(f"  {line}" for line in declarations)
            + "\n}"
        )
    for match in re.finditer(r'(\[data-theme="[^"]+"\]) \{(.*?)^\}', source, re.M | re.S):
        blocks.append(f"{match.group(1)} {{{match.group(2)}\n}}")
    if len(blocks) != 5:
        raise ValueError(f"Expected two theme and two app-token blocks, found {len(blocks) - 1}")
    return "\n\n".join(blocks) + "\n"


if __name__ == "__main__":
    OUTPUT.write_text(build(), encoding="utf-8")
