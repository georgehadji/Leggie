"""Tests for the Flow State Machine — pure state transitions."""

import pytest
from leggie.application.workflow.flow_state_machine import FlowStateMachine
from leggie.domain.models import WorkflowState


class TestFlowStateMachine:
    def test_transition_idle_to_ingesting(self):
        result = FlowStateMachine.transition(WorkflowState.IDLE, "ingest_started")
        assert result == WorkflowState.INGESTING

    def test_transition_executing_to_aggregating(self):
        result = FlowStateMachine.transition(WorkflowState.EXECUTING, "execution_completed")
        assert result == WorkflowState.AGGREGATING

    def test_transition_reporting_to_done(self):
        result = FlowStateMachine.transition(WorkflowState.REPORTING, "report_completed")
        assert result == WorkflowState.DONE

    def test_transition_verify_failed_returns_to_executing(self):
        result = FlowStateMachine.transition(WorkflowState.VERIFYING, "verify_failed")
        assert result == WorkflowState.EXECUTING

    def test_transition_any_to_failed(self):
        result = FlowStateMachine.transition(WorkflowState.EXECUTING, "execution_failed")
        assert result == WorkflowState.FAILED

    def test_transition_undefined_returns_none(self):
        result = FlowStateMachine.transition(WorkflowState.IDLE, "nonexistent_event")
        assert result is None

    def test_can_transition_true(self):
        assert FlowStateMachine.can_transition(WorkflowState.IDLE, "ingest_started") is True

    def test_can_transition_false(self):
        assert FlowStateMachine.can_transition(WorkflowState.IDLE, "report_completed") is False

    def test_valid_events_for_idle(self):
        events = FlowStateMachine.valid_events_for(WorkflowState.IDLE)
        assert "ingest_started" in events
        assert len(events) == 1

    def test_valid_events_for_executing(self):
        events = FlowStateMachine.valid_events_for(WorkflowState.EXECUTING)
        assert "execution_completed" in events
        assert "execution_failed" in events

    def test_terminal_states(self):
        terminal = FlowStateMachine.terminal_states()
        assert WorkflowState.DONE in terminal
        assert WorkflowState.FAILED in terminal
        assert WorkflowState.IDLE not in terminal

    def test_is_terminal(self):
        assert FlowStateMachine.is_terminal(WorkflowState.DONE) is True
        assert FlowStateMachine.is_terminal(WorkflowState.IDLE) is False

    def test_is_error_state(self):
        assert FlowStateMachine.is_error_state(WorkflowState.FAILED) is True
        assert FlowStateMachine.is_error_state(WorkflowState.DONE) is False
