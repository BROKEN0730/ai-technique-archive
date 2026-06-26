-- AI技法知識庫 v2 — 初始 schema + RPC + RLS
-- 一次貼進 Supabase SQL Editor 執行。

CREATE EXTENSION IF NOT EXISTS vector;

-- 表一：sources
CREATE TABLE sources (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  url TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  type TEXT NOT NULL DEFAULT 'rss' CHECK (type IN ('rss')),
  language TEXT NOT NULL DEFAULT 'en' CHECK (language IN ('en','zh','ja','multi')),
  trust_score INTEGER NOT NULL DEFAULT 50 CHECK (trust_score BETWEEN 0 AND 100),
  last_fetched TIMESTAMPTZ,
  is_active BOOLEAN NOT NULL DEFAULT true,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 表二：staging
CREATE TABLE staging (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  url TEXT NOT NULL UNIQUE,
  title TEXT NOT NULL,
  content_summary TEXT,
  raw_content TEXT,
  source_id UUID REFERENCES sources(id),
  tags_machine TEXT[] NOT NULL DEFAULT '{}',
  first_seen TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  last_seen TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  verification_count INTEGER NOT NULL DEFAULT 1,
  status TEXT NOT NULL DEFAULT 'waiting' CHECK (status IN ('waiting','promoted','archived')),
  promoted_at TIMESTAMPTZ,
  language TEXT NOT NULL DEFAULT 'en' CHECK (language IN ('en','zh','ja','multi')),
  embedding vector(384),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 表三：knowledge
CREATE TABLE knowledge (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  title_zh TEXT NOT NULL,
  title_en TEXT,
  summary_zh TEXT NOT NULL,
  mechanism_zh TEXT NOT NULL,
  success_criteria TEXT NOT NULL,
  copyable_prompt_template TEXT NOT NULL,
  diagnostic_checklist JSONB NOT NULL DEFAULT '[]',
  onboarding_prompt_template TEXT NOT NULL,
  access_level TEXT NOT NULL DEFAULT 'green' CHECK (access_level IN ('green','yellow','orange','red')),
  applicable_scenarios TEXT[] NOT NULL DEFAULT '{}',
  failure_conditions TEXT[] NOT NULL DEFAULT '{}',
  tags_machine TEXT[] NOT NULL DEFAULT '{}',
  tags_human TEXT[] NOT NULL DEFAULT '{}',
  versions JSONB NOT NULL DEFAULT '[]',
  relations JSONB NOT NULL DEFAULT '[]',
  source_convergence_count INTEGER NOT NULL DEFAULT 1,
  confidence_level TEXT NOT NULL DEFAULT 'single_source'
    CHECK (confidence_level IN ('single_source','multi_source','official')),
  verified_count INTEGER NOT NULL DEFAULT 0,
  partial_count INTEGER NOT NULL DEFAULT 0,
  failed_count INTEGER NOT NULL DEFAULT 0,
  first_recorded TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  promoted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  last_updated TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  staging_id UUID REFERENCES staging(id),
  embedding vector(384)
);

-- 表四：source_trust_log
CREATE TABLE source_trust_log (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  source_id UUID REFERENCES sources(id),
  event_type TEXT NOT NULL CHECK (event_type IN ('verified','falsified','spam','duplicate')),
  score_delta INTEGER NOT NULL,
  reason TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 表五：feedback
CREATE TABLE feedback (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  knowledge_id UUID REFERENCES knowledge(id),
  result_type TEXT NOT NULL CHECK (result_type IN ('success','partial','failed','modified_success')),
  ai_model_used TEXT,
  scenario_type TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
ALTER TABLE feedback ENABLE ROW LEVEL SECURITY;
CREATE POLICY "允許匿名INSERT feedback" ON feedback FOR INSERT TO anon
  WITH CHECK (knowledge_id IN (SELECT id FROM knowledge));

-- 表六：budget_tracking
CREATE TABLE budget_tracking (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  year_month TEXT NOT NULL UNIQUE,
  tokens_input INTEGER NOT NULL DEFAULT 0,
  tokens_output INTEGER NOT NULL DEFAULT 0,
  estimated_cost_usd NUMERIC(10,4) NOT NULL DEFAULT 0,
  api_calls INTEGER NOT NULL DEFAULT 0,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
INSERT INTO budget_tracking (year_month) VALUES (TO_CHAR(NOW(),'YYYY-MM')) ON CONFLICT (year_month) DO NOTHING;

-- 索引（hnsw，不用 ivfflat）
CREATE INDEX ON staging USING hnsw (embedding vector_cosine_ops);
CREATE INDEX ON knowledge USING hnsw (embedding vector_cosine_ops);

-- 語意比對 RPC
CREATE OR REPLACE FUNCTION match_staging(query_embedding vector(384), match_threshold float, match_count int)
RETURNS TABLE (id UUID, similarity float) LANGUAGE sql STABLE AS $$
  SELECT id, 1 - (embedding <=> query_embedding) AS similarity FROM staging
  WHERE embedding IS NOT NULL AND status='waiting' AND 1 - (embedding <=> query_embedding) > match_threshold
  ORDER BY similarity DESC LIMIT match_count;
$$;

CREATE OR REPLACE FUNCTION match_knowledge(query_embedding vector(384), match_threshold float, match_count int)
RETURNS TABLE (id UUID, similarity float) LANGUAGE sql STABLE AS $$
  SELECT id, 1 - (embedding <=> query_embedding) AS similarity FROM knowledge
  WHERE embedding IS NOT NULL AND 1 - (embedding <=> query_embedding) > match_threshold
  ORDER BY similarity DESC LIMIT match_count;
$$;

-- verification_count 遞增（只在不同來源時 +1，永遠更新 last_seen）
CREATE OR REPLACE FUNCTION bump_verification(p_id uuid, p_increment boolean)
RETURNS void LANGUAGE sql AS $$
  UPDATE staging SET
    verification_count = verification_count + (CASE WHEN p_increment THEN 1 ELSE 0 END),
    last_seen = NOW()
  WHERE id = p_id;
$$;

-- 已知概念再現：source_convergence_count +1 並重算 confidence_level
CREATE OR REPLACE FUNCTION bump_convergence(p_id uuid, p_official boolean)
RETURNS void LANGUAGE sql AS $$
  UPDATE knowledge SET
    source_convergence_count = source_convergence_count + 1,
    confidence_level = CASE
      WHEN confidence_level='official' OR p_official THEN 'official'
      WHEN source_convergence_count + 1 >= 3 THEN 'multi_source'
      ELSE 'single_source' END,
    last_updated = NOW()
  WHERE id = p_id;
$$;

-- 取得可升級的 staging（含來源 trust），條件 A/B
CREATE OR REPLACE FUNCTION get_promotable()
RETURNS TABLE (id uuid, url text, title text, raw_content text, content_summary text,
               source_id uuid, embedding vector(384), trust_score int)
LANGUAGE sql STABLE AS $$
  SELECT s.id, s.url, s.title, s.raw_content, s.content_summary, s.source_id, s.embedding,
         COALESCE(so.trust_score, 50)
  FROM staging s LEFT JOIN sources so ON so.id = s.source_id
  WHERE s.status='waiting' AND (
    (COALESCE(so.trust_score,50) < 80  AND s.verification_count >= 2) OR
    (COALESCE(so.trust_score,50) >= 80 AND s.verification_count >= 1));
$$;

-- 升級：INSERT knowledge + UPDATE staging（單一函數 = 單一 transaction，失敗自動 rollback）
CREATE OR REPLACE FUNCTION promote(p_staging_id uuid, p_knowledge jsonb, p_embedding text)
RETURNS uuid LANGUAGE plpgsql AS $$
DECLARE new_id uuid;
BEGIN
  INSERT INTO knowledge (
    title_zh,title_en,summary_zh,mechanism_zh,success_criteria,
    copyable_prompt_template,diagnostic_checklist,onboarding_prompt_template,
    access_level,applicable_scenarios,failure_conditions,tags_machine,tags_human,
    versions,relations,source_convergence_count,confidence_level,staging_id,embedding
  ) VALUES (
    p_knowledge->>'title_zh',
    p_knowledge->>'title_en',
    p_knowledge->>'summary_zh',
    p_knowledge->>'mechanism_zh',
    p_knowledge->>'success_criteria',
    p_knowledge->>'copyable_prompt_template',
    COALESCE(p_knowledge->'diagnostic_checklist','[]'::jsonb),
    p_knowledge->>'onboarding_prompt_template',
    COALESCE(p_knowledge->>'access_level','green'),
    ARRAY(SELECT jsonb_array_elements_text(COALESCE(p_knowledge->'applicable_scenarios','[]'::jsonb))),
    ARRAY(SELECT jsonb_array_elements_text(COALESCE(p_knowledge->'failure_conditions','[]'::jsonb))),
    ARRAY(SELECT jsonb_array_elements_text(COALESCE(p_knowledge->'tags_machine','[]'::jsonb))),
    ARRAY(SELECT jsonb_array_elements_text(COALESCE(p_knowledge->'tags_human','[]'::jsonb))),
    COALESCE(p_knowledge->'versions','[]'::jsonb),
    COALESCE(p_knowledge->'relations','[]'::jsonb),
    COALESCE((p_knowledge->>'source_convergence_count')::int, 1),
    COALESCE(p_knowledge->>'confidence_level','single_source'),
    p_staging_id,
    NULLIF(p_embedding,'')::vector
  ) RETURNING id INTO new_id;
  UPDATE staging SET status='promoted', promoted_at=NOW() WHERE id=p_staging_id;
  RETURN new_id;
END;$$;

-- trust_score 更新 + log（單一函數）
CREATE OR REPLACE FUNCTION update_trust(p_source_id uuid, p_event text, p_reason text)
RETURNS void LANGUAGE plpgsql AS $$
DECLARE d int;
BEGIN
  IF p_source_id IS NULL THEN RETURN; END IF;
  d := CASE p_event WHEN 'verified' THEN 5 WHEN 'falsified' THEN -10
                    WHEN 'spam' THEN -15 WHEN 'duplicate' THEN -3 ELSE 0 END;
  INSERT INTO source_trust_log (source_id,event_type,score_delta,reason)
    VALUES (p_source_id,p_event,d,p_reason);
  UPDATE sources SET trust_score = LEAST(100, GREATEST(0, trust_score + d)) WHERE id=p_source_id;
END;$$;

-- 每日 feedback 彙整（純 SQL）
CREATE OR REPLACE FUNCTION aggregate_feedback()
RETURNS void LANGUAGE sql AS $$
  UPDATE knowledge k SET
    verified_count=(SELECT COUNT(*) FROM feedback WHERE knowledge_id=k.id AND result_type IN ('success','modified_success')),
    partial_count =(SELECT COUNT(*) FROM feedback WHERE knowledge_id=k.id AND result_type='partial'),
    failed_count  =(SELECT COUNT(*) FROM feedback WHERE knowledge_id=k.id AND result_type='failed'),
    last_updated=NOW()
  WHERE id IN (SELECT DISTINCT knowledge_id FROM feedback);
$$;

-- 90 天 archive（清空 raw_content，保留 metadata）
CREATE OR REPLACE FUNCTION archive_old_staging()
RETURNS void LANGUAGE sql AS $$
  UPDATE staging SET status='archived', raw_content=NULL
  WHERE status='waiting' AND first_seen < NOW() - INTERVAL '90 days';
$$;

-- 初始來源
INSERT INTO sources (url, name, type, language, trust_score) VALUES
('https://simonwillison.net/atom/everything/', 'Simon Willison Blog', 'rss', 'en', 80),
('https://www.reddit.com/r/ClaudeAI/.rss', 'Reddit ClaudeAI', 'rss', 'en', 50),
('https://www.reddit.com/r/ChatGPT/.rss', 'Reddit ChatGPT', 'rss', 'en', 50),
('https://www.reddit.com/r/LocalLLaMA/.rss', 'Reddit LocalLLaMA', 'rss', 'en', 60),
('https://news.ycombinator.com/rss', 'Hacker News', 'rss', 'en', 70),
('https://buttondown.com/ainews/rss', 'AI News Newsletter', 'rss', 'en', 65),
('https://www.reddit.com/r/PromptEngineering/.rss', 'Reddit PromptEngineering', 'rss', 'en', 65),
('https://www.reddit.com/r/AIAssistants/.rss', 'Reddit AIAssistants', 'rss', 'en', 50),
('https://sspai.com/tag/AI/feed', '少数派AI', 'rss', 'zh', 65),
('https://zenn.dev/topics/chatgpt/feed', 'Zenn ChatGPT', 'rss', 'ja', 60)
ON CONFLICT (url) DO NOTHING;
