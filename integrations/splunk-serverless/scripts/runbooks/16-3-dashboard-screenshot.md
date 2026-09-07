# 16.3 ダッシュボード作成 + スクリーンショット取得手順

## 概要

FSx for ONTAP 監査ログを可視化する Splunk ダッシュボードを作成し、スクリーンショットを撮影して E2E 検証エビデンスとして保存する手順書。

## 前提条件

- Splunk Web にログイン済み
- FSx for ONTAP 監査ログが Splunk に到着済み（Task 15.3 完了）
- `index=fsxn_audit` に十分なデータが存在すること
- スクリーンショットツール

## ダッシュボード構成

4つのパネルで構成:

| パネル | 可視化タイプ | 目的 |
|--------|-------------|------|
| ログ量推移 | Line Chart (timechart) | 時間帯別のログ量を把握 |
| 操作別内訳 | Pie Chart | 操作種別の分布を把握 |
| ユーザー別アクティビティ | Bar Chart | ユーザーごとの操作量を把握 |
| エラー率 | Single Value + Gauge | 失敗率を監視 |

## 手順

### Step 1: ダッシュボード作成

1. Splunk Web にログイン
2. **Dashboards** → **Create New Dashboard** をクリック
3. 設定:
   - **Title**: `FSx for ONTAP Audit Log Overview`
   - **Description**: `Amazon FSx for NetApp ONTAP audit log dashboard`
   - **Dashboard Type**: Classic Dashboard
   - **Permissions**: Private（検証用）
4. **Create** をクリック

### Step 2: パネル1 — ログ量推移（Log Volume Over Time）

**Edit** モードで以下のパネルを追加:

1. **Add Panel** → **New** → **Line Chart**
2. SPL クエリ:

```spl
index=fsxn_audit sourcetype=fsxn:ontap:audit earliest=-24h
| timechart span=1h count as "Log Count"
```

3. パネル設定:
   - **Title**: `Log Volume Over Time`
   - **Time Range**: Last 24 hours
   - **Visualization**: Line Chart
   - X軸: `_time`
   - Y軸: `Log Count`

### Step 3: パネル2 — 操作別内訳（Operation Breakdown）

1. **Add Panel** → **New** → **Pie Chart**
2. SPL クエリ:

```spl
index=fsxn_audit sourcetype=fsxn:ontap:audit earliest=-24h
| stats count by operation
| sort -count
| head 10
```

3. パネル設定:
   - **Title**: `Operation Breakdown`
   - **Visualization**: Pie Chart
   - Label: `operation`
   - Value: `count`

### Step 4: パネル3 — ユーザー別アクティビティ（User Activity）

1. **Add Panel** → **New** → **Bar Chart**
2. SPL クエリ:

```spl
index=fsxn_audit sourcetype=fsxn:ontap:audit earliest=-24h
| stats count by user
| sort -count
| head 10
```

3. パネル設定:
   - **Title**: `User Activity (Top 10)`
   - **Visualization**: Bar Chart (horizontal)
   - X軸: `count`
   - Y軸: `user`

### Step 5: パネル4 — エラー率（Error Rate）

1. **Add Panel** → **New** → **Single Value**
2. SPL クエリ:

```spl
index=fsxn_audit sourcetype=fsxn:ontap:audit earliest=-24h
| stats count(eval(result="Failure")) as failures, count as total
| eval error_rate=round((failures/total)*100, 2)
| fields error_rate
| eval error_rate=error_rate."%"
```

3. パネル設定:
   - **Title**: `Error Rate (24h)`
   - **Visualization**: Single Value
   - **Color**: Red if > 5%, Yellow if > 1%, Green otherwise

### Step 6: ダッシュボードレイアウト調整

1. パネルを2x2グリッドに配置:
   ```
   +-------------------+-------------------+
   | Log Volume        | Operation         |
   | Over Time         | Breakdown         |
   +-------------------+-------------------+
   | User Activity     | Error Rate        |
   | (Top 10)          | (24h)             |
   +-------------------+-------------------+
   ```
2. 各パネルのサイズを均等に調整
3. **Save** をクリック

### Step 7: スクリーンショット撮影

**キャプチャ対象:**

以下の要素がすべて画面内に収まるようにスクリーンショットを撮影:

1. **ダッシュボードタイトル**: `FSx for ONTAP Audit Log Overview` が表示されている
2. **4つのパネル**: すべてのパネルにデータが表示されている
3. **可視化**: グラフ・チャートが正しくレンダリングされている
4. **時間範囲**: ダッシュボードの時間範囲が確認できる

