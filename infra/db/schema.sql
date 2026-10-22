-- Day Zero 토론형 스키마. 정본은 docs/architecture/data-model.md(필드 이름·상태값은 docs/contracts/README.md와 같다).
-- LangGraph checkpointer 테이블은 라이브러리가 만든다. 마이그레이션 도구 없이 빈 볼륨에서 한 번 적용한다.
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- 계정·채용 건 ------------------------------------------------------------------

CREATE TABLE companies (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE admins (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id uuid NOT NULL REFERENCES companies(id),
  email text NOT NULL UNIQUE,
  password_hash text NOT NULL,              -- bcrypt
  created_at timestamptz NOT NULL DEFAULT now()
);

-- 요소 선택 컬럼 없음(모든 채용 건이 5요소·카드 6장). scenario는 생성 때 조립·복사하고 수정·삭제 없음.
CREATE TABLE positions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id uuid NOT NULL REFERENCES companies(id),
  name text NOT NULL,
  mission_id text NOT NULL DEFAULT 'debate',     -- scenarios/missions/<id>/
  session_minutes int NOT NULL DEFAULT 60 CHECK (session_minutes IN (60, 15)),
  catalog_version text NOT NULL,                 -- 미션 폴더 해시(scenarios/version.py)
  scenario jsonb NOT NULL,                       -- seats·keywords·안·카드·평가표·프롬프트 복사본(docs/product/scenario.md)
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE invites (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  position_id uuid NOT NULL REFERENCES positions(id),
  alias text NOT NULL,
  email text NOT NULL,
  token_hash text NOT NULL UNIQUE,          -- 토큰 원문은 링크에만
  expires_at timestamptz NOT NULL,
  used_at timestamptz,
  cancelled_at timestamptz,
  previous_session_id uuid,                 -- 재응시: 이전 세션
  reissued_at timestamptz,                  -- 재발급(같은 행의 token_hash·expires_at 교체)
  superseded_at timestamptz,                -- 재응시로 대체된 초대(같은 별칭 재사용 허용)
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX invites_alias_active ON invites (position_id, alias) WHERE superseded_at IS NULL;

-- 세션과 토론 상태 ----------------------------------------------------------------

CREATE TABLE sessions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  invite_id uuid NOT NULL UNIQUE REFERENCES invites(id),
  status text NOT NULL DEFAULT 'invited'
    CHECK (status IN ('invited','cancelled','in_progress','completed','report_ready','approving','reviewed','failed')),
  phase text CHECK (phase IN ('keyword','problem','solution','proposal','completed')),
  display_name text CHECK (display_name IS NULL OR char_length(display_name) BETWEEN 1 AND 20),
  consented_at timestamptz,
  started_at timestamptz,
  ends_at timestamptz,
  phase_started_at timestamptz,
  phase_deadline_at timestamptz,            -- 누적 고정 마감
  generation int NOT NULL DEFAULT 0,        -- 지원자 메시지 저장·단계 전환마다 +1
  invite_delivered_phases text[] NOT NULL DEFAULT '{}',
  submitted_at timestamptz,
  completed_at timestamptz,
  ended_at timestamptz,                     -- completed·failed(session)·cancelled 시각(90일 보관 기준)
  llm_failures int NOT NULL DEFAULT 0,
  debate_cost_usd numeric NOT NULL DEFAULT 0,   -- 토론 LLM 비용(하드 상한용)
  cost_usd numeric NOT NULL DEFAULT 0,          -- 토론 + 리포트 전체(관측)
  report_enqueued_at timestamptz,
  approve_enqueued_at timestamptz,
  eval_round int NOT NULL DEFAULT 1,
  failed_stage text CHECK (failed_stage IN ('session','report')),
  failed_reason text,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX sessions_in_progress ON sessions (phase_deadline_at) WHERE status = 'in_progress';

CREATE TABLE messages (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  session_id uuid NOT NULL REFERENCES sessions(id),
  channel text NOT NULL CHECK (channel IN ('room','assistant')),
  sender text NOT NULL CHECK (sender IN ('candidate','p1','p2','p3','system','assistant')),
  phase text CHECK (phase IN ('keyword','problem','solution','proposal')),
  content text NOT NULL,
  run_id uuid,
  seq int,
  client_msg_id text,
  origin text CHECK (origin IN ('spontaneous','invited')),   -- 지원자 room 메시지만
  is_invite boolean NOT NULL DEFAULT false,
  card_ids text[] NOT NULL DEFAULT '{}',
  cited_material_ids text[] NOT NULL DEFAULT '{}',
  meta jsonb NOT NULL DEFAULT '{}'::jsonb,  -- 토큰·지연·모델·cost_usd·guard·stance_check·kind
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (session_id, run_id, seq)
);
CREATE INDEX messages_session_time ON messages (session_id, created_at);
CREATE UNIQUE INDEX messages_client_msg ON messages (session_id, client_msg_id) WHERE client_msg_id IS NOT NULL;

CREATE TABLE options (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  session_id uuid NOT NULL REFERENCES sessions(id),
  phase text NOT NULL CHECK (phase IN ('keyword','problem','solution')),
  label text NOT NULL,
  proposer text NOT NULL CHECK (proposer IN ('candidate','p1','p2','p3')),   -- 최초 제안자
  scenario_option_key text,
  source_message_id uuid REFERENCES messages(id),
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX options_scenario_key ON options (session_id, phase, scenario_option_key)
  WHERE scenario_option_key IS NOT NULL;
CREATE UNIQUE INDEX options_free_source ON options (session_id, phase, source_message_id)
  WHERE scenario_option_key IS NULL AND source_message_id IS NOT NULL;

CREATE TABLE message_intents (               -- 지원자 room 메시지마다 1행(메일박스가 분류)
  message_id uuid PRIMARY KEY REFERENCES messages(id),
  session_id uuid NOT NULL REFERENCES sessions(id),
  phase text NOT NULL CHECK (phase IN ('keyword','problem','solution','proposal')),   -- messages.phase
  intent text NOT NULL CHECK (intent IN ('propose','agree','oppose','question','other')),
  target_option_id uuid REFERENCES options(id),
  scenario_option_key text,
  new_option_label text,
  confidence text NOT NULL CHECK (confidence IN ('high','low')),
  addressed_to text[] NOT NULL DEFAULT '{}',
  fallback boolean NOT NULL DEFAULT false,
  model text,
  latency_ms int,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX message_intents_session ON message_intents (session_id, phase);

CREATE TABLE stances (                       -- append-only 입장 이력
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  session_id uuid NOT NULL REFERENCES sessions(id),
  phase text NOT NULL CHECK (phase IN ('keyword','problem','solution')),
  participant text NOT NULL CHECK (participant IN ('candidate','p1','p2','p3')),
  option_id uuid NOT NULL REFERENCES options(id),
  stance text NOT NULL CHECK (stance IN ('support','oppose')),
  source_message_id uuid NOT NULL REFERENCES messages(id),
  run_id uuid,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (session_id, source_message_id, participant, option_id)
);
CREATE INDEX stances_session_phase ON stances (session_id, phase, created_at);

CREATE TABLE slots (                         -- start에서 keyword·problem·solution 3행(empty). 확정 값은 불변
  session_id uuid NOT NULL REFERENCES sessions(id),
  phase text NOT NULL CHECK (phase IN ('keyword','problem','solution')),
  status text NOT NULL DEFAULT 'empty' CHECK (status IN ('empty','confirmed')),
  value text,
  option_id uuid REFERENCES options(id),
  filled_by text CHECK (filled_by IN ('candidate','p1','p2','p3')),
  resolution text CHECK (resolution IN ('consensus','timeout')),
  support jsonb,                            -- 확정 순간 [{option_id, supporters}]
  confirmed_at timestamptz,
  run_id uuid,
  PRIMARY KEY (session_id, phase),
  CHECK (
    (status = 'empty' AND value IS NULL AND filled_by IS NULL AND resolution IS NULL)
    OR (status = 'confirmed' AND value IS NOT NULL AND filled_by IS NOT NULL
        AND resolution IS NOT NULL AND confirmed_at IS NOT NULL)
  )
);

CREATE TABLE proposals (
  session_id uuid PRIMARY KEY REFERENCES sessions(id),
  sections jsonb NOT NULL DEFAULT '{}'::jsonb,   -- {keyword, problem, solution, rationale, expected_effect, idea_credit}
  revision int NOT NULL DEFAULT 0,
  updated_at timestamptz NOT NULL DEFAULT now(),
  submitted jsonb,                          -- 제출본(불변)
  submitted_at timestamptz,
  auto_submitted boolean NOT NULL DEFAULT false,
  submit_blank_sections text[],             -- 제출본에서 공백뿐인 칸 id(템플릿 순서)
  blank_warning_shown boolean               -- 빈칸 경고 창을 본 뒤 제출했는지, 자동 제출이면 null
);

CREATE TABLE agent_runs (                    -- 자극 실행 기록이자 메일박스의 영속 사본
  run_id uuid PRIMARY KEY,
  session_id uuid NOT NULL REFERENCES sessions(id),
  graph text NOT NULL CHECK (graph IN ('debate','assistant')),
  stimulus text CHECK (stimulus IN ('message','phase_started','idle','card_due')),
  ref_id text,                              -- message_id 또는 card_id
  generation int,
  status text NOT NULL DEFAULT 'queued'
    CHECK (status IN ('queued','started','finished','deferred','skipped','failed')),
  decision jsonb,                           -- 턴 매니저 결정 스키마
  usage jsonb NOT NULL DEFAULT '{}'::jsonb, -- llm_calls·retries·tokens_in/out·latency_ms·tool_calls·cost_usd
  queued_at timestamptz NOT NULL DEFAULT now(),
  started_at timestamptz,
  finished_at timestamptz
);
CREATE INDEX agent_runs_pending ON agent_runs (session_id) WHERE status IN ('queued','started');

CREATE TABLE connection_gaps (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  session_id uuid NOT NULL REFERENCES sessions(id),
  disconnected_at timestamptz NOT NULL,
  reconnected_at timestamptz
);
CREATE INDEX connection_gaps_session ON connection_gaps (session_id);

CREATE TABLE material_views (                -- 자료 탭 열람(평가하지 않는 사실)
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  session_id uuid NOT NULL REFERENCES sessions(id),
  material_id text NOT NULL,
  viewed_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX material_views_session ON material_views (session_id);

CREATE TABLE assistant_calls (               -- AI 활용 사실(평가하지 않음), 세션 60회 한도
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  session_id uuid NOT NULL REFERENCES sessions(id),
  request_message_id uuid NOT NULL REFERENCES messages(id),
  response_message_id uuid REFERENCES messages(id),
  cited_material_ids text[] NOT NULL DEFAULT '{}',
  tool_calls jsonb NOT NULL DEFAULT '[]'::jsonb,   -- [{name, query?, material_id?}] 요청당 최대 3
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX assistant_calls_session ON assistant_calls (session_id);

CREATE TABLE debrief_surveys (
  session_id uuid PRIMARY KEY REFERENCES sessions(id),
  answers jsonb NOT NULL,                   -- [{q, value}] natural·fair 1~5
  free_comment text CHECK (free_comment IS NULL OR char_length(free_comment) <= 500),
  created_at timestamptz NOT NULL DEFAULT now()
);

-- 관찰·분석·리포트 -----------------------------------------------------------------

CREATE TABLE session_cards (                 -- 카드 진행 상태(card_due 첫 처리 때 생성)
  session_id uuid NOT NULL REFERENCES sessions(id),
  card_id text NOT NULL,
  locked_option_id uuid REFERENCES options(id),
  delivered_seats jsonb NOT NULL DEFAULT '[]'::jsonb,
  locked_at timestamptz,
  PRIMARY KEY (session_id, card_id)
);

CREATE TABLE observations (                  -- 카드 관찰 기록(레이블·수준 없음)
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  session_id uuid NOT NULL REFERENCES sessions(id),
  card_id text NOT NULL,
  element text NOT NULL,
  phase text NOT NULL CHECK (phase IN ('keyword','problem','solution','proposal')),
  fired_at timestamptz NOT NULL,
  trigger_message_id uuid REFERENCES messages(id),
  run_id uuid,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (session_id, card_id)
);

CREATE TABLE evaluation_runs (               -- 평가 실행 1행, evaluations와 한 트랜잭션
  session_id uuid NOT NULL REFERENCES sessions(id),
  run_id uuid NOT NULL,
  rubric_version text NOT NULL,
  prompt_versions jsonb NOT NULL,           -- {common: 해시, <element>: 해시}
  model text NOT NULL,
  elements jsonb NOT NULL,                  -- ElementSummary 5행
  has_failed boolean NOT NULL DEFAULT false,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (session_id, run_id)
);

CREATE TABLE evaluations (                   -- 평가 에이전트의 차원별 판단
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  session_id uuid NOT NULL REFERENCES sessions(id),
  run_id uuid NOT NULL,
  dimension_id text NOT NULL,
  level int CHECK (level BETWEEN 1 AND 5),
  no_record_reason text CHECK (no_record_reason IN
    ('no_activity','evaluate_failed','not_applicable','event_not_fired','auto_submitted_blank')),
  evidence_refs jsonb NOT NULL DEFAULT '[]'::jsonb,
  rationale text NOT NULL DEFAULT '',
  confidence text CHECK (confidence IN ('high','medium','low')),
  created_at timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (session_id, run_id) REFERENCES evaluation_runs(session_id, run_id),
  UNIQUE (session_id, dimension_id, run_id),
  CHECK ((level IS NULL) <> (no_record_reason IS NULL)),
  CHECK (no_record_reason IS NULL OR confidence IS NULL)
);

CREATE TABLE reports (                       -- 총점·합계 컬럼 없음. final은 draft content 그대로
  session_id uuid PRIMARY KEY REFERENCES sessions(id),
  stage text NOT NULL CHECK (stage IN ('draft','final')),
  content jsonb NOT NULL,
  content_hash text NOT NULL,
  eval_run_id uuid,                         -- 이 리포트가 읽은 evaluation_runs.run_id
  generated_at timestamptz NOT NULL DEFAULT now(),
  reviewed_at timestamptz,
  attempts int NOT NULL DEFAULT 0,
  usage jsonb NOT NULL DEFAULT '{}'::jsonb,
  reviewer_name text
);

CREATE TABLE rubric_adjustments (            -- 담당자 차원 단위 조정(append-only, 같은 차원은 마지막 행)
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  session_id uuid NOT NULL REFERENCES sessions(id),
  dimension_id text NOT NULL,
  level int CHECK (level BETWEEN 1 AND 5),
  no_record_reason text CHECK (no_record_reason IN
    ('no_activity','evaluate_failed','not_applicable','event_not_fired','auto_submitted_blank')),
  memo text NOT NULL,
  adjusted_by uuid NOT NULL REFERENCES admins(id),
  reviewer_name text NOT NULL,
  adjusted_at timestamptz NOT NULL DEFAULT now(),
  CHECK ((level IS NULL) <> (no_record_reason IS NULL))
);
CREATE INDEX rubric_adjustments_session ON rubric_adjustments (session_id, dimension_id, adjusted_at);

CREATE TABLE report_notes (                  -- 검토 완료 뒤 메모(값 불변)
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  session_id uuid NOT NULL REFERENCES sessions(id),
  text text NOT NULL,
  reviewer_name text NOT NULL,
  admin_id uuid NOT NULL REFERENCES admins(id),
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX report_notes_session ON report_notes (session_id);

-- 검색 저장소 ------------------------------------------------------------------------

CREATE TABLE materials (                     -- 자료 원문의 버전별 사본(scripts/seed.py가 시드, 이전 버전 유지)
  catalog_version text NOT NULL,
  material_id text NOT NULL,
  keyword_key text NOT NULL,
  title text NOT NULL,
  content text NOT NULL,                    -- Markdown
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (catalog_version, material_id)
);

CREATE TABLE material_chunks (               -- rag-mcp만 접근
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  material_id text NOT NULL,
  keyword_key text NOT NULL,
  chunk_no int NOT NULL,
  content text NOT NULL,
  embedding vector(1024) NOT NULL,
  model text NOT NULL DEFAULT 'BAAI/bge-m3',
  catalog_version text NOT NULL,
  UNIQUE (catalog_version, material_id, chunk_no)
);
CREATE INDEX material_chunks_filter ON material_chunks (catalog_version, keyword_key);

-- 음성 발언권과 임시 자산. API만 쓰고 세션 삭제에 종속된다.
CREATE TABLE voice_states (
  session_id uuid PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
  data jsonb NOT NULL DEFAULT '{}'
);
CREATE TABLE speech_jobs (
  id uuid PRIMARY KEY,
  session_id uuid NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  kind text NOT NULL CHECK (kind IN ('tts', 'stt')),
  data jsonb NOT NULL,
  audio bytea,
  created_at timestamptz NOT NULL,
  expires_at timestamptz NOT NULL
);
CREATE INDEX speech_jobs_session ON speech_jobs(session_id);
CREATE INDEX speech_jobs_expiry ON speech_jobs(expires_at);
CREATE TABLE speech_quotas (
  scope text PRIMARY KEY,
  data jsonb NOT NULL DEFAULT '{}'
);
