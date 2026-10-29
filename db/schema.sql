-- papersolve DB 스키마 (메인 PRD v3.1 §7.4의 프로토타입 부분집합).
-- 사업장마다 표를 만들지 않는다. 표는 모든 계정이 같고, 계정별 차이는 정의 표의 행과 records.data(JSONB)로 담는다.
-- 계정은 tenant_id 로 나눈다. 서버가 .env 의 SUPABASE_SECRET_KEY 로 접근하므로 RLS 는 켜 두고 규칙은 만들지 않는다.
-- 새 DB 에서는 SQL Editor 에서 이 파일을 통째로 한 번 실행한다.

-- 계정
create table public.tenants (
  id bigint generated always as identity primary key,
  name text not null unique,
  created_at timestamptz not null default now()
);

-- 정의 (사용자가 확정한 것만 들어간다. 확정 전 후보는 proposals 에 있다)
create table public.object_types (           -- 기록 종류(재고 점검, 고객 요청) 또는 대상 분류(품목, 설비, 고객)
  id bigint generated always as identity primary key,
  tenant_id bigint not null references public.tenants(id) on delete cascade,
  name text not null,
  kind text not null default '기록' check (kind in ('기록', '대상')),
  parent_name text,                          -- 대상 분류의 상위 분류 (예: 식품 → 품목)
  created_at timestamptz not null default now(),
  unique (tenant_id, name)
);

create table public.property_defs (          -- 기록 종류의 칸, 또는 대상 분류의 정보 칸
  id bigint generated always as identity primary key,
  tenant_id bigint not null references public.tenants(id) on delete cascade,
  type_name text not null,
  name text not null,
  dtype text not null check (dtype in ('number', 'text', 'date', 'choice')),
  unit text,
  description text,                          -- 칸 설명. 사용자가 지식그래프에서 적는다
  is_entity boolean not null default false,  -- 행이 가리키는 주 대상(품목·설비·고객 등)
  ref_class text,                            -- 이 칸의 값이 가리키는 대상 분류
  position integer not null default 0,
  created_at timestamptz not null default now(),
  unique (tenant_id, type_name, name)
);

create table public.alias_dict (             -- 종이에서 읽은 이름 → 칸. 비어 있으면 쓰지 않기로 한 이름, '@칸.정보'면 대상 정보로 옮겨 간 칸
  id bigint generated always as identity primary key,
  tenant_id bigint not null references public.tenants(id) on delete cascade,
  type_name text not null,
  alias text not null,
  prop_name text,
  created_at timestamptz not null default now(),
  unique (tenant_id, type_name, alias)
);

create table public.link_types (             -- 관계. ref 는 '대상 분류', '기록 종류.칸', '대상 분류.정보'
  id bigint generated always as identity primary key,
  tenant_id bigint not null references public.tenants(id) on delete cascade,
  from_ref text not null,
  to_ref text not null,
  kind text not null check (kind in ('연결', '함께 움직임', '최소 기준', '최대 기준', '의존', '영향', '대체')),
  reason text,
  name text,                                 -- 관계 이름 (예: 거래처)
  attr_defs jsonb not null default '[]',     -- 관계 정보 칸 (예: [{"name": "단가", "dtype": "number"}])
  created_at timestamptz not null default now(),
  unique (tenant_id, from_ref, to_ref, kind)
);

-- 대상
create table public.entities (               -- 대상 하나 (예: 생수 500ml). attrs 는 정보 칸 → 값
  id bigint generated always as identity primary key,
  tenant_id bigint not null references public.tenants(id) on delete cascade,
  class_name text not null,
  name text not null,
  attrs jsonb not null default '{}',
  created_at timestamptz not null default now(),
  unique (tenant_id, class_name, name)
);

create table public.entity_aliases (         -- 대상의 다른 표기 (예: P-03 = 3호 프레스). 이름 자신도 넣는다
  id bigint generated always as identity primary key,
  tenant_id bigint not null references public.tenants(id) on delete cascade,
  entity_id bigint not null references public.entities(id) on delete cascade,
  alias text not null,
  created_at timestamptz not null default now(),
  unique (tenant_id, entity_id, alias)
);

