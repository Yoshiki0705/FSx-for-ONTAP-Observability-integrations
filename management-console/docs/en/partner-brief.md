# FSx for ONTAP Management Console — Partner Brief

## Problem Statement

Storage administrators managing Amazon FSx for NetApp ONTAP need unified monitoring and management capabilities without relying on external SaaS platforms or ONTAP CLI expertise.

## Solution Pattern

Self-hosted management console deployed within the account owner's VPC using AWS managed services:
- **Monitoring**: NetApp Harvest → Amazon Managed Prometheus → Amazon Managed Grafana (20+ dashboards)
- **Management**: Low-code UI (Appsmith/ToolJet) on ECS Fargate with ONTAP REST API integration
- **Authentication**: Amazon Cognito with MFA support

## How This Compares

This pattern is one option among several; the right choice depends on data residency, customization needs, and whether standard SaaS/CLI tooling already meets requirements.

| Consideration | This pattern | NetApp DII<!-- allow:naming -->/BlueXP<!-- allow:naming --> | Custom development (React/etc.) | CLI-only |
|----------------------|----------------------|----------------------|----------------------|-------------|
| External SaaS dependency | None | Required | None | None |
| Data residency | VPC-internal | External SaaS | Depends on hosting | VPC-internal |
| Authentication | AWS-native (Cognito) | NetApp's own | Custom-built | ONTAP local |
| Build effort | Low-code configuration | None (managed service) | Frontend development required | None |
| Dashboards | Pre-built via Harvest | Pre-built | Must build | Not applicable |
| Operations interface | GUI | GUI | GUI (custom) | Terminal only |
| Trade-off | ~$250/month AWS resource cost; you operate it | Ongoing SaaS subscription | Development + maintenance cost | No GUI; steeper learning curve for new operators |

> **How to choose**: Lead with the requirement, not the tool. If a data residency constraint rules out external SaaS, this pattern or CLI-only are the remaining options. If no such constraint exists and the team already has a NetApp SaaS relationship, DII<!-- allow:naming -->/BlueXP<!-- allow:naming --> may be the faster path since it needs no additional AWS infrastructure to operate.

> **AWS-native GUI default**: this self-hosted console is one of two AWS-native GUI options this project uses by default. For end-user **file management** of NAS data (browse, process, view) the companion option is the [Amplify Gen2 file portal](https://github.com/Yoshiki0705/FSx-for-ONTAP-S3AccessPoints-Serverless-Patterns/tree/main/solutions/amplify-portal) implemented in the sibling repo (React + Amplify Gen2, Cognito auth, S3-Access-Point-backed). AWS offers no integrated managed Box/Drive-like service for FSx for ONTAP NAS data, so you assemble one — the portal adds NAS-specific capabilities (Snapshot restore, SnapLock WORM, ransomware containment, AI-on-NAS). The trade-off is symmetric with the SaaS alternative: you deploy and operate this self-assembled app yourself. See its [admin capability map](https://github.com/Yoshiki0705/FSx-for-ONTAP-S3AccessPoints-Serverless-Patterns/blob/main/solutions/amplify-portal/docs/admin-capability-map.en.md) for the Console-equivalent management features it covers. NetApp Console / DII<!-- allow:naming -->/BlueXP<!-- allow:naming --> remains the alternative for teams already using NetApp SaaS.

## PoC Success Criteria

| Criteria | Measurement | Target |
|----------|-------------|--------|
| Deployment | All 5 stacks CREATE_COMPLETE | < 30 minutes |
| Metrics | Grafana dashboard shows ONTAP data | < 5 min after deploy |
| Operations | Volume create/resize/delete via UI | < 5 sec response |
| Authentication | Cognito login → access both layers | Single sign-on |

## Deployment Requirements

- Existing VPC with 2+ AZs (public + private subnets)
- FSx for ONTAP file system with management endpoint
- AWS CLI v2, no CDK/SAM required
- Estimated cost: ~$0.30/hour (NAT GW + ECS + RDS + VPC Endpoints)

## Workshop Flow (90 minutes)

1. **Architecture Review** (15 min) — 2-layer design, component roles
2. **Deploy Stack 1-2** (15 min) — Network + Auth (live demo)
3. **Deploy Stack 3-5** (30 min) — Observability + Console + Monitoring
4. **UI Walkthrough** (20 min) — Volume management, Grafana dashboards
5. **Q&A + Next Steps** (10 min) — Customization options, production hardening

## Next Steps After PoC

1. Custom domain + ACM certificate for ALB
2. Grafana dashboard customization for customer's specific volumes/SVMs
3. RBAC configuration (Cognito groups → Admin/Viewer roles)
4. Integration with an existing observability pipeline (if applicable)

## Resources

- GitHub: `management-console/` directory
- Setup Guide: `docs/en/setup-guide.md`
- Local Dev: `docs/en/local-dev-guide.md`
- Sibling Amplify Gen2 file portal: [solutions/amplify-portal](https://github.com/Yoshiki0705/FSx-for-ONTAP-S3AccessPoints-Serverless-Patterns/tree/main/solutions/amplify-portal) ([portal README](https://github.com/Yoshiki0705/FSx-for-ONTAP-S3AccessPoints-Serverless-Patterns/blob/main/solutions/amplify-portal/README.md) · [end-user guide](https://github.com/Yoshiki0705/FSx-for-ONTAP-S3AccessPoints-Serverless-Patterns/blob/main/docs/en/portal-user-guide.md))
