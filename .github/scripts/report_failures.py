"""Turn failing pytest cases into GitHub check annotations.

Downloading CI logs and artifacts needs repository admin rights, while
check-run annotations are readable by anyone. This script reads the JUnit
XML that pytest writes and emits one ``::error::`` per failed or errored
case, so a red build names the broken test without opening the log.
"""

from __future__ import annotations

import glob
import sys
import xml.etree.ElementTree as ET

MAX_REPORTED = 10


def report(path: str) -> None:
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as exc:
        print(f"::error title=JUnit unreadable::{path}: {exc}")
        return

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
                raw[0][:180] if raw else "",
            )
        )

    if not failed:
        print(f"::notice title=No failing cases in JUnit::{path} ({len(cases)} collected)")
        return

    for classname, name, message in failed[:MAX_REPORTED]:
        print(f"::error title={len(failed)} of {len(cases)} tests failed::{classname}.{name} {message}")


def main(argv: list[str]) -> int:
    for pattern in argv[1:] or ["backend/pytest-*.xml"]:
        for path in sorted(glob.glob(pattern)):
            report(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
