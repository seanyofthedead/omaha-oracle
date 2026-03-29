#!/usr/bin/env python3
"""
Omaha Oracle — CDK application entry point.

Usage
-----
    # default env = dev
    cdk synth

    # target a specific environment
    cdk synth -c env=prod
    cdk deploy --all -c env=staging
"""
import aws_cdk as cdk
from stacks.analysis_stack import AnalysisStack
from stacks.data_stack import DataStack
from stacks.monitoring_stack import MonitoringStack
from stacks.portfolio_stack import PortfolioStack

app = cdk.App()

env_name: str = (app.node.try_get_context("env") or "dev").lower()

# AWS env (account/region from CLI profile at deploy time)
aws_env = cdk.Environment(
    account=app.node.try_get_context("account"),
    region=app.node.try_get_context("region") or "us-east-1",
)

# ------------------------------------------------------------------ #
# Stacks                                                              #
# ------------------------------------------------------------------ #

DataStack(
    app,
    f"OmahaOracle-{env_name.capitalize()}-Data",
    env_name=env_name,
    env=aws_env,
    description=f"Omaha Oracle [{env_name}] — storage (S3, DynamoDB, SQS)",
)

AnalysisStack(
    app,
    f"OmahaOracle-{env_name.capitalize()}-Analysis",
    env_name=env_name,
    env=aws_env,
    description=f"Omaha Oracle [{env_name}] — analysis Lambdas and Step Functions pipeline",
)

PortfolioStack(
    app,
    f"OmahaOracle-{env_name.capitalize()}-Portfolio",
    env_name=env_name,
    env=aws_env,
    description=f"Omaha Oracle [{env_name}] — portfolio allocation and risk Lambdas",
)

alert_email = app.node.try_get_context("alert_email") or ""

MonitoringStack(
    app,
    f"OmahaOracle-{env_name.capitalize()}-Monitoring",
    env_name=env_name,
    alert_email=alert_email,
    env=aws_env,
    description=f"Omaha Oracle [{env_name}] — SNS alerts, cost monitor, owner letter",
)

app.synth()
