from app.security.auth import hash_password, verify_password


def test_hash_password_does_not_return_the_plaintext():
    hashed = hash_password("correct horse battery staple")

    assert hashed != "correct horse battery staple"


def test_verify_password_accepts_the_correct_password():
    hashed = hash_password("correct horse battery staple")

    assert verify_password("correct horse battery staple", hashed) is True


def test_verify_password_rejects_an_incorrect_password():
    hashed = hash_password("correct horse battery staple")

    assert verify_password("wrong password", hashed) is False
