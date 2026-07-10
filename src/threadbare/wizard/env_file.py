"""Safe .env rewriting for the setup wizard's finish step -- the only place
in the codebase that mutates the operator's .env file at runtime. Not safe
against concurrent writers (no file locking): an accepted, documented gap
given the single-operator deployment assumption (DESIGN.md §2), matching
this project's convention of naming known-not-solved gaps rather than
silently assuming them away (see DESIGN.md §10's private-archived-threads
entry for the same pattern).
"""

import os
import re
import tempfile
from collections.abc import Mapping
from pathlib import Path

ADDED_COMMENT = "# Added by threadbare setup wizard"

_NEEDS_QUOTING = re.compile(r"[\s#]")


class EnvFileError(Exception):
    pass


def _format_value(value: str) -> str:
    if _NEEDS_QUOTING.search(value):
        return f'"{value}"'
    return value


def rewrite_env_text(text: str, updates: Mapping[str, str]) -> str:
    """Line-by-line rewrite: a line matching `KEY=...` for a KEY in
    `updates` is replaced with `KEY=value` (quoted if the value contains
    whitespace or '#', both of which python-dotenv would otherwise
    misparse); every other line -- comments, blank lines, unrelated keys --
    passes through byte-for-byte, preserving order and formatting. Keys in
    `updates` never seen in `text` are appended at the end, once, under a
    single added comment line.
    """
    remaining = dict(updates)
    lines = text.splitlines(keepends=True)
    result_lines = []

    for line in lines:
        stripped = line.rstrip("\n")
        match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=", stripped)
        if match and match.group(1) in remaining:
            key = match.group(1)
            result_lines.append(f"{key}={_format_value(remaining.pop(key))}\n")
        else:
            result_lines.append(line)

    if remaining:
        if result_lines and not result_lines[-1].endswith("\n"):
            result_lines.append("\n")
        if result_lines:
            result_lines.append("\n")
        result_lines.append(f"{ADDED_COMMENT}\n")
        for key, value in remaining.items():
            result_lines.append(f"{key}={_format_value(value)}\n")

    return "".join(result_lines)


def write_env_updates(
    path: Path, updates: Mapping[str, str], *, template_path: Path | None = None
) -> None:
    """Applies `updates` to the .env file at `path`, creating it from
    `template_path` (default: `.env.example` alongside `path`) if it
    doesn't exist yet -- mirrors DEVELOPMENT.md's manual `cp .env.example
    .env` bootstrap step in code. Writes atomically: builds the new content
    in memory, writes to a temp file in the same directory, then
    os.replace()s it over the target -- avoids a half-written .env if the
    process dies mid-write.
    """
    if path.exists():
        text = path.read_text()
    else:
        template = template_path if template_path is not None else path.parent / ".env.example"
        if not template.exists():
            raise EnvFileError(f"neither {path} nor template {template} exists")
        text = template.read_text()

    new_text = rewrite_env_text(text, updates)

    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(new_text)
        os.replace(tmp_name, path)
    except BaseException:
        if os.path.exists(tmp_name):
            os.remove(tmp_name)
        raise
