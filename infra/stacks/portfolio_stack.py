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
    aws_iam as iam,
)
from aws_cdk import (
    aws_lambda as lambda_,
)
from constructs import Construct

_ACCOUNT = "292085144804"
_REGION = "us-east-1"


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
                f"https://sqs.{_REGION}.amazonaws.com/{_ACCOUNT}/{prefix}-analysis-queue"
            ),
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
            resources=[f"arn:aws:dynamodb:{_REGION}:{_ACCOUNT}:table/{prefix}-*"],
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
                f"arn:aws:ssm:{_REGION}:{_ACCOUNT}:parameter/omaha-oracle/{env_name}/*"
            ],
        )

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
                code=lambda_.Code.from_asset("../src"),
                handler=handler,
                description=description,
                memory_size=memory_size,
                timeout=timeout,
                environment=env,
                layers=[deps_layer],
            )
            fn.add_to_role_policy(data_policy)
            fn.add_to_role_policy(s3_policy)
            fn.add_to_role_policy(ssm_policy)
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
