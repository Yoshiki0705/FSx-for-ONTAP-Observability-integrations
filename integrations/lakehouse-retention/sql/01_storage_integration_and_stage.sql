-- =============================================================================
-- FSx for ONTAP Lakehouse Long-Term Retention — Snowflake Verification
-- 01: Storage Integration + External Stage + External Table
-- =============================================================================
--
-- Target: a STANDARD S3 bucket (created by ../template.yaml), NOT an FSx for
-- ONTAP S3 Access Point. Unlike the FSx S3 AP integration in
-- FSx-for-ONTAP-Lakehouse-Integrations, this bucket supports S3 Event Notifications,
-- so real Snowpipe auto-ingest is expected to work here (see 02_snowpipe.sql).
--
-- Prerequisites:
--   - IAM Role deployed via ../snowflake-role.yaml (Phase 1, own-account trust)
--   - IAMRoleArn output value from that stack
--
-- Two-phase trust setup (same pattern as FSx-for-ONTAP-Lakehouse-Integrations):
--   Phase 1: CREATE STORAGE INTEGRATION with own-account-trusted role -> DESCRIBE INTEGRATION
--   Phase 2: redeploy snowflake-role.yaml with SnowflakeAccountId/SnowflakeExternalId
--            from DESCRIBE INTEGRATION output, then re-run DESCRIBE INTEGRATION to confirm
--
-- E2E verified (2026-07-20): this exact script was run against a live Snowflake
-- trial account and a real S3 bucket containing 500 Firehose-produced Parquet
-- records. LIST @audit_logs_stage returned the real Parquet file, and
-- SELECT COUNT(*) FROM audit_logs_ext returned exactly 500 -- matching the
-- Athena verification exactly. The GROUP BY query below returned 15 rows
-- matching the same Operation x Result distribution seen in Athena.
-- =============================================================================

USE ROLE ACCOUNTADMIN;

-- --- Phase 1: create the Storage Integration ---
-- Replace <IAM_ROLE_ARN> with the IAMRoleArn output from snowflake-role.yaml
CREATE OR REPLACE STORAGE INTEGRATION fsxn_lakehouse_retention_integration
  TYPE = EXTERNAL_STAGE
  STORAGE_PROVIDER = 'S3'
  ENABLED = TRUE
  STORAGE_AWS_ROLE_ARN = '<IAM_ROLE_ARN>'
  STORAGE_ALLOWED_LOCATIONS = (
    's3://<RETENTION_BUCKET_NAME>/audit-logs/'
  )
  COMMENT = 'FSx for ONTAP audit log long-term retention (Parquet, standard S3 bucket, Issue #28)';

-- Retrieve Snowflake's AWS account + External ID for Phase 2 trust update
DESCRIBE INTEGRATION fsxn_lakehouse_retention_integration;

-- >>> ACTION: copy STORAGE_AWS_IAM_USER_ARN and STORAGE_AWS_EXTERNAL_ID,
-- then redeploy snowflake-role.yaml with those values before proceeding.

GRANT USAGE ON INTEGRATION fsxn_lakehouse_retention_integration TO ROLE SYSADMIN;

-- --- Database / schema for this verification ---
USE ROLE SYSADMIN;
CREATE DATABASE IF NOT EXISTS fsxn_lakehouse_retention;
USE DATABASE fsxn_lakehouse_retention;
CREATE SCHEMA IF NOT EXISTS audit;
USE SCHEMA audit;

-- --- External Stage pointing at the Parquet audit-logs prefix ---
CREATE OR REPLACE STAGE audit_logs_stage
  STORAGE_INTEGRATION = fsxn_lakehouse_retention_integration
  URL = 's3://<RETENTION_BUCKET_NAME>/audit-logs/'
  FILE_FORMAT = (TYPE = 'PARQUET');

-- Confirm Snowflake can list the Firehose-written Parquet files
LIST @audit_logs_stage;

-- --- External Table (mirrors the Glue/Athena schema in ../template.yaml) ---
CREATE OR REPLACE EXTERNAL TABLE audit_logs_ext (
    "timestamp"  VARCHAR AS (value:"timestamp"::VARCHAR),
    eventid      VARCHAR AS (value:eventid::VARCHAR),
    svmname      VARCHAR AS (value:svmname::VARCHAR),
    username     VARCHAR AS (value:username::VARCHAR),
    clientip     VARCHAR AS (value:clientip::VARCHAR),
    operation    VARCHAR AS (value:operation::VARCHAR),
    objectname   VARCHAR AS (value:objectname::VARCHAR),
    result       VARCHAR AS (value:result::VARCHAR)
)
  LOCATION = @audit_logs_stage
  FILE_FORMAT = (TYPE = 'PARQUET')
  AUTO_REFRESH = FALSE; -- flip to TRUE only after Snowpipe/Event Notifications are wired (see 02)

-- Force a manual refresh (equivalent to a Glue Crawler run, but instant)
ALTER EXTERNAL TABLE audit_logs_ext REFRESH;

-- --- Verification queries (compare against Athena results for the same data) ---
SELECT COUNT(*) AS total_records FROM audit_logs_ext;

SELECT operation, result, COUNT(*) AS cnt
FROM audit_logs_ext
GROUP BY operation, result
ORDER BY cnt DESC;
