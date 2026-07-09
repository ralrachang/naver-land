FROM python:3.12-slim

# git 은 GitHub Pages 배포에 필요
RUN apt-get update && apt-get install -y --no-install-recommends git tzdata \
    && rm -rf /var/lib/apt/lists/*

ENV TZ=Asia/Seoul
WORKDIR /opt/naver-land
COPY . .

# 서드파티 의존성 없음(stdlib). 필요 시 requirements.txt 설치.
# RUN pip install --no-cache-dir -r requirements.txt

# 단발 실행(크론/스케줄러가 컨테이너를 주기적으로 기동한다고 가정)
ENTRYPOINT ["python", "run.py"]
