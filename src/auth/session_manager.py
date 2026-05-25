"""Auth session manager with refresh-token-to-session-rotation binding.

Fixes issue #3862: refresh tokens must be bound to session lifecycle.
When a session rotates (logout, password change, manual revoke), all
refresh tokens issued under the old session must be invalidated.
"""

import hashlib
import hmac
import os
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set


@dataclass
class Session:
    session_id: str
    user_id: str
    workspace_id: Optional[str] = None
    rotation_id: str = ""  # rotates on logout, password change, manual revoke
    created_at: float = field(default_factory=time.time)
    expires_at: float = 0.0
    scopes: Set[str] = field(default_factory=set)
    revoked: bool = False

    def is_valid(self, now: Optional[float] = None) -> bool:
        if now is None:
            now = time.time()
        return not self.revoked and (self.expires_at == 0 or now < self.expires_at)


@dataclass
class RefreshToken:
    token_id: str
    session_id: str
    rotation_id: str  # snapshot of session rotation_id at issuance
    user_id: str
    issued_at: float
    expires_at: float
    consumed: bool = False
    parent_token_id: Optional[str] = None  # chain for rotation tracking

    def is_valid(self, session_rotation_id: str, now: Optional[float] = None) -> bool:
        if now is None:
            now = time.time()
        # Token must NOT be consumed, must NOT be expired, AND
        # rotation_id must match current session rotation_id
        return (
            not self.consumed
            and now < self.expires_at
            and self.rotation_id == session_rotation_id
        )


