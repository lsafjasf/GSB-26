"""Cryptographically secure session and salt identifiers."""

from __future__ import annotations

import base64
import os


ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
MIN_SESSION_ID_LENGTH = 32
MIN_SALT_LENGTH = 32
DEFAULT_SESSION_ID_LENGTH = 32
DEFAULT_SALT_LENGTH = 32


def _valid_length(value: object, minimum: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if value < minimum:
        raise ValueError(f"{name} must be at least {minimum} characters")
    return value


def _random_token(length: int) -> str:
    groups, remainder = divmod(length, 4)
    if remainder == 0:
        return base64.urlsafe_b64encode(os.urandom(groups * 3)).decode("ascii")

    groups += 1
    chars = list(base64.urlsafe_b64encode(os.urandom(groups * 3)).decode("ascii"))
    remove_by_remainder = {1: 3, 2: 2, 3: 1}
    del chars[-remove_by_remainder[remainder]:]
    return "".join(chars)


def generate_session_id(length: int = DEFAULT_SESSION_ID_LENGTH) -> str:
    """Return a URL-safe random session identifier."""
    return _random_token(_valid_length(length, MIN_SESSION_ID_LENGTH, "length"))


def generate_salt(length: int = DEFAULT_SALT_LENGTH) -> str:
    """Return a URL-safe random salt."""
    return _random_token(_valid_length(length, MIN_SALT_LENGTH, "length"))


# Generic name for callers that do not distinguish sessions from salts.
generate_id = generate_session_id