**撮影のポイント:**
- 全4パネルが1画面に収まるようにブラウザをフルスクリーンにする
- 各パネルにデータが表示されていること（空のパネルがないこと）
- ダッシュボードタイトルが見える状態で撮影

### Step 8: ファイル保存

```bash
# 保存先ディレクトリ
docs/screenshots/splunk/

# ファイル名（YYYYMMDD は撮影日）
splunk-dashboard-YYYYMMDD.png

# 例: 2026年1月20日に撮影した場合
docs/screenshots/splunk/splunk-dashboard-20260120.png
```

### Step 9: ファイルサイズ確認

```bash
# 500KB 以下であることを確認
ls -la docs/screenshots/splunk/splunk-dashboard-*.png

# 500KB を超える場合はリサイズ
sips --resampleWidth 1440 docs/screenshots/splunk/splunk-dashboard-YYYYMMDD.png
```

### Step 10: マスキング処理

```bash
# 機密情報をマスクしてからコミット
python3 docs/screenshots/mask_screenshots.py
```

## ダッシュボード XML（参考）

Classic Dashboard の XML ソース（手動インポート用）:

```xml
<dashboard version="1.1">
  <label>FSx for ONTAP Audit Log Overview</label>
  <description>Amazon FSx for NetApp ONTAP audit log dashboard</description>
  <row>
    <panel>
      <title>Log Volume Over Time</title>
      <chart>
        <search>
          <query>index=fsxn_audit sourcetype=fsxn:ontap:audit earliest=-24h | timechart span=1h count as "Log Count"</query>
          <earliest>-24h@h</earliest>
          <latest>now</latest>
        </search>
        <option name="charting.chart">line</option>
        <option name="charting.axisTitleX.text">Time</option>
        <option name="charting.axisTitleY.text">Events</option>
      </chart>
    </panel>
    <panel>
      <title>Operation Breakdown</title>
      <chart>
        <search>
          <query>index=fsxn_audit sourcetype=fsxn:ontap:audit earliest=-24h | stats count by operation | sort -count | head 10</query>
          <earliest>-24h@h</earliest>
          <latest>now</latest>
        </search>
        <option name="charting.chart">pie</option>
      </chart>
    </panel>
  </row>
  <row>
    <panel>
      <title>User Activity (Top 10)</title>
      <chart>
        <search>
          <query>index=fsxn_audit sourcetype=fsxn:ontap:audit earliest=-24h | stats count by user | sort -count | head 10</query>
          <earliest>-24h@h</earliest>
          <latest>now</latest>
        </search>
        <option name="charting.chart">bar</option>
        <option name="charting.chart.orientation">horizontal</option>
      </chart>
    </panel>
    <panel>
      <title>Error Rate (24h)</title>
      <single>
        <search>
          <query>index=fsxn_audit sourcetype=fsxn:ontap:audit earliest=-24h | stats count(eval(result="Failure")) as failures, count as total | eval error_rate=round((failures/total)*100, 2) | fields error_rate | eval error_rate=error_rate."%"</query>
          <earliest>-24h@h</earliest>
          <latest>now</latest>
        </search>
        <option name="colorMode">block</option>
        <option name="rangeColors">["0x65a637","0xf8be34","0xd93f3c"]</option>
        <option name="rangeValues">[1,5]</option>
        <option name="useColors">1</option>
      </single>
    </panel>
  </row>
</dashboard>
```

## 検証チェックリスト

- [ ] ダッシュボードが作成された（タイトル: `FSx for ONTAP Audit Log Overview`）
- [ ] パネル1: ログ量推移（Line Chart）にデータが表示されている
- [ ] パネル2: 操作別内訳（Pie Chart）にデータが表示されている
- [ ] パネル3: ユーザー別アクティビティ（Bar Chart）にデータが表示されている
- [ ] パネル4: エラー率（Single Value）にデータが表示されている
- [ ] 全4パネルが1画面に収まるスクリーンショットが撮影された
- [ ] ファイル名が命名規約に準拠（`splunk-dashboard-YYYYMMDD.png`）
- [ ] ファイルサイズが 500KB 以下
- [ ] マスキング処理が完了

## トラブルシューティング

### パネルでのデータの未表示

- **原因**: 時間範囲内にデータがない
- **解決**: `earliest=-24h` を `earliest=-7d` に変更して再試行

### ダッシュボードの保存の不可

- **原因**: 権限不足
- **解決**: `admin` ロールまたは適切な App 権限を確認

### 1 画面に収まらないパネル

- **解決**: ブラウザをフルスクリーンにし、ズームレベルを 80% に設定

## 関連タスク

- Task 15.3: Splunk Search でログ到着確認
- Task 16.1: HEC 設定画面スクリーンショット
- Task 16.2: 検索結果スクリーンショット
