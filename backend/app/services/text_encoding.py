from __future__ import annotations


def restore_utf8_cjk_label(value: str) -> str:
    """Recover UTF-8 Chinese bytes persisted as Latin-1 without guessing labels."""
    if not value or not any(0x80 <= ord(character) <= 0xFF for character in value):
        return value
    if any(ord(character) > 0xFF for character in value):
        return value
    try:
        decoded = value.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return value
    has_cjk = any("\u3400" <= character <= "\u9fff" for character in decoded)
    return decoded if decoded != value and has_cjk else value
