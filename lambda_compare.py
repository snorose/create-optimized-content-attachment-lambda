import boto3
import json
import base64
import re
import time
import os
from urllib.parse import quote_plus

REGION_NAME = os.environ.get('REGION_NAME')
BUCKET_NAME = os.environ.get('BUCKET_NAME')
TEST_KEY = "post-attachment/1718321/test_video.mp4" 
LAMBDA_FUNCTION_NAME = os.environ.get('LAMBDA_FUNCTION_NAME')
ECR_REPOSITORY_NAME = os.environ.get('ECR_REPOSITORY_NAME')

client = boto3.client('lambda', region_name=REGION_NAME)

def create_s3_event_payload(bucket, key):
    """
    lambda_function.py가 기대하는 S3 Event 구조 생성
    """
    # 실제 S3 이벤트처럼 Key를 URL 인코딩
    encoded_key = quote_plus(key)
    
    payload = {
        "Records": [
            {
                "eventVersion": "2.0",
                "eventSource": "aws:s3",
                "awsRegion": REGION_NAME,
                "eventTime": "1970-01-01T00:00:00.000Z",
                "eventName": "ObjectCreated:Put",
                "s3": {
                    "bucket": {
                        "name": bucket,
                        "arn": f"arn:aws:s3:::{bucket}"
                    },
                    "object": {
                        "key": key,
                        "size": 1024,
                        "eTag": "test-etag",
                        "sequencer": "test-sequencer"
                    }
                }
            }
        ]
    }
    return json.dumps(payload).encode('utf-8')

def trigger_cold_start(func_name):
    """
    환경 변수를 업데이트하여 강제로 Cold Start 환경을 조성
    """
    print(f"\n[Setup] '{func_name}' 초기화 중 (Cold Start 유도)...")
    
    try:
        config = client.get_function_configuration(FunctionName=func_name)
        env_vars = config.get('Environment', {}).get('Variables', {})
        
        # 현재 시간으로 환경 변수 업데이트하여 컨테이너 재성성 유도
        env_vars['FORCE_COLD_START'] = str(time.time())
        
        client.update_function_configuration(
            FunctionName=func_name,
            Environment={'Variables': env_vars}
        )
        
        # 업데이트 완료 대기
        while True:
            response = client.get_function(FunctionName=func_name)
            status = response['Configuration']['LastUpdateStatus']
            state = response['Configuration']['State']
            
            if state == 'Active' and status == 'Successful':
                print(" -> 업데이트 완료 (Ready)")
                break
            elif status == 'Failed':
                reason = response['Configuration'].get('LastUpdateStatusReason', 'Unknown')
                raise Exception(f"함수 업데이트 실패: {reason}")
                
            time.sleep(1)
            
    except Exception as e:
        print(f"[Warning] Cold Start 유도 실패 (권한 확인 필요): {e}")

def run_test(func_name, label, payload):
    trigger_cold_start(func_name)
    
    print(f"▶ Testing: {label} [{func_name}]")
    
    try:
        start_time = time.time()
        # InvocationType='RequestResponse'는 실행이 끝날 때까지 대기 (Latency 측정용)
        response = client.invoke(
            FunctionName=func_name,
            InvocationType='RequestResponse',
            LogType='Tail',
            Payload=payload
        )
        end_time = time.time()
        
        # 로그 디코딩
        log_result = base64.b64decode(response['LogResult']).decode('utf-8')
        
        # 정규표현식으로 시간 추출
        init_match = re.search(r"Init Duration:\s*([\d.]+)\s*ms", log_result)
        duration_match = re.search(r"Duration:\s*([\d.]+)\s*ms", log_result)
        max_mem_match = re.search(r"Max Memory Used:\s*(\d+)\s*MB", log_result)
        
        init_time = float(init_match.group(1)) if init_match else 0.0
        exec_time = float(duration_match.group(1)) if duration_match else 0.0
        max_mem = int(max_mem_match.group(1)) if max_mem_match else 0
        
        total_latency = (end_time - start_time) * 1000

        # 결과 출력
        print(f"   ▷ [Total Client Latency] : {total_latency:.2f} ms")
        print(f"   ▷ [Lambda Execution]     : {exec_time:.2f} ms")
        print(f"   ▷ [Max Memory Used]      : {max_mem} MB")
        
        if init_time > 0:
            print(f"   ★ [Cold Start Init]      : {init_time:.2f} ms")
            print(f"   ★ [Total Cold Latency]   : {(init_time + exec_time):.2f} ms (Init + Exec)")
        else:
            print(f"   [Cold Start Init]      : 0 ms (Warm Start 상태 혹은 감지 불가)")

        # 함수 실행 에러 확인
        if "FunctionError" in response:
             print(f"   [!!!] Function Error: {response['FunctionError']}")
             # 에러 상세 페이로드 확인
             err_payload = response['Payload'].read().decode('utf-8')
             print(f"   ERROR Details: {err_payload}")

    except Exception as e:
        print(f"   [Error] 테스트 실행 중 오류 발생: {e}")

if __name__ == "__main__":
    print("=== Lambda 성능 비교 테스트 (FFmpeg/Image Resize) ===\n")
    
    # 테스트 페이로드 생성
    payload_bytes = create_s3_event_payload(BUCKET_NAME, TEST_KEY)
    
    # Console 버전 테스트
    if FUNC_CONSOLE:
        run_test(FUNC_CONSOLE, "Console (Zip) Version", payload_bytes)
    else:
        print("FUNC_CONSOLE 이름이 설정되지 않아 건너뜁니다.")
        
    print("-" * 50)

    # Container Image 버전 테스트
    if FUNC_IMAGE:
        run_test(FUNC_IMAGE, "Container Image Version", payload_bytes)
    else:
        print("FUNC_IMAGE 이름이 설정되지 않아 건너뜁니다.")
    
    print("\n=== 테스트 완료 ===")