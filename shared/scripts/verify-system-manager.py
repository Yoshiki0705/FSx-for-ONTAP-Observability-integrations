#!/usr/bin/env python3
"""ONTAP REST API verification script for System Manager GUI guide.

This script verifies the current state of audit logging, quotas, and EMS
configuration on FSx for ONTAP via the ONTAP REST API. It is designed to
run from a host with network access to the ONTAP management endpoint.

Usage:
    # Set environment variables
    export ONTAP_MGMT_IP=<management-endpoint-ip>
    export ONTAP_USER=fsxadmin
    export ONTAP_PASS=<password>  # Or use AWS Secrets Manager

    # Run verification
    python3 verify-system-manager.py

    # Run specific checks only
    python3 verify-system-manager.py --check audit
    python3 verify-system-manager.py --check quota
    python3 verify-system-manager.py --check ems
"""

import argparse
import json
import os
import sys
import urllib3

# Disable SSL warnings for self-signed certificates
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def get_ontap_client() -> tuple[str, urllib3.PoolManager, dict]:
    """Create ONTAP REST API client from environment variables."""
    mgmt_ip = os.environ.get("ONTAP_MGMT_IP")
    user = os.environ.get("ONTAP_USER", "fsxadmin")
    password = os.environ.get("ONTAP_PASS")

    if not mgmt_ip or not password:
        print("ERROR: Set ONTAP_MGMT_IP and ONTAP_PASS environment variables")
        sys.exit(1)

    base_url = f"https://{mgmt_ip}/api"
    http = urllib3.PoolManager(cert_reqs="CERT_NONE")
    headers = urllib3.make_headers(basic_auth=f"{user}:{password}")
    headers["Accept"] = "application/json"

    return base_url, http, headers


def api_get(base_url: str, http: urllib3.PoolManager, headers: dict, path: str) -> dict:
    """Make GET request to ONTAP REST API."""
    url = f"{base_url}{path}"
    resp = http.request("GET", url, headers=headers)
    if resp.status != 200:
        print(f"  ERROR: {url} returned HTTP {resp.status}")
        return {}
    return json.loads(resp.data.decode("utf-8"))


def check_svms(base_url: str, http: urllib3.PoolManager, headers: dict) -> list[dict]:
    """List all SVMs."""
    print("\n" + "=" * 60)
    print("SVM (Storage Virtual Machine) 一覧")
    print("=" * 60)

    data = api_get(base_url, http, headers, "/svm/svms")
    svms = data.get("records", [])

    for svm in svms:
        print(f"  - {svm['name']} (UUID: {svm['uuid']})")

    return svms


def check_audit(base_url: str, http: urllib3.PoolManager, headers: dict, svms: list[dict]) -> None:
    """Check audit logging configuration for each SVM."""
    print("\n" + "=" * 60)
    print("監査ログ設定状態")
    print("=" * 60)

    for svm in svms:
        svm_name = svm["name"]
        svm_uuid = svm["uuid"]
        print(f"\n  SVM: {svm_name}")

        # Check audit configuration
        data = api_get(base_url, http, headers, f"/protocols/audit/{svm_uuid}")
        if not data:
            print("    監査ログ: ❌ 未設定")
            continue

        enabled = data.get("enabled", False)
        log_path = data.get("log_path", "N/A")
        log_format = data.get("log", {}).get("format", "N/A")
        rotation = data.get("log", {}).get("rotation", {})

        status = "✅ 有効" if enabled else "❌ 無効"
        print(f"    監査ログ: {status}")
        print(f"    保存先: {log_path}")
        print(f"    フォーマット: {log_format}")
        print(f"    ローテーション: {json.dumps(rotation, indent=6)}")


def check_quotas(base_url: str, http: urllib3.PoolManager, headers: dict) -> None:
    """Check quota rules and status."""
    print("\n" + "=" * 60)
    print("クォータ設定状態")
    print("=" * 60)

    # List quota rules
    data = api_get(base_url, http, headers, "/storage/quota/rules")
    rules = data.get("records", [])

    if not rules:
        print("  クォータルール: なし")
        return

    print(f"  クォータルール数: {len(rules)}")
    for rule in rules:
        print("\n  ルール:")
        print(f"    タイプ: {rule.get('type', 'N/A')}")
        print(f"    ボリューム: {rule.get('volume', {}).get('name', 'N/A')}")
        print(f"    Qtree: {rule.get('qtree', {}).get('name', 'N/A')}")
        space = rule.get("space", {})
        if space:
            hard = space.get("hard_limit", "未設定")
            soft = space.get("soft_limit", "未設定")
            print(f"    ハードリミット: {hard}")
            print(f"    ソフトリミット: {soft}")

    # Check quota reports (current usage)
    print("\n  --- クォータ使用状況 ---")
    data = api_get(base_url, http, headers, "/storage/quota/reports")
    reports = data.get("records", [])

    if not reports:
        print("  使用状況レポート: なし（クォータ未初期化の可能性）")
        return

    for report in reports[:10]:  # Limit to first 10
        vol_name = report.get("volume", {}).get("name", "N/A")
        qtree_name = report.get("qtree", {}).get("name", "N/A")
        space = report.get("space", {})
        used = space.get("used", {}).get("total", 0)
        hard = space.get("hard_limit", 0)
        print(f"    {vol_name}/{qtree_name}: 使用={used} / 上限={hard}")


