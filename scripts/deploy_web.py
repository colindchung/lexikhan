"""Publish built assets and public runtime configuration from CDK stack outputs."""

import argparse
import json
import mimetypes
from pathlib import Path
from uuid import uuid4

import boto3
from smoke_backend import outputs_for


def deploy(session, stage, dist):
    outputs = outputs_for(session, f"Lexikhan-{stage}")
    if outputs.get("Stage") != stage:
        raise ValueError("Stack stage mismatch")
    if not (dist / "index.html").is_file():
        raise ValueError("Build web/ with npm run build before deployment")
    config = {
        "apiUrl": outputs["ApiUrl"],
        "authority": outputs["AuthAuthority"],
        "clientId": outputs["UserPoolClientId"],
        "authDomain": outputs["AuthDomain"],
    }
    s3 = session.client("s3")
    bucket = outputs["WebBucketName"]
    # Keep older hashed assets available to tabs still running the previous release.
    for path in sorted(dist.rglob("*")):
        if not path.is_file() or path.name in ("index.html", "config.json"):
            continue
        key = path.relative_to(dist).as_posix()
        s3.put_object(
            Bucket=bucket,
            Key=key,
            Body=path.read_bytes(),
            ContentType=mimetypes.guess_type(key)[0] or "application/octet-stream",
            CacheControl="public,max-age=31536000,immutable"
            if key.startswith("assets/")
            else "no-cache",
        )
    s3.put_object(
        Bucket=bucket,
        Key="config.json",
        Body=json.dumps(config).encode(),
        ContentType="application/json",
        CacheControl="no-store",
    )
    s3.put_object(
        Bucket=bucket,
        Key="index.html",
        Body=(dist / "index.html").read_bytes(),
        ContentType="text/html",
        CacheControl="no-cache",
    )
    session.client("cloudfront").create_invalidation(
        DistributionId=outputs["DistributionId"],
        InvalidationBatch={
            "CallerReference": str(uuid4()),
            "Paths": {
                "Quantity": 4,
                "Items": [
                    "/index.html",
                    "/config.json",
                    "/sw.js",
                    "/manifest.webmanifest",
                ],
            },
        },
    )
    print(f"Published {stage} frontend: {outputs['WebUrl']}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", required=True, choices=["dev", "prod"])
    parser.add_argument("--profile")
    parser.add_argument("--region", default="us-east-2")
    parser.add_argument("--dist", type=Path, default=Path("web/dist"))
    args = parser.parse_args()
    deploy(
        boto3.Session(profile_name=args.profile, region_name=args.region),
        args.stage,
        args.dist,
    )
