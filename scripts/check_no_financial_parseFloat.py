#!/usr/bin/env python3
"""CI check: prohibit parseFloat/Number/unary-+ on financial fields outside display adapters.

Run from project root: python scripts/check_no_financial_parseFloat.py
Returns exit code 1 if violations found.
"""
import re
import sys
from pathlib import Path

# These patterns indicate authoritative financial calculations using native JS numbers
VIOLATION_PATTERNS = [
    r"parseFloat\s*\(\s*\w*\.(estimated_value|qty|market_value|cost_basis|price|weight|target_weight)",
    r"Number\s*\(\s*\w*\.(estimated_value|qty|market_value|cost_basis|price|weight|target_weight)",
]

# Files where parseFloat/Number() is explicitly allowed (display boundaries)
EXEMPT_SUFFIXES = {
    "dashboard/src/lib/decimal.ts",   # the display adapter itself
    "dashboard/src/constants.ts",     # fmtMoney is display-only (documented)
}

def check(root: Path) -> list[tuple[str, int, str]]:
    violations: list[tuple[str, int, str]] = []
    for f in root.glob("dashboard/src/**/*.ts*"):
        rel = str(f.relative_to(root)).replace("\\", "/")
        if rel in EXEMPT_SUFFIXES:
            continue
        lines = f.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines, 1):
            for pat in VIOLATION_PATTERNS:
                if re.search(pat, line):
                    violations.append((rel, i, line.strip()))
    return violations

if __name__ == "__main__":
    root = Path(__file__).parent.parent
    violations = check(root)
    if violations:
        print("FAIL: Prohibited float conversion on authoritative financial fields:")
        for path, lineno, text in violations:
            print(f"  {path}:{lineno}: {text}")
        sys.exit(1)
    print("OK: No prohibited float conversions on authoritative financial fields.")