def check_qtrees(base_url: str, http: urllib3.PoolManager, headers: dict) -> None:
    """Check Qtree configuration."""
    print("\n" + "=" * 60)
    print("Qtree 一覧")
    print("=" * 60)

    data = api_get(base_url, http, headers, "/storage/qtrees")
    qtrees = data.get("records", [])

    if not qtrees:
        print("  Qtree: なし")
        return

    print(f"  Qtree 数: {len(qtrees)}")
    for qtree in qtrees:
        name = qtree.get("name", "(default)")
        vol = qtree.get("volume", {}).get("name", "N/A")
        security = qtree.get("security_style", "N/A")
        print(f"    - {vol}/{name} (security: {security})")


def check_ems(base_url: str, http: urllib3.PoolManager, headers: dict) -> None:
    """Check EMS notification configuration."""
    print("\n" + "=" * 60)
    print("EMS 通知設定")
    print("=" * 60)

    # Check EMS destinations
    print("\n  --- 通知先 (Destinations) ---")
    data = api_get(base_url, http, headers, "/support/ems/destinations")
    destinations = data.get("records", [])

    if not destinations:
        print("  通知先: なし")
    else:
        for dest in destinations:
            name = dest.get("name", "N/A")
            dest_type = dest.get("type", "N/A")
            print(f"    - {name} (type: {dest_type})")

    # Check EMS filters
    print("\n  --- イベントフィルタ (Filters) ---")
    data = api_get(base_url, http, headers, "/support/ems/filters")
    filters = data.get("records", [])

    if not filters:
        print("  フィルタ: なし")
    else:
        for f in filters:
            name = f.get("name", "N/A")
            print(f"    - {name}")

    # Check recent EMS events (last 10)
    print("\n  --- 最近の EMS イベント (直近10件) ---")
    data = api_get(
        base_url, http, headers,
        "/support/ems/events?max_records=10&order_by=time desc"
    )
    events = data.get("records", [])

    if not events:
        print("  最近のイベント: なし")
    else:
        for event in events:
            time = event.get("time", "N/A")
            name = event.get("message", {}).get("name", "N/A")
            severity = event.get("message", {}).get("severity", "N/A")
            print(f"    [{time}] {name} (severity: {severity})")


def check_volumes(base_url: str, http: urllib3.PoolManager, headers: dict) -> None:
    """Check volume list and capacity."""
    print("\n" + "=" * 60)
    print("ボリューム一覧と容量")
    print("=" * 60)

    data = api_get(base_url, http, headers, "/storage/volumes?fields=space,svm")
    volumes = data.get("records", [])

    if not volumes:
        print("  ボリューム: なし")
        return

    print(f"  ボリューム数: {len(volumes)}")
    for vol in volumes:
        name = vol.get("name", "N/A")
        svm_name = vol.get("svm", {}).get("name", "N/A")
        space = vol.get("space", {})
        size = space.get("size", 0)
        used = space.get("used", 0)
        pct = (used / size * 100) if size > 0 else 0
        size_gb = size / (1024**3)
        used_gb = used / (1024**3)
        print(f"    - {svm_name}/{name}: {used_gb:.1f}GB / {size_gb:.1f}GB ({pct:.1f}%)")


def main():
    """Run verification checks."""
    parser = argparse.ArgumentParser(description="ONTAP System Manager verification")
    parser.add_argument(
        "--check",
        choices=["all", "audit", "quota", "ems", "volume", "qtree"],
        default="all",
        help="Which check to run (default: all)",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("FSx for ONTAP System Manager 検証スクリプト")
    print("=" * 60)

    base_url, http, headers = get_ontap_client()

    # Test connectivity
    print(f"\n接続先: {os.environ.get('ONTAP_MGMT_IP')}")
    data = api_get(base_url, http, headers, "/cluster")
    if data:
        print(f"クラスタ名: {data.get('name', 'N/A')}")
        print(f"ONTAP バージョン: {data.get('version', {}).get('full', 'N/A')}")
    else:
        print("ERROR: ONTAP REST API に接続できません")
        sys.exit(1)

    svms = check_svms(base_url, http, headers)

    if args.check in ("all", "volume"):
        check_volumes(base_url, http, headers)

    if args.check in ("all", "audit"):
        check_audit(base_url, http, headers, svms)

    if args.check in ("all", "qtree"):
        check_qtrees(base_url, http, headers)

    if args.check in ("all", "quota"):
        check_quotas(base_url, http, headers)

    if args.check in ("all", "ems"):
        check_ems(base_url, http, headers)

    print("\n" + "=" * 60)
    print("検証完了")
    print("=" * 60)


if __name__ == "__main__":
    main()
