import argparse
import concurrent.futures
import json
import statistics
import time
import urllib.request

def run(url,concurrency=10,request_count=100,timeout=10):
 def one(_):
  start=time.perf_counter()
  try:
   with urllib.request.urlopen(url,timeout=timeout) as response:response.read(1024);status=response.status;error=None
  except Exception as exc:status=0;error=str(exc)
  return {'latency_ms':(time.perf_counter()-start)*1000,'status':status,'error':error}
 started=time.time()
 with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:results=list(pool.map(one,range(request_count)))
 latencies=sorted(x['latency_ms'] for x in results)
 def percentile(value):return latencies[min(len(latencies)-1,max(0,int(len(latencies)*value)-1))] if latencies else None
 return {'url':url,'concurrency':concurrency,'requests':request_count,'duration_seconds':round(time.time()-started,3),'errors':sum(1 for x in results if x['status']<200 or x['status']>=400),'p50_ms':round(statistics.median(latencies),2) if latencies else None,'p95_ms':round(percentile(.95),2) if latencies else None,'p99_ms':round(percentile(.99),2) if latencies else None,'statuses':{str(status):sum(1 for x in results if x['status']==status) for status in sorted(set(x['status'] for x in results))}}

def main():
 parser=argparse.ArgumentParser(description='Bounded PeaWork HTTP load-test harness');parser.add_argument('--url',required=True);parser.add_argument('--concurrency',type=int,default=10);parser.add_argument('--requests',type=int,default=100);parser.add_argument('--timeout',type=float,default=10);parser.add_argument('--output',default='load-test-result.json');args=parser.parse_args()
 result=run(args.url,args.concurrency,args.requests,args.timeout);open(args.output,'w').write(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
if __name__=='__main__':main()
