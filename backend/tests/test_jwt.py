from datetime import UTC, datetime, timedelta

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from personal_os_backend.auth import InvalidAccessToken, JwtValidator
from personal_os_backend.config import Settings


def settings() -> Settings:
    return Settings(
        database_url="postgresql://postgres:password@localhost:5432/postgres",
        supabase_url="https://example.supabase.co",
        allowed_user_id="c3c8d239-162d-4e5e-a2a1-4ac0d1b3695f",
        allowed_email="eahtree@gmail.com",
    )


async def test_validator_checks_signature_issuer_audience_and_claims() -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_jwk = jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key(), as_dict=True)
    public_jwk.update({"kid": "test-key", "alg": "RS256"})
    config = settings()

    def handle(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == config.jwks_url
        return httpx.Response(200, json={"keys": [public_jwk]})

    now = datetime.now(UTC)
    token = jwt.encode(
        {
            "sub": str(config.allowed_user_id),
            "email": config.allowed_email,
            "iss": config.jwt_issuer,
            "aud": config.jwt_audience,
            "iat": now,
            "exp": now + timedelta(minutes=5),
            "user_metadata": {"full_name": "Eah Tree", "avatar_url": "https://example.com/a"},
        },
        private_key,
        algorithm="RS256",
        headers={"kid": "test-key"},
    )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        principal = await JwtValidator(config, client).verify(token)

    assert principal.user_id == config.allowed_user_id
    assert principal.email == config.allowed_email
    assert principal.display_name == "Eah Tree"


async def test_validator_rejects_a_tampered_token() -> None:
    config = settings()
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"keys": []}))
    ) as client:
        with pytest.raises(InvalidAccessToken):
            await JwtValidator(config, client).verify("not-a-jwt")
