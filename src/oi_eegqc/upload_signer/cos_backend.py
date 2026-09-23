"""Tencent COS; permanent credentials remain exclusively on the signing server."""
import json
import os

class CosStorage:
    bucket='neurolm-1442740494'
    region='ap-beijing'
    prefix='neuro-lm/data/inbox'
    url_ttl=3600

    def __init__(self,client):self.client=client

    @classmethod
    def from_env(cls):
        from qcloud_cos import CosConfig,CosS3Client
        return cls(CosS3Client(CosConfig(Region=cls.region,SecretId=os.environ['COS_SECRET_ID'],SecretKey=os.environ['COS_SECRET_KEY'],Scheme='https')))

    def _key(self,key):
        if not key.startswith(self.prefix+'/') or '..' in key.split('/'):raise ValueError('Object is outside the dedicated inbox')
        return key

    def sign_put(self,key):return self.client.get_presigned_url(Bucket=self.bucket,Key=self._key(key),Method='PUT',Expired=self.url_ttl)

    def _marker(self,upload_id,name,body):
        self.client.put_object(Bucket=self.bucket,Key=self._key(f'{self.prefix}/{upload_id}/{name}'),Body=json.dumps(body).encode(),ContentType='application/json')

    def begin_round(self,upload_id,round_id,started_at):
        self.client.delete_object(Bucket=self.bucket,Key=self._key(f'{self.prefix}/{upload_id}/_COMPLETE'))
        self._marker(upload_id,'_UPDATING',dict(uploadId=upload_id,roundId=round_id,startedAt=started_at))

    def promote(self,source,destination,etag):
        self.client.copy_object(Bucket=self.bucket,Key=self._key(destination),CopySource=dict(Bucket=self.bucket,Region=self.region,Key=self._key(source)),**({'CopySourceIfMatch':etag} if etag else {}))

    def discard_staged(self,key):self.client.delete_object(Bucket=self.bucket,Key=self._key(key))

    def finish_round(self,upload_id,round_id,completed_at):
        self._marker(upload_id,'_COMPLETE',dict(uploadId=upload_id,roundId=round_id,completedAt=completed_at))
        self.client.delete_object(Bucket=self.bucket,Key=self._key(f'{self.prefix}/{upload_id}/_UPDATING'))

    def create_multipart(self,key):return self.client.create_multipart_upload(Bucket=self.bucket,Key=self._key(key))['UploadId']

    def sign_part(self,key,multipart_upload_id,part_number):
        return self.client.get_presigned_url(Bucket=self.bucket,Key=self._key(key),Method='PUT',Expired=self.url_ttl,Params=dict(uploadId=multipart_upload_id,partNumber=str(part_number)))

    def list_parts(self,key,multipart_upload_id):
        result=[];marker=0
        while True:
            page=self.client.list_parts(Bucket=self.bucket,Key=self._key(key),UploadId=multipart_upload_id,MaxParts=1000,PartNumberMarker=marker)
            result.extend(dict(partNumber=int(p['PartNumber']),size=int(p['Size']),etag=p['ETag']) for p in page.get('Part',[]))
            if str(page.get('IsTruncated','false')).lower()!='true':return sorted(result,key=lambda p:p['partNumber'])
            marker=int(page['NextPartNumberMarker'])

    def complete_multipart(self,key,multipart_upload_id,parts):
        return self.client.complete_multipart_upload(Bucket=self.bucket,Key=self._key(key),UploadId=multipart_upload_id,MultipartUpload={'Part':[dict(PartNumber=p['partNumber'],ETag=p['etag']) for p in parts]})['ETag']
