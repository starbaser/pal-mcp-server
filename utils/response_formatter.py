import logging

import yaml

logger = logging.getLogger("pal_mcp")

_NEWLINE_THRESHOLD = 2


def _is_formatted_text(value: object) -> bool:
    """Return True if value is a string with >= _NEWLINE_THRESHOLD newlines."""
    return isinstance(value, str) and value.count("\n") >= _NEWLINE_THRESHOLD


def _separate_fields(
    data: dict,
    prefix: str = "",
    depth: int = 0,
    max_depth: int = 2,
) -> tuple[dict, list[tuple[str, str]]]:
    """Recursively separate formatted text from inline metadata.

    Returns:
        inline: dict preserving insertion order, with formatted-text values removed
        formatted: list of (dotted_key, text_value) in insertion order
    """
    inline = {}
    formatted = []

    for key, value in data.items():
        full_key = f"{prefix}.{key}" if prefix else key

        if value is None:
            continue

        if _is_formatted_text(value):
            formatted.append((full_key, value))

        elif isinstance(value, dict) and depth < max_depth:
            nested_inline, nested_formatted = _separate_fields(
                value, prefix=full_key, depth=depth + 1, max_depth=max_depth
            )
            formatted.extend(nested_formatted)
            if nested_inline:
                inline[key] = nested_inline

        elif isinstance(value, list) and value:
            multiline = [v for v in value if _is_formatted_text(v)]
            if multiline:
                formatted.append((full_key, "\n\n".join(multiline)))
                rest = [v for v in value if not _is_formatted_text(v)]
                if rest:
                    inline[key] = rest
            else:
                inline[key] = value

        else:
            inline[key] = value

    return inline, formatted


# ---------------------------------------------------------------------------
# Markdown rendering (default MCP output format)
# ---------------------------------------------------------------------------


def _make_section_title(dotted_key: str) -> str:
    """Derive a markdown section title from a dotted key path.

    ``content`` → ``Content``
    ``continuation_offer.note`` → ``Note``
    ``expert_analysis`` → ``Expert Analysis``
    """
    leaf = dotted_key.rsplit(".", 1)[-1]
    return leaf.replace("_", " ").title()


def render_markdown_output(data: dict) -> str:
    """Transform a tool output dict into a markdown document with YAML front matter.

    Multi-line text fields are extracted from the JSON and rendered as markdown
    sections below the front matter.  Scalar metadata stays in the front matter
    under the ``json:`` key.
    """
    inline, formatted = _separate_fields(data)

    # Build reference map: inject "→ ## Title" placeholders for extracted fields
    section_titles: list[tuple[str, str]] = []
    for dotted_key, _ in formatted:
        title = _make_section_title(dotted_key)
        section_titles.append((dotted_key, title))

    # Patch inline dict with placeholder references for extracted keys
    _inject_placeholders(inline, section_titles)

    # Build YAML front matter
    yaml_body = yaml.dump({"json": inline}, default_flow_style=False, sort_keys=False, allow_unicode=True).rstrip()
    parts = [f"---\n{yaml_body}\n---"]

    # Render extracted sections
    for (_dotted_key, text), (__, title) in zip(formatted, section_titles):
        parts.append(f"## {title}\n\n{text}")

    return "\n\n".join(parts)


def _inject_placeholders(inline: dict, section_titles: list[tuple[str, str]]) -> None:
    """Walk *inline* and set placeholder values for keys that were extracted.

    ``section_titles`` is a list of ``(dotted_key, title)`` pairs.  For a
    top-level key like ``content`` the inline dict gets
    ``inline["content"] = "→ ## Content"``.  For nested keys like
    ``continuation_offer.note`` we walk into ``inline["continuation_offer"]``
    and set ``["note"]``.
    """
    for dotted_key, title in section_titles:
        parts = dotted_key.split(".")
        target = inline
        for part in parts[:-1]:
            if part not in target or not isinstance(target[part], dict):
                target[part] = {}
            target = target[part]
        target[parts[-1]] = f"\u2192 ## {title}"
