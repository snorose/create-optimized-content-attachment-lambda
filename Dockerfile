FROM public.ecr.aws/lambda/python:3.12

# 시스템 패키지 업데이트 및 FFmpeg 다운로드를 위한 도구 설치
RUN dnf update -y && \
    dnf install -y tar xz wget && \
    dnf clean all

# FFmpeg 정적 빌드 다운로드 및 설치
RUN mkdir -p /opt/bin && \
    wget https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz && \
    tar -xvf ffmpeg-release-amd64-static.tar.xz && \
    mv ffmpeg-*-amd64-static/ffmpeg /opt/bin/ffmpeg && \
    mv ffmpeg-*-amd64-static/ffprobe /opt/bin/ffprobe && \
    rm -rf ffmpeg-*-static*

# /opt/bin 실행 권한 부여
RUN chmod +x /opt/bin/ffmpeg /opt/bin/ffprobe

# 파이썬 의존성 설치
COPY requirements.txt ${LAMBDA_TASK_ROOT}
RUN pip install --no-cache-dir -r requirements.txt

# 소스 코드 복사
COPY lambda_function.py ${LAMBDA_TASK_ROOT}/

# 실행 핸들러 지정
CMD [ "lambda_function.lambda_handler" ]