"""
Test that Lambda reserved concurrency in DataStack stays within safe AWS limits.

AWS requires at least 10 unreserved concurrent executions in an account.
On accounts with the default 50-unit limit, total reserved across all functions
must be <= 40.  We enforce a tighter budget here: each ingestion Lambda gets at
most 1 reserved execution, and the total across the stack is <= 10.
"""
from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import assertions

from infra.stacks.data_stack import DataStack


def _synth_data_stack() -> assertions.Template:
    app = cdk.App(context={"env": "dev"})
    stack = DataStack(app, "TestDataStack", env_name="dev")
    return assertions.Template.from_stack(stack)


def test_ingestion_lambdas_reserved_concurrency_at_most_one():
    """Each ingestion Lambda should reserve at most 1 concurrent execution."""
    template = _synth_data_stack()

    resources = template.to_json()["Resources"]
    lambda_functions = {
        k: v
        for k, v in resources.items()
        if v["Type"] == "AWS::Lambda::Function"
    }

    for logical_id, resource in lambda_functions.items():
        props = resource["Properties"]
        reserved = props.get("ReservedConcurrentExecutions")
        if reserved is not None:
            assert reserved <= 1, (
                f"{logical_id} has ReservedConcurrentExecutions={reserved}, "
                f"expected <= 1 to stay within account concurrency limits"
            )


def test_total_reserved_concurrency_within_budget():
    """Total reserved concurrency across all Lambdas in DataStack must be <= 10."""
    template = _synth_data_stack()

    resources = template.to_json()["Resources"]
    total_reserved = sum(
        v["Properties"].get("ReservedConcurrentExecutions", 0)
        for v in resources.values()
        if v["Type"] == "AWS::Lambda::Function"
    )

    assert total_reserved <= 10, (
        f"Total reserved concurrency is {total_reserved}, "
        f"must be <= 10 to keep unreserved above AWS minimum of 10"
    )
