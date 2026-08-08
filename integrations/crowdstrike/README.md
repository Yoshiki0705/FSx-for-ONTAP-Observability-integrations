# CrowdStrike Falcon LogScale Integration

🌐 [日本語](#概要) | [English](#overview)

---

## 概要

FSx for ONTAP 監査ログを CrowdStrike Falcon LogScale (Next-Gen SIEM) にサーバーレスで配信するパターンです。

Falcon LogScale は Splunk HEC 互換エンドポイントをサポートしているため、本統合は HEC フォーマットで配信します。

### 配信方式

| 方式 | エンドポイント | 認証 | 備考 |
|------|-------------|------|------|
| **HEC (推奨)** | `/api/v1/ingest/hec` | Bearer Token (Ingest Token) | Splunk HEC 互換 |
| Structured Data | `/api/v1/ingest/humio-structured` | Bearer Token | JSON 構造化データ |
| Raw | `/api/v1/ingest/raw` | Bearer Token | 非構造化テキスト |

### アーキテクチャ

```
FSx for ONTAP → S3 Access Point → EventBridge Scheduler → Lambda → LogScale HEC
```

### 前提条件

- CrowdStrike Falcon LogScale アカウント (Cloud or Self-hosted)
- LogScale Ingest Token (リポジトリに紐付け)
- AWS アカウント + FSx for ONTAP (監査ログ有効化済み)

### スクリプト

| スクリプト | 用途 |
|-----------|------|
| `scripts/deploy.sh` | スタックをデプロイしハンドラコードをアップロード |
| `scripts/create-alerts.sh` | LogScale アラート 3 件を作成（機密パスへのアクセス / 大量削除 / 認証失敗の急増） |
| `scripts/setup-full-observability.sh` | デプロイ → アラート作成 → 検証をワンコマンドで実行 |
| `scripts/verify.sh` | デプロイ後の E2E 検証 |
| `scripts/cleanup.sh` | スタックと任意リソースの削除 |

LogScale のトークンは 2 種類あり、互換性はありません。

| トークン | 使用箇所 | 権限 |
|---------|---------|------|
| Ingest Token | Lambda（`INGEST_TOKEN_SECRET_ARN`） | イベントの書き込みのみ |
| 管理 API トークン | `create-alerts.sh`（`LOGSCALE_API_TOKEN`） | GraphQL でアラートとアクションを管理 |

`create-alerts.sh` に Ingest Token を渡すと認可エラーになります。[LogScale API tokens](https://library.humio.com/falcon-logscale-cloud/security-apitokens.html) を参照してください。

> **検知範囲に関する補足**
>
> この統合は `template.yaml` のみを提供し、EMS Webhook と FPolicy ハンドラはありません。ランサムウェア（ARP）検知は EMS イベントに依存するため、ここで作成するアラートはすべてファイルアクセス監査ログから導出したものです。どの検知がどのイベントソースを必要とするかは [detection-use-cases.md](../../docs/ja/detection-use-cases.md) を参照してください。

---

## Overview

Serverless delivery of FSx for ONTAP audit logs to CrowdStrike Falcon LogScale (Next-Gen SIEM).

Falcon LogScale supports Splunk HEC-compatible endpoints, so this integration ships logs in HEC format.

### Delivery Methods

| Method | Endpoint | Auth | Notes |
|--------|----------|------|-------|
| **HEC (recommended)** | `/api/v1/ingest/hec` | Bearer Token (Ingest Token) | Splunk HEC compatible |
| Structured Data | `/api/v1/ingest/humio-structured` | Bearer Token | JSON structured data |
| Raw | `/api/v1/ingest/raw` | Bearer Token | Unstructured text |

### Architecture

```
FSx for ONTAP → S3 Access Point → EventBridge Scheduler → Lambda → LogScale HEC
```

### Prerequisites

- CrowdStrike Falcon LogScale account (Cloud or Self-hosted)
- LogScale Ingest Token (associated with a repository)
- AWS account + FSx for ONTAP (audit logging enabled)

> **ONTAP constraint**: Each SVM supports only one audit log format at a time (`-format evtx` or `-format xml`). You cannot generate both formats simultaneously for the same SVM. For this integration, **XML format is recommended** — it enables full field extraction without additional dependencies. See [architecture.md](../../docs/en/architecture.md#audit-log-format-evtx-vs-xml) for details.

### Quick Start

```bash
# 1. Set environment variables
export CROWDSTRIKE_LOGSCALE_URL=https://cloud.us.humio.com  # or your self-hosted URL
export CROWDSTRIKE_INGEST_TOKEN=<your-ingest-token>

# 2. Test with sample XML audit log
python3 shared/scripts/test-xml-e2e.py --vendor crowdstrike

# 3. Deploy CloudFormation stack
aws cloudformation deploy \
  --template-file integrations/crowdstrike/template.yaml \
  --stack-name fsxn-crowdstrike-integration \
  --parameter-overrides \
    FsxS3AccessPointArn=arn:aws:s3:ap-northeast-1:123456789012:accesspoint/fsxn-audit-ap \
    LogScaleIngestTokenSecretArn=<secret-arn> \
    LogScaleUrl=https://cloud.us.humio.com \
  --capabilities CAPABILITY_NAMED_IAM

# 4. Update Lambda code (template uses placeholder — real code must be uploaded)
cd integrations/crowdstrike/lambda
zip function.zip handler.py
aws lambda update-function-code \
  --function-name fsxn-crowdstrike-integration-shipper \
  --zip-file fileb://function.zip \
  --region ap-northeast-1
```

> **Note**: The CloudFormation template deploys a placeholder Lambda that raises `NotImplementedError`. After stack creation, upload the actual handler code using step 4 above or via `scripts/deploy.sh`.

### Scripts

| Script | Purpose |
|--------|---------|
| `scripts/deploy.sh` | Deploy the stack and upload the handler code |
| `scripts/create-alerts.sh` | Create 3 LogScale alerts (sensitive path access, mass deletion, failed access spike) |
| `scripts/setup-full-observability.sh` | Deploy → create alerts → verify, in one command |
| `scripts/verify.sh` | Post-deployment E2E verification |
| `scripts/cleanup.sh` | Remove the stack and optional resources |

Two different LogScale tokens are involved, and they are not interchangeable:

| Token | Used by | Capability |
|-------|---------|-----------|
| Ingest token | Lambda (`INGEST_TOKEN_SECRET_ARN`) | Write events only |
| Management API token | `create-alerts.sh` (`LOGSCALE_API_TOKEN`) | Manage alerts and actions via GraphQL |

Passing the ingest token to `create-alerts.sh` returns an authorization error. See [LogScale API tokens](https://library.humio.com/falcon-logscale-cloud/security-apitokens.html).

> **Detection scope note**: this integration ships `template.yaml` only — no EMS webhook and no FPolicy handler. Ransomware (ARP) detection depends on EMS events, so the alerts created here are all derived from file access audit logs. See [detection-use-cases.md](../../docs/en/detection-use-cases.md) for which detections need which event source.

### Roadmap

- [x] Audit log handler (HEC delivery)
- [x] Unit tests (14 tests passing)
- [x] HEC protocol verification (via Splunk Enterprise Docker — same HEC format)
- [ ] EMS Webhook handler (`template-ems.yaml`)
- [ ] FPolicy handler (`template-fpolicy.yaml`)
- [ ] E2E verification with live LogScale instance (requires paid Next-Gen SIEM license)

### E2E Verification Status

| Item | Result |
|------|--------|
| Lambda handler code | ✅ Complete (HEC format) |
| Unit tests | ✅ 14 tests passing |
| HEC protocol compatibility | ✅ Verified via Splunk Enterprise (identical HEC format) |
| Live LogScale ingest | ⚠️ Requires paid Next-Gen SIEM license |

**Trial limitation**: The CrowdStrike Falcon EDR trial includes read-only access to the Next-Gen SIEM UI (log search, dashboards, repository list) but **does NOT include the Data Connectors / HEC ingest functionality**. The "Add data connector" page returns "Page not found" on the trial. A paid Falcon Next-Gen SIEM license is required for external data ingestion via HEC.

**Protocol verification**: Since Falcon LogScale uses a Splunk HEC-compatible endpoint (`/api/v1/ingest/hec`), the successful Splunk Enterprise E2E test (HTTP 200, 5 events indexed and searchable) validates the HEC payload format used by this integration.

Screenshot: [`screenshots/crowdstrike-hec-verification-splunk.png`](screenshots/crowdstrike-hec-verification-splunk.png) — CrowdStrike HEC payload (sourcetype `fsxn:audit:xml-crowdstrike`, field `integration=crowdstrike-logscale`) accepted and searchable in Splunk Enterprise (HEC-compatible receiver).

### Architecture Note: Shared Parser

The handler includes an inline XML/JSON parser for self-contained deployment. For production, consider using the shared Lambda Layer (`shared/lambda-layers/log-parser/`) to centralize parser updates across all vendors.

### CrowdStrike LogScale URLs

| Region | URL |
|--------|-----|
| US (cloud) | `https://cloud.us.humio.com` |
| EU (cloud) | `https://cloud.humio.com` |
| US-2 (cloud) | `https://cloud.community.humio.com` |
| Self-hosted | Your custom URL |

### References

- [LogScale Ingest API Docs](https://library.humio.com/logscale-api/api-ingest.html)
- [LogScale HEC Endpoint](https://library.humio.com/logscale-api/log-shippers-hec.html)
- [LogScale Structured Data API](https://library.humio.com/logscale-api/api-ingest-structured-data.html)
- [CrowdStrike Developer Center](https://developer.crowdstrike.com/ngsiem/data-ingestion/)
