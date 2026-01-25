import shutil
import boto3
import os
import subprocess
import json
from urllib.parse import unquote_plus
from PIL import Image, ImageOps

s3_client = boto3.client('s3')
resized_bucket = 'snorose-bucket-resized'

IMG_EXT_LIST = ("jpg","jpeg","png","jfif","bmp","webp")
VDO_EXT_LIST = ("mp4","mov")

FFMPEG_BIN = "/opt/bin/ffmpeg"
FFPROBE_BIN = "/opt/bin/ffprobe"


def resize_content_image(image_path, resized_path, max_size=1080, quality=70):
    """
    본문용 이미지 리사이징 (BICUBIC 변경으로 속도 향상)
    """
    # 이미지 파일을 열고 자동으로 닫히도록 with 구문 사용
    with Image.open(image_path) as image:
        # EXIF 데이터의 회전 정보를 적용하여 올바른 방향으로 이미지 조정
        image = ImageOps.exif_transpose(image)

        width, height = image.size
        max_dimension = max(width, height)

        # 긴 변이 설정된 최대 크기보다 큰 경우에만 리사이징 수행
        if max_dimension > max_size:
            # 긴 변을 max_size로 맞추기 위한 비율 계산
            scale = max_size / max_dimension
            new_width = int(width * scale)
            new_height = int(height * scale)
            
            # LANCZOS -> BICUBIC으로 변경하여 속도 향상
            resized = image.resize((new_width, new_height), Image.BICUBIC)
        else:
            # 이미지가 충분히 작으면 원본 복사본 생성
            resized = image.copy()

        # WebP 저장
        resized.save(resized_path, format='WEBP', quality=quality, optimize=True)

def get_video_resolution(video_path):
    cmd = [
        FFPROBE_BIN,
        "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height,duration",
        "-of", "csv=p=0:s=x",
        str(video_path)
    ]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


    if result.returncode == 0:
        try:
            parts = result.stdout.strip().split("x")

            # 맨 끝에 'x'가 하나 더 붙는 경우, 전체 None으로 리턴되는 경우 발생
            if len(parts) >= 3:
                width = int(parts[0])
                height = int(parts[1])
                duration = float(parts[2])
                return width, height, duration
        except Exception:
            pass
    return None, None, None

def resize_content_video(download_path, resized_path, max_size=720):
    """
    본문용 비디오 리사이징 (속도 최적화 버전)
    """
    try:
        width, height, duration = get_video_resolution(download_path)
        if width is None or height is None or duration is None:
            print("[ERROR] Failed to get video resolution.")
            return False

        max_dimension = max(width, height)
        
        # 원본이 목표 크기보다 작으면 인코딩 없이 바로 복사
        if max_dimension <= max_size:
            shutil.copy(download_path, resized_path)
            print(f"[SKIP] Video is already small. Copied as-is to {resized_path}")
            return True

        scale_filter = f"scale={max_size}:-2" if width >= height else f"scale=-2:{max_size}"

        cmd = [
            FFMPEG_BIN,
            "-y",
            "-i", download_path,
            "-vf", scale_filter,
            "-c:v", "libx264",
            
            # 인코딩 속도 우선 설정
            "-preset", "ultrafast",
            "-tune", "fastdecode",
            
            # 멀티 코어 활용
            "-threads", "0",        
            
            "-b:v", "1000k",        # 비디오 비트레이트 제한
            "-maxrate", "1000k",    # 최대 비트레이트 제한
            "-bufsize", "2000k",    
            
            "-c:a", "copy",         # 오디오는 재인코딩 없이 복사
            "-movflags", "+faststart",
            resized_path
        ]
        
        print(f"[DEBUG] Running FFmpeg Optimized: {' '.join(cmd)}")
        
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        if result.returncode != 0:
            print(f"[FFmpeg FAILED] exit {result.returncode}")
            print("STDERR (Last 500 chars):", result.stderr[-500:])
            return False

        print(f"[OK] Resized video saved to {resized_path}")
        return True
    except Exception as e:
        print(f"[ERROR] Unexpected error during video resize: {e}")
        return False

