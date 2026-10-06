import os

from aws_cdk import (
    CfnOutput,
    Duration,
    RemovalPolicy,
    Stack,
    aws_cloudfront as cloudfront,
    aws_cloudfront_origins as origins,
    aws_dynamodb as dynamodb,
    aws_iam as iam,
    aws_s3 as s3,
    aws_s3_deployment as s3deploy,
    custom_resources as cr,
)
from constructs import Construct

# Carpeta con el portafolio de ejemplo (challenge-01-student/application)
APP_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "application")
)


class CdkStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # ------------------------------------------------------------------
        # R1 - Bucket S3 privado (acceso público bloqueado)
        # ------------------------------------------------------------------
        bucket = s3.Bucket(
            self,
            "PortfolioBucket",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            removal_policy=RemovalPolicy.DESTROY,  # para poder hacer cdk destroy
            auto_delete_objects=True,              # vacía el bucket al destruir
        )

        # ------------------------------------------------------------------
        # Boss Fight - Archivos privados con CloudFront Signed URLs
       
        # ------------------------------------------------------------------
        key_group = None
        public_key_file = self.node.try_get_context("public_key_file")
        if public_key_file:
            with open(public_key_file, "r") as f:
                pem = f.read()
            public_key = cloudfront.PublicKey(self, "SigningPublicKey", encoded_key=pem)
            key_group = cloudfront.KeyGroup(
                self, "SigningKeyGroup", items=[public_key]
            )

      
        s3_origin = origins.S3BucketOrigin.with_origin_access_control(bucket)

        additional_behaviors = {}
        if key_group:
            additional_behaviors["private/*"] = cloudfront.BehaviorOptions(
                origin=s3_origin,
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                trusted_key_groups=[key_group],  # exige URL firmada
            )

        distribution = cloudfront.Distribution(
            self,
            "PortfolioDistribution",
            default_root_object="index.html",
            price_class=cloudfront.PriceClass.PRICE_CLASS_200,
            default_behavior=cloudfront.BehaviorOptions(
                origin=s3_origin,
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
            ),
            additional_behaviors=additional_behaviors,
        )

        # Sube el portafolio de ejemplo e invalida la caché
        s3deploy.BucketDeployment(
            self,
            "DeployPortfolio",
            sources=[s3deploy.Source.asset(APP_DIR)],
            destination_bucket=bucket,
            distribution=distribution,
            distribution_paths=["/*"],
        )

       
        table = dynamodb.Table(
            self,
            "PortfoliosTable",
            partition_key=dynamodb.Attribute(
                name="studentId", type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,
        )

        # Ítem de prueba (entregable) creado en el mismo cdk deploy
        cr.AwsCustomResource(
            self,
            "SeedPortfolioItem",
            on_create=cr.AwsSdkCall(
                service="DynamoDB",
                action="putItem",
                parameters={
                    "TableName": table.table_name,
                    "Item": {
                        "studentId": {"S": "est-001"},
                        "name": {"S": "Estudiante de Prueba"},
                        "createdAt": {"S": "2026-10-03"},
                        "url": {
                            "S": f"https://{distribution.distribution_domain_name}/index.html"
                        },
                    },
                },
                physical_resource_id=cr.PhysicalResourceId.of("seed-est-001"),
            ),
            policy=cr.AwsCustomResourcePolicy.from_sdk_calls(
                resources=[table.table_arn]
            ),
        )


        # Escritor: solo puede subir objetos al bucket (sin leer ni borrar)
        uploader_role = iam.Role(
            self,
            "PortfolioUploaderRole",
            assumed_by=iam.AccountRootPrincipal(),
            description="Puede escribir archivos de portafolio en S3",
            max_session_duration=Duration.hours(1),
        )
        bucket.grant_put(uploader_role)

        # Lector: solo lectura sobre la tabla de metadatos
        reader_role = iam.Role(
            self,
            "PortfolioMetadataReaderRole",
            assumed_by=iam.AccountRootPrincipal(),
            description="Puede leer metadatos de portafolios en DynamoDB",
            max_session_duration=Duration.hours(1),
        )
        table.grant_read_data(reader_role)

        CfnOutput(self, "CloudFrontURL", value=f"https://{distribution.distribution_domain_name}")
        CfnOutput(self, "BucketName", value=bucket.bucket_name)
        CfnOutput(self, "TableName", value=table.table_name)
        CfnOutput(self, "UploaderRoleArn", value=uploader_role.role_arn)
        CfnOutput(self, "ReaderRoleArn", value=reader_role.role_arn)
        if key_group:
            CfnOutput(self, "KeyGroupId", value=key_group.key_group_id)