from __future__ import annotations

import json
import subprocess


class TosStorage:
    def __init__(self, client, bucket="xiekp", prefix="eeg/inbox", url_ttl=3600):
        self.client = client
        self.bucket = bucket
        self.prefix = prefix
        self.url_ttl = url_ttl

    @classmethod
    def from_tosutil_config(
        cls,
        path,
        bucket="xiekp",
        endpoint="tos-cn-beijing.volces.com",
        region="cn-beijing",
    ):
        import tos

        values = {}
        with open(path, encoding="utf-8") as stream:
            for raw in stream:
                line = raw.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    values[key.strip()] = value.strip()
        access_key, secret_key = values.get("ak"), values.get("sk")
        if not access_key or not secret_key:
            raise RuntimeError("missing TOS credentials")
        if not access_key.startswith("AK"):
            access_key = cls._decrypt(access_key)
            secret_key = cls._decrypt(secret_key)
        token = values.get("token") or None
        if token and "Provide your security token" in token:
            token = None
        client = tos.TosClientV2(
            ak=access_key,
            sk=secret_key,
            security_token=token,
            endpoint=endpoint,
            region=region,
        )
        return cls(client, bucket=bucket)

    @staticmethod
    def _decrypt(value):
        result = subprocess.run(
            [
                "openssl",
                "enc",
                "-d",
                "-aes-128-cbc",
                "-a",
                "-A",
                "-K",
                "746f737574696c6165733132386b6579",
                "-iv",
                "746f737574696c61657331323869765f",
            ],
            input=value.encode("ascii"),
            capture_output=True,
            check=True,
        )
        return result.stdout.decode("utf-8")

    def sign_put(self, key):
        import tos

        return self.client.pre_signed_url(
            tos.HttpMethodType.Http_Method_Put,
            self.bucket,
            key,
            expires=self.url_ttl,
        ).signed_url

    def begin_round(self, upload_id, round_id, started_at):
        complete = f"{self.prefix}/{upload_id}/_COMPLETE"
        updating = f"{self.prefix}/{upload_id}/_UPDATING"
        self.client.delete_object(self.bucket, complete)
        body = json.dumps(
            {"uploadId": upload_id, "roundId": round_id, "startedAt": started_at},
            separators=(",", ":"),
        ).encode("utf-8")
        self.client.put_object(
            self.bucket,
            updating,
            content=body,
            content_length=len(body),
            content_type="application/json",
        )

    def promote(self, source, destination, etag):
        self.client.copy_object(
            self.bucket, destination, self.bucket, source,
            copy_source_if_match=etag or None,
        )

    def discard_staged(self, key):
        self.client.delete_object(self.bucket, key)

    def finish_round(self, upload_id, round_id, completed_at):
        complete = f"{self.prefix}/{upload_id}/_COMPLETE"
        updating = f"{self.prefix}/{upload_id}/_UPDATING"
        body = json.dumps(
            {"uploadId": upload_id, "roundId": round_id, "completedAt": completed_at},
            separators=(",", ":"),
        ).encode("utf-8")
        self.client.put_object(
            self.bucket,
            complete,
            content=body,
            content_length=len(body),
            content_type="application/json",
        )
        self.client.delete_object(self.bucket, updating)

    def create_multipart(self, key):
        return self.client.create_multipart_upload(self.bucket, key).upload_id

    def sign_part(self, key, multipart_upload_id, part_number):
        import tos

        return self.client.pre_signed_url(
            tos.HttpMethodType.Http_Method_Put,
            self.bucket,
            key,
            expires=self.url_ttl,
            query={"uploadId": multipart_upload_id, "partNumber": str(part_number)},
        ).signed_url

    def list_parts(self, key, multipart_upload_id):
        result, marker = [], None
        while True:
            output = self.client.list_parts(
                self.bucket,
                key,
                multipart_upload_id,
                part_number_marker=marker,
                max_parts=1000,
            )
            result.extend(
                {"partNumber": part.part_number, "size": part.size, "etag": part.etag}
                for part in output.parts
            )
            if not output.is_truncated:
                return sorted(result, key=lambda item: item["partNumber"])
            marker = output.next_part_number_marker

    def complete_multipart(self, key, multipart_upload_id, parts):
        from tos.models2 import UploadedPart

        uploaded = [
            UploadedPart(part["partNumber"], part["etag"], size=part["size"])
            for part in parts
        ]
        output = self.client.complete_multipart_upload(
            self.bucket, key, multipart_upload_id, parts=uploaded
        )
        return output.etag
