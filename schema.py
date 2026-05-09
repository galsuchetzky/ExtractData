"""Schema loader, validator, and prompt-fragment renderer."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

SCALAR_TYPES = {"string", "integer", "number", "boolean", "enum", "date", "float"}
LIST_TYPES = {"list_of_strings", "list_of_objects"}
ALL_TYPES = SCALAR_TYPES | LIST_TYPES


@dataclass
class Field:
    name: str
    type: str
    description: str = ""
    hebrew_aliases: list[str] = field(default_factory=list)
    values: list[str] = field(default_factory=list)
    item_schema: dict[str, str] = field(default_factory=dict)
    default: Any = None


@dataclass
class Schema:
    version: int
    language_hint: str
    fields: list[Field]

    def field_names(self) -> list[str]:
        return [f.name for f in self.fields]

    def render_for_prompt(self) -> str:
        lines: list[str] = []
        for f in self.fields:
            parts = [f"- {f.name} ({f.type})"]
            if f.description:
                parts.append(f": {f.description.strip().replace(chr(10), ' ')}")
            if f.hebrew_aliases:
                parts.append(f" [aliases: {', '.join(f.hebrew_aliases)}]")
            if f.type == "enum" and f.values:
                parts.append(f" [allowed: {', '.join(f.values)}]")
            if f.type == "list_of_objects" and f.item_schema:
                items = ", ".join(f"{k}:{v}" for k, v in f.item_schema.items())
                parts.append(f" [each item has: {items}]")
            lines.append("".join(parts))
        return "\n".join(lines)

    def empty_row(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for f in self.fields:
            if f.default is not None:
                out[f.name] = f.default
            else:
                out[f.name] = [] if f.type in LIST_TYPES else None
        return out

    def json_schema(self) -> dict[str, Any]:
        """Render a JSON schema for Ollama's structured-output mode.

        Ollama enforces this server-side, which is far more reliable than
        format='json' (which gemma4:26b sometimes loops on, emitting duplicate
        keys / repeating tokens).
        """
        type_map: dict[str, Any] = {
            "string": {"type": ["string", "null"]},
            "integer": {"type": ["integer", "null"]},
            "number": {"type": ["number", "null"]},
            "boolean": {"type": ["boolean", "null"]},
            "date": {"type": ["string", "null"]},
            "float": {"type": ["number", "null"]},
        }
        properties: dict[str, Any] = {}
        for f in self.fields:
            if f.type in type_map:
                properties[f.name] = type_map[f.type]
            elif f.type == "enum":
                properties[f.name] = {"type": "string", "enum": list(f.values)}
            elif f.type == "list_of_strings":
                properties[f.name] = {
                    "type": "array",
                    "items": {"type": "string"},
                }
            elif f.type == "list_of_objects":
                item_props: dict[str, Any] = {}
                for sub_name, sub_type in (f.item_schema or {}).items():
                    item_props[sub_name] = type_map.get(
                        sub_type, {"type": ["string", "null"]}
                    )
                properties[f.name] = {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": item_props,
                        "required": list(item_props),
                    },
                }
            else:  # pragma: no cover -- guarded by load_schema
                properties[f.name] = {"type": ["string", "null"]}
        return {
            "type": "object",
            "properties": properties,
            "required": list(properties),
        }


def load_schema(path: Path) -> Schema:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or "fields" not in raw:
        raise ValueError(f"Invalid schema at {path}: missing top-level 'fields' list")
    fields: list[Field] = []
    seen: set[str] = set()
    for entry in raw["fields"]:
        if not isinstance(entry, dict) or "name" not in entry:
            raise ValueError(f"Schema field missing 'name': {entry!r}")
        name = entry["name"]
        if name in seen:
            raise ValueError(f"Duplicate schema field: {name}")
        seen.add(name)
        ftype = entry.get("type", "string")
        if ftype not in ALL_TYPES:
            raise ValueError(
                f"Unknown type '{ftype}' for field '{name}'. "
                f"Allowed: {sorted(ALL_TYPES)}"
            )
        fields.append(
            Field(
                name=name,
                type=ftype,
                description=entry.get("description", "") or "",
                hebrew_aliases=list(entry.get("hebrew_aliases") or []),
                values=list(entry.get("values") or entry.get("options") or []),
                item_schema=dict(entry.get("item_schema") or {}),
                default=entry.get("default"),
            )
        )
    return Schema(
        version=int(raw.get("version", 1)),
        language_hint=str(raw.get("language_hint") or ""),
        fields=fields,
    )
