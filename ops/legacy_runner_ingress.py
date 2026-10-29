"""Explicit compatibility adapters for pre-formal Runner input APIs.

The default product never imports this module to choose formal behavior.  It
exists so archived tools and explicitly non-formal Guild/Actor compositions can
continue to translate their unscoped text input into the canonical received
event transaction without pretending that a Conversation delivery exists.
"""

from ops.received_participant_events import (
    legacy_external_input,
    legacy_peer_input,
)


def _compat_received_event(text, response_mode):
    if response_mode not in {"required", "peer"}:
        raise ValueError("未知回复入口模式")
    return (legacy_external_input(text) if response_mode == "required"
            else legacy_peer_input(text))


def submit_compat_runner_message(
    runner,
    text,
    *,
    now_ms=None,
    delay_ms=None,
    explicit_feedback=None,
    response_mode="required",
    on_prepared=None,
    on_draft=None,
    should_continue=None,
    begin_commit=None,
    ready_clock=None,
):
    """Run one explicitly unscoped compatibility input transaction."""
    received_event = _compat_received_event(text, response_mode)
    return runner.on_received_participant_event(
        received_event,
        now_ms=now_ms,
        delay_ms=delay_ms,
        explicit_feedback=explicit_feedback,
        _on_prepared=on_prepared,
        _on_draft=on_draft,
        _should_continue=should_continue,
        _begin_commit=begin_commit,
        _ready_clock=ready_clock,
    )


def prepare_compat_runner_message(
    runner,
    text,
    *,
    now_ms=None,
    delay_ms=None,
    explicit_feedback=None,
    response_mode="required",
    on_prepared=None,
    on_draft=None,
    should_continue=None,
    begin_commit=None,
    ready_clock=None,
):
    """Prepare one explicitly unscoped compatibility input transaction."""
    received_event = _compat_received_event(text, response_mode)
    return runner.prepare_received_participant_event(
        received_event,
        now_ms=now_ms,
        delay_ms=delay_ms,
        explicit_feedback=explicit_feedback,
        _on_prepared=on_prepared,
        _on_draft=on_draft,
        _should_continue=should_continue,
        _begin_commit=begin_commit,
        _ready_clock=ready_clock,
    )


class LegacyRunnerIngressMixin:
    """Compatibility surface deliberately excluded from the product default."""

    def on_user_message(self, text, now_ms=None, delay_ms=None, explicit_feedback=None,
                        _on_prepared=None, _on_draft=None, _should_continue=None,
                        _begin_commit=None, _ready_clock=None, _response_mode="required"):
        return submit_compat_runner_message(
            self,
            text,
            now_ms=now_ms,
            delay_ms=delay_ms,
            explicit_feedback=explicit_feedback,
            response_mode=_response_mode,
            on_prepared=_on_prepared,
            on_draft=_on_draft,
            should_continue=_should_continue,
            begin_commit=_begin_commit,
            ready_clock=_ready_clock,
        )

    def prepare_user_message(self, text, now_ms=None, delay_ms=None, explicit_feedback=None,
                             _on_prepared=None, _on_draft=None, _should_continue=None,
                             _begin_commit=None, _ready_clock=None, _response_mode="required"):
        return prepare_compat_runner_message(
            self,
            text,
            now_ms=now_ms,
            delay_ms=delay_ms,
            explicit_feedback=explicit_feedback,
            response_mode=_response_mode,
            on_prepared=_on_prepared,
            on_draft=_on_draft,
            should_continue=_should_continue,
            begin_commit=_begin_commit,
            ready_clock=_ready_clock,
        )

    def on_peer_message(self, text, now_ms=None, delay_ms=None):
        return submit_compat_runner_message(
            self,
            text,
            now_ms=now_ms,
            delay_ms=delay_ms,
            response_mode="peer",
        )
