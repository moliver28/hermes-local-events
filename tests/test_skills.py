"""Skill registry integrity: every skill registered in __init__ exists on disk,
has valid frontmatter, and every on-disk skill is registered (no orphans)."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INIT = (ROOT / "__init__.py").read_text(encoding="utf-8")


def _registered_skills() -> list[tuple[str, str]]:
    block = re.search(r"for skill_name, skill_path in \((.*?)\):", INIT, re.DOTALL)
    assert block, "skill registration block not found in __init__.py"
    return re.findall(r'\("([^"]+)",\s*"([^"]+)"\)', block.group(1))


def test_all_registered_skill_files_exist_with_frontmatter():
    registered = _registered_skills()
    assert registered, "no skills registered"
    for name, rel in registered:
        p = ROOT / rel
        assert p.exists(), f"registered skill missing on disk: {rel}"
        text = p.read_text(encoding="utf-8")
        m = re.match(r"^---\n(.*?)\n---", text, re.DOTALL)
        assert m, f"{rel} missing YAML frontmatter"
        fm = m.group(1)
        assert re.search(rf"^name:\s*{re.escape(name)}\s*$", fm, re.MULTILINE), (
            f"{rel} frontmatter name != registered name {name}"
        )
        assert re.search(r"^description:\s*\S", fm, re.MULTILINE), f"{rel} missing description"


def test_no_orphan_skill_files():
    registered = {rel for _, rel in _registered_skills()}
    on_disk = {str(p.relative_to(ROOT)).replace("\\", "/") for p in (ROOT / "skills").rglob("SKILL.md")}
    assert on_disk, "no skill files on disk"
    assert on_disk == registered, f"orphan or missing skills: {on_disk ^ registered}"


def test_skill_coverage_of_tools():
    """Every tool name must be mentioned by at least one skill (agent teachability)."""
    all_skill_text = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / "skills").rglob("SKILL.md"))
    from schemas import SCHEMAS

    for tool in SCHEMAS:
        assert tool in all_skill_text, f"tool {tool} is not taught by any skill"
