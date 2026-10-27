"""Highlight colour palette: three built-in names plus per-user extras.

Built-ins are immutable (id, name, hex) so existing notes keep a stable
anchor. Custom colours live in UserSettings key="note_colors" as
``{"custom": [{"id", "name", "hex"}, ...]}``.
"""
from __future__ import annotations

import re
from typing import Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import UserSettings


NOTE_COLORS_KEY = "note_colors"
MAX_CUSTOM_COLORS = 16
HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
SLUG_RE = re.compile(r"[^a-z0-9]+")

DEFAULT_NOTE_COLORS = (
    {"id": "amber", "name": "amber", "hex": "#fbbf24", "hint": "claim"},
    {"id": "green", "name": "green", "hex": "#34d399", "hint": "definition"},
    {"id": "indigo", "name": "indigo", "hex": "#818cf8", "hint": "question"},
)

_BUILTIN_IDS = {c["id"] for c in DEFAULT_NOTE_COLORS}
_BUILTIN_NAMES = {c["name"].lower() for c in DEFAULT_NOTE_COLORS}


def normalize_hex(value: str) -> str:
    raw = (value or "").strip()
    if HEX_RE.match(raw):
        return raw.lower()
    raise HTTPException(status_code=400, detail="Color must be a 6-digit hex value like #fbbf24")


def normalize_name(value: str) -> str:
    name = " ".join((value or "").split())
    if not name:
        raise HTTPException(status_code=400, detail="Color name is required")
    if len(name) > 40:
        raise HTTPException(status_code=400, detail="Color name must be 40 characters or fewer")
    return name


def _slug(name: str) -> str:
    slug = SLUG_RE.sub("-", name.lower()).strip("-")[:32]
    return slug or "color"


def _get_or_create_row(db: Session, user_id: int) -> UserSettings:
    row = (
        db.query(UserSettings)
        .filter(UserSettings.user_id == user_id, UserSettings.key == NOTE_COLORS_KEY)
        .first()
    )
    if row is None:
        row = UserSettings(user_id=user_id, key=NOTE_COLORS_KEY, value={"custom": []})
        db.add(row)
        db.flush()
    return row


def _custom_list(row: UserSettings) -> list[dict]:
    value = row.value or {}
    custom = value.get("custom")
    return list(custom) if isinstance(custom, list) else []


def list_palette(db: Session, user_id: int) -> list[dict]:
    """Built-ins first, then this user's custom colours."""
    row = (
        db.query(UserSettings)
        .filter(UserSettings.user_id == user_id, UserSettings.key == NOTE_COLORS_KEY)
        .first()
    )
    custom = _custom_list(row) if row is not None else []
    out = [
        {
            "id": c["id"],
            "name": c["name"],
            "hex": c["hex"],
            "built_in": True,
            "hint": c.get("hint"),
        }
        for c in DEFAULT_NOTE_COLORS
    ]
    for item in custom:
        cid = item.get("id")
        name = item.get("name")
        hex_val = item.get("hex")
        if not cid or not name or not hex_val:
            continue
        out.append(
            {
                "id": cid,
                "name": name,
                "hex": hex_val,
                "built_in": False,
                "hint": None,
            }
        )
    return out


def known_color_ids(db: Session, user_id: int) -> set[str]:
    return {c["id"] for c in list_palette(db, user_id)}


def add_color(db: Session, user_id: int, name: str, hex_value: str) -> dict:
    name = normalize_name(name)
    hex_value = normalize_hex(hex_value)
    if name.lower() in _BUILTIN_NAMES:
        raise HTTPException(status_code=400, detail="That name is reserved for a default colour")

    row = _get_or_create_row(db, user_id)
    custom = _custom_list(row)
    if len(custom) >= MAX_CUSTOM_COLORS:
        raise HTTPException(
            status_code=400,
            detail=f"At most {MAX_CUSTOM_COLORS} extra colours",
        )
    existing_names = {str(c.get("name", "")).lower() for c in custom}
    if name.lower() in existing_names:
        raise HTTPException(status_code=400, detail="A colour with that name already exists")

    slug = _slug(name)
    taken = _BUILTIN_IDS | {str(c.get("id")) for c in custom}
    candidate = slug
    n = 2
    while candidate in taken:
        candidate = f"{slug}-{n}"
        n += 1

    entry = {"id": candidate, "name": name, "hex": hex_value}
    custom.append(entry)
    row.value = {"custom": custom}
    db.commit()
    db.refresh(row)
    return {**entry, "built_in": False, "hint": None}


def update_color(db: Session, user_id: int, color_id: str, name: Optional[str], hex_value: Optional[str]) -> dict:
    if color_id in _BUILTIN_IDS:
        raise HTTPException(status_code=400, detail="Default colours cannot be changed")

    row = _get_or_create_row(db, user_id)
    custom = _custom_list(row)
    target = next((c for c in custom if c.get("id") == color_id), None)
    if target is None:
        raise HTTPException(status_code=404, detail="Colour not found")

    if name is not None:
        name = normalize_name(name)
        if name.lower() in _BUILTIN_NAMES:
            raise HTTPException(status_code=400, detail="That name is reserved for a default colour")
        clash = next(
            (c for c in custom if c.get("id") != color_id and str(c.get("name", "")).lower() == name.lower()),
            None,
        )
        if clash:
            raise HTTPException(status_code=400, detail="A colour with that name already exists")
        target["name"] = name
    if hex_value is not None:
        target["hex"] = normalize_hex(hex_value)

    row.value = {"custom": custom}
    db.commit()
    db.refresh(row)
    return {**target, "built_in": False, "hint": None}


def delete_color(db: Session, user_id: int, color_id: str, in_use: bool) -> None:
    if color_id in _BUILTIN_IDS:
        raise HTTPException(status_code=400, detail="Default colours cannot be removed")
    if in_use:
        raise HTTPException(status_code=400, detail="Colour is used by existing notes")

    row = _get_or_create_row(db, user_id)
    custom = _custom_list(row)
    next_custom = [c for c in custom if c.get("id") != color_id]
    if len(next_custom) == len(custom):
        raise HTTPException(status_code=404, detail="Colour not found")
    row.value = {"custom": next_custom}
    db.commit()
