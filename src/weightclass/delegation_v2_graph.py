"""Pure delegation protocol-2 graph validation through frozen stage 10."""

from __future__ import annotations

import heapq
from dataclasses import dataclass

from .delegation_v2_permissions import validate_write_scope_conflicts
from .delegation_v2_schema import DelegationV2InvalidInputError
from .delegation_v2_types import DelegationOutputV2, DelegationWorkflowV2


@dataclass(frozen=True, slots=True)
class ValidatedDelegationGraphV2:
    """Value-free topology produced after validation stages 3 through 10."""

    topological_task_ids: tuple[str, ...]


def _unique(values: list[str]) -> None:
    if len(values) != len(set(values)):
        raise DelegationV2InvalidInputError()


def _global_namespaces(workflow: DelegationWorkflowV2) -> None:
    _unique([task.task_id for task in workflow.tasks])
    _unique([edge.dependency_id for edge in workflow.dependencies])
    _unique([gate.gate_id for gate in workflow.gates])
    _unique(
        [projection.projection_id for task in workflow.tasks for projection in task.projections]
    )
    _unique([output.output_id for task in workflow.tasks for output in task.outputs])
    _unique(
        [
            artifact.artifact_id
            for task in workflow.tasks
            for output in task.outputs
            for artifact in output.artifacts
        ]
    )


def _references_and_ownership(
    workflow: DelegationWorkflowV2,
) -> tuple[dict[str, DelegationOutputV2], dict[str, str]]:
    task_ids = {task.task_id for task in workflow.tasks}
    outputs: dict[str, DelegationOutputV2] = {}
    owners: dict[str, str] = {}
    for task in workflow.tasks:
        for output in task.outputs:
            outputs[output.output_id] = output
            owners[output.output_id] = task.task_id

    for edge in workflow.dependencies:
        if edge.from_task_id not in task_ids or edge.to_task_id not in task_ids:
            raise DelegationV2InvalidInputError()
    for gate in workflow.gates:
        if (
            gate.from_task_id not in task_ids
            or gate.to_task_id not in task_ids
            or gate.output.producer_task_id not in task_ids
            or owners.get(gate.output.producer_output_id) != gate.output.producer_task_id
        ):
            raise DelegationV2InvalidInputError()
    for task in workflow.tasks:
        for projection in task.projections:
            if (
                projection.producer_task_id not in task_ids
                or owners.get(projection.producer_output_id) != projection.producer_task_id
            ):
                raise DelegationV2InvalidInputError()
    return outputs, owners


def _edges(workflow: DelegationWorkflowV2) -> tuple[tuple[str, str], ...]:
    dependency_pairs = [(edge.from_task_id, edge.to_task_id) for edge in workflow.dependencies]
    gate_pairs = [(gate.from_task_id, gate.to_task_id) for gate in workflow.gates]
    if (
        len(dependency_pairs) != len(set(dependency_pairs))
        or len(gate_pairs) != len(set(gate_pairs))
        or any(source == target for source, target in dependency_pairs + gate_pairs)
        or any(gate.output.producer_task_id != gate.from_task_id for gate in workflow.gates)
    ):
        raise DelegationV2InvalidInputError()
    return tuple(dependency_pairs + gate_pairs)


def _topology(
    workflow: DelegationWorkflowV2, edges: tuple[tuple[str, str], ...]
) -> tuple[tuple[str, ...], dict[str, set[str]], dict[str, set[str]]]:
    outgoing: dict[str, set[str]] = {task.task_id: set() for task in workflow.tasks}
    incoming: dict[str, set[str]] = {task.task_id: set() for task in workflow.tasks}
    for source, target in edges:
        outgoing[source].add(target)
        incoming[target].add(source)
    remaining = {task_id: len(sources) for task_id, sources in incoming.items()}
    ready = [task_id for task_id, count in remaining.items() if count == 0]
    heapq.heapify(ready)
    ordered: list[str] = []
    while ready:
        task_id = heapq.heappop(ready)
        ordered.append(task_id)
        for target in sorted(outgoing[task_id]):
            remaining[target] -= 1
            if remaining[target] == 0:
                heapq.heappush(ready, target)
    if len(ordered) != len(workflow.tasks):
        raise DelegationV2InvalidInputError()
    return tuple(ordered), outgoing, incoming


def _terminal(
    workflow: DelegationWorkflowV2,
    outgoing: dict[str, set[str]],
    incoming: dict[str, set[str]],
) -> None:
    synthesizers = [task.task_id for task in workflow.tasks if task.request.mode == "synthesizer"]
    if workflow.terminal_mode == "raw_independent":
        if workflow.terminal_task_id is not None or synthesizers:
            raise DelegationV2InvalidInputError()
        return
    terminal = workflow.terminal_task_id
    if terminal is None or synthesizers != [terminal] or outgoing.get(terminal) != set():
        raise DelegationV2InvalidInputError()
    if sum(not targets for targets in outgoing.values()) != 1:
        raise DelegationV2InvalidInputError()
    # Traverse reverse edges without relying on a roots invariant.
    pending = [terminal]
    reached = {terminal}
    while pending:
        current = pending.pop()
        for source in incoming[current]:
            if source not in reached:
                reached.add(source)
                pending.append(source)
    if len(reached) != len(workflow.tasks):
        raise DelegationV2InvalidInputError()


def _projections(workflow: DelegationWorkflowV2, outputs: dict[str, DelegationOutputV2]) -> None:
    for task in workflow.tasks:
        if len(task.inputs) != len(task.projections):
            raise DelegationV2InvalidInputError()
        inputs = {input_value.input_id: input_value for input_value in task.inputs}
        binding_counts = {input_id: 0 for input_id in inputs}
        for projection in task.projections:
            input_value = inputs.get(projection.input_id)
            output = outputs.get(projection.producer_output_id)
            if (
                input_value is None
                or output is None
                or input_value.input_type != output.output_type
            ):
                raise DelegationV2InvalidInputError()
            binding_counts[projection.input_id] += 1
        if any(count != 1 for count in binding_counts.values()):
            raise DelegationV2InvalidInputError()


def _strict_ancestry(
    workflow: DelegationWorkflowV2,
    incoming: dict[str, set[str]],
) -> None:
    ancestors: dict[str, set[str]] = {}
    for task in workflow.tasks:
        found: set[str] = set()
        pending = list(incoming[task.task_id])
        while pending:
            parent = pending.pop()
            if parent not in found:
                found.add(parent)
                pending.extend(incoming[parent])
        ancestors[task.task_id] = found
    for task in workflow.tasks:
        if any(
            projection.producer_task_id not in ancestors[task.task_id]
            for projection in task.projections
        ):
            raise DelegationV2InvalidInputError()


def validate_delegation_v2_graph(
    workflow: DelegationWorkflowV2,
) -> ValidatedDelegationGraphV2:
    """Validate frozen stages 3 through 10 in their normative order."""
    _global_namespaces(workflow)  # stage 3
    outputs, _ = _references_and_ownership(workflow)  # stage 4
    edges = _edges(workflow)  # stage 5
    topological, outgoing, incoming = _topology(workflow, edges)  # stage 6
    _terminal(workflow, outgoing, incoming)  # stage 7
    _projections(workflow, outputs)  # stage 8
    _strict_ancestry(workflow, incoming)  # stage 9
    validate_write_scope_conflicts(workflow)  # stage 10
    return ValidatedDelegationGraphV2(topological)
