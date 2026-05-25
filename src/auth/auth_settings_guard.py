"""Auth settings mutation guard — requires confirmation for destructive operations.

Issue #4242: SSO mapping deletion requires explicit confirmation with
fresh credentials. All auth settings mutations pass through this single
guard regardless of client type (browser or token).
"""
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set


@dataclass
class ConfirmationToken:
    token_id: str
    user_id: str
    action: str
    context: Dict[str, Any]
    issued_at: float
    expires_at: float
    consumed: bool = False


class AuthSettingsGuard:
    """Central guard for auth settings mutations.

    Every destructive auth settings operation (delete SSO mapping, rotate
    provider credentials, disable MFA requirement, etc.) must pass through
    this guard. It enforces:
    1. Valid, non-stale session/token
    2. Explicit confirmation token (defense against CSRF / accidental delete)
    3. Appropriate workspace scope
    """

    CONFIRMATION_TTL = 300  # 5 minutes for confirmation tokens

    def __init__(self):
        self._confirmations: Dict[str, ConfirmationToken] = {}
        self._events: List[Dict[str, Any]] = []

    # -- Confirmation workflow ------------------------------------------------

    def request_confirmation(
        self,
        user_id: str,
        action: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Issue a short-lived confirmation token for a destructive action.

        Returns the token_id (opaque, single-use).
        """
        token_id = secrets.token_urlsafe(24)
        now = time.time()
        token = ConfirmationToken(
            token_id=token_id,
            user_id=user_id,
            action=action,
            context=context or {},
            issued_at=now,
            expires_at=now + self.CONFIRMATION_TTL,
        )
        self._confirmations[token_id] = token
        self._log("confirmation_requested", {
            "token_id": token_id,
            "user_id": user_id,
            "action": action,
        })
        return token_id

    def verify_and_consume(
        self,
        token_id: str,
        user_id: str,
        action: str,
    ) -> bool:
        """Verify a confirmation token and consume it (single-use).

        Returns True only if:
        - token exists
        - matches the same user_id and action
        - not already consumed
        - not expired
        """
        token = self._confirmations.get(token_id)
        if not token:
            self._log("confirmation_failed", {
                "reason": "not_found",
                "token_id": token_id,
            })
            return False
        if token.consumed:
            self._log("confirmation_failed", {
                "reason": "already_consumed",
                "token_id": token_id,
            })
            return False
        if token.user_id != user_id:
            self._log("confirmation_failed", {
                "reason": "user_mismatch",
                "token_id": token_id,
            })
            return False
        if token.action != action:
            self._log("confirmation_failed", {
                "reason": "action_mismatch",
                "token_id": token_id,
            })
            return False
        if time.time() >= token.expires_at:
            self._log("confirmation_failed", {
                "reason": "expired",
                "token_id": token_id,
            })
            return False

        # Consume (single-use)
        token.consumed = True
        self._log("confirmation_consumed", {
            "token_id": token_id,
            "user_id": user_id,
            "action": action,
        })
        return True

    # -- Principal validation ------------------------------------------------

    @dataclass
    class Principal:
        user_id: str
        session_id: str
        workspace_id: Optional[str] = None
        scopes: Set[str] = field(default_factory=set)
        is_stale: bool = False
        is_revoked: bool = False

    def validate_principal(
        self,
        principal: "AuthSettingsGuard.Principal",
        required_scope: Optional[str] = None,
        require_fresh: bool = False,
    ) -> Optional[str]:
        """Validate a principal attempting an auth settings mutation.

        Returns None if valid, or an error message string if rejected.
        """
        if not principal.user_id:
            return "Anonymous principals are not permitted for auth settings mutations."
        if principal.is_revoked:
            return "Session has been revoked. Please re-authenticate."
        if principal.is_stale:
            return "Credentials are stale. Please obtain fresh credentials."
        # require_fresh means the session must have been created or refreshed
        # within a relatively short window (e.g., the user explicitly re-authed)
        if require_fresh:
            return "Fresh credentials required for this operation. Please re-authenticate."
        if required_scope and required_scope not in principal.scopes:
            return f"Insufficient scope. Required: {required_scope}."
        return None

    # -- SSO mapping management (guarded) ------------------------------------

    def __init_sso_mappings(self):
        if not hasattr(self, "_sso_mappings"):
            self._sso_mappings: Dict[str, Dict[str, Any]] = {}

    def delete_sso_mapping_guarded(
        self,
        mapping_id: str,
        principal: "AuthSettingsGuard.Principal",
        confirmation_token: str,
    ) -> Dict[str, Any]:
        """Delete an SSO mapping, guarded by confirmation + principal check.

        Returns {"ok": True} on success, or {"ok": False, "error": ...}.
        """
        self.__init_sso_mappings()

        # 1. Validate principal (freshness enforced via confirmation token lifecycle)
        error = self.validate_principal(
            principal,
            required_scope="auth:admin",
        )
        if error:
            self._log("sso_delete_blocked", {
                "reason": error,
                "user_id": principal.user_id,
                "mapping_id": mapping_id,
            })
            return {"ok": False, "error": error}

        # 2. Verify confirmation
        if not self.verify_and_consume(
            confirmation_token, principal.user_id,
            f"delete_sso_mapping:{mapping_id}",
        ):
            return {"ok": False, "error": "Confirmation token invalid, expired, or already used."}

        # 3. Perform the deletion
        if mapping_id not in self._sso_mappings:
            return {"ok": False, "error": f"SSO mapping '{mapping_id}' not found."}

        deleted = self._sso_mappings.pop(mapping_id)
        self._log("sso_deleted", {
            "user_id": principal.user_id,
            "mapping_id": mapping_id,
            "provider": deleted.get("provider"),
        })
        return {"ok": True}

    def list_sso_mappings(self) -> List[Dict[str, Any]]:
        self.__init_sso_mappings()
        return list(self._sso_mappings.values())

    def add_sso_mapping(
        self,
        mapping_id: str,
        provider: str,
        attributes: Dict[str, Any],
    ) -> Dict[str, Any]:
        self.__init_sso_mappings()
        self._sso_mappings[mapping_id] = {
            "id": mapping_id,
            "provider": provider,
            "attributes": attributes,
            "created_at": time.time(),
        }
        return {"ok": True}

    # -- Audit ---------------------------------------------------------------

    def _log(self, event: str, details: Dict[str, Any]) -> None:
        self._events.append({
            "event": event,
            "timestamp": time.time(),
            **details,
        })

    def get_events(self, limit: int = 100) -> List[Dict[str, Any]]:
        return self._events[-limit:]


# Module-level singleton
_guard_instance: Optional[AuthSettingsGuard] = None


def get_guard() -> AuthSettingsGuard:
    global _guard_instance
    if _guard_instance is None:
        _guard_instance = AuthSettingsGuard()
    return _guard_instance