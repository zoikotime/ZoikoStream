-- ZoikoStream public Guide: least-privilege database principals.
-- ZST-DB-PUB-GUIDE-001 §19 + §22 migration group 10 ("service grants after RBAC approval"),
-- ZST-ARCH-PUB-GUIDE-001 §22, ZST-RBAC-PUB-GUIDE-001 §9-10 and §20.
--
-- Idempotent: safe to run any number of times. Run it against the GUIDE database (never the
-- platform database) as a role with CREATEROLE that owns the guide tables (or a superuser),
-- after migrations. server/app/guide/deploy.py re-applies it after every migration once the
-- roles exist, so tables added later are granted immediately.
--
-- These are NOLOGIN group roles. Create one LOGIN user per deployable and environment and
-- put it in exactly one group (RBAC §9.1: no shared all-powerful guide credential), e.g.:
--   CREATE ROLE guide_api_prod      LOGIN PASSWORD '<from secrets manager>' IN ROLE guide_runtime;
--   CREATE ROLE guide_worker_prod   LOGIN PASSWORD '<...>' IN ROLE guide_maintenance;
--   CREATE ROLE guide_ingest_prod   LOGIN PASSWORD '<...>' IN ROLE guide_ingest;
--   CREATE ROLE guide_migrate_prod  LOGIN PASSWORD '<...>' IN ROLE guide_migrator;
--   CREATE ROLE guide_audit_reader  LOGIN PASSWORD '<...>' IN ROLE guide_readonly;
-- and point the settings at them:
--   GUIDE_DATABASE_URL              -> guide_runtime member     (the running API)
--   GUIDE_MAINTENANCE_DATABASE_URL  -> guide_maintenance member (purge / reconcile thread)
--   GUIDE_INGEST_DATABASE_URL       -> guide_ingest member      (knowledge index build)
--   GUIDE_MIGRATION_DATABASE_URL    -> guide_migrator member    (deploy-time DDL only)
--
-- Principal            | May                                               | Must not
-- guide_migrator       | DDL on the guide schemas during deployment        | serve runtime traffic
-- guide_runtime        | DML on session/conversation/lead/... tables;      | DDL; write the RAG index
--                      | SELECT on knowledge metadata and guide_rag        |   or knowledge registry
-- guide_maintenance    | SELECT/UPDATE/DELETE for purge + reconcile;       | INSERT content rows
--                      | INSERT audit facts                                |
-- guide_ingest         | write guide_rag + knowledge source registry;      | touch session data
--                      | INSERT audit facts; SELECT registry/config        |
-- guide_readonly       | SELECT non-content metadata (audit, config,       | read ciphertext tables
--                      | registry, releases)                               |

DO $$
DECLARE r text;
BEGIN
  FOREACH r IN ARRAY ARRAY['guide_migrator', 'guide_runtime', 'guide_maintenance',
                           'guide_ingest', 'guide_readonly'] LOOP
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
      EXECUTE format('CREATE ROLE %I NOLOGIN', r);
    END IF;
  END LOOP;
END $$;

CREATE SCHEMA IF NOT EXISTS guide_rag;

-- Nobody gets anything by default.
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
REVOKE ALL ON SCHEMA guide_rag FROM PUBLIC;

GRANT USAGE, CREATE ON SCHEMA public, guide_rag TO guide_migrator;
GRANT USAGE ON SCHEMA public, guide_rag
  TO guide_runtime, guide_maintenance, guide_ingest, guide_readonly;

DO $$
DECLARE
  t record;
  -- Content-governance tables: written by ingestion, read by the runtime.
  knowledge text[] := ARRAY['guide_knowledge_sources', 'guide_knowledge_source_versions'];
  -- Non-content metadata a read-only (audit/SRE) principal may see (RBAC §19.1, §20.1).
  readable text[] := ARRAY['guide_audit_events', 'guide_runtime_config',
                           'guide_destination_registry', 'guide_knowledge_sources',
                           'guide_knowledge_source_versions', 'guide_alembic_version'];
