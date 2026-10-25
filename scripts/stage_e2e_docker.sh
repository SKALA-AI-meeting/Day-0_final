#!/bin/sh
# 단계별 화면 테스트(Playwright)를 공식 Playwright 이미지(리눅스 chromium + fake 마이크) 안에서 돌린다.
# 왜: macOS에서 마이크 권한이 없는 터미널 앱이 띄운 브라우저는 getUserMedia가 끝나지 않아(권한 창도 없음)
# 음성 프로필 테스트가 시작 전 마이크 확인에서 멈춘다. 컨테이너 안 chromium은 fake 장치를 그대로 쓴다.
# 컨테이너의 localhost:<포트>를 socat으로 호스트(host.docker.internal)에 넘겨 스택 주소는 호스트와 같다.
# 내부 토큰·VOICE_ENABLED는 띄운 스택의 env 파일(infra/.env.resolved-<프로필>)이 정본이다. 토큰은 출력하지 않는다.
# 사용: PROFILE=test-voice sh scripts/stage_e2e_docker.sh [Playwright 인자(STAGE)...]  (보통 make e2e SUITE=stages STAGE_RUNNER=docker)
set -eu
ROOT=$(cd "$(dirname "$0")/.." && pwd)
PROFILE=${PROFILE:-test-voice}
ENV_FILE="$ROOT/infra/.env.resolved-$PROFILE"
if [ ! -f "$ENV_FILE" ]; then
  echo "$ENV_FILE 이 없어요. make up PROFILE=$PROFILE 로 스택을 띄운 뒤 돌리세요" >&2
  exit 1
fi
from_env() { grep "^$1=" "$ENV_FILE" | tail -n 1 | cut -d= -f2- || true; }

# 스택의 음성 설정과 테스트 모드가 다르면 음성 전용 단언이 건너뛰어지거나 잘못 돈다
STACK_VOICE=$(from_env VOICE_ENABLED)
STACK_VOICE=${STACK_VOICE:-1}
if [ -n "${VOICE_ENABLED:-}" ] && [ "$VOICE_ENABLED" != "$STACK_VOICE" ]; then
  echo "VOICE_ENABLED=$VOICE_ENABLED 가 $PROFILE 스택 값($STACK_VOICE)과 달라요. 지우거나 프로필을 맞추세요" >&2
  exit 1
fi
VOICE_ENABLED=$STACK_VOICE

if [ -z "${INTERNAL_TOKEN:-}" ]; then INTERNAL_TOKEN=$(from_env INTERNAL_TOKEN); fi
if [ -z "$INTERNAL_TOKEN" ]; then
  echo "INTERNAL_TOKEN을 찾지 못했어요(env 또는 $ENV_FILE)" >&2
  exit 1
fi

VERSION=$(sed -n 's/.*"@playwright\/test": "\([0-9.]*\)".*/\1/p' "$ROOT/apps/web/package.json")
IMAGE=${PLAYWRIGHT_IMAGE:-mcr.microsoft.com/playwright:v$VERSION-noble}
WEB_URL=${WEB_URL:-http://localhost:3100}
E2E_INTERNAL_URL=${E2E_INTERNAL_URL:-http://localhost:8101}
E2E_API_URL=${E2E_API_URL:-${E2E_PUBLIC_URL:-http://localhost:8100}}
# URL의 포트. 없으면 스킴 기본값(http 80, https 443)
port() {
  explicit=$(echo "$1" | sed -nE 's#^[a-z]+://[^/:]+:([0-9]+).*#\1#p')
  if [ -n "$explicit" ]; then echo "$explicit"
  elif echo "$1" | grep -q '^https://'; then echo 443
  else echo 80; fi
}
PORTS=$(printf '%s\n' "$(port "$WEB_URL")" "$(port "$E2E_API_URL")" "$(port "$E2E_INTERNAL_URL")" | sort -u | tr '\n' ' ')
export INTERNAL_TOKEN
exec docker run --rm \
  --add-host=host.docker.internal:host-gateway \
  -e INTERNAL_TOKEN -e VOICE_ENABLED="$VOICE_ENABLED" -e CI=1 \
  -e WEB_URL="$WEB_URL" -e E2E_INTERNAL_URL="$E2E_INTERNAL_URL" -e E2E_API_URL="$E2E_API_URL" \
  -e PORTS="$PORTS" \
  -v "$ROOT":/repo -w /repo/apps/web "$IMAGE" \
  bash -lc 'set -e
    if ! command -v socat >/dev/null; then
      apt-get -qq update && apt-get -qq install -y socat >/dev/null \
        || { echo "socat을 설치하지 못했어요(컨테이너 네트워크를 확인하세요)" >&2; exit 1; }
    fi
    for p in $PORTS; do socat TCP-LISTEN:$p,fork,reuseaddr TCP:host.docker.internal:$p & done
    sleep 1
    set +e
    npx playwright test --reporter=line "$@"' playwright "$@"
