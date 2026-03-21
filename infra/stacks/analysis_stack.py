"""
AnalysisStack — analysis Lambdas and Step Functions pipeline.

Pipeline flow (triggered by SQS message or direct invocation):
  1. quant_screen       — fast quantitative filter
  2. Map over passing_tickers:
       moat → (if moat≥7) → management → intrinsic_value → (if MoS>30%) → thesis
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
    aws_lambda_event_sources as lambda_events,
)
from aws_cdk import (
    aws_sns as sns,
)
from aws_cdk import (
    aws_sqs as sqs,
)
from aws_cdk import (
    aws_stepfunctions as sfn,
)
from aws_cdk import (
    aws_stepfunctions_tasks as sfn_tasks,
)
from constructs import Construct

class AnalysisStack(cdk.Stack):
    """
    Analysis pipeline: five Lambdas orchestrated by a Step Functions
    state machine.

    Public properties
    -----------------
    state_machine   Step Functions StateMachine
    fn_quant_screen
    fn_intrinsic_value
    fn_moat_analysis
    fn_management_quality
    fn_thesis_generator
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
        queue_url = (
            f"https://sqs.{self.region}.amazonaws.com/{self.account}/{prefix}-analysis-queue"
        )
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
            "ANALYSIS_QUEUE_URL": queue_url,
            "SNS_TOPIC_ARN": alert_topic_arn,
        }

        # ---------------------------------------------------------------- #
        # Lambda layer (self-contained)                                     #
        # ---------------------------------------------------------------- #

        deps_layer = lambda_.LayerVersion(
            self,
            "DepsLayer",
            layer_version_name=f"{prefix}-analysis-deps",
            code=lambda_.Code.from_asset("layer"),
            compatible_runtimes=[lambda_.Runtime.PYTHON_3_12],
            description="Omaha Oracle — pip deps for analysis Lambdas",
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
                environment=shared_env,
                layers=[deps_layer],
            )
            fn.add_to_role_policy(data_policy)
            fn.add_to_role_policy(s3_policy)
            fn.add_to_role_policy(ssm_policy)
            _add_lambda_alarms(fn, construct_id)
            return fn

        # ---------------------------------------------------------------- #
        # Analysis Lambdas                                                  #
        # ---------------------------------------------------------------- #

        self.fn_quant_screen = _fn(
            "FnQuantScreen",
            "analysis.quant_screen.handler.handler",
            "Quantitative screening — P/E, P/B, FCF yield, debt/equity filters",
        )

        self.fn_intrinsic_value = _fn(
            "FnIntrinsicValue",
            "analysis.intrinsic_value.handler.handler",
            "Intrinsic value — DCF and Graham Number",
            memory_size=512,
        )

        self.fn_moat_analysis = _fn(
            "FnMoatAnalysis",
            "analysis.moat_analysis.handler.handler",
            "Competitive moat assessment via LLM (Opus)",
            memory_size=512,
        )

        self.fn_management_quality = _fn(
            "FnManagementQuality",
            "analysis.management_quality.handler.handler",
            "Management quality scoring via LLM (Sonnet)",
        )

        self.fn_thesis_generator = _fn(
            "FnThesisGenerator",
            "analysis.thesis_generator.handler.handler",
            "Investment thesis synthesis via LLM (Opus)",
            memory_size=1024,
            timeout=Duration.minutes(10),
        )

        # ---------------------------------------------------------------- #
        # SQS trigger for quant screen (import by ARN — no cross-stack ref) #
        # ---------------------------------------------------------------- #

        analysis_queue = sqs.Queue.from_queue_arn(
            self,
            "AnalysisQueue",
            f"arn:aws:sqs:{self.region}:{self.account}:{prefix}-analysis-queue",
        )
        self.fn_quant_screen.add_event_source(
            lambda_events.SqsEventSource(
                analysis_queue,
                batch_size=1,
                report_batch_item_failures=True,
            )
        )

        # ---------------------------------------------------------------- #
        # Step Functions state machine                                      #
        # ---------------------------------------------------------------- #

        # Retry config shared by all Lambda tasks (transient Lambda/SFN errors)
        _retry_kwargs = {
            "errors": [
                "Lambda.ServiceException",
                "Lambda.AWSLambdaException",
                "Lambda.SdkClientException",
                "Lambda.TooManyRequestsException",
                "States.TaskFailed",
            ],
            "interval": Duration.seconds(2),
            "max_attempts": 2,
            "backoff_rate": 2,
        }

        # Fail state — surfaced when a per-company analysis Lambda raises
        pipeline_fail = sfn.Fail(
            self,
            "AnalysisPipelineFailed",
            cause="One or more analysis Lambda tasks raised an unhandled exception",
            error="AnalysisPipelineFailed",
        )

        # Step 1 — quantitative screen (output: passing_tickers, total_screened, total_passed)
        quant_step = sfn_tasks.LambdaInvoke(
            self,
            "QuantScreen",
            lambda_function=self.fn_quant_screen,
            output_path="$.Payload",
            comment="Run quantitative filter; pass if qualifies",
        )
        quant_step.add_retry(**_retry_kwargs)
        quant_step.add_catch(pipeline_fail, result_path="$.error_info")

        # Per-company steps (each receives full item: ticker, company_name, metrics, quant_result)
        moat_step = sfn_tasks.LambdaInvoke(
            self,
            "MoatAnalysis",
            lambda_function=self.fn_moat_analysis,
            payload=sfn.TaskInput.from_json_path_at("$"),
            output_path="$.Payload",
            comment="Competitive moat assessment",
        )
        moat_step.add_retry(**_retry_kwargs)

        mgmt_step = sfn_tasks.LambdaInvoke(
            self,
            "ManagementQuality",
            lambda_function=self.fn_management_quality,
            output_path="$.Payload",
            comment="Management quality scoring",
        )
        mgmt_step.add_retry(**_retry_kwargs)

        iv_step = sfn_tasks.LambdaInvoke(
            self,
            "IntrinsicValue",
            lambda_function=self.fn_intrinsic_value,
            output_path="$.Payload",
            comment="DCF / Graham intrinsic value",
        )
        iv_step.add_retry(**_retry_kwargs)

        thesis_step = sfn_tasks.LambdaInvoke(
            self,
            "ThesisGenerator",
            lambda_function=self.fn_thesis_generator,
            output_path="$.Payload",
            comment="Buffett-style investment thesis",
        )
        thesis_step.add_retry(**_retry_kwargs)

        # Chain: moat → (if moat≥7) → mgmt → iv → (if MoS>0.30) → thesis
        moat_pass = sfn.Choice(self, "MoatPassCheck")
        mos_pass = sfn.Choice(self, "MarginOfSafetyCheck")

        moat_pass.when(
            sfn.Condition.number_greater_than_equals("$.moat_score", 7),
            mgmt_step.next(iv_step).next(
                mos_pass.when(
                    sfn.Condition.number_greater_than("$.margin_of_safety", 0.30),
                    thesis_step,
                ).otherwise(sfn.Pass(self, "InsufficientMoS"))
            ),
        ).otherwise(sfn.Pass(self, "MoatFailed"))

        per_company = sfn.Map(
            self,
            "PerCompanyAnalysis",
            items_path="$.passing_tickers",
            max_concurrency=3,
            comment="Run moat → mgmt → iv → thesis for each passing ticker",
        )
        per_company.iterator(moat_step.next(moat_pass))
        per_company.add_catch(pipeline_fail, result_path="$.error_info")

        definition = quant_step.next(per_company)

        self.state_machine = sfn.StateMachine(
            self,
            "AnalysisPipeline",
            state_machine_name=f"{prefix}-analysis-pipeline",
            definition_body=sfn.DefinitionBody.from_chainable(definition),
            timeout=Duration.minutes(30),
            comment="Omaha Oracle end-to-end analysis pipeline",
        )

        # Alert on any Step Functions execution failure
        sfn_alarm = cloudwatch.Alarm(
            self,
            "AnalysisPipelineFailures",
            metric=self.state_machine.metric_failed(),
            threshold=1,
            evaluation_periods=1,
            alarm_description="Analysis pipeline Step Functions execution failed",
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
        )
        sfn_alarm.add_alarm_action(cw_actions.SnsAction(alert_topic))

        # ---------------------------------------------------------------- #
        # Stack outputs                                                     #
        # ---------------------------------------------------------------- #

        cdk.CfnOutput(
            self,
            "StateMachineArn",
            value=self.state_machine.state_machine_arn,
        )
