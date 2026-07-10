from threadbare.wizard.steps import (
    WIZARD_STEPS,
    is_step_reachable,
    next_step,
    resolve_resume_step,
)


def test_next_step_returns_following_step():
    assert next_step("intro") == "token"
    assert next_step("token") == "invite"
    assert next_step("invite") == "channels"
    assert next_step("channels") == "oauth"
    assert next_step("oauth") == "complete"


def test_next_step_returns_none_after_last_step():
    assert next_step(WIZARD_STEPS[-1]) is None


def test_is_step_reachable_true_for_completed_or_earlier_step():
    assert is_step_reachable("intro", completed_step="channels") is True
    assert is_step_reachable("channels", completed_step="channels") is True


def test_is_step_reachable_true_for_the_immediate_next_step():
    assert is_step_reachable("oauth", completed_step="channels") is True


def test_is_step_reachable_false_for_step_beyond_progress():
    assert is_step_reachable("complete", completed_step="token") is False


def test_resolve_resume_step_bounces_to_token_when_bot_token_missing():
    assert (
        resolve_resume_step(wizard_step="channels", has_bot_token=False, has_client_secret=True)
        == "token"
    )


def test_resolve_resume_step_bounces_to_token_regardless_of_how_far_progressed():
    assert (
        resolve_resume_step(wizard_step="complete", has_bot_token=False, has_client_secret=True)
        == "token"
    )


def test_resolve_resume_step_bounces_to_oauth_when_only_client_secret_missing():
    assert (
        resolve_resume_step(wizard_step="complete", has_bot_token=True, has_client_secret=False)
        == "oauth"
    )


def test_resolve_resume_step_returns_wizard_step_unchanged_when_both_secrets_present():
    assert (
        resolve_resume_step(wizard_step="channels", has_bot_token=True, has_client_secret=True)
        == "channels"
    )


def test_resolve_resume_step_ignores_missing_client_secret_before_oauth_step():
    # Not having reached /oauth yet, the client secret was never expected --
    # this shouldn't bounce anywhere.
    assert (
        resolve_resume_step(wizard_step="channels", has_bot_token=True, has_client_secret=False)
        == "channels"
    )
