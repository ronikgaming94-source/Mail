from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken


class CredentialCipher:
    def __init__(self, key: str) -> None:
        try:
            self.fernet = Fernet(key.encode())
        except (ValueError, TypeError):
            derived = base64.urlsafe_b64encode(hashlib.sha256(key.encode()).digest())
            self.fernet = Fernet(derived)

    def encrypt(self, value: str) -> str:
        return self.fernet.encrypt(value.encode()).decode()

    def decrypt(self, value: str) -> str:
        try:
            return self.fernet.decrypt(value.encode()).decode()
        except InvalidToken as exc:
            raise RuntimeError("Stored mailbox credential cannot be decrypted") from exc
