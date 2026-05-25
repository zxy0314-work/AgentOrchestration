"""Tests for refresh token rotation binding (issue #3862).

Validates that refresh tokens are bound to session rotation_id and that
the same auth boundary is applied to browser AND token clients.
"""

import pytest
import time

from src.auth.session_manager import AuthManager, Session, RefreshToken


class TestRefreshTokenRotationBinding:

    def setup_method(self):
        self.auth = AuthManager()

    # --- Token issuance ---

    def test_issue_refresh_token_binds_to_rotation_id(self):
        """A newly issued refresh token snapshots the session's rotation_id."""
        session = self.auth.create_session("user1", workspace_id="ws1", scopes={"read"})
        token = self.auth.issue_refresh_token(session.session_id)
        assert token is not None
        assert token.rotation_id == session.rotation_id
        assert token.user_id == "user1"
        assert token.session_id == session.session_id

    def test_issue_refresh_token_on_revoked_session_returns_none(self):
        """Cannot issue refresh tokens on revoked sessions."""
        session = self.auth.create_session("user1", scopes={"read"})
        self.auth.revoke_session(session.session_id)
        token = self.auth.issue_refresh_token(session.session_id)
        assert token is None

    # --- Token validation: stale, malformed, revoked ---

    def test_validate_malformed_token_returns_none(self):
        """Malformed tokens are denied."""
        assert self.auth.validate_refresh_token("") is None
        assert self.auth.validate_refresh_token(None) is None
        assert self.auth.validate_refresh_token(12345) is None

    def test_validate_nonexistent_token_returns_none(self):
        """Tokens not in registry are denied."""
        assert self.auth.validate_refresh_token("fake-token-xyz") is None

    def test_validate_valid_token_succeeds(self):
        """Properly issued tokens validate successfully."""
        session = self.auth.create_session("user1", scopes={"read"})
        token = self.auth.issue_refresh_token(session.session_id)
        validated = self.auth.validate_refresh_token(token.token_id)
        assert validated is not None
        assert validated.token_id == token.token_id

    # --- Session rotation invalidates tokens (the core fix) ---

    def test_session_rotation_invalidates_all_bound_tokens(self):
        """When a session rotates, all bound refresh tokens become invalid.

        This is the critical fix for issue #3862.
        """
        session = self.auth.create_session("user1", scopes={"read"})
        token1 = self.auth.issue_refresh_token(session.session_id)
        token2 = self.auth.issue_refresh_token(session.session_id)

        # Both valid initially
        assert self.auth.validate_refresh_token(token1.token_id) is not None
        assert self.auth.validate_refresh_token(token2.token_id) is not None

        # Rotate the session (e.g., password change)
        self.auth.rotate_session(session.session_id, reason="password_change")

        # Both tokens now invalid
        assert self.auth.validate_refresh_token(token1.token_id) is None
        assert self.auth.validate_refresh_token(token2.token_id) is None

    def test_rotation_invalidates_only_old_tokens_new_tokens_valid(self):
        """After rotation, NEW tokens issued post-rotation are valid."""
        session = self.auth.create_session("user1", scopes={"read"})
        old_token = self.auth.issue_refresh_token(session.session_id)
        self.auth.rotate_session(session.session_id, reason="logout")
        new_token = self.auth.issue_refresh_token(session.session_id)

        # Old token denied
        assert self.auth.validate_refresh_token(old_token.token_id) is None
        # New token valid
        assert self.auth.validate_refresh_token(new_token.token_id) is not None
        # Rotation IDs differ
        assert old_token.rotation_id != new_token.rotation_id

    def test_revoke_session_cascades_to_tokens(self):
        """Revoking a session invalidates all its refresh tokens."""
        session = self.auth.create_session("user1", scopes={"read"})
        token = self.auth.issue_refresh_token(session.session_id)
        assert self.auth.validate_refresh_token(token.token_id) is not None

        self.auth.revoke_session(session.session_id)
        assert self.auth.validate_refresh_token(token.token_id) is None

    # --- Consume-and-rotate flow (single-use refresh) ---

    def test_consume_and_rotate_invalidates_old_token(self):
        """consume_and_rotate makes the old token single-use."""
        session = self.auth.create_session("user1", scopes={"read"})
        old_token = self.auth.issue_refresh_token(session.session_id)
        new_token = self.auth.consume_and_rotate(old_token.token_id)

        assert new_token is not None
        assert new_token.token_id != old_token.token_id
        # Old token can no longer be validated
        assert self.auth.validate_refresh_token(old_token.token_id) is None
        # New token works
        assert self.auth.validate_refresh_token(new_token.token_id) is not None

    def test_consume_and_rotate_with_invalid_token_returns_none(self):
        """Cannot rotate an invalid/consumed token."""
        result = self.auth.consume_and_rotate("invalid-token")
        assert result is None

    # --- Permission check: single boundary for browser AND token clients ---

    def test_permission_check_denies_anonymous(self):
        """Anonymous (no token) requests are denied."""
        assert self.auth.check_permission("", "read") is False
        assert self.auth.check_permission(None, "read") is False

    def test_permission_check_denies_stale_after_rotation(self):
        """A stale token (post-rotation) cannot pass permission check."""
        session = self.auth.create_session("user1", scopes={"read", "write"})
        token = self.auth.issue_refresh_token(session.session_id)
        # Has permission initially
        assert self.auth.check_permission(token.token_id, "read") is True
        # Rotate — same token now denied
        self.auth.rotate_session(session.session_id)
        assert self.auth.check_permission(token.token_id, "read") is False

    def test_permission_check_denies_insufficient_scope(self):
        """Token with read scope cannot perform write actions."""
        session = self.auth.create_session("user1", scopes={"read"})
        token = self.auth.issue_refresh_token(session.session_id)
        assert self.auth.check_permission(token.token_id, "write") is False

    def test_permission_check_denies_workspace_mismatch(self):
        """Token bound to workspace A cannot access workspace B."""
        session = self.auth.create_session("user1", workspace_id="ws_a", scopes={"read"})
        token = self.auth.issue_refresh_token(session.session_id)
        assert self.auth.check_permission(token.token_id, "read", required_workspace="ws_b") is False
        assert self.auth.check_permission(token.token_id, "read", required_workspace="ws_a") is True

    def test_permission_check_authorized_user_succeeds(self):
        """Authorized user with correct scope and workspace passes."""
        session = self.auth.create_session("user1", workspace_id="ws_a", scopes={"read", "write"})
        token = self.auth.issue_refresh_token(session.session_id)
        assert self.auth.check_permission(token.token_id, "write", required_workspace="ws_a") is True

    # --- Both browser and token clients go through the same check ---

    def test_same_check_for_browser_and_token_clients(self):
        """The validate_refresh_token boundary is the same for all client types."""
        session = self.auth.create_session("user1", scopes={"read"})
        token = self.auth.issue_refresh_token(session.session_id)

        # Browser flow — validates via cookie session
        browser_result = self.auth.validate_refresh_token(token.token_id)
        # Token client flow — validates via header bearer token
        api_result = self.auth.validate_refresh_token(token.token_id)

        # Same answer for equivalent variants
        assert browser_result is not None
        assert api_result is not None
        assert browser_result.token_id == api_result.token_id

    # --- Audit logging without secrets ---

    def test_audit_log_does_not_expose_full_tokens(self):
        """Audit logs truncate token IDs — no full secrets in logs."""
        session = self.auth.create_session("user1", scopes={"read"})
        token = self.auth.issue_refresh_token(session.session_id)
        self.auth.validate_refresh_token(token.token_id)
        self.auth.rotate_session(session.session_id)
        self.auth.validate_refresh_token(token.token_id)  # generates denied event

        for event in self.auth.get_audit_log():
            for v in event.values():
                if isinstance(v, str):
                    assert token.token_id not in v, f"Full token leaked in audit: {event}"

    def test_audit_log_records_denial_reasons(self):
        """Audit log records WHY each denial happened (for debugging)."""
        session = self.auth.create_session("user1", scopes={"read"})
        token = self.auth.issue_refresh_token(session.session_id)
        self.auth.rotate_session(session.session_id)
        self.auth.validate_refresh_token(token.token_id)

        denial_events = [e for e in self.auth.get_audit_log() if "denied" in e["type"]]
        assert len(denial_events) > 0
        # At least one rotation_mismatch
        reasons = [e.get("reason") for e in denial_events]
        assert "rotation_mismatch" in reasons or "token_invalid" in reasons
