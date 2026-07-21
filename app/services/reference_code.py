import secrets

# Excludes 0/O and 1/I/L — easy to misread aloud or on a small screen (SPEC.md §6.1).
ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
_LENGTH = 6


def generate_reference_code() -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(_LENGTH))
