"""Pure step-ordering and resume logic for the setup wizard. Two distinct
concerns live here: (1) plain forward/backward navigation
(next_step/is_step_reachable), and (2) session-loss resilience
(resolve_resume_step) -- since only the bot token and OAuth client secret
live in the wizard's ephemeral Flask session (everything else persists in
the wizard_state table), losing that session only ever costs re-pasting
those two secrets, never redoing earlier steps. resolve_resume_step is
what notices a secret is missing and bounces a request back to whichever
step re-collects it, taking precedence over is_step_reachable's "don't
skip ahead" check.
"""

WIZARD_STEPS = ["intro", "token", "invite", "channels", "oauth", "complete"]

_STEPS_AFTER_TOKEN = frozenset(WIZARD_STEPS[WIZARD_STEPS.index("token") + 1 :])
_STEPS_AFTER_OAUTH = frozenset(WIZARD_STEPS[WIZARD_STEPS.index("oauth") + 1 :])


def next_step(current: str) -> str | None:
    idx = WIZARD_STEPS.index(current)
    if idx + 1 >= len(WIZARD_STEPS):
        return None
    return WIZARD_STEPS[idx + 1]


def is_step_reachable(target: str, *, completed_step: str) -> bool:
    return WIZARD_STEPS.index(target) <= WIZARD_STEPS.index(completed_step) + 1


def resolve_resume_step(*, wizard_step: str, has_bot_token: bool, has_client_secret: bool) -> str:
    if wizard_step in _STEPS_AFTER_TOKEN and not has_bot_token:
        return "token"
    if wizard_step in _STEPS_AFTER_OAUTH and not has_client_secret:
        return "oauth"
    return wizard_step