create table public.entity_links (           -- 관계로 이어진 대상 쌍과 관계 정보 값 (예: 생수 500ml–대한상회, 단가 500)
  id bigint generated always as identity primary key,
  tenant_id bigint not null references public.tenants(id) on delete cascade,
  link_id bigint not null references public.link_types(id) on delete cascade,
  from_entity bigint not null references public.entities(id) on delete cascade,
  to_entity bigint not null references public.entities(id) on delete cascade,
  attrs jsonb not null default '{}',
  created_at timestamptz not null default now(),
  unique (tenant_id, link_id, from_entity, to_entity)
);

-- 입력·기록
create table public.source_inputs (          -- 올린 사진이나 파일 1개, 또는 직접 적은 기록 1개
  id bigint generated always as identity primary key,
  tenant_id bigint not null references public.tenants(id) on delete cascade,
  channel text not null check (channel in ('사진', '파일', '직접 입력')),
  name text,
  storage_path text,
  created_at timestamptz not null default now()
);

create table public.records (                -- 표의 한 행 = 기록 하나. data 는 읽은 이름 → 값
  id bigint generated always as identity primary key,
  tenant_id bigint not null references public.tenants(id) on delete cascade,
  type_name text not null,
  occurred_at date not null,
  data jsonb not null default '{}',
  uncertain jsonb not null default '[]',
  photo_path text,
  source_input_id bigint references public.source_inputs(id) on delete cascade,
  status text not null default '대기' check (status in ('대기', '확정', '거절')),
  created_at timestamptz not null default now()
);
create index records_tenant_type on public.records (tenant_id, type_name, status);

-- 확인: 모든 예/아니오가 지나는 한 문
create table public.proposals (
  id bigint generated always as identity primary key,
  tenant_id bigint not null references public.tenants(id) on delete cascade,
  kind text not null check (kind in ('종류', '칸', '같은 칸', '애매한 값', '연결', '지표', '업무',
                                     '대상 분류', '대상', '같은 대상', '대상 속성', '속성 값', '상위 분류', '관계 속성', '칸 설정', '되돌리기')),
  payload jsonb not null,
  evidence jsonb,
  status text not null default '대기' check (status in ('대기', '승인', '고침', '거절')),
  source_input_id bigint references public.source_inputs(id) on delete cascade,
  decided_at timestamptz,
  created_at timestamptz not null default now()
);

-- 활용
create table public.widgets (                -- 대시보드에 올린 지표
  id bigint generated always as identity primary key,
  tenant_id bigint not null references public.tenants(id) on delete cascade,
  spec jsonb not null,
  status text not null default '올림' check (status in ('올림', '내림')),
  created_at timestamptz not null default now()
);

create table public.automations (            -- 켜 둔 업무 규칙. 홈의 "오늘 먼저 볼 것"을 채운다
  id bigint generated always as identity primary key,
  tenant_id bigint not null references public.tenants(id) on delete cascade,
  spec jsonb not null,
  status text not null default '켜짐' check (status in ('켜짐', '꺼짐')),
  created_at timestamptz not null default now()
);

create table public.eval_votes (             -- 평가 모드: 같은 질문의 확정 전·후 답 중 사용자가 맞다고 고른 쪽
  id bigint generated always as identity primary key,
  tenant_id bigint not null references public.tenants(id) on delete cascade,
  question text not null,
  pick text not null check (pick in ('확정 전', '확정 후')),
  answer_before text not null,
  answer_after text not null,
  created_at timestamptz not null default now()
);

create table public.ai_logs (                -- AI 호출 1번 = 1줄
  id bigint generated always as identity primary key,
  created_at timestamptz not null default now(),
  tenant_id bigint references public.tenants(id) on delete set null,
  model text not null,
  prompt text not null,
  answer text not null,
  input_tokens integer not null,
  output_tokens integer not null,
  cost_usd numeric not null
);

alter table public.tenants enable row level security;
alter table public.object_types enable row level security;
alter table public.property_defs enable row level security;
alter table public.alias_dict enable row level security;
alter table public.link_types enable row level security;
alter table public.entities enable row level security;
alter table public.entity_aliases enable row level security;
alter table public.entity_links enable row level security;
alter table public.source_inputs enable row level security;
alter table public.records enable row level security;
alter table public.proposals enable row level security;
alter table public.widgets enable row level security;
alter table public.automations enable row level security;
alter table public.eval_votes enable row level security;
alter table public.ai_logs enable row level security;
