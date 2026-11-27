"""Private bucket operations; credentials and signed URLs must never be logged."""
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import urlsplit
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError, BotoCoreError
from app.config import missing_config

PART_SIZE = 16 * 1024 * 1024

def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()

class S3StorageGateway:
    def __init__(self):
        missing = [k for k in missing_config() if k.startswith('S3_')]
        if missing:
            raise ValueError('missing_storage_configuration')
        endpoint = os.environ['S3_ENDPOINT'].strip().rstrip('/')
        parsed = urlsplit(endpoint)
        if parsed.scheme != 'https' or not parsed.hostname or not parsed.hostname.endswith('.r2.cloudflarestorage.com') or parsed.path or parsed.query:
            raise ValueError('invalid_r2_endpoint')
        self.buckets = {os.environ['S3_ORIGINALS_BUCKET'], os.environ['S3_DERIVED_BUCKET']}
        self.client = boto3.client('s3', endpoint_url=endpoint, region_name=os.getenv('S3_REGION','auto'),
            aws_access_key_id=os.environ['S3_ACCESS_KEY_ID'], aws_secret_access_key=os.environ['S3_SECRET_ACCESS_KEY'],
            config=Config(signature_version='s3v4',connect_timeout=10,read_timeout=90,retries={'max_attempts':3},s3={'addressing_style':'path'}))

    def validate(self,bucket,key):
        if bucket not in self.buckets or not key or key.startswith('/') or '..' in key.split('/'):
            raise ValueError('invalid_object_reference')

    def head(self,bucket,key):
        self.validate(bucket,key)
        try:
            return self.client.head_object(Bucket=bucket,Key=key)
        except ClientError as e:
            if e.response['Error']['Code'] in ('404','NoSuchKey','NotFound'):
                return None
            raise

    def sign_read(self,bucket,key,ttl=300):
        self.validate(bucket,key)
        return self.client.generate_presigned_url('get_object',Params={'Bucket':bucket,'Key':key},ExpiresIn=ttl)

    def sign_upload(self,bucket,key,mime,ttl=300):
        self.validate(bucket,key)
        return self.client.generate_presigned_url('put_object',Params={'Bucket':bucket,'Key':key,'ContentType':mime},ExpiresIn=ttl)

    def upload(self,path,bucket,key,mime,state_dir,stop_after=None):
        path=Path(path); self.validate(bucket,key)
        sha=digest(path); size=path.stat().st_size
        existing=self.head(bucket,key)
        if existing:
            if existing['ContentLength'] != size or existing.get('Metadata',{}).get('sha256') != sha:
                raise ValueError('immutable_object_conflict')
            return sha,size
        state_dir=Path(state_dir);state_dir.mkdir(parents=True,exist_ok=True)
        state_file=state_dir/(hashlib.sha256((bucket+'/'+key).encode()).hexdigest()+'.json')
        state=json.loads(state_file.read_text()) if state_file.exists() else None
        if state and (state['sha256'] != sha or state['size'] != size):
            raise ValueError('resume_source_changed')
        if not state:
            upload=self.client.create_multipart_upload(Bucket=bucket,Key=key,ContentType=mime,Metadata={'sha256':sha})
            state={'upload_id':upload['UploadId'],'sha256':sha,'size':size}
            state_file.write_text(json.dumps(state))
        args={'Bucket':bucket,'Key':key,'UploadId':state['upload_id']}
        parts=[]
        try:
            for page in self.client.get_paginator('list_parts').paginate(**args):
                parts.extend(page.get('Parts',[]))
        except ClientError as e:
            if e.response['Error']['Code']=='NoSuchUpload':
                state_file.unlink()
                return self.upload(path,bucket,key,mime,state_dir,stop_after)
            raise
        done={x['PartNumber']:x for x in parts}; count=0
        with path.open('rb') as f:
            for number in range(1,(size+PART_SIZE-1)//PART_SIZE+1):
                block=f.read(PART_SIZE)
                if number in done:
                    continue
                result=self.client.upload_part(**args,PartNumber=number,Body=block)
                done[number]={'PartNumber':number,'ETag':result['ETag']};count+=1
                print(json.dumps({'event':'part_uploaded','part':number,'bytes_total':size}),flush=True)
                if stop_after and count>=stop_after:
                    raise InterruptedError('intentional_resume_test')
        ordered=[{'PartNumber':n,'ETag':p['ETag']} for n,p in sorted(done.items())]
        self.client.complete_multipart_upload(**args,MultipartUpload={'Parts':ordered})
        info=self.head(bucket,key)
        if not info or info['ContentLength']!=size or info.get('Metadata',{}).get('sha256')!=sha:
            raise ValueError('upload_verification_failed')
        state_file.unlink(missing_ok=True)
        return sha,size

    def verify_content(self,bucket,key,sha):
        self.validate(bucket,key)
        response=self.client.get_object(Bucket=bucket,Key=key);h=hashlib.sha256()
        try:
            for block in response['Body'].iter_chunks(1024*1024):h.update(block)
        finally:
            response['Body'].close()
        if h.hexdigest()!=sha:raise ValueError('download_hash_mismatch')

    def health(self):
        for bucket in sorted(self.buckets):
            self.client.list_objects_v2(Bucket=bucket,MaxKeys=1)
        return {'status':'ok','bucket_count':len(self.buckets)}

def storage_health():
    try:
        return S3StorageGateway().health()
    except ValueError:
        return {'status':'missing_or_invalid_configuration'}
    except (ClientError,BotoCoreError):
        return {'status':'connection_or_permission_failed'}
