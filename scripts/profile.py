"""실행 프로필 해석기(표준 라이브러리만). `make profile`·`make doctor`·`make up` 등이 부른다.

값은 기본값 → `infra/.env`(비밀값) → `infra/profiles/<PROFILE>.env`(비밀 아닌 선택값) 순으로 겹친다.
셸에 모드 변수(`LLM_FAKE`·`ENABLE_TEST_API`·`AGENT_RUNNER`·추적 플래그)나 호스트 주소 변수
(`E2E_INTERNAL_URL`·`WEB_URL` 등)가 프로필과 다르게 있으면 오류다(어느 쪽이 맞는지 추측하지 않는다).
비밀값은 이름과 있음·없음만 출력하고 값은 쓰지 않는다.

    python3 scripts/profile.py show   [PROFILE]                 # 유효 모드 출력
    python3 scripts/profile.py doctor [PROFILE] [--starting]    # 출력 + 검사(외부 호출 없음)
    python3 scripts/profile.py get    PROFILE KEY               # 비밀 아닌 값 하나(Makefile용)
    python3 scripts/profile.py render PROFILE [--starting] [--out PATH]   # compose용 합친 env 파일
    python3 scripts/profile.py exec   PROFILE [--starting] [--need real|fake] -- CMD...

`--starting`은 실제로 무언가를 띄우거나 부를 때 붙인다: 안전하지 않은 조합(deploy에 가짜 LLM·테스트
API, 실제 LLM에 키 없음)을 거부하고, 실제 LLM이면 `ALLOW_API=1`이 없는 한 멈춘다. 키 선택 규칙은
`services/api/app/agent/llm/roles.py`가 정본이고 여기는 같은 허용 목록의 사본이다.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Mapping
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROFILE = "dev-fake"
PURPOSES = ("dev", "test", "deploy")
# roles.py RESTRICTED_MODELS와 같은 목록
RESTRICTED_MODELS = frozenset(
    {"gpt-4o-mini", "gpt-4.1-mini", "gpt-5.4-mini", "gpt-5.4-nano", "gpt-5-nano"}
)
ROLE_ENVS = {
    "intent": "MODEL_INTENT",
    "turn": "MODEL_TURN",
    "persona": "MODEL_PERSONA",
    "assistant": "MODEL_ASSISTANT",
    "evaluator": "MODEL_EVALUATOR",
}
# 셸과 프로필이 다르면 오류로 보는 모드 변수
MODE_KEYS = (
    "LLM_FAKE",
    "VOICE_ENABLED",
    "VOICE_PROVIDER",
    "ENABLE_TEST_API",
    "AGENT_RUNNER",
    "LANGCHAIN_TRACING_V2",
    "LANGSMITH_TRACING",
)
SECRET_MARKERS = ("KEY", "SECRET", "TOKEN", "PASSWORD")
PLACEHOLDER = "change-me"
# 프로필이 정하는 호스트 쪽 주소(derived 키) → 호스트 러너가 읽는 env 이름. exec 때 프로필 값이
# 이기고, 셸에 다른 값이 있으면 모드 변수처럼 오류다(묵은 셸·.env 값이 대상 스택을 바꾸지 못하게)
HOST_ENV_MAP = {
    "PUBLIC_URL": ("E2E_PUBLIC_URL", "VC_API_URL"),
    "INTERNAL_URL": ("E2E_INTERNAL_URL",),
    "WEB_URL": ("WEB_URL",),
    "SEED_DATABASE_URL": ("E2E_DATABASE_URL", "SEED_DATABASE_URL"),
    "PG_CONTAINER": ("VC_PG_CONTAINER",),
}
DEFAULTS = {
    "PROFILE_PURPOSE": "dev",
    "PROFILE_COMPOSE_PROJECT": "infra",
    "PROFILE_COMPOSE_OVERRIDE": "none",
    "LLM_FAKE": "1",
    "VOICE_ENABLED": "1",
    "VOICE_PROVIDER": "azure",
    "ENABLE_TEST_API": "1",
    "AGENT_RUNNER": "session",
    "LANGCHAIN_TRACING_V2": "false",
    "LANGSMITH_TRACING": "false",
    "OPENAI_MODEL": "gpt-4o-mini",
    "API_PORT": "8000",
    "WEB_PORT": "3000",
    "RABBITMQ_UI_PORT": "15672",
}


class ProfileError(Exception):
    pass


def is_secret(name: str) -> bool:
    return any(m in name for m in SECRET_MARKERS)


def parse_env_file(path: Path) -> dict[str, str]:
    """KEY=VALUE 줄만 읽는다(주석·빈 줄 무시, 양쪽 따옴표 제거, `export ` 접두 허용)."""
    out: dict[str, str] = {}
    for raw in path.read_text("utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :]
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        out[key] = value
    return out


def key_name_for(model: str) -> str:
    if model in RESTRICTED_MODELS:
        return "OPENAI_API_KEY"
    if model.startswith("gemini-"):
        return "GEMINI_API_KEY"
    if model.startswith("gpt-"):
        allowed = ", ".join(sorted(RESTRICTED_MODELS))
        raise ProfileError(f"모델 {model}은 제한 키 허용 목록({allowed}) 밖이다(개인 키 경로 없음)")
    raise ProfileError(f"키 규칙이 없는 모델: {model}")


class Profile:
    def __init__(self, name: str, root: Path = ROOT, shell: Mapping[str, str] | None = None):
        self.name = name
        self.root = root
        self.shell = os.environ if shell is None else shell
        self.path = root / "infra" / "profiles" / f"{name}.env"
        self.env_path = root / "infra" / ".env"
        if not self.path.is_file():
            names = sorted(p.stem for p in self.path.parent.glob("*.env"))
            raise ProfileError(f"프로필 {name}이 없다. 가능한 값: {', '.join(names)}")
        self.profile_values = parse_env_file(self.path)
        for key, value in self.profile_values.items():
            if is_secret(key) and value:
                raise ProfileError(
                    f"프로필 파일에 비밀값({key})을 두지 않는다. infra/.env로 옮긴다"
                )
        self.env_values = parse_env_file(self.env_path) if self.env_path.is_file() else {}
        self.values: dict[str, str] = {**DEFAULTS, **self.env_values, **self.profile_values}

    # ---- 읽기 --------------------------------------------------------------------------

    def get(self, key: str) -> str:
        return self.values.get(key, "")

    @property
    def purpose(self) -> str:
        return self.get("PROFILE_PURPOSE")

    @property
    def llm_fake(self) -> bool:
        return self.get("LLM_FAKE") == "1"

    @property
    def llm_mode(self) -> str:
        return "fake" if self.llm_fake else "live"

    @property
    def voice_mode(self) -> str:
        return (
            "off"
            if self.get("VOICE_ENABLED").lower() in ("0", "false")
            else self.get("VOICE_PROVIDER")
        )

    def role_models(self) -> dict[str, str]:
        base = self.get("OPENAI_MODEL") or DEFAULTS["OPENAI_MODEL"]
        return {role: self.get(env) or base for role, env in ROLE_ENVS.items()}

    def has(self, key: str) -> bool:
        return bool(self.values.get(key))

    def derived(self) -> dict[str, str]:
        """프로필 값에서 계산되는 비밀 아닌 값(Makefile `get`으로 읽는다)."""
        override = self.get("PROFILE_COMPOSE_OVERRIDE")
        files = ["infra/docker-compose.yml"]
        if override == "e2e":
            files.append("infra/docker-compose.e2e.yml")
        return {
            "LLM_MODE": self.llm_mode,
            "COMPOSE_PROJECT": self.get("PROFILE_COMPOSE_PROJECT"),
            "COMPOSE_FILES": " ".join(f"-f {f}" for f in files),
            "ENV_FILE": f".env.resolved-{self.name}",
            "PUBLIC_URL": self.get("PROFILE_PUBLIC_URL")
            or f"http://localhost:{self.get('API_PORT')}",
            "INTERNAL_URL": self.get("PROFILE_INTERNAL_URL") or "http://localhost:8001",
            "WEB_URL": self.get("PROFILE_WEB_URL") or f"http://localhost:{self.get('WEB_PORT')}",
            "RAG_URL": self.get("PROFILE_RAG_URL") or "http://localhost:9102/mcp",
            "SEED_DATABASE_URL": self.get("PROFILE_DATABASE_URL"),
            "PG_CONTAINER": f"{self.get('PROFILE_COMPOSE_PROJECT')}-postgres-1",
        }

    # ---- 검사 --------------------------------------------------------------------------

    def problems(self, starting: bool = False, need: str | None = None) -> list[str]:
        out: list[str] = []
        if self.purpose not in PURPOSES:
            out.append(f"PROFILE_PURPOSE={self.purpose!r}: {'/'.join(PURPOSES)} 중 하나여야 한다")
        if self.get("LLM_FAKE") not in ("0", "1"):
            out.append(f"LLM_FAKE={self.get('LLM_FAKE')!r}: 0 또는 1")
        if self.get("VOICE_ENABLED").lower() not in ("0", "1", "false", "true"):
            out.append("VOICE_ENABLED는 true 또는 false(1 또는 0)")
        if self.get("VOICE_PROVIDER") not in ("azure", "openai", "gemini", "fake"):
            out.append("VOICE_PROVIDER는 azure·openai·gemini·fake 중 하나")
        if self.voice_mode == "off" and self.get("ENABLE_TEST_API") != "1":
            out.append("VOICE_ENABLED=0에는 ENABLE_TEST_API=1이 필요하다")
        if self.get("ENABLE_TEST_API") not in ("", "0", "1"):
            out.append(f"ENABLE_TEST_API={self.get('ENABLE_TEST_API')!r}: 비움·0·1")
        if self.get("PROFILE_COMPOSE_OVERRIDE") not in ("none", "e2e"):
            out.append("PROFILE_COMPOSE_OVERRIDE는 none 또는 e2e")
        for key in MODE_KEYS:
            if key in self.shell and self.shell[key] != self.get(key):
                out.append(f"셸의 {key}={self.shell[key]!r}가 프로필 값 {self.get(key)!r}과 다르다")
        for key, value in self.host_env().items():
            if key in self.shell and self.shell[key] != value:
                out.append(
                    f"셸의 {key}={self.shell[key]!r}가 프로필 호스트 주소 {value!r}와 다르다"
                )
        if self.purpose == "deploy":
            if self.voice_mode != "azure":
                out.append("deploy에는 VOICE_ENABLED=1·VOICE_PROVIDER=azure가 필요하다")
            if self.llm_fake:
                out.append("deploy 프로필에 LLM_FAKE=1은 금지")
            if self.get("ENABLE_TEST_API") == "1":
                out.append("deploy 프로필에 ENABLE_TEST_API=1은 금지")
            for key in ("JWT_SECRET", "INTERNAL_TOKEN", "ASSESSMENT_MCP_TOKEN"):
                if not self.has(key) or self.get(key).startswith(PLACEHOLDER):
                    out.append(f"deploy에는 {key}를 실제 값으로 둔다")
        if not self.llm_fake:
            for role, model in self.role_models().items():
                try:
                    name = key_name_for(model)
                except ProfileError as exc:
                    out.append(f"{role}: {exc}")
                    continue
                if not self.has(name):
                    out.append(f"{role} 모델 {model}에 필요한 {name}가 비어 있다")
            if self.get("LANGCHAIN_TRACING_V2") == "true" and not self.has("LANGCHAIN_API_KEY"):
                out.append("LANGCHAIN_TRACING_V2=true인데 LANGCHAIN_API_KEY가 없다")
        if need == "real" and self.llm_fake:
            out.append(f"이 작업은 실제 LLM 프로필이 필요하다(지금 {self.name}은 LLM_FAKE=1)")
        if need == "fake" and not self.llm_fake:
            out.append(f"이 작업은 가짜 LLM 프로필 전용이다(지금 {self.name}은 LLM_FAKE=0)")
        if starting and not self.llm_fake and self.shell.get("ALLOW_API") != "1":
            out.append("실제 LLM(제한키·과금)을 쓴다. ALLOW_API=1을 명시해야 시작한다")
        return out

    # ---- 출력 --------------------------------------------------------------------------

    def describe(self) -> str:
        d = self.derived()
        override = self.get("PROFILE_COMPOSE_OVERRIDE")
        if override == "e2e":
            ports = (
                f"web {self.get('WEB_PORT')}, api {self.get('API_PORT')}, 내부 8101, "
                f"rabbitmq UI {self.get('RABBITMQ_UI_PORT')}, postgres 5442, rag 9112(127.0.0.1)"
            )
        else:
            ports = (
                f"web {self.get('WEB_PORT')}, api {self.get('API_PORT')}, "
                f"rabbitmq UI {self.get('RABBITMQ_UI_PORT')}; 내부 API·postgres·rag는 비공개"
                "(SCOPE=infra면 postgres 5432·rabbitmq 5672·rag 9102)"
            )
        lines = [
            f"프로필 {self.name} ({self.path.relative_to(self.root)})",
            f"  목적          {self.purpose}",
            f"  LLM           {self.llm_mode} (LLM_FAKE={self.get('LLM_FAKE')})"
            + ("" if self.llm_fake else "  ← 제한키·과금, ALLOW_API=1 필요"),
            f"  음성          {self.voice_mode} (VOICE_ENABLED={self.get('VOICE_ENABLED')}, "
            f"VOICE_PROVIDER={self.get('VOICE_PROVIDER')})",
            f"  테스트 API    ENABLE_TEST_API={self.get('ENABLE_TEST_API') or '(비움)'}",
            f"  AGENT_RUNNER  {self.get('AGENT_RUNNER')}",
            f"  LangSmith     LANGCHAIN_TRACING_V2={self.get('LANGCHAIN_TRACING_V2')}, "
            f"LANGSMITH_TRACING={self.get('LANGSMITH_TRACING')}",
            f"  compose       project {d['COMPOSE_PROJECT']}, {d['COMPOSE_FILES']}, "
            f"env {d['ENV_FILE']}",
            f"  포트          {ports}",
            f"  호스트 주소   api {d['PUBLIC_URL']}, 내부 {d['INTERNAL_URL']}, web {d['WEB_URL']}",
            "  역할 모델",
        ]
        for role, model in self.role_models().items():
            if self.llm_fake:
                lines.append(f"    {role:<10} {model} (fake: 호출 없음)")
                continue
            try:
                name = key_name_for(model)
                state = "있음" if self.has(name) else "없음"
                lines.append(f"    {role:<10} {model} → {name} ({state})")
            except ProfileError as exc:
                lines.append(f"    {role:<10} {model} → 오류: {exc}")
        keys = ("OPENAI_API_KEY", "GEMINI_API_KEY", "LANGCHAIN_API_KEY", "VC_JUDGE_MODEL")
        lines.append(
            "  키·판정       "
            + ", ".join(
                f"{k} {'있음' if (self.has(k) or self.shell.get(k)) else '없음'}" for k in keys
            )
            + " (판정 모델은 기본값 없음)"
        )
        if self.voice_mode == "azure":
            lines.append(
                "  Azure 음성 키 "
                + ", ".join(
                    f"{key} {'있음' if self.has(key) else '없음'}"
                    for key in (
                        "AZURE_SPEECH_KEY",
                        "AZURE_SPEECH_REGION",
                        "AZURE_SPEECH_RESOURCE_ID",
                        "AZURE_TENANT_ID",
                        "AZURE_CLIENT_ID",
                        "AZURE_CLIENT_SECRET",
                        "VOICE_QUOTA_PERIOD",
                    )
                )
            )
        if self.voice_mode in ("openai", "gemini"):
            label, key_name, stt, tts = (
                ("OpenAI", "OPENAI_API_KEY", "gpt-4o-mini-transcribe", "gpt-4o-mini-tts")
                if self.voice_mode == "openai"
                else (
                    "Gemini",
                    "GEMINI_API_KEY",
                    "gemini-3.5-flash-lite",
                    "gemini-2.5-flash-preview-tts",
                )
            )
            stt = self.get("MODEL_STT") or stt
            tts = self.get("MODEL_TTS") or tts
            has_key = self.has(key_name) or self.shell.get(key_name)
            key = "있음" if has_key else "없음"
            period = "있음" if self.has("VOICE_QUOTA_PERIOD") else "없음"
            lines.append(
                f"  {label} 음성   STT {stt}, TTS {tts} → {key_name} ({key}), "
                f"VOICE_QUOTA_PERIOD {period}"
            )
        lines.append(
            f"  .env 파일     {'있음' if self.env_path.is_file() else '없음'} "
            f"({self.env_path.relative_to(self.root)})"
        )
        return "\n".join(lines)

    # ---- 산출 --------------------------------------------------------------------------

    def rendered(self) -> str:
        """compose `--env-file`·컨테이너 `env_file`에 함께 주는 합친 env 파일 본문."""
        head = [
            f"# 생성 파일(make가 매번 다시 만든다). 프로필 {self.name} + infra/.env. 커밋 금지.",
        ]
        body = [f"{k}={v}" for k, v in sorted(self.values.items())]
        return "\n".join(head + body) + "\n"

    def host_env(self) -> dict[str, str]:
        """호스트 러너에 주는 주소 env(프로필이 정한 값만. 빈 값은 넣지 않는다)."""
        derived = self.derived()
        return {
            target: derived[src]
            for src, targets in HOST_ENV_MAP.items()
            if derived[src]
            for target in targets
        }

    def exec_env(self) -> dict[str, str]:
        """셸 < 프로필 값 < 프로필 호스트 주소. 충돌은 problems()가 먼저 거른다."""
        return {**self.shell, **self.values, **self.host_env()}


def _fail(problems: list[str]) -> None:
    sys.stdout.flush()
    for p in problems:
        print(f"  ✗ {p}", file=sys.stderr)
    sys.exit(1)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=ROOT, help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("show", "doctor"):
        s = sub.add_parser(name)
        s.add_argument("profile", nargs="?", default=None)
        s.add_argument("--starting", action="store_true")
    g = sub.add_parser("get")
    g.add_argument("profile")
    g.add_argument("key")
    r = sub.add_parser("render")
    r.add_argument("profile")
    r.add_argument("--starting", action="store_true")
    r.add_argument("--need", choices=("real", "fake"), default=None)
    r.add_argument("--out", type=Path, default=None)
    e = sub.add_parser("exec")
    e.add_argument("profile")
    e.add_argument("--starting", action="store_true")
    e.add_argument("--need", choices=("real", "fake"), default=None)
    argv = sys.argv[1:] if argv is None else list(argv)
    cmd: list[str] = []
    if "--" in argv:  # exec PROFILE [옵션] -- CMD...: `--` 뒤는 그대로 실행할 명령
        cut = argv.index("--")
        argv, cmd = argv[:cut], argv[cut + 1 :]
    args = parser.parse_args(argv)
    name = args.profile or os.environ.get("PROFILE") or DEFAULT_PROFILE
    try:
        profile = Profile(name, root=args.root)
        if args.command == "get":
            value = profile.derived().get(args.key)
            if value is None:
                value = "" if is_secret(args.key) else profile.get(args.key)
            print(value)
            return 0
        if args.command in ("show", "doctor"):
            print(profile.describe())
            if args.command == "show":
                return 0
            problems = profile.problems(starting=args.starting)
            if problems:
                print("doctor: 문제", file=sys.stderr)
                _fail(problems)
            print("doctor: 문제 없음")
            return 0
        if args.command == "render":
            problems = profile.problems(starting=args.starting, need=args.need)
            if problems:
                _fail(problems)
            out = args.out or (profile.root / "infra" / profile.derived()["ENV_FILE"])
            out.write_text(profile.rendered(), "utf-8")
            print(out.relative_to(profile.root) if out.is_relative_to(profile.root) else out)
            return 0
        if args.command == "exec":
            if not cmd:
                raise ProfileError("exec: 실행할 명령이 없다(`-- CMD...`)")
            problems = profile.problems(starting=args.starting, need=args.need)
            if problems:
                _fail(problems)
            os.execvpe(cmd[0], cmd, profile.exec_env())
    except ProfileError as exc:
        print(f"profile: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
