"""RustFS/S3 storage. Long-lived credentials are read only by the server."""
import json
import os

from ..upload_destination import (
    S3_BUCKET, S3_ENDPOINT, S3_MULTIPART_THRESHOLD, normalize_destination,
)


class S3Storage:
    provider = "s3"
    url_ttl = 3600
    single_put_limit = S3_MULTIPART_THRESHOLD

    def __init__(self, client, *, endpoint, bucket, prefix, region="us-east-1"):
        self.destination = normalize_destination(dict(
            provider="s3", endpoint=endpoint, bucket=bucket, prefix=prefix, region=region,
        ))
        self.client = client
        self.endpoint = self.destination["endpoint"]
        self.bucket = self.destination["bucket"]
        self.prefix = self.destination["prefix"]
        self.region = self.destination["region"]

    @classmethod
    def from_env(cls, *, endpoint=None, bucket=None, prefix=None, region=None):
        import boto3
        from botocore.config import Config

        target = normalize_destination(dict(
            provider="s3", endpoint=endpoint or os.environ.get("S3_ENDPOINT", S3_ENDPOINT),
            bucket=bucket or os.environ.get("S3_BUCKET", S3_BUCKET),
            prefix=prefix or os.environ.get("S3_PREFIX", ""),
            region=region or os.environ.get("S3_REGION", "us-east-1"),
        ))
        client = boto3.client(
            "s3", endpoint_url=target["endpoint"], region_name=target["region"],
            # None preserves the standard AWS environment/profile credential chain.
            aws_access_key_id=os.environ.get("S3_ACCESS_KEY_ID"),
            aws_secret_access_key=os.environ.get("S3_SECRET_ACCESS_KEY"),
            config=Config(
                signature_version="s3v4", s3={"addressing_style": "path"},
                connect_timeout=8, read_timeout=60, retries={"max_attempts": 3},
                request_checksum_calculation="when_required",
                response_checksum_validation="when_required",
            ),
        )
        return cls(client, **{k: v for k, v in target.items() if k != "provider"})

    def _key(self, key):
        if (not key.startswith(self.prefix + "/") or "\\" in key
                or any(p in ("", ".", "..") for p in key.rstrip("/").split("/"))):
            raise ValueError("object is outside the dedicated S3 prefix")
        return key

    def sign_put(self, key):
        return self.client.generate_presigned_url(
            "put_object", Params=dict(Bucket=self.bucket, Key=self._key(key)),
            ExpiresIn=self.url_ttl, HttpMethod="PUT",
        )

    def _marker(self, upload_id, name, body):
        self.client.put_object(
            Bucket=self.bucket, Key=self._key(f"{self.prefix}/{upload_id}/{name}"),
            Body=json.dumps(body).encode(), ContentType="application/json",
        )

    def begin_round(self, upload_id, round_id, started_at):
        self.client.delete_object(Bucket=self.bucket, Key=self._key(f"{self.prefix}/{upload_id}/_COMPLETE"))
        self._marker(upload_id, "_UPDATING", dict(uploadId=upload_id, roundId=round_id, startedAt=started_at))

    def promote(self, source, destination, etag):
        self.client.copy_object(
            Bucket=self.bucket, Key=self._key(destination),
            CopySource=dict(Bucket=self.bucket, Key=self._key(source)),
            **({"CopySourceIfMatch": etag} if etag else {}),
        )

    def discard_staged(self, key):
        self.client.delete_object(Bucket=self.bucket, Key=self._key(key))

    def finish_round(self, upload_id, round_id, completed_at):
        self._marker(upload_id, "_COMPLETE", dict(uploadId=upload_id, roundId=round_id, completedAt=completed_at))
        self.client.delete_object(Bucket=self.bucket, Key=self._key(f"{self.prefix}/{upload_id}/_UPDATING"))

    def create_multipart(self, key):
        return self.client.create_multipart_upload(Bucket=self.bucket, Key=self._key(key))["UploadId"]

    def sign_part(self, key, multipart_upload_id, part_number):
        return self.client.generate_presigned_url(
            "upload_part", Params=dict(Bucket=self.bucket, Key=self._key(key),
                                       UploadId=multipart_upload_id, PartNumber=part_number),
            ExpiresIn=self.url_ttl, HttpMethod="PUT",
        )

    def list_parts(self, key, multipart_upload_id):
        result, marker = [], 0
        while True:
            page = self.client.list_parts(Bucket=self.bucket, Key=self._key(key),
                                          UploadId=multipart_upload_id, MaxParts=1000,
                                          PartNumberMarker=marker)
            result.extend(dict(partNumber=p["PartNumber"], size=p["Size"], etag=p["ETag"])
                          for p in page.get("Parts", []))
            if not page.get("IsTruncated"):
                return sorted(result, key=lambda p: p["partNumber"])
            marker = page["NextPartNumberMarker"]

    def complete_multipart(self, key, multipart_upload_id, parts):
        return self.client.complete_multipart_upload(
            Bucket=self.bucket, Key=self._key(key), UploadId=multipart_upload_id,
            MultipartUpload={"Parts": [dict(PartNumber=p["partNumber"], ETag=p["etag"])
                                       for p in parts]},
        )["ETag"]
