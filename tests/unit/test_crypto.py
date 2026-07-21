from cryptography.fernet import Fernet

from app.services.crypto import TokenCipher


def test_encrypt_then_decrypt_returns_original_plaintext():
    cipher = TokenCipher(key=Fernet.generate_key().decode())

    ciphertext = cipher.encrypt("a-refresh-token")

    assert cipher.decrypt(ciphertext) == "a-refresh-token"


def test_encrypt_output_does_not_contain_plaintext():
    cipher = TokenCipher(key=Fernet.generate_key().decode())

    ciphertext = cipher.encrypt("a-refresh-token")

    assert b"a-refresh-token" not in ciphertext
