"""
DataStack — storage layer for Omaha Oracle.

Creates:
  - S3 data bucket (raw/ lifecycle → IA after 90 days)
  - 9 DynamoDB tables (on-demand billing, PITR enabled)
  - SQS analysis queue (with dead-letter queue)
  - Lambda layer (shared pip dependencies from layer/)
  - 4 ingestion Lambdas (SEC, Yahoo Finance, FRED, Insider scanner)
  - EventBridge scheduled rules for ingestion
  - Helper: create_ingestion_lambda()
"""
from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import (
    Duration,
    RemovalPolicy,
)
from aws_cdk import (
    aws_cloudwatch as cloudwatch,
)
from aws_cdk import (
    aws_cloudwatch_actions as cw_actions,
)
from aws_cdk import (
    aws_dynamodb as dynamodb,
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
    aws_s3 as s3,
)
from aws_cdk import (
    aws_sns as sns,
)
from aws_cdk import (
    aws_sqs as sqs,
)
from constructs import Construct

# Defaults applied to every Lambda this stack creates
_LAMBDA_DEFAULTS: dict[str, object] = {
    "runtime": lambda_.Runtime.PYTHON_3_12,
    "memory_size": 256,
    "timeout": Duration.minutes(5),
}


class DataStack(cdk.Stack):
    """
    Storage layer: S3, DynamoDB, SQS, Lambda layer.

    Internal resources only — no exports. Other stacks reference resources
    via hardcoded ARNs using env_name.
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        env_name: str,
        **kwargs: object,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)  # type: ignore[arg-type]

        self._env_name = env_name
        prefix = f"omaha-oracle-{env_name}"
        alert_topic_arn = f"arn:aws:sns:{self.region}:{self.account}:{prefix}-alerts"
        alert_topic = sns.Topic.from_topic_arn(self, "AlertTopic", alert_topic_arn)

        # ---------------------------------------------------------------- #
        # S3 bucket                                                         #
        # ---------------------------------------------------------------- #

        self.bucket = s3.Bucket(
            self,
            "DataBucket",
            bucket_name=f"{prefix}-data",
            removal_policy=RemovalPolicy.RETAIN,
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            versioned=True,
            lifecycle_rules=[
                s3.LifecycleRule(
                    id="raw-to-ia",
                    prefix="raw/",
                    transitions=[
                        s3.Transition(
                            storage_class=s3.StorageClass.INFREQUENT_ACCESS,
                            transition_after=Duration.days(90),
                        )
                    ],
                )
            ],
        )

        # ---------------------------------------------------------------- #
        # DynamoDB tables                                                   #
        # ---------------------------------------------------------------- #

        def _table(
            construct_id: str,
            table_name: str,
            pk: str,
            pk_type: dynamodb.AttributeType = dynamodb.AttributeType.STRING,
            sk: str | None = None,
            sk_type: dynamodb.AttributeType = dynamodb.AttributeType.STRING,
        ) -> dynamodb.Table:
            kwargs_ddb: dict[str, object] = dict(
                table_name=table_name,
                billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
                point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(
                    point_in_time_recovery_enabled=True
                ),
                removal_policy=RemovalPolicy.RETAIN,
                partition_key=dynamodb.Attribute(name=pk, type=pk_type),
            )
            if sk is not None:
                kwargs_ddb["sort_key"] = dynamodb.Attribute(name=sk, type=sk_type)
            return dynamodb.Table(self, construct_id, **kwargs_ddb)  # type: ignore[arg-type]

        self.tbl_companies = _table(
            "TblCompanies", f"{prefix}-companies", pk="ticker"
        )
        self.tbl_financials = _table(
            "TblFinancials", f"{prefix}-financials", pk="ticker", sk="period"
        )
        self.tbl_analysis = _table(
            "TblAnalysis", f"{prefix}-analysis", pk="ticker", sk="analysis_date"
        )
        self.tbl_portfolio = _table(
            "TblPortfolio", f"{prefix}-portfolio", pk="pk", sk="sk"
        )
        self.tbl_cost_tracker = _table(
            "TblCostTracker", f"{prefix}-cost-tracker", pk="month_key", sk="timestamp"
        )
        self.tbl_config = _table(
            "TblConfig", f"{prefix}-config", pk="config_key"
        )
        self.tbl_decisions = _table(
            "TblDecisions", f"{prefix}-decisions", pk="decision_id", sk="timestamp"
        )
        self.tbl_decisions.add_global_secondary_index(
            partition_key=dynamodb.Attribute(
                name="record_type", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="timestamp", type=dynamodb.AttributeType.STRING
            ),
            index_name="record_type-timestamp-index",
            projection_type=dynamodb.ProjectionType.ALL,
        )
        self.tbl_watchlist = _table(
            "TblWatchlist", f"{prefix}-watchlist", pk="ticker"
        )
        self.tbl_lessons = _table(
            "TblLessons", f"{prefix}-lessons", pk="lesson_type", sk="lesson_id"
        )
        self.tbl_lessons.add_global_secondary_index(
            partition_key=dynamodb.Attribute(
                name="active_flag", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="expires_at", type=dynamodb.AttributeType.STRING
            ),
            index_name="active_flag-expires_at-index",
            projection_type=dynamodb.ProjectionType.ALL,
        )

        # ---------------------------------------------------------------- #
        # SQS — analysis queue with DLQ                                    #
        # ---------------------------------------------------------------- #

        analysis_dlq = sqs.Queue(
            self,
            "AnalysisDLQ",
            queue_name=f"{prefix}-analysis-dlq",
            retention_period=Duration.days(14),
        )

        self.analysis_queue = sqs.Queue(
            self,
            "AnalysisQueue",
            queue_name=f"{prefix}-analysis-queue",
            visibility_timeout=Duration.minutes(6),  # > Lambda timeout
            dead_letter_queue=sqs.DeadLetterQueue(
                max_receive_count=3,
                queue=analysis_dlq,
            ),
        )

        # Alert when messages land in the DLQ — indicates persistent ingestion failure
        dlq_alarm = cloudwatch.Alarm(
            self,
            "AnalysisDLQDepthAlarm",
            metric=analysis_dlq.metric_approximate_number_of_messages_visible(),
            threshold=1,
            evaluation_periods=1,
            alarm_description="Messages in analysis DLQ — ingestion event processing failed",
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
        )
        dlq_alarm.add_alarm_action(cw_actions.SnsAction(alert_topic))

        # ---------------------------------------------------------------- #
        # Lambda layer (internal only — not exported to other stacks)       #
        # ---------------------------------------------------------------- #

        self._deps_layer = lambda_.LayerVersion(
            self,
            "DepsLayer",
            layer_version_name=f"{prefix}-data-deps",
            code=lambda_.Code.from_asset("layer"),
            compatible_runtimes=[lambda_.Runtime.PYTHON_3_12],
            description="Omaha Oracle — pip deps for ingestion Lambdas",
        )

        # ---------------------------------------------------------------- #
        # Shared environment variables passed to every Lambda              #
        # ---------------------------------------------------------------- #

        shared_env: dict[str, str] = {
            "ENVIRONMENT": env_name,
            "TABLE_COMPANIES": self.tbl_companies.table_name,
            "TABLE_FINANCIALS": self.tbl_financials.table_name,
            "TABLE_ANALYSIS": self.tbl_analysis.table_name,
            "TABLE_PORTFOLIO": self.tbl_portfolio.table_name,
            "TABLE_COST_TRACKING": self.tbl_cost_tracker.table_name,
            "TABLE_CONFIG": self.tbl_config.table_name,
            "TABLE_DECISIONS": self.tbl_decisions.table_name,
            "TABLE_WATCHLIST": self.tbl_watchlist.table_name,
            "TABLE_LESSONS": self.tbl_lessons.table_name,
            "S3_BUCKET": self.bucket.bucket_name,
            "ANALYSIS_QUEUE_URL": self.analysis_queue.queue_url,
            "SNS_TOPIC_ARN": alert_topic_arn,
        }

        # ---------------------------------------------------------------- #
        # IAM policies for ingestion Lambdas (PolicyStatement only)         #
        # ---------------------------------------------------------------- #

        dynamodb_policy = iam.PolicyStatement(
            actions=[
                "dynamodb:GetItem",
                "dynamodb:PutItem",
                "dynamodb:UpdateItem",
                "dynamodb:DeleteItem",
                "dynamodb:Query",
                "dynamodb:Scan",
                "dynamodb:BatchWriteItem",
            ],
            resources=[f"arn:aws:dynamodb:{self.region}:{self.account}:table/{prefix}-*"],
        )
        s3_policy = iam.PolicyStatement(
            actions=[
                "s3:GetObject",
                "s3:PutObject",
                "s3:DeleteObject",
                "s3:ListBucket",
            ],
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
        sqs_policy = iam.PolicyStatement(
            actions=["sqs:SendMessage"],
            resources=[f"arn:aws:sqs:{self.region}:{self.account}:{prefix}-*"],
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

        def _ingestion_fn(
            construct_id: str,
            handler: str,
            description: str,
            memory_size: int = 256,
            timeout: Duration = Duration.minutes(5),
        ) -> lambda_.Function:
            fn = lambda_.Function(
                self,
                construct_id,
                function_name=f"{prefix}-{construct_id.lower()}",
                runtime=lambda_.Runtime.PYTHON_3_12,
                code=lambda_.Code.from_asset(
                    "../src",
                    exclude=["dashboard", "dashboard/**", "**/__pycache__", "**/*.pyc"],
                ),
                handler=handler,
                description=description,
                memory_size=memory_size,
                timeout=timeout,
                environment=shared_env,
                layers=[self._deps_layer],
                # Limit concurrency to avoid triggering rate limits on SEC EDGAR / Yahoo Finance.
                # Keep at 1 so total reserved across all Lambdas stays well under the
                # account-wide unreserved-concurrency minimum of 10.
                reserved_concurrent_executions=1,
            )
            fn.add_to_role_policy(dynamodb_policy)
            fn.add_to_role_policy(s3_policy)
            fn.add_to_role_policy(ssm_policy)
            fn.add_to_role_policy(sqs_policy)
            _add_lambda_alarms(fn, construct_id)
            return fn

        # ---------------------------------------------------------------- #
        # Ingestion Lambdas                                                 #
        # ---------------------------------------------------------------- #

        fn_sec_scanner = _ingestion_fn(
            "SecScanner",
            "ingestion.sec_edgar.handler.handler",
            "SEC EDGAR filings ingestion (full_refresh)",
            memory_size=512,
            timeout=Duration.minutes(10),
        )

        fn_yahoo_finance = _ingestion_fn(
            "YahooFinance",
            "ingestion.yahoo_finance.handler.handler",
            "Yahoo Finance batch_prices ingestion",
            memory_size=256,
            timeout=Duration.minutes(5),
        )

        fn_fred_macro = _ingestion_fn(
            "FredMacro",
            "ingestion.fred.handler.handler",
            "FRED macroeconomic data ingestion",
            memory_size=256,
            timeout=Duration.minutes(5),
        )

        fn_insider_scanner = _ingestion_fn(
            "InsiderScanner",
            "ingestion.insider_transactions.handler.handler",
            "Insider transactions scanner",
            memory_size=512,
            timeout=Duration.minutes(10),
        )

        # ---------------------------------------------------------------- #
        # EventBridge scheduled rules                                       #
        # ---------------------------------------------------------------- #

        # Daily 11pm UTC: Yahoo Finance (batch_prices)
        events.Rule(
            self,
            "YahooFinanceSchedule",
            rule_name=f"{prefix}-yahoofinance-schedule",
            description="Trigger Yahoo Finance ingestion daily at 23:00 UTC",
            schedule=events.Schedule.cron(hour="23", minute="0"),
        ).add_target(targets.LambdaFunction(fn_yahoo_finance))

        # Daily 11:30pm UTC: Insider scanner
        events.Rule(
            self,
            "InsiderScannerSchedule",
            rule_name=f"{prefix}-insiderscanner-schedule",
            description="Trigger insider scanner daily at 23:30 UTC",
            schedule=events.Schedule.cron(hour="23", minute="30"),
        ).add_target(targets.LambdaFunction(fn_insider_scanner))

        # Weekly Sunday 2am UTC: SEC scanner (full_refresh)
        events.Rule(
            self,
            "SecScannerSchedule",
            rule_name=f"{prefix}-secscanner-schedule",
            description="Trigger SEC scanner weekly Sunday at 02:00 UTC",
            schedule=events.Schedule.cron(hour="2", minute="0", week_day="SUN"),
        ).add_target(targets.LambdaFunction(fn_sec_scanner))

        # Monthly 1st at 3am UTC: FRED
        events.Rule(
            self,
            "FredMacroSchedule",
            rule_name=f"{prefix}-fredmacro-schedule",
            description="Trigger FRED ingestion monthly on 1st at 03:00 UTC",
            schedule=events.Schedule.cron(hour="3", minute="0", day="1"),
        ).add_target(targets.LambdaFunction(fn_fred_macro))

        # Keep shared_env for create_ingestion_lambda
        self.shared_env = shared_env

    # -------------------------------------------------------------------- #
    # Helper: factory for ingestion Lambdas                                 #
    # -------------------------------------------------------------------- #

    def create_ingestion_lambda(
        self,
        construct_id: str,
        handler: str,
        description: str = "",
        memory_size: int = 256,
        timeout: Duration = Duration.minutes(5),
        extra_env: dict[str, str] | None = None,
        tables_rw: list[dynamodb.Table] | None = None,
        tables_ro: list[dynamodb.Table] | None = None,
    ) -> lambda_.Function:
        """
        Create an ingestion Lambda pre-wired to the shared data resources.

        Parameters
        ----------
        construct_id:
            CDK construct ID (unique within this stack).
        handler:
            Python dotted handler path, e.g.
            ``"ingestion.yahoo_finance.handler.handler"``.
        description:
            Human-readable Lambda description.
        memory_size:
            Megabytes of RAM.
        timeout:
            Lambda execution timeout.
        extra_env:
            Additional environment variables merged on top of shared_env.
        tables_rw:
            Tables to grant read+write access to.  Defaults to all tables.
        tables_ro:
            Tables to grant read-only access to.
        """
        env = {**self.shared_env, **(extra_env or {})}

        fn = lambda_.Function(
            self,
            construct_id,
            function_name=f"omaha-oracle-{self._env_name}-{construct_id.lower()}",
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
            layers=[self._deps_layer],
        )

        # Grant S3 read/write by default
        self.bucket.grant_read_write(fn)

        # Grant DynamoDB access
        write_targets = tables_rw if tables_rw is not None else self._all_tables()
        for tbl in write_targets:
            tbl.grant_read_write_data(fn)
        for tbl in (tables_ro or []):
            tbl.grant_read_data(fn)

        # Allow SQS send
        self.analysis_queue.grant_send_messages(fn)

        # SSM read (for secret fallback)
        fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=["ssm:GetParameter"],
                resources=[
                    f"arn:aws:ssm:{self.region}:{self.account}:"
                    f"parameter/omaha-oracle/{self._env_name}/*"
                ],
            )
        )

        return fn

    def _all_tables(self) -> list[dynamodb.Table]:
        return [
            self.tbl_companies,
            self.tbl_financials,
            self.tbl_analysis,
            self.tbl_portfolio,
            self.tbl_cost_tracker,
            self.tbl_config,
            self.tbl_decisions,
            self.tbl_watchlist,
            self.tbl_lessons,
        ]
