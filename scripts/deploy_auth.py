"""Apply versioned Cognito branding to the stack's browser client."""

import argparse
from pathlib import Path

import boto3
from smoke_backend import outputs_for

ASSETS = Path(__file__).resolve().parents[1] / "infra" / "auth"


def deploy(session, stage):
    outputs = outputs_for(session, f"Lexikhan-{stage}")
    if outputs.get("Stage") != stage:
        raise ValueError("Stack stage mismatch")
    css = (ASSETS / "hosted-ui.css").read_text()
    logo = (ASSETS / "logo.png").read_bytes()
    if len(css.encode()) > 3072 or len(logo) > 100 * 1024:
        raise ValueError("Cognito branding assets exceed supported size")
    client = session.client("cognito-idp")
    client.set_ui_customization(
        UserPoolId=outputs["UserPoolId"],
        ClientId=outputs["UserPoolClientId"],
        CSS=css,
        ImageFile=logo,
    )
    branding = client.get_ui_customization(
        UserPoolId=outputs["UserPoolId"], ClientId=outputs["UserPoolClientId"]
    )["UICustomization"]
    if branding.get("CSS") != css or not branding.get("ImageUrl"):
        raise RuntimeError("Cognito branding verification failed")
    print(f"Published {stage} authentication branding")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", required=True, choices=["dev", "prod"])
    parser.add_argument("--profile")
    parser.add_argument("--region", default="us-east-2")
    args = parser.parse_args()
    deploy(
        boto3.Session(profile_name=args.profile, region_name=args.region), args.stage
    )