class AuthManager:
    """Central authentication service. All requests pass through this single
    boundary regardless of whether they come from browser or token clients.
    """

    REFRESH_TOKEN_TTL = 30 * 24 * 3600  # 30 days
    SESSION_TTL = 24 * 3600  # 24 hours

    def __init__(self):
        self._sessions: Dict[str, Session] = {}
        self._refresh_tokens: Dict[str, RefreshToken] = {}
        # Tokens grouped by session for fast bulk revocation on rotation
        self._tokens_by_session: Dict[str, Set[str]] = {}
        # Audit log
        self._events: List[Dict[str, Any]] = []

    # -------------------- Session lifecycle --------------------

    def create_session(self, user_id: str, workspace_id: Optional[str] = None,
                        scopes: Optional[Set[str]] = None) -> Session:
        session_id = secrets.token_urlsafe(32)
        rotation_id = secrets.token_urlsafe(16)
        session = Session(
            session_id=session_id,
            user_id=user_id,
            workspace_id=workspace_id,
            rotation_id=rotation_id,
            expires_at=time.time() + self.SESSION_TTL,
            scopes=scopes or set(),
        )
        self._sessions[session_id] = session
        self._tokens_by_session[session_id] = set()
        self._log("session_created", {"session_id": session_id, "user_id": user_id})
        return session

    def rotate_session(self, session_id: str, reason: str = "manual") -> bool:
        """Rotate the session — invalidates ALL refresh tokens bound to it.

        Called on:
        - User logout (explicit revoke)
        - Password change
        - Suspicious activity / manual admin revoke
        - Workspace role change
        """
        session = self._sessions.get(session_id)
        if not session:
            return False

        # Generate new rotation_id
        old_rotation_id = session.rotation_id
        session.rotation_id = secrets.token_urlsafe(16)

        # Invalidate all refresh tokens bound to the old rotation_id
        invalidated = 0
        for token_id in list(self._tokens_by_session.get(session_id, set())):
            token = self._refresh_tokens.get(token_id)
            if token and token.rotation_id == old_rotation_id:
                token.consumed = True
                invalidated += 1

        self._log("session_rotated", {
            "session_id": session_id,
            "reason": reason,
            "tokens_invalidated": invalidated,
        })
        return True

    def revoke_session(self, session_id: str) -> bool:
        """Revoke a session entirely. All bound refresh tokens become invalid."""
        session = self._sessions.get(session_id)
        if not session:
            return False
        session.revoked = True
        # Cascade: invalidate all refresh tokens
        for token_id in self._tokens_by_session.get(session_id, set()):
            token = self._refresh_tokens.get(token_id)
            if token:
                token.consumed = True
        self._log("session_revoked", {"session_id": session_id})
        return True

    # -------------------- Refresh token operations --------------------

    def issue_refresh_token(self, session_id: str, parent_token_id: Optional[str] = None) -> Optional[RefreshToken]:
        """Issue a refresh token bound to the current session rotation_id."""
        session = self._sessions.get(session_id)
        if not session or not session.is_valid():
            self._log("refresh_issue_denied", {"session_id": session_id, "reason": "invalid_session"})
            return None

        token_id = secrets.token_urlsafe(32)
        token = RefreshToken(
            token_id=token_id,
            session_id=session_id,
            rotation_id=session.rotation_id,  # bind to current rotation
            user_id=session.user_id,
            issued_at=time.time(),
            expires_at=time.time() + self.REFRESH_TOKEN_TTL,
            parent_token_id=parent_token_id,
        )
        self._refresh_tokens[token_id] = token
        self._tokens_by_session.setdefault(session_id, set()).add(token_id)
        self._log("refresh_token_issued", {"token_id": token_id[:8] + "...", "session_id": session_id})
        return token

    def validate_refresh_token(self, token_id: str) -> Optional[RefreshToken]:
        """Validate a refresh token. Single boundary used by ALL request variants.

        Returns the token if valid, None if rejected. Stale, malformed,
        revoked, or rotation-mismatched tokens all return None consistently.
        """
        if not token_id or not isinstance(token_id, str):
            self._log("refresh_validate_denied", {"reason": "malformed"})
            return None

        token = self._refresh_tokens.get(token_id)
        if not token:
            self._log("refresh_validate_denied", {"reason": "not_found", "token_id": token_id[:8] + "..."})
            return None

        session = self._sessions.get(token.session_id)
        if not session:
            self._log("refresh_validate_denied", {"reason": "session_missing", "token_id": token_id[:8] + "..."})
            return None

        if not session.is_valid():
            self._log("refresh_validate_denied", {"reason": "session_invalid", "token_id": token_id[:8] + "..."})
            return None

        # CRITICAL: rotation_id must match — this is the bind
        if token.rotation_id != session.rotation_id:
            self._log("refresh_validate_denied", {
                "reason": "rotation_mismatch",
                "token_id": token_id[:8] + "...",
            })
            return None

        if not token.is_valid(session.rotation_id):
            self._log("refresh_validate_denied", {"reason": "token_invalid", "token_id": token_id[:8] + "..."})
            return None

        return token

    def consume_and_rotate(self, token_id: str) -> Optional[RefreshToken]:
        """Consume an old refresh token and issue a new one (token rotation).

        This is the standard refresh flow: old token becomes single-use.
        """
        token = self.validate_refresh_token(token_id)
        if not token:
            return None
        token.consumed = True
        return self.issue_refresh_token(token.session_id, parent_token_id=token_id)

    # -------------------- Permission check (single boundary) --------------------

    def check_permission(self, token_id: str, required_scope: str,
                          required_workspace: Optional[str] = None) -> bool:
        """Single auth boundary. Same check whether browser or token client.

        Returns True only if:
        - Refresh token is valid
        - Session is bound and active
        - Required scope is held
        - Workspace matches (if required)
        """
        token = self.validate_refresh_token(token_id)
        if not token:
            return False

        session = self._sessions.get(token.session_id)
        if not session:
            return False

        if required_scope not in session.scopes:
            self._log("permission_denied", {
                "reason": "insufficient_scope",
                "required": required_scope,
            })
            return False

        if required_workspace and session.workspace_id != required_workspace:
            self._log("permission_denied", {
                "reason": "workspace_mismatch",
                "required": required_workspace,
            })
            return False

        return True

    # -------------------- Audit --------------------

    def _log(self, event_type: str, data: Dict[str, Any]) -> None:
        # Bounded audit log
        if len(self._events) >= 1000:
            self._events = self._events[-999:]
        self._events.append({
            "type": event_type,
            "timestamp": time.time(),
            **data,
        })

    def get_audit_log(self) -> List[Dict[str, Any]]:
        return list(self._events)
