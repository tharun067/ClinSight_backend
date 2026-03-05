import bcrypt


def hash_password(password: str) -> str:
    """
    Hash a plaintext password using bcrypt.
    Raises ValueError if password exceeds 72 bytes (bcrypt hard limit).
    """
    password_bytes = password.encode("utf-8")
    if len(password_bytes) > 72:
        raise ValueError(
            "Password must not exceed 72 bytes when UTF-8 encoded. "
            "Please use a shorter password."
        )
    salt = bcrypt.gensalt(rounds=12)
    hashed = bcrypt.hashpw(password_bytes, salt)
    return hashed.decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Verify a plaintext password against a hashed password.
    Returns False on any error (including malformed hash).
    """
    try:
        password_bytes = plain_password.encode("utf-8")
        if len(password_bytes) > 72:
            return False
        return bcrypt.checkpw(password_bytes, hashed_password.encode("utf-8"))
    except Exception:
        return False