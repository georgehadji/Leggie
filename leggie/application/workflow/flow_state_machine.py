"""FlowStateMachine — pure state-transition logic for Leggie's analysis workflow.

Adapted from weebot's flow_state_machine.py. Pure transition-table FSM.
Does NOT hold references to any flow, session, or tool objects.
"""

from __future__ import annotations

from leggie.domain.models import WorkflowState


# State transition rules: (current_state, event_type) → next_state
# event_type strings describe what happened to trigger the transition.
_TRANSITION_TABLE: dict[tuple[WorkflowState, str], WorkflowState] = {
    (WorkflowState.IDLE, "ingest_started"): WorkflowState.INGESTING,
    (WorkflowState.INGESTING, "ingest_completed"): WorkflowState.PARSING,
    (WorkflowState.PARSING, "parse_completed"): WorkflowState.PLANNING,
    (WorkflowState.PLANNING, "plan_approved"): WorkflowState.EXECUTING,
    (WorkflowState.EXECUTING, "execution_completed"): WorkflowState.AGGREGATING,
    (WorkflowState.AGGREGATING, "aggregation_completed"): WorkflowState.VERIFYING,
    (WorkflowState.VERIFYING, "verify_passed"): WorkflowState.IMPROVING,
    (WorkflowState.VERIFYING, "verify_failed"): WorkflowState.EXECUTING,
    (WorkflowState.IMPROVING, "improvement_completed"): WorkflowState.REPORTING,
    (WorkflowState.REPORTING, "report_completed"): WorkflowState.DONE,
    # Error transitions
    (WorkflowState.INGESTING, "ingest_failed"): WorkflowState.FAILED,
    (WorkflowState.PARSING, "parse_failed"): WorkflowState.FAILED,
    (WorkflowState.PLANNING, "plan_failed"): WorkflowState.FAILED,
    (WorkflowState.EXECUTING, "execution_failed"): WorkflowState.FAILED,
    (WorkflowState.AGGREGATING, "aggregation_failed"): WorkflowState.FAILED,
    (WorkflowState.VERIFYING, "verify_failed_abort"): WorkflowState.FAILED,
    (WorkflowState.IMPROVING, "improvement_failed"): WorkflowState.FAILED,
    (WorkflowState.REPORTING, "report_failed"): WorkflowState.FAILED,
}


class FlowStateMachine:
    """Pure state machine for Leggie's bill analysis workflow.

    Does NOT hold references to any flow, session, or tool objects.
    """

    @staticmethod
    def transition(current: WorkflowState, event: str) -> WorkflowState | None:
        """Return the next state given the current state and event.

        Args:
            current: The current WorkflowState.
            event: The event type string (e.g. "execution_completed").

        Returns:
            The next WorkflowState, or None if no transition is defined.
        """
        return _TRANSITION_TABLE.get((current, event))

    @staticmethod
    def can_transition(current: WorkflowState, event: str) -> bool:
        """Check whether a transition is defined for this (state, event) pair."""
        return (current, event) in _TRANSITION_TABLE

    @staticmethod
    def valid_events_for(state: WorkflowState) -> list[str]:
        """Return all valid event types for a given state."""
        return [event for (s, event) in _TRANSITION_TABLE if s == state]

    @staticmethod
    def terminal_states() -> set[WorkflowState]:
        """Return states that are terminal (no outgoing transitions)."""
        outgoing = {s for (s, _) in _TRANSITION_TABLE}
        return {s for s in WorkflowState if s not in outgoing}

    @staticmethod
    def is_terminal(state: WorkflowState) -> bool:
        """Check if a state is terminal (no outgoing transitions)."""
        return state in FlowStateMachine.terminal_states()

    @staticmethod
    def is_error_state(state: WorkflowState) -> bool:
        """Check if a state is an error/failure state."""
        return state == WorkflowState.FAILED