def extract_post_id(key_path):
    """
    S3 키에서 게시물 ID 추출
    예: post-attachment/12345/image.jpg -> 12345
    """
    # S3 키를 '/'로 분할하여 경로 구성요소들을 리스트로 변환
    parts = key_path.split('/')
    # 경로가 최소 2개 구성요소를 가지고 첫 번째가 'post-attachment'인지 확인
    if len(parts) >= 2 and parts[0] == 'post-attachment' and parts[1].isdigit():
        # 두 번째 구성요소가 게시물 ID이므로 반환
        return parts[1]
    # 조건에 맞지 않으면 None 반환
    return None

def image_handler(post_id, base_name, filename, download_path):
    resized_filename = f"resized-{base_name}.webp"
    upload_path = f'/tmp/{resized_filename}'
    resize_content_image(download_path, upload_path)
    resized_key = f'post-attachment/{post_id}/{resized_filename}'
    content_type = 'image/webp'
    print(f"Creating content image: {filename} -> {resized_filename}")
    return resized_key, upload_path, content_type

def video_handler(post_id, base_name, filename, download_path):
    resized_filename = f"resized-{base_name}.mp4"
    upload_path = f'/tmp/{resized_filename}'
    is_succeeded = resize_content_video(download_path, upload_path)

    # 비디오 리사이징 예외처리
    if not is_succeeded:
        return None

    resized_key = f'post-attachment/{post_id}/{resized_filename}'
    content_type = 'video/mp4'
    print(f"Creating content video: {filename} -> {resized_filename}")
    return resized_key, upload_path, content_type

def lambda_handler(event, context):
    download_path = None
    upload_path = None

    for record in event.get('Records', []):
        s3_filename = None

        # 임시 파일 경로 초기화 (예외 처리에서 사용하기 위해)
        try:
            # S3 이벤트에서 버킷 이름 추출
            bucket = record['s3']['bucket']['name']
            # S3 이벤트에서 객체 키 추출 및 URL 디코딩
            key = unquote_plus(record['s3']['object']['key'])

            # post-attachment 경로가 아닌 파일은 처리하지 않음 (이중 보안)
            if not key.startswith('post-attachment/'):
                print(f"Skipping non-post file: {key}")
                continue

            # S3 키에서 파일명만 추출 (경로 제외)
            filename = os.path.basename(key)
            s3_filename = filename  # S3 파일명 저장
            # S3 키에서 게시물 ID 추출 (fallback 검증용)
            post_id = extract_post_id(key)

            # 게시물 ID를 추출할 수 없는 경우 처리 중단
            if not post_id:
                print(f"Could not extract post ID from key: {key}")
                continue

            # 고유한 임시 파일 경로 생성 (충돌 방지)
            download_path = f'/tmp/{filename}'

            # S3에서 원본 이미지를 Lambda 임시 디렉토리로 다운로드
            s3_client.download_file(bucket, key, download_path)

            base_name = filename.rsplit('.', 1)[0]

            # 포맷 확인 (대소문자 구분 없이)
            if filename.lower().endswith(IMG_EXT_LIST):
                resized_key, upload_path, content_type = image_handler(post_id, base_name, filename, download_path)
            elif filename.lower().endswith(VDO_EXT_LIST):
                result = video_handler(post_id, base_name, filename, download_path)
                if not result:
                    error_message = f"Video resize failed for {filename}"
                    print(error_message)
                    continue
                resized_key, upload_path, content_type = result
            else:
                error_message = f"Unsupported file format: {filename}"
                print(error_message)
                continue

            # 리사이징된 이미지를 해당 S3 버킷에 업로드
            with open(upload_path, 'rb') as f:
                s3_client.upload_fileobj(
                    f,
                    resized_bucket,
                    resized_key,
                    ExtraArgs={
                        'ContentType': content_type,
                        'CacheControl': 'max-age=31536000, public',
                    }
                )
            file_size_bytes = os.path.getsize(upload_path)
            file_size_mb = file_size_bytes / (1024 * 1024)

            print(f"[OK] Successfully processed: {key} -> {resized_bucket}/{resized_key}, {file_size_mb:.2f}MB used.")

        except Exception as e:
            error_message = str(e)

            print(f"Error processing file {key} from bucket {bucket}: {e}")

        finally:
            try:
                # 다운로드된 원본 파일이 존재하면 삭제
                if download_path and os.path.exists(download_path):
                    os.remove(download_path)
                # 리사이징된 파일이 존재하면 삭제
                if upload_path and os.path.exists(upload_path):
                    os.remove(upload_path)
            except:
                # 파일 삭제 중 오류가 발생해도 무시 (이미 삭제되었을 수 있음)
                pass
