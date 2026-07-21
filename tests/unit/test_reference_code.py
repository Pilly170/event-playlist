from app.services.reference_code import ALPHABET, generate_reference_code


def test_generates_a_six_character_code():
    code = generate_reference_code()

    assert len(code) == 6


def test_only_uses_unambiguous_characters():
    code = generate_reference_code()

    assert all(char in ALPHABET for char in code)
    assert not set(code) & set("0O1IL")


def test_generates_different_codes_across_many_calls():
    codes = {generate_reference_code() for _ in range(200)}

    assert len(codes) == 200
