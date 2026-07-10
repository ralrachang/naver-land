#!/usr/bin/env bash
# VPS 직접 서빙 전환: nginx + 공유 비밀번호 로그인 + HTTPS(Let's Encrypt)
# 실행(1회): cd /opt/naver-land && bash deploy/nginx-setup.sh
set -euo pipefail

HOST="srv1730165.hstgr.cloud"
SITE_DIR="/opt/naver-land/site"
HTPASSWD="/etc/nginx/.htpasswd-naverland"
EMAIL="ralrachang@gmail.com"

echo "== 1) 패키지 설치 (nginx, htpasswd, certbot) =="
apt-get update -qq
apt-get install -y -qq nginx apache2-utils certbot python3-certbot-nginx

echo
echo "== 2) 로그인 계정 =="
# 기본: 공유 계정 'team' 1개. 나중에 아이디를 나누고 싶으면(누가 접속했는지 로그에 남음):
#   htpasswd /etc/nginx/.htpasswd-naverland kim   (반복해서 추가, -c 붙이면 안 됨)
#   htpasswd -D /etc/nginx/.htpasswd-naverland team   (계정 삭제)
if [ ! -f "$HTPASSWD" ]; then
  echo "공유 계정 'team'의 비밀번호를 정해서 입력하세요:"
  htpasswd -c "$HTPASSWD" team
else
  echo "기존 계정 파일 유지: $(cut -d: -f1 "$HTPASSWD" | tr '\n' ' ')"
fi

echo
echo "== 3) nginx 설정 =="
cat > /etc/nginx/sites-available/naverland <<NGINX
# IP로 직접 접근(스캐너 등)은 응답 없이 차단
server {
    listen 80 default_server;
    server_name _;
    return 444;
}
server {
    listen 80;
    server_name ${HOST};
    root ${SITE_DIR};
    index index.html;
    auth_basic "naver-land private";
    auth_basic_user_file ${HTPASSWD};
    access_log /var/log/nginx/naverland_access.log;
    # 인증서 발급/갱신 경로는 로그인 없이 통과
    location /.well-known/acme-challenge/ { auth_basic off; }
    location / { try_files \$uri \$uri/ =404; }
}
NGINX
rm -f /etc/nginx/sites-enabled/default
ln -sf /etc/nginx/sites-available/naverland /etc/nginx/sites-enabled/naverland
nginx -t
systemctl reload nginx

if command -v ufw >/dev/null 2>&1 && ufw status | grep -q "Status: active"; then
  echo "== 방화벽(ufw) 80/443 허용 =="
  ufw allow 'Nginx Full' || true
fi

echo
echo "== 4) HTTPS 인증서 =="
certbot --nginx -d "$HOST" -m "$EMAIL" --agree-tos --no-eff-email --redirect || {
  echo "!! certbot 실패 — http://$HOST 로는 동작합니다. 위 오류를 복사해서 알려주세요."
}

echo
echo "== 5) GitHub Pages 배포 끄기 (.env.sh 의 DEPLOY_ENABLED 주석 처리) =="
if [ -f /opt/naver-land/.env.sh ]; then
  sed -i 's/^\(export \)\?DEPLOY_ENABLED=.*/# & (VPS 직접 서빙으로 전환)/' /opt/naver-land/.env.sh
  grep -n "DEPLOY_ENABLED" /opt/naver-land/.env.sh || true
fi

echo
echo "== 6) 자체 점검 =="
code=$(curl -s -o /dev/null -w '%{http_code}' -H "Host: $HOST" http://127.0.0.1/ || true)
echo "비밀번호 없이 접근 시: HTTP $code (401 또는 https 리다이렉트 301이면 정상)"
echo
echo "완료! 접속 주소: https://$HOST  (아이디 team / 설정한 비밀번호)"
echo "접속 현황 확인:  python3 /opt/naver-land/deploy/access_report.py"