BEGIN
  FOR t IN SELECT schemaname, tablename FROM pg_tables
           WHERE schemaname = 'public' AND tablename LIKE 'guide\_%' LOOP
    EXECUTE format('REVOKE ALL ON %I.%I FROM PUBLIC', t.schemaname, t.tablename);
    EXECUTE format('GRANT ALL ON %I.%I TO guide_migrator', t.schemaname, t.tablename);

    IF t.tablename = 'guide_alembic_version' THEN
      -- Readiness checks compare the applied revision; only the migrator changes it.
      EXECUTE format('GRANT SELECT ON %I.%I TO guide_runtime, guide_maintenance',
                     t.schemaname, t.tablename);
    ELSIF t.tablename = ANY(knowledge) THEN
      EXECUTE format('GRANT SELECT ON %I.%I TO guide_runtime, guide_maintenance',
                     t.schemaname, t.tablename);
      EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON %I.%I TO guide_ingest',
                     t.schemaname, t.tablename);
    ELSE
      EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON %I.%I TO guide_runtime',
                     t.schemaname, t.tablename);
      EXECUTE format('GRANT SELECT, UPDATE, DELETE ON %I.%I TO guide_maintenance',
                     t.schemaname, t.tablename);
    END IF;

    IF t.tablename = 'guide_audit_events' THEN
      -- Purge runs and ingestion/governance (content_published, index_activated,
      -- index_rolled_back, break_glass, index_rebuild_refused) record non-content facts.
      EXECUTE format('GRANT INSERT ON %I.%I TO guide_maintenance, guide_ingest',
                     t.schemaname, t.tablename);
    END IF;
    IF t.tablename IN ('guide_destination_registry', 'guide_runtime_config',
                       'guide_alembic_version') THEN
      -- A release manifest records the destination-registry / prompt / policy versions in
      -- force at build time (knowledge/ingest.runtime_versions).
      EXECUTE format('GRANT SELECT ON %I.%I TO guide_ingest', t.schemaname, t.tablename);
    END IF;
    IF t.tablename = 'guide_message_sources' THEN
      -- An index rebuild may clear provenance joins to retired versions.
      EXECUTE format('GRANT SELECT, DELETE ON %I.%I TO guide_ingest', t.schemaname,
                     t.tablename);
    END IF;
    IF t.tablename = ANY(readable) THEN
      EXECUTE format('GRANT SELECT ON %I.%I TO guide_readonly', t.schemaname, t.tablename);
    END IF;
  END LOOP;

  FOR t IN SELECT schemaname, tablename FROM pg_tables WHERE schemaname = 'guide_rag' LOOP
    EXECUTE format('REVOKE ALL ON %I.%I FROM PUBLIC', t.schemaname, t.tablename);
    EXECUTE format('GRANT ALL ON %I.%I TO guide_migrator', t.schemaname, t.tablename);
    EXECUTE format('GRANT SELECT ON %I.%I TO guide_runtime', t.schemaname, t.tablename);
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON %I.%I TO guide_ingest',
                   t.schemaname, t.tablename);
    IF t.tablename = 'rag_index_releases' THEN
      EXECUTE format('GRANT SELECT ON %I.%I TO guide_readonly', t.schemaname, t.tablename);
    END IF;
  END LOOP;

  -- Identity columns (audit events, support step events, ...) need their sequences.
  FOR t IN SELECT schemaname, sequencename AS tablename FROM pg_sequences
           WHERE (schemaname = 'public' AND sequencename LIKE 'guide\_%')
              OR schemaname = 'guide_rag' LOOP
    EXECUTE format('GRANT ALL ON SEQUENCE %I.%I TO guide_migrator', t.schemaname, t.tablename);
    IF t.schemaname = 'guide_rag' THEN
      EXECUTE format('GRANT USAGE, SELECT ON SEQUENCE %I.%I TO guide_ingest',
                     t.schemaname, t.tablename);
    ELSIF t.tablename LIKE 'guide\_audit\_events%' THEN
      EXECUTE format('GRANT USAGE, SELECT ON SEQUENCE %I.%I TO guide_runtime, '
                     'guide_maintenance, guide_ingest', t.schemaname, t.tablename);
    ELSE
      EXECUTE format('GRANT USAGE, SELECT ON SEQUENCE %I.%I TO guide_runtime, guide_maintenance',
                     t.schemaname, t.tablename);
    END IF;
  END LOOP;
END $$;
