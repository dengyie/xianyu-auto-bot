"""Account ownership policy shared by account-scoped use cases."""

from __future__ import annotations

from typing import Any, Protocol


class AccountRepository(Protocol):
    def get_all_cookies(self, user_id: int) -> dict[str, str]: ...


class AccountOwnershipError(Exception):
    """Base error for account ownership checks."""


class MissingAccountId(AccountOwnershipError):
    pass


class AccountForbidden(AccountOwnershipError):
    pass


class AccountOwnershipPolicy:
    def __init__(self, repository: AccountRepository):
        self.repository = repository

    def require_owned_account(self, user_id: int, account_id: Any) -> str:
        cleaned_id = str(account_id or "").strip()
        if not cleaned_id:
            raise MissingAccountId
        owned_accounts = self.repository.get_all_cookies(user_id)
        if cleaned_id not in owned_accounts:
            raise AccountForbidden
        return cleaned_id
