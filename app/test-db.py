"""
Script one-shot : supprime toutes les versions des objets landsat_* dans B2.
À lancer UNE FOIS après la migration Landsat → Sentinel.
"""

import os
import boto3
from botocore.config import Config

B2_KEY_ID = os.getenv("B2_KEY_ID")
B2_APPLICATION_KEY = os.getenv("B2_APPLICATION_KEY")
B2_BUCKET_NAME = os.getenv("B2_BUCKET_NAME")
B2_ENDPOINT_URL = os.getenv("B2_ENDPOINT_URL")
B2_REGION = os.getenv("B2_REGION", "us-east-005")

client = boto3.client(
    "s3",
    endpoint_url=B2_ENDPOINT_URL,
    aws_access_key_id=B2_KEY_ID,
    aws_secret_access_key=B2_APPLICATION_KEY,
    region_name=B2_REGION,
    config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
)

paginator = client.get_paginator("list_object_versions")
deleted = 0

for page in paginator.paginate(Bucket=B2_BUCKET_NAME, Prefix="rasters/landsat_"):
    for v in page.get("Versions", []):
        client.delete_object(Bucket=B2_BUCKET_NAME, Key=v["Key"], VersionId=v["VersionId"])
        deleted += 1
    for m in page.get("DeleteMarkers", []):
        client.delete_object(Bucket=B2_BUCKET_NAME, Key=m["Key"], VersionId=m["VersionId"])
        deleted += 1

print(f"🧹 {deleted} objets landsat_* supprimés de B2")