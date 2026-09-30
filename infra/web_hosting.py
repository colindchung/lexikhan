"""Private static Vite assets served over HTTPS. Deployment uploads are separate."""

from aws_cdk import RemovalPolicy
from aws_cdk import aws_cloudfront as cloudfront
from aws_cdk import aws_cloudfront_origins as origins
from aws_cdk import aws_s3 as s3
from constructs import Construct


class WebHosting(Construct):
    def __init__(self, scope: Construct, construct_id: str, *, stage_name: str):
        super().__init__(scope, construct_id)
        self.bucket = s3.Bucket(
            self,
            "Assets",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            removal_policy=RemovalPolicy.RETAIN
            if stage_name == "prod"
            else RemovalPolicy.DESTROY,
        )
        origin = origins.S3BucketOrigin.with_origin_access_control(self.bucket)
        router = cloudfront.Function(
            self,
            "SpaRouter",
            runtime=cloudfront.FunctionRuntime.JS_2_0,
            code=cloudfront.FunctionCode.from_inline("""function handler(event) {
    var request = event.request;
    if (!request.uri.split('/').pop().includes('.')) request.uri = '/index.html';
    return request;
}"""),
        )
        self.distribution = cloudfront.Distribution(
            self,
            "Distribution",
            default_root_object="index.html",
            default_behavior=cloudfront.BehaviorOptions(
                origin=origin,
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                cache_policy=cloudfront.CachePolicy.CACHING_DISABLED,
                response_headers_policy=cloudfront.ResponseHeadersPolicy.SECURITY_HEADERS,
                function_associations=[
                    cloudfront.FunctionAssociation(
                        function=router,
                        event_type=cloudfront.FunctionEventType.VIEWER_REQUEST,
                    )
                ],
            ),
            additional_behaviors={
                "assets/*": cloudfront.BehaviorOptions(
                    origin=origin,
                    viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                    cache_policy=cloudfront.CachePolicy.CACHING_OPTIMIZED,
                    response_headers_policy=cloudfront.ResponseHeadersPolicy.SECURITY_HEADERS,
                )
            },
        )
        self.url = f"https://{self.distribution.distribution_domain_name}"
