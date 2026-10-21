from marketplace.services import users
from marketplace_api.apis.auth_api_base import BaseAuthApi
from marketplace_api.models.login_request import LoginRequest
from marketplace_api.models.refresh_request import RefreshRequest
from marketplace_api.models.register_request import RegisterRequest
from marketplace_api.models.token_pair import TokenPair
from marketplace_api.models.user_response import UserResponse


class AuthHandler(BaseAuthApi):
    async def register(self, register_request: RegisterRequest) -> UserResponse:
        row = await users.register(register_request.email, register_request.password, register_request.role or "USER")
        return UserResponse(id=row["id"], email=row["email"], role=row["role"], created_at=row["created_at"])

    async def login(self, login_request: LoginRequest) -> TokenPair:
        return TokenPair(**await users.login(login_request.email, login_request.password))

    async def refresh(self, refresh_request: RefreshRequest) -> TokenPair:
        return TokenPair(**await users.refresh(refresh_request.refresh_token))
