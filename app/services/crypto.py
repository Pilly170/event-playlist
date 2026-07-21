from cryptography.fernet import Fernet


class TokenCipher:
    def __init__(self, key: str):
        self._fernet = Fernet(key.encode())

    def encrypt(self, plaintext: str) -> bytes:
        return self._fernet.encrypt(plaintext.encode())

    def decrypt(self, ciphertext: bytes) -> str:
        return self._fernet.decrypt(ciphertext).decode()
