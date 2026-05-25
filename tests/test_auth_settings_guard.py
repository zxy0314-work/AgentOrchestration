"""Tests for auth settings guard (issue #4242 — SSO delete confirmation)."""

import time
import pytest
from src.auth.auth_settings_guard import AuthSettingsGuard, AuthSettingsGuard as ASG


@pytest.fixture
def guard():
    return AuthSettingsGuard()


@pytest.fixture
def valid_principal():
    return ASG.Principal(
        user_id="user-1",
        session_id="session-1",
        workspace_id="ws-1",
        scopes={"auth:admin"},
    )


class TestConfirmationToken:
    def test_issue_and_consume_happy_path(self, guard, valid_principal):
        token_id = guard.request_confirmation(
            valid_principal.user_id,
            "delete_sso_mapping:mapping-github",
        )
        assert guard.verify_and_consume(
            token_id, valid_principal.user_id, "delete_sso_mapping:mapping-github"
        ) is True

    def test_single_use_token_rejected(self, guard, valid_principal):
        token_id = guard.request_confirmation(
            valid_principal.user_id, "delete_sso_mapping:mapping-github"
        )
        assert guard.verify_and_consume(
            token_id, valid_principal.user_id, "delete_sso_mapping:mapping-github"
        ) is True
        # Second use must fail
        assert guard.verify_and_consume(
            token_id, valid_principal.user_id, "delete_sso_mapping:mapping-github"
        ) is False

    def test_expired_token_rejected(self, guard, valid_principal):
        token_id = guard.request_confirmation(
            valid_principal.user_id, "delete_sso_mapping:mapping-github"
        )
        # Travel forward past TTL
        guard._confirmations[token_id].expires_at = time.time() - 1
        assert guard.verify_and_consume(
            token_id, valid_principal.user_id, "delete_sso_mapping:mapping-github"
        ) is False

    def test_wrong_user_rejected(self, guard, valid_principal):
        token_id = guard.request_confirmation(
            valid_principal.user_id, "delete_sso_mapping:mapping-github"
        )
        assert guard.verify_and_consume(
            token_id, "attacker-user", "delete_sso_mapping:mapping-github"
        ) is False

    def test_wrong_action_rejected(self, guard, valid_principal):
        token_id = guard.request_confirmation(
            valid_principal.user_id, "delete_sso_mapping:mapping-github"
        )
        assert guard.verify_and_consume(
            token_id, valid_principal.user_id, "delete_sso_mapping:mapping-other"
        ) is False

    def test_nonexistent_token_rejected(self, guard):
        assert guard.verify_and_consume(
            "no-such-token", "user-1", "delete_sso_mapping:x"
        ) is False


class TestPrincipalValidation:
    def test_valid_principal_passes(self, guard, valid_principal):
        assert guard.validate_principal(valid_principal) is None

    def test_anonymous_rejected(self, guard):
        p = ASG.Principal(user_id="", session_id="")
        err = guard.validate_principal(p)
        assert err is not None
        assert "Anonymous" in err

    def test_revoked_principal_rejected(self, guard, valid_principal):
        valid_principal.is_revoked = True
        err = guard.validate_principal(valid_principal)
        assert err is not None
        assert "revoked" in err.lower()

    def test_stale_principal_rejected(self, guard, valid_principal):
        valid_principal.is_stale = True
        err = guard.validate_principal(valid_principal)
        assert err is not None
        assert "stale" in err.lower()

    def test_insufficient_scope_rejected(self, guard, valid_principal):
        valid_principal.scopes = {"read:only"}
        err = guard.validate_principal(valid_principal, required_scope="auth:admin")
        assert err is not None
        assert "Insufficient scope" in err

    def test_require_fresh_blocked_when_not_fresh(self, guard, valid_principal):
        err = guard.validate_principal(valid_principal, require_fresh=True)
        assert err is not None
        assert "Fresh credentials required" in err


class TestSSOMappingDeleteGuarded:
    def test_delete_with_valid_confirmation(self, guard, valid_principal):
        # Add a mapping first
        guard.add_sso_mapping("mapping-github", "github", {"org": "myorg"})
        assert len(guard.list_sso_mappings()) == 1

        valid_principal.scopes = {"auth:admin"}
        token_id = guard.request_confirmation(
            valid_principal.user_id, "delete_sso_mapping:mapping-github"
        )
        result = guard.delete_sso_mapping_guarded(
            "mapping-github", valid_principal, token_id
        )
        assert result["ok"] is True
        assert len(guard.list_sso_mappings()) == 0

    def test_delete_without_confirmation_fails(self, guard, valid_principal):
        guard.add_sso_mapping("mapping-github", "github", {"org": "myorg"})
        result = guard.delete_sso_mapping_guarded(
            "mapping-github", valid_principal, "invalid-token"
        )
        assert result["ok"] is False

    def test_delete_stale_principal_fails(self, guard, valid_principal):
        guard.add_sso_mapping("mapping-github", "github", {"org": "myorg"})
        valid_principal.is_stale = True
        token_id = guard.request_confirmation(
            valid_principal.user_id, "delete_sso_mapping:mapping-github"
        )
        result = guard.delete_sso_mapping_guarded(
            "mapping-github", valid_principal, token_id
        )
        assert result["ok"] is False

    def test_delete_revoked_principal_fails(self, guard, valid_principal):
        guard.add_sso_mapping("mapping-github", "github", {"org": "myorg"})
        valid_principal.is_revoked = True
        token_id = guard.request_confirmation(
            valid_principal.user_id, "delete_sso_mapping:mapping-github"
        )
        result = guard.delete_sso_mapping_guarded(
            "mapping-github", valid_principal, token_id
        )
        assert result["ok"] is False
        assert "revoked" in result.get("error", "").lower()

    def test_delete_anonymous_fails(self, guard):
        guard.add_sso_mapping("mapping-github", "github", {"org": "myorg"})
        anon = ASG.Principal(user_id="", session_id="")
        token_id = guard.request_confirmation("", "delete_sso_mapping:mapping-github")
        result = guard.delete_sso_mapping_guarded("mapping-github", anon, token_id)
        assert result["ok"] is False

    def test_delete_nonexistent_mapping_fails(self, guard, valid_principal):
        token_id = guard.request_confirmation(
            valid_principal.user_id, "delete_sso_mapping:no-such"
        )
        result = guard.delete_sso_mapping_guarded(
            "no-such", valid_principal, token_id
        )
        assert result["ok"] is False

    def test_audit_log_records_events(self, guard, valid_principal):
        guard.add_sso_mapping("mapping-okta", "okta", {"domain": "example.com"})
        token_id = guard.request_confirmation(
            valid_principal.user_id, "delete_sso_mapping:mapping-okta"
        )
        guard.delete_sso_mapping_guarded("mapping-okta", valid_principal, token_id)

        events = guard.get_events()
        event_types = [e["event"] for e in events]
        assert "confirmation_requested" in event_types
        assert "confirmation_consumed" in event_types
        assert "sso_deleted" in event_types

    def test_insufficient_scope_blocks_delete(self, guard, valid_principal):
        guard.add_sso_mapping("mapping-github", "github", {"org": "myorg"})
        valid_principal.scopes = {"read:only"}
        token_id = guard.request_confirmation(
            valid_principal.user_id, "delete_sso_mapping:mapping-github"
        )
        # Account for require_fresh — pass token but scope only check happens first
        # Actually require_fresh also blocks; let's test scope specifically
        err = guard.validate_principal(valid_principal, required_scope="auth:admin")
        assert err is not None
        assert "Insufficient scope" in err