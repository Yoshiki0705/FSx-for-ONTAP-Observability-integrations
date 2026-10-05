# Terraform equivalent of shared/templates/fsxn-monitoring-dashboard.yaml.
#
# Metric names, namespaces, dimensions, and statistics come from the AWS docs:
#   https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/file-system-metrics.html
#   https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/so-file-system-metrics.html
#   https://docs.aws.amazon.com/fsx/latest/ONTAPGuide/volume-metrics.html

data "aws_region" "current" {}

locals {
  region     = data.aws_region.current.region
  create_sns = var.notification_email != ""

  alarm_actions = local.create_sns ? [aws_sns_topic.alarm[0].arn] : []

  fs_dimensions = {
    FileSystemId = var.file_system_id
  }

  # StorageCapacityUtilization at file-system level is documented with
  # FileSystemId + StorageTier + DataType. The CloudFormation template uses
  # the same set (checked by
  # shared/python/tests/test_monitoring_dashboard_dimensions.py).
  capacity_dimensions = {
    FileSystemId = var.file_system_id
    StorageTier  = "SSD"
    DataType     = "All"
  }

  file_server_metrics = {
    cpu_utilization = {
      enabled     = var.enable_cpu_utilization_alarm
      metric_name = "CPUUtilization"
      threshold   = var.cpu_utilization_threshold_percent
      name_suffix = "cpu-high"
    }
    disk_iops_utilization = {
      enabled     = var.enable_disk_iops_utilization_alarm
      metric_name = "FileServerDiskIopsUtilization"
      threshold   = var.disk_iops_utilization_threshold_percent
      name_suffix = "disk-iops-high"
    }
    disk_throughput_utilization = {
      enabled     = var.enable_disk_throughput_utilization_alarm
      metric_name = "FileServerDiskThroughputUtilization"
      threshold   = var.disk_throughput_utilization_threshold_percent
      name_suffix = "disk-throughput-high"
    }
  }

  # With no file_server_names, one alarm per enabled metric keyed by
  # FileSystemId only (the first-generation dimension set). With names, one
  # alarm per metric and file server keyed by FileSystemId + FileServer (the
  # second-generation set). An alarm is never emitted with a dimension set
  # the docs do not list for the metric.
  file_server_targets = length(var.file_server_names) == 0 ? [""] : var.file_server_names

  file_server_alarms = merge([
    for key, m in local.file_server_metrics : {
      for s in local.file_server_targets :
      (s == "" ? key : "${key}/${s}") => {
        metric_name = m.metric_name
        threshold   = m.threshold
        alarm_name  = s == "" ? "${var.name_prefix}-${m.name_suffix}" : "${var.name_prefix}-${m.name_suffix}-${s}"
        dimensions  = { for k, v in { FileSystemId = var.file_system_id, FileServer = s } : k => v if v != "" }
      }
    } if m.enabled
  ]...)

  volumes = toset(var.volume_ids)
}

# ----------------------------------------------------------------
# SNS topic for alarms (only when notification_email is set)
# ----------------------------------------------------------------
# Unencrypted, as in the CloudFormation template.
resource "aws_sns_topic" "alarm" {
  count = local.create_sns ? 1 : 0

  name         = "${var.name_prefix}-alarms"
  display_name = "FSx for ONTAP alarms (${var.file_system_name})"
  tags         = var.tags
}

resource "aws_sns_topic_subscription" "email" {
  count = local.create_sns ? 1 : 0

  topic_arn = aws_sns_topic.alarm[0].arn
  protocol  = "email"
  endpoint  = var.notification_email
}

# ----------------------------------------------------------------
# Parity alarms (same two alarms as the CloudFormation template)
# ----------------------------------------------------------------
# ok_actions is set alongside alarm_actions on purpose; the template has
# only AlarmActions.
resource "aws_cloudwatch_metric_alarm" "storage_capacity" {
  alarm_name        = "${var.name_prefix}-capacity-high"
  alarm_description = "FSx for ONTAP (${var.file_system_name}) SSD storage capacity utilization is above ${var.capacity_threshold_percent}%. Expand SSD capacity, or enable or tune FabricPool tiering to move cold data to the capacity pool."

  namespace           = "AWS/FSx"
  metric_name         = "StorageCapacityUtilization"
  dimensions          = local.capacity_dimensions
  statistic           = "Average"
  period              = 300
  evaluation_periods  = 3
  threshold           = var.capacity_threshold_percent
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "missing"

  alarm_actions = local.alarm_actions
  ok_actions    = local.alarm_actions
  tags          = var.tags
}

# The template hardcodes the threshold at 80. It is a variable here
# (throughput_threshold_percent, default 80) so callers can tune it without
# forking the module.
resource "aws_cloudwatch_metric_alarm" "network_throughput" {
  alarm_name        = "${var.name_prefix}-throughput-high"
  alarm_description = "FSx for ONTAP (${var.file_system_name}) network throughput utilization is above ${var.throughput_threshold_percent}%. Increase throughput capacity or investigate high-I/O workloads."

  namespace           = "AWS/FSx"
  metric_name         = "NetworkThroughputUtilization"
  dimensions          = local.fs_dimensions
  statistic           = "Average"
  period              = 300
  evaluation_periods  = 3
  threshold           = var.throughput_threshold_percent
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "missing"

  alarm_actions = local.alarm_actions
  ok_actions    = local.alarm_actions
  tags          = var.tags
}

# ----------------------------------------------------------------
# Opt-in file-server alarms
# ----------------------------------------------------------------
resource "aws_cloudwatch_metric_alarm" "file_server" {
  for_each = local.file_server_alarms

  alarm_name        = each.value.alarm_name
  alarm_description = "FSx for ONTAP (${var.file_system_name}) ${each.value.metric_name} is above ${each.value.threshold}%."

  namespace           = "AWS/FSx"
  metric_name         = each.value.metric_name
  dimensions          = each.value.dimensions
  statistic           = "Average"
  period              = 300
  evaluation_periods  = 3
  threshold           = each.value.threshold
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "missing"

  alarm_actions = local.alarm_actions
  ok_actions    = local.alarm_actions
  tags          = var.tags
}

# ----------------------------------------------------------------
# Opt-in per-volume alarms (2 per volume)
# ----------------------------------------------------------------
resource "aws_cloudwatch_metric_alarm" "volume_capacity" {
  for_each = local.volumes

  alarm_name        = "${var.name_prefix}-${each.key}-capacity-high"
  alarm_description = "FSx for ONTAP (${var.file_system_name}) volume ${each.key} storage capacity utilization is above ${var.volume_capacity_threshold_percent}%."

  namespace = "AWS/FSx"
  # Volume-level StorageCapacityUtilization; Average is its only valid statistic.
  metric_name = "StorageCapacityUtilization"
  dimensions = {
    FileSystemId = var.file_system_id
    VolumeId     = each.key
  }
  statistic           = "Average"
  period              = 300
  evaluation_periods  = 3
  threshold           = var.volume_capacity_threshold_percent
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "missing"

  alarm_actions = local.alarm_actions
  ok_actions    = local.alarm_actions
  tags          = var.tags
}

# There is no InodeUtilization metric. Inode utilization is computed with
# metric math over FilesUsed (Average is its only valid statistic) and
# FilesCapacity (Maximum is its only valid statistic).
resource "aws_cloudwatch_metric_alarm" "volume_inode" {
  for_each = local.volumes

  alarm_name        = "${var.name_prefix}-${each.key}-inode-high"
  alarm_description = "FSx for ONTAP (${var.file_system_name}) volume ${each.key} inode utilization is above ${var.volume_inode_threshold_percent}%."

  evaluation_periods  = 3
  threshold           = var.volume_inode_threshold_percent
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "missing"

  metric_query {
    id          = "inode_pct"
    expression  = "100 * files_used / files_capacity"
    label       = "Inode utilization (%)"
    return_data = true
  }

  metric_query {
    id = "files_used"
    metric {
      namespace   = "AWS/FSx"
      metric_name = "FilesUsed"
      period      = 300
      stat        = "Average"
      dimensions = {
        FileSystemId = var.file_system_id
        VolumeId     = each.key
      }
    }
  }

  metric_query {
    id = "files_capacity"
    metric {
      namespace   = "AWS/FSx"
      metric_name = "FilesCapacity"
      period      = 300
      stat        = "Maximum"
      dimensions = {
        FileSystemId = var.file_system_id
        VolumeId     = each.key
      }
    }
  }

  alarm_actions = local.alarm_actions
  ok_actions    = local.alarm_actions
  tags          = var.tags
}

