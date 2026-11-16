import os
import boto3
endpoint=os.getenv('PEAWORK_OBJECT_ENDPOINT');bucket=os.environ['PEAWORK_OBJECT_BUCKET']
client=boto3.client('s3',endpoint_url=endpoint,region_name=os.getenv('PEAWORK_OBJECT_REGION'))
existing=[x['Name'] for x in client.list_buckets().get('Buckets',[])]
if bucket not in existing:client.create_bucket(Bucket=bucket)
print(f'Object bucket ready: {bucket}')
