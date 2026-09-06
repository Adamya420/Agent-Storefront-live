"""Enforces NON-NEGOTIABLE RULE #1: no LLM in the money path.

The Offer Engine and the Authorization Gate must be fully deterministic. This is
the backbone of the "explainable, bounded, gated" claim: a bound you can prove
holds is worth more than a model you hope behaves. If someone later "just asks
Gemini" for an edge case inside backend/gateway/ or backend/offer/, this test
fails and the safety story is protected.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
PROTECTED_DIRS = [ROOT / "backend" / "gateway", ROOT / "backend" / "offer"]

# Import/callsite markers for any LLM SDK. Substring match on source text.
FORBIDDEN_PATTERNS = [
    r"\bimport\s+google\.generativeai\b",
    r"\bfrom\s+google\.generativeai\b",
    r"\bgenerativeai\b",
    r"\bGenerativeModel\b",
    r"\bimport\s+openai\b",
    r"\bfrom\s+openai\b",
    r"\bimport\s+anthropic\b",
    r"\bfrom\s+anthropic\b",
    r"\bgenerate_content\b",
    r"\bchat\.completions\b",
    r"\bmessages\.create\b",
    r"GEMINI_API_KEY",
]


def _python_files(directory: Path) -> list[Path]:
    if not directory.exists():
        return []
    return [p for p in directory.rglob("*.py") if "__pycache__" not in p.parts]


@pytest.mark.parametrize("directory", PROTECTED_DIRS, ids=lambda p: p.name)
def test_no_llm_references_in_money_path(directory: Path):
    offenders: list[str] = []
    for path in _python_files(directory):
        # Skip this guard's own fixtures if ever colocated.
        text = path.read_text(encoding="utf-8")
        for pattern in FORBIDDEN_PATTERNS:
            for match in re.finditer(pattern, text):
                line_no = text[: match.start()].count("\n") + 1
                line = text.splitlines()[line_no - 1].strip()
                # Allow prose in comments/docstrings that merely NAMES the rule.
                if line.lstrip().startswith("#") or _in_docstring(text, match.start()):
                    continue
                offenders.append(f"{path.relative_to(ROOT)}:{line_no}: {line}")

    assert not offenders, (
        "LLM usage detected in the deterministic money path (NON-NEGOTIABLE RULE #1):\n  "
        + "\n  ".join(offenders)
    )


def _in_docstring(text: str, index: int) -> bool:
    """Rough check: an odd number of triple quotes before index means we're inside one."""
    before = text[:index]
    return (before.count('"""') % 2 == 1) or (before.count("'''") % 2 == 1)


def test_protected_dirs_exist_and_have_code():
    """Guard against the guard silently passing because the dirs vanished."""
    for directory in PROTECTED_DIRS:
        assert directory.exists(), f"{directory} is missing — the guard would pass vacuously"
    assert _python_files(PROTECTED_DIRS[0]), "backend/gateway has no python files to check"
