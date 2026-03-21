"""
PortfolioStack — allocation, execution and risk Lambdas.

Three functions mirror the three sub-modules in src/portfolio/:
  allocation  — position sizing and buy/sell logic
  execution   — Alpaca order placement
  risk        — guardrails (concentration, drawdown checks)

Execution is intentionally kept separate from allocation so that the
guardrail check can gate any actual order submission.
"""
from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import (
    Duration,
)
from aws_cdk import (
    aws_cloudwatch as cloudwatch,
)
from aws_cdk import (
    aws_cloudwatch_actions as cw_actions,
)
from aws_cdk import (
    aws_iam as iam,
)
from aws_cdk import (
    aws_lambda as lambda_,
)
from aws_cdk import (
    aws_sns as sns,
)
from constructs import Construct

class PortfolioStack(cdk.Stack):
    """
    Portfolio management Lambdas.

    Public properties
    -----------------
    fn_allocation
    fn_execution
    fn_risk
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        env_name: str,
        **kwargs: object,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)  # type: ignore[arg-type]

        prefix = f"omaha-oracle-{env_name}"
        alert_topic_arn = f"arn:aws:sns:{self.region}:{self.account}:{prefix}-alerts"
        alert_topic = sns.Topic.from_topic_arn(self, "AlertTopic", alert_topic_arn)

        shared_env = {
            "ENVIRONMENT": env_name,
            "TABLE_COMPANIES": f"{prefix}-companies",
            "TABLE_FINANCIALS": f"{prefix}-financials",
            "TABLE_ANALYSIS": f"{prefix}-analysis",
            "TABLE_PORTFOLIO": f"{prefix}-portfolio",
            "TABLE_COST_TRACKING": f"{prefix}-cost-tracker",
            "TABLE_CONFIG": f"{prefix}-config",
            "TABLE_DECISIONS": f"{prefix}-decisions",
            "TABLE_WATCHLIST": f"{prefix}-watchlist",
            "TABLE_LESSONS": f"{prefix}-lessons",
            "S3_BUCKET": f"{prefix}-data",
            "ANALYSIS_QUEUE_URL": (
                f"https://sqs.{self.region}.amazonaws.com/{self.account}/{prefix}-analysis-queue"
            ),
            "SNS_TOPIC_ARN": alert_topic_arn,
        }

        # ---------------------------------------------------------------- #
        # Lambda layer (self-contained)                                     #
        # ---------------------------------------------------------------- #

        deps_layer = lambda_.LayerVersion(
            self,
            "DepsLayer",
            layer_version_name=f"{prefix}-portfolio-deps",
            code=lambda_.Code.from_asset("layer"),
            compatible_runtimes=[lambda_.Runtime.PYTHON_3_12],
            description="Omaha Oracle — pip deps for portfolio Lambdas",
        )

        data_policy = iam.PolicyStatement(
            actions=[
                "dynamodb:GetItem",
                "dynamodb:PutItem",
                "dynamodb:Query",
                "dynamodb:Scan",
                "dynamodb:UpdateItem",
                "dynamodb:DeleteItem",
                "dynamodb:BatchWriteItem",
            ],
            resources=[f"arn:aws:dynamodb:{self.region}:{self.account}:table/{prefix}-*"],
        )
        s3_policy = iam.PolicyStatement(
            actions=["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket"],
            resources=[
                f"arn:aws:s3:::{prefix}-data",
                f"arn:aws:s3:::{prefix}-data/*",
            ],
        )
        ssm_policy = iam.PolicyStatement(
            actions=["ssm:GetParameter"],
            resources=[
                f"arn:aws:ssm:{self.region}:{self.account}:parameter/omaha-oracle/{env_name}/*"
            ],
        )

        def _add_lambda_alarms(fn: lambda_.Function, name: str) -> None:
            """Add error and throttle alarms routing to the alert SNS topic."""
            for metric_fn, alarm_id, desc in [
                (fn.metric_errors, f"{name}Errors", f"{name}: Lambda errors > 0"),
                (fn.metric_throttles, f"{name}Throttles", f"{name}: Lambda throttled"),
            ]:
                alarm = cloudwatch.Alarm(
                    self,
                    alarm_id,
                    metric=metric_fn(),
                    threshold=1,
                    evaluation_periods=1,
                    alarm_description=desc,
                    treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
                )
                alarm.add_alarm_action(cw_actions.SnsAction(alert_topic))

        def _fn(
            construct_id: str,
            handler: str,
            description: str,
            memory_size: int = 256,
            timeout: Duration = Duration.minutes(5),
            extra_env: dict[str, str] | None = None,
        ) -> lambda_.Function:
            env = {**shared_env, **(extra_env or {})}
            fn = lambda_.Function(
                self,
                construct_id,
                function_name=f"{prefix}-{construct_id.lower().replace('fn', '').strip('-')}",
                runtime=lambda_.Runtime.PYTHON_3_12,
                code=lambda_.Code.from_asset(
                    "../src",
                    exclude=["dashboard", "dashboard/**", "**/__pycache__", "**/*.pyc"],
                ),
                handler=handler,
                description=description,
                memory_size=memory_size,
                timeout=timeout,
                environment=env,
                layers=[deps_layer],
                # Explicit retry config — safe after idempotency guards are deployed (PR 5)
                retry_attempts=2,
                max_event_age=Duration.hours(1),
            )
            fn.add_to_role_policy(data_policy)
            fn.add_to_role_policy(s3_policy)
            fn.add_to_role_policy(ssm_policy)
            _add_lambda_alarms(fn, construct_id)
            return fn

        # ---------------------------------------------------------------- #
        # Risk guardrail Lambda                                             #
        # Must run before execution — invoked synchronously by execution   #
        # ---------------------------------------------------------------- #

        self.fn_risk = _fn(
            "FnRisk",
            "portfolio.risk.handler.handler",
            "Portfolio risk guardrails — concentration, drawdown, position limits",
        )

        # ---------------------------------------------------------------- #
        # Allocation Lambda                                                 #
        # ---------------------------------------------------------------- #

        self.fn_allocation = _fn(
            "FnAllocation",
            "portfolio.allocation.handler.handler",
            "Position sizing and buy/sell logic based on margin of safety",
        )
        self.fn_risk.grant_invoke(self.fn_allocation)

        # ---------------------------------------------------------------- #
        # Execution Lambda (Alpaca)                                         #
        # ---------------------------------------------------------------- #

        self.fn_execution = _fn(
            "FnExecution",
            "portfolio.execution.handler.handler",
            "Alpaca order placement — paper and live trading",
        )
        self.fn_risk.grant_invoke(self.fn_execution)

        # ---------------------------------------------------------------- #
        # Stack outputs                                                     #
        # ---------------------------------------------------------------- #

        cdk.CfnOutput(self, "AllocationFnArn", value=self.fn_allocation.function_arn)
        cdk.CfnOutput(self, "ExecutionFnArn", value=self.fn_execution.function_arn)
        cdk.CfnOutput(self, "RiskFnArn", value=self.fn_risk.function_arn)
