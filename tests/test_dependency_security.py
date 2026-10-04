"""Guard the authentication dependency's security floor and options isolation."""
from pathlib import Path

import jwt
import pytest
from packaging.version import Version


ROOT = Path(__file__).resolve().parents[1]
TEST_KEY = "garden-jwt-regression-fixture-key-not-a-real-credential"


def test_pyjwt_security_floor_is_installed_and_locked():
    assert Version(jwt.__version__) >= Version("2.15.1")
    assert "pyjwt>=2.15.1,<3" in (ROOT / "requirements.txt").read_text()
    for filename in ("requirements.lock.txt", "requirements-dev.lock.txt"):
        locked = next(line for line in (ROOT / filename).read_text().splitlines() if line.startswith("pyjwt=="))
        assert Version(locked.split("==", 1)[1].split()[0]) >= Version("2.15.1")


def test_reusing_decode_options_does_not_disable_expiry_verification():
    """PYSEC-2026-4146: unverified decode must not mutate the caller's options."""
    token = jwt.encode({"sub": "test-only", "exp": 1}, TEST_KEY, algorithm="HS256")
    options = {"verify_signature": False}
    assert jwt.decode(token, options=options)["sub"] == "test-only"
    assert options == {"verify_signature": False}
    options["verify_signature"] = True
    with pytest.raises(jwt.ExpiredSignatureError):
        jwt.decode(token, TEST_KEY, algorithms=["HS256"], options=options)


def test_signature_and_audience_validation_remain_enabled():
    token = jwt.encode({"sub": "test-only", "aud": "garden-test"}, TEST_KEY, algorithm="HS256")
    assert jwt.decode(token, TEST_KEY, algorithms=["HS256"], audience="garden-test")["sub"] == "test-only"
    with pytest.raises(jwt.InvalidSignatureError):
        jwt.decode(token, TEST_KEY + "-wrong", algorithms=["HS256"], audience="garden-test")
    with pytest.raises(jwt.InvalidAudienceError):
        jwt.decode(token, TEST_KEY, algorithms=["HS256"], audience="wrong-audience")
