"""Password policy shared by account creation and password changes.

The policy deliberately measures Python/Unicode code points and never changes
the submitted password.  In particular, leading/trailing spaces are valid and
are part of the password checked by the hashing layer.
"""
from __future__ import annotations

import re


PASSWORD_MIN_LENGTH = 15
PASSWORD_MAX_LENGTH = 4096

# Keep this local list intentionally small and explainable.  The password
# manager/identity provider remains the right place for a large breach corpus.
COMMON_PASSWORDS = frozenset({
    "password", "password123", "passw0rd", "qwerty", "qwerty123",
    "123456", "12345678", "123456789", "1234567890", "111111111111111",
    "admin", "admin123", "administrator", "letmein", "welcome",
    "changeme", "default", "root", "user", "пароль", "пароль123",
    "админ", "админ123", "йцукен", "йцукен123",
})


def _comparison_text(value: str) -> str:
    """Comparison-only representation; never use this to hash a password."""
    # Do not trim or Unicode-normalize: the submitted value is hashed exactly
    # as received.  Case-folding is used only to recognize login/common-word
    # variants and never replaces the original password.
    return value.casefold()


def _identifier_parts(value: str) -> set[str]:
    value = _comparison_text(value or "")
    parts = {part for part in re.split(r"[^0-9a-zа-яё]+", value) if part}
    compact = re.sub(r"[^0-9a-zа-яё]", "", value)
    if compact:
        parts.add(compact)
    return {part for part in parts if len(part) >= 3}


def _is_login_derived(password: str, username: str, email: str | None) -> bool:
    candidate = _comparison_text(password)
    identifiers = _identifier_parts(username)
    if email:
        local = str(email).split("@", 1)[0]
        identifiers.update(_identifier_parts(local))
    for identifier in identifiers:
        # Reject an identifier with only a very common/simple decoration.
        # Do not reject unrelated long passphrases merely containing a name.
        if candidate == identifier:
            return True
        if len(candidate) >= len(identifier) * 2 and (
                candidate == identifier * (len(candidate) // len(identifier))):
            return True
        for decoration in ("123", "1234", "!", "!!", "2024", "2025", "2026",
                           "password", "пароль", "admin", "админ"):
            if candidate in (identifier + decoration, decoration + identifier):
                return True
        if len(identifier) >= 5 and (
                candidate.startswith(identifier) or candidate.endswith(identifier)):
            remainder = candidate[len(identifier):] if candidate.startswith(identifier) \
                else candidate[:-len(identifier)]
            if (not remainder or len(remainder) <= 4
                    or not any(char.isalpha() for char in remainder)):
                return True
    return False


def validate_password(password: str, username: str = "",
                       email: str | None = None) -> str | None:
    """Return a user-facing error, or ``None`` when the password is valid.

    ``email`` is optional for forward compatibility.  The current application
    has no email column and callers should leave it unset.
    """
    if not isinstance(password, str):
        return "Пароль должен быть текстом."
    if len(password) < PASSWORD_MIN_LENGTH:
        return (f"Пароль должен содержать не менее {PASSWORD_MIN_LENGTH} "
                "символов Unicode.")
    if len(password) > PASSWORD_MAX_LENGTH:
        return (f"Пароль должен содержать не более {PASSWORD_MAX_LENGTH} "
                "символов Unicode.")
    comparison = _comparison_text(password)
    if comparison in COMMON_PASSWORDS:
        return "Пароль слишком распространённый."
    for common in COMMON_PASSWORDS:
        if len(comparison) <= len(common) or not comparison.startswith(common):
            continue
        suffix = comparison[len(common):]
        # A common word followed by a short numeric/punctuation decoration is
        # still a common password variant (e.g. пароль123456789), while a
        # normal passphrase containing the word is not rejected.
        if len(suffix) <= 16 and (
                suffix.isdigit()
                or (len(suffix) <= 5
                    and all(not char.isalnum() for char in suffix))):
            return "Пароль слишком распространённый."
    if _is_login_derived(password, username, email):
        return "Пароль не должен повторять логин или его простой вариант."
    return None


def password_policy_state(password: str, username: str = "",
                          email: str | None = None) -> dict[str, object]:
    """Small JSON-safe payload for the reusable browser meter."""
    error = validate_password(password, username=username, email=email)
    return {
        "length": len(password) if isinstance(password, str) else 0,
        "minimum": PASSWORD_MIN_LENGTH,
        "plausible": error is None,
        "message": error or "",
    }