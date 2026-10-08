from dataclasses import dataclass
from uuid import UUID

from pydantic import BaseModel


@dataclass(frozen=True, slots=True)
class Principal:
    user_id: UUID
    email: str
    display_name: str
    avatar_url: str | None


class AccountResponse(BaseModel):
    user_id: UUID
    email: str
    display_name: str
    avatar_url: str | None

    @classmethod
    def from_principal(cls, principal: Principal) -> "AccountResponse":
        return cls(
            user_id=principal.user_id,
            email=principal.email,
            display_name=principal.display_name,
            avatar_url=principal.avatar_url,
        )


class HealthResponse(BaseModel):
    status: str
