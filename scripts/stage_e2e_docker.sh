#!/bin/sh
# 단계별 화면 테스트(Playwright)를 공식 Playwright 이미지(리눅스 chromium + fake 마이크) 안에서 돌린다.
# 왜: macOS에서 마이크 권한이 없는 터미널 앱이 띄운 브라우저는 getUserMedia가 끝나지 않아(권한 창도 없음)
# 음성 프로필 테스트가 시작 전 마이크 확인에서 멈춘다. 컨테이너 안 chromium은 fake 장치를 그대로 쓴다.
# 컨테이너의 localhost:<포트>를 socat으로 호스트(host.docker.internal)에 넘겨 스택 주소는 호스트와 같다.
# 내부 토큰은 env(INTERNAL_TOKEN) 또는 infra/.env.resolved-<프로필>에서 읽고 출력하지 않는다(값은 env로만 넘긴다).
# 사용: PROFILE=test-voice sh scripts/stage_e2e_docker.sh [Playwright 인자(STAGE)...]  (보통 make e2e SUITE=stages STAGE_RUNNER=docker)
set -eu
ROOT=$(cd "$(dirname "$0")/.." && pwd)
PROFILE=${PROFILE:-test-voice}
VERSION=$(sed -n 's/.*"@playwright\/test": "\([0-9.]*\)".*/\1/p' "$ROOT/apps/web/package.json")
IMAGE=${PLAYWRIGHT_IMAGE:-mcr.microsoft.com/playwright:v$VERSION-noble}
WEB_URL=${WEB_URL:-http://localhost:3100}
E2E_INTERNAL_URL=${E2E_INTERNAL_URL:-http://localhost:8101}
E2E_API_URL=${E2E_API_URL:-${E2E_PUBLIC_URL:-http://localhost:8100}}
if [ -z "${INTERNAL_TOKEN:-}" ]; then
  INTERNAL_TOKEN=$(grep '^INTERNAL_TOKEN=' "$ROOT/infra/.env.resolved-$PROFILE" 2>/dev/null | cut -d= -f2- || true)
fi
if [ -z "$INTERNAL_TOKEN" ]; then
  echo "INTERNAL_TOKEN을 찾지 못했어요(env 또는 infra/.env.resolved-$PROFILE). make up PROFILE=$PROFILE 뒤에 돌리세요" >&2
  exit 1
fi
port() { echo "$1" | sed -E 's#^[a-z]+://[^:/]+:([0-9]+).*#\1#'; }
PORTS="$(port "$WEB_URL") $(port "$E2E_API_URL") $(port "$E2E_INTERNAL_URL")"
export INTERNAL_TOKEN
exec docker run --rm \
  --add-host=host.docker.internal:host-gateway \
  -e INTERNAL_TOKEN -e VOICE_ENABLED="${VOICE_ENABLED:-1}" -e CI=1 \
  -e WEB_URL="$WEB_URL" -e E2E_INTERNAL_URL="$E2E_INTERNAL_URL" -e E2E_API_URL="$E2E_API_URL" \
  -e PORTS="$PORTS" \
  -v "$ROOT":/repo -w /repo/apps/web "$IMAGE" \
  bash -lc 'apt-get -qq update >/dev/null && apt-get -qq install -y socat >/dev/null 2>&1
    for p in $PORTS; do socat TCP-LISTEN:$p,fork,reuseaddr TCP:host.docker.internal:$p 2>/dev/null & done
    sleep 1
    npx playwright test --reporter=line "$@"' playwright "$@"
