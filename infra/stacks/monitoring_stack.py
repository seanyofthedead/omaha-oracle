"""
MonitoringStack — SNS alerts, cost monitor, and owner's letter.

Scheduled jobs
--------------
Cost monitor  : daily  09:00 UTC — checks monthly LLM spend vs budget
Owner's letter: weekly 06:00 UTC Sunday — Buffett-style portfolio update
Alerts        : invoked on-demand by other Lambdas via SNS publish
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
    aws_events as events,
)
from aws_cdk import (
    aws_events_targets as targets,
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
from aws_cdk import (
    aws_sns_subscriptions as sns_subscriptions,
)
from constructs import Construct

class MonitoringStack(cdk.Stack):
    """
    Monitoring and alerting infrastructure.

    Public properties
    -----------------
    alert_topic         SNS topic for operational alerts
    fn_cost_monitor
    fn_owners_letter
    fn_alerts
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        env_name: str,
        alert_email: str = "",
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
                f"https://sqs.{self.region}.amazonaws.com/{self.account}/{prefix}-analysis-queue"
            ),
        }

        # ---------------------------------------------------------------- #
        # SNS alert topic                                                   #
        # ---------------------------------------------------------------- #

        self.alert_topic = sns.Topic(
            self,
            "AlertTopic",
            topic_name=f"{prefix}-alerts",
            display_name=f"Omaha Oracle [{env_name}] Alerts",
        )

        if alert_email:
            self.alert_topic.add_subscription(
                sns_subscriptions.EmailSubscription(alert_email)
            )

        # ---------------------------------------------------------------- #
        # Lambda layer (self-contained; no cross-stack reference)            #
        # ---------------------------------------------------------------- #

        deps_layer = lambda_.LayerVersion(
            self,
            "DepsLayer",
            layer_version_name=f"{prefix}-monitoring-deps",
            code=lambda_.Code.from_asset("layer"),
            compatible_runtimes=[lambda_.Runtime.PYTHON_3_12],
            description="Omaha Oracle — pip deps for monitoring Lambdas",
        )

        # ---------------------------------------------------------------- #
        # Shared Lambda config                                              #
        # ---------------------------------------------------------------- #

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

        monitoring_env = {
            **shared_env,
            "SNS_TOPIC_ARN": self.alert_topic.topic_arn,
        }

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
                alarm.add_alarm_action(cw_actions.SnsAction(self.alert_topic))

        def _fn(
            construct_id: str,
            handler: str,
            description: str,
            memory_size: int = 256,
            timeout: Duration = Duration.minutes(5),
        ) -> lambda_.Function:
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
                environment=monitoring_env,
                layers=[deps_layer],
            )
            fn.add_to_role_policy(data_policy)
            fn.add_to_role_policy(s3_policy)
            fn.add_to_role_policy(ssm_policy)
            _add_lambda_alarms(fn, construct_id)
            return fn

        # ---------------------------------------------------------------- #
        # Cost monitor Lambda                                               #
        # ---------------------------------------------------------------- #

        self.fn_cost_monitor = _fn(
            "FnCostMonitor",
            "monitoring.cost_monitor.handler.handler",
            "Daily LLM spend check against monthly budget; publishes SNS alert if exceeded",
        )
        self.alert_topic.grant_publish(self.fn_cost_monitor)
        self.fn_cost_monitor.add_to_role_policy(
            iam.PolicyStatement(
                actions=["ce:GetCostAndUsage"],
                resources=["*"],
            )
        )

        cost_monitor_rule = events.Rule(
            self,
            "CostMonitorSchedule",
            rule_name=f"{prefix}-cost-monitor-schedule",
            description="Trigger cost monitor Lambda daily at 09:00 UTC",
            schedule=events.Schedule.cron(hour="9", minute="0"),
        )
        cost_monitor_rule.add_target(
            targets.LambdaFunction(self.fn_cost_monitor)
        )

        # ---------------------------------------------------------------- #
        # Owner's letter Lambda                                             #
        # ---------------------------------------------------------------- #

        self.fn_owners_letter = _fn(
            "FnOwnersLetter",
            "monitoring.owners_letter.handler.handler",
            "Weekly Buffett-style portfolio update synthesised by LLM; stored in S3 and emailed",
            memory_size=512,
            timeout=Duration.minutes(10),
        )
        self.alert_topic.grant_publish(self.fn_owners_letter)

        owners_letter_rule = events.Rule(
            self,
            "OwnersLetterSchedule",
            rule_name=f"{prefix}-owners-letter-schedule",
            description="Trigger owner's letter Lambda every Sunday at 06:00 UTC",
            schedule=events.Schedule.cron(hour="6", minute="0", week_day="SUN"),
        )
        owners_letter_rule.add_target(
            targets.LambdaFunction(self.fn_owners_letter)
        )

        # ---------------------------------------------------------------- #
        # Alerts Lambda (on-demand, invoked by other Lambdas)              #
        # ---------------------------------------------------------------- #

        self.fn_alerts = _fn(
            "FnAlerts",
            "monitoring.alerts.handler.handler",
            "Dispatch operational alerts via SNS (budget exhausted, trade executed, errors)",
        )
        self.alert_topic.grant_publish(self.fn_alerts)

        # Allow only the monitoring Lambdas in this stack to invoke alerts
        self.fn_alerts.add_permission(
            "AllowCostMonitorInvoke",
            principal=iam.ServicePrincipal("lambda.amazonaws.com"),
            source_arn=self.fn_cost_monitor.function_arn,
        )
        self.fn_alerts.add_permission(
            "AllowOwnersLetterInvoke",
            principal=iam.ServicePrincipal("lambda.amazonaws.com"),
            source_arn=self.fn_owners_letter.function_arn,
        )

        # ---------------------------------------------------------------- #
        # Stack outputs                                                     #
        # ---------------------------------------------------------------- #

        cdk.CfnOutput(self, "AlertTopicArn", value=self.alert_topic.topic_arn)
        cdk.CfnOutput(self, "CostMonitorFnArn", value=self.fn_cost_monitor.function_arn)
        cdk.CfnOutput(self, "OwnersLetterFnArn", value=self.fn_owners_letter.function_arn)
