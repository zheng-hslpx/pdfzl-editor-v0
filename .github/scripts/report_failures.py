"""Turn failing pytest results into GitHub check annotations.

Downloading CI logs and artifacts needs repository admin rights, while
check-run annotations are readable by anyone. This script reads the JUnit
XML that pytest writes, and falls back to the captured pytest output when
the XML is missing, so a red build names the broken test without opening
the log.

Every failure mode prints an annotation instead of raising: a reporting
step that dies silently is worse than no reporting step at all.
"""

from __future__ import annotations

import glob
import os
import re
import sys
import traceback
import xml.etree.ElementTree as ET

MAX_ITEMS = 8
MAX_MESSAGE = 400


def clean(text: str, limit: int = MAX_MESSAGE) -> str:
    """Keep annotation text single-line and ASCII-safe for the runner console."""
    flat = " ".join((text or "").split())
    flat = flat.encode("ascii", "replace").decode("ascii")
    return flat[:limit]


def emit(level: str, title: str, message: str, limit: int = 1500) -> None:
    print(f"::{level} title={clean(title, 120)}::{clean(message, limit)}", flush=True)


def from_junit(path: str) -> int:
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as exc:
        emit("error", "JUnit unreadable", f"{path}: {exc}")
        return 0

    cases = list(root.iter("testcase"))
    failed: list[tuple[str, str, str]] = []
    for case in cases:
        node = case.find("failure")
        if node is None:
            node = case.find("error")
        if node is None:
            continue
        raw = (node.get("message") or node.text or "").strip().splitlines()
        failed.append(
            (
                case.get("classname", ""),
                case.get("name", ""),
                raw[0] if raw else "",
            )
        )

    if not failed:
        emit("notice", "No failing cases in JUnit", f"{path} ({len(cases)} collected)")
        return 0

    for classname, name, message in failed[:MAX_ITEMS]:
        emit("error", f"{len(failed)} of {len(cases)} tests failed", f"{classname}.{name} {message}")
    return len(failed)


def from_log(path: str) -> int:
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            lines = [line.rstrip() for line in handle]
    except OSError as exc:
        emit("warning", "pytest log unreadable", f"{path}: {exc}")
        return 0

    hits = [line for line in lines if re.match(r"^(FAILED|ERROR)\s", line)]
    for line in hits[:MAX_ITEMS]:
        emit("error", "pytest failure", line)
    if not hits:
        tail = " | ".join(line for line in lines[-25:] if line.strip())
        emit("error", "pytest failed without FAILED lines", tail, limit=3000)
    return len(hits)


def main(argv: list[str]) -> int:
    try:
        total = 0
        junit = sorted(glob.glob("backend/pytest-*.xml"))
        for path in junit:
            total += from_junit(path)
        if not junit:
            emit("warning", "no JUnit XML found", "backend/pytest-*.xml matched nothing")
        for path in argv[1:] or ["backend/pytest-output.txt"]:
            if os.path.exists(path):
                total += from_log(path)
            else:
                emit("warning", "no pytest log", f"{path} does not exist")
        emit("notice", "report done", f"{total} failing entries reported")
    except Exception:  # noqa: BLE001
        emit("error", "report_failures crashed", traceback.format_exc().replace("\n", " | "))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