# ----------------------------------------------------------------
# CloudWatch dashboard (same widgets as the CloudFormation template)
# ----------------------------------------------------------------
resource "aws_cloudwatch_dashboard" "this" {
  dashboard_name = "${var.name_prefix}-${var.file_system_name}"

  dashboard_body = jsonencode({
    widgets = [
      {
        type   = "text"
        x      = 0
        y      = 0
        width  = 24
        height = 2
        properties = {
          markdown = "# FSx for ONTAP — ${var.file_system_name}\n\nFile System: `${var.file_system_id}` | Region: `${local.region}` | [FSx Console](https://console.aws.amazon.com/fsx/home?region=${local.region}#file-system-details/${var.file_system_id})"
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 2
        width  = 12
        height = 6
        properties = {
          title = "Network Throughput (MB/s)"
          metrics = [
            ["AWS/FSx", "DataReadBytes", "FileSystemId", var.file_system_id, { stat = "Sum", period = 60, label = "Read", id = "read" }],
            ["AWS/FSx", "DataWriteBytes", "FileSystemId", var.file_system_id, { stat = "Sum", period = 60, label = "Write", id = "write" }],
            [{ expression = "read/60/1048576", label = "Read MB/s", id = "readMBs" }],
            [{ expression = "write/60/1048576", label = "Write MB/s", id = "writeMBs" }],
          ]
          view   = "timeSeries"
          region = local.region
          period = 60
          yAxis  = { left = { label = "MB/s", min = 0 } }
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 2
        width  = 12
        height = 6
        properties = {
          title = "IOPS (Operations/s)"
          metrics = [
            ["AWS/FSx", "DataReadOperations", "FileSystemId", var.file_system_id, { stat = "Sum", period = 60, label = "Read IOPS", id = "riops" }],
            ["AWS/FSx", "DataWriteOperations", "FileSystemId", var.file_system_id, { stat = "Sum", period = 60, label = "Write IOPS", id = "wiops" }],
            [{ expression = "riops/60", label = "Read IOPS", id = "readIOPS" }],
            [{ expression = "wiops/60", label = "Write IOPS", id = "writeIOPS" }],
          ]
          view   = "timeSeries"
          region = local.region
          period = 60
          yAxis  = { left = { label = "ops/s", min = 0 } }
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 8
        width  = 12
        height = 6
        properties = {
          title = "Network Throughput Utilization (%)"
          metrics = [
            ["AWS/FSx", "NetworkThroughputUtilization", "FileSystemId", var.file_system_id, { stat = "Average", period = 60 }],
          ]
          view   = "timeSeries"
          region = local.region
          period = 60
          yAxis  = { left = { label = "%", min = 0, max = 100 } }
          annotations = {
            horizontal = [{ value = var.throughput_threshold_percent, label = "Alarm threshold", color = "#d62728" }]
          }
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 8
        width  = 12
        height = 6
        properties = {
          title = "Storage Capacity Utilization (%)"
          metrics = [
            # Documented dimension set (see local.capacity_dimensions).
            ["AWS/FSx", "StorageCapacityUtilization", "FileSystemId", var.file_system_id, "StorageTier", "SSD", "DataType", "All", { stat = "Average", period = 300 }],
          ]
          view   = "timeSeries"
          region = local.region
          period = 300
          yAxis  = { left = { label = "%", min = 0, max = 100 } }
          annotations = {
            horizontal = [{ value = var.capacity_threshold_percent, label = "Capacity alarm", color = "#d62728" }]
          }
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 14
        width  = 12
        height = 6
        properties = {
          title = "Network Sent/Received (MB/s)"
          metrics = [
            ["AWS/FSx", "NetworkSentBytes", "FileSystemId", var.file_system_id, { stat = "Sum", period = 60, id = "sent" }],
            ["AWS/FSx", "NetworkReceivedBytes", "FileSystemId", var.file_system_id, { stat = "Sum", period = 60, id = "recv" }],
            [{ expression = "sent/60/1048576", label = "Sent MB/s", id = "sentMBs" }],
            [{ expression = "recv/60/1048576", label = "Received MB/s", id = "recvMBs" }],
          ]
          view   = "timeSeries"
          region = local.region
          period = 60
          yAxis  = { left = { label = "MB/s", min = 0 } }
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 14
        width  = 12
        height = 6
        properties = {
          title = "Storage Used (GB)"
          metrics = [
            ["AWS/FSx", "StorageUsed", "FileSystemId", var.file_system_id, { stat = "Average", period = 300, id = "used" }],
            [{ expression = "used/1073741824", label = "Used GB", id = "usedGB" }],
          ]
          view   = "timeSeries"
          region = local.region
          period = 300
          yAxis  = { left = { label = "GB", min = 0 } }
        }
      },
    ]
  })
}
