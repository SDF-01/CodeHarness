"""Read the small YAML frontmatter used by skills, rules, and agents."""

from __future__ import annotations


def split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Return frontmatter fields and the Markdown body. Missing frontmatter is empty."""
    cleaned = text.lstrip("\ufeff").strip()
    if not cleaned.startswith("---"):
        return {}, cleaned
    end = cleaned.find("\n---", 3)
    if end == -1:
        return {}, cleaned
    raw = cleaned[3:end]
    body = cleaned[end + 4 :].strip()
    fields: dict[str, str] = {}
    for line in raw.splitlines():
        if not line.strip() or line.lstrip().startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        fields[key.strip()] = value.strip()
    return fields, body


def as_bool(value: str) -> bool:
    return value.strip().lower() in {"true", "yes", "1"}
