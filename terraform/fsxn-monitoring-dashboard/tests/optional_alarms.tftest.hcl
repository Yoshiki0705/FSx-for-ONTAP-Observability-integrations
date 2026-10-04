# Offline: mock provider, plan only, no AWS credentials.
mock_provider "aws" {
  override_during = plan

  mock_data "aws_region" {
    defaults = {
      region = "us-east-1"
    }
  }

  mock_resource "aws_sns_topic" {
    defaults = {
      arn = "arn:aws:sns:us-east-1:123456789012:mock-alarms"
    }
  }
}

variables {
  file_system_id = "fs-0123456789abcdef0"
}

run "cpu_utilization" {
  command = plan

  variables {
    enable_cpu_utilization_alarm      = true
    cpu_utilization_threshold_percent = 70
  }

  assert {
    condition     = length(aws_cloudwatch_metric_alarm.file_server) == 1
    error_message = "Expected exactly one file-server alarm."
  }

  assert {
    condition = (
      aws_cloudwatch_metric_alarm.file_server["cpu_utilization"].namespace == "AWS/FSx" &&
      aws_cloudwatch_metric_alarm.file_server["cpu_utilization"].metric_name == "CPUUtilization" &&
      aws_cloudwatch_metric_alarm.file_server["cpu_utilization"].statistic == "Average" &&
      aws_cloudwatch_metric_alarm.file_server["cpu_utilization"].threshold == 70 &&
      aws_cloudwatch_metric_alarm.file_server["cpu_utilization"].alarm_name == "fsxn-monitoring-cpu-high" &&
      aws_cloudwatch_metric_alarm.file_server["cpu_utilization"].dimensions == tomap({ FileSystemId = "fs-0123456789abcdef0" })
    )
    error_message = "CPU alarm does not use the documented metric and dimensions."
  }
}

run "disk_iops_utilization" {
  command = plan

  variables {
    enable_disk_iops_utilization_alarm = true
  }

  assert {
    condition     = length(aws_cloudwatch_metric_alarm.file_server) == 1
    error_message = "Expected exactly one file-server alarm."
  }

  assert {
    condition = (
      aws_cloudwatch_metric_alarm.file_server["disk_iops_utilization"].namespace == "AWS/FSx" &&
      aws_cloudwatch_metric_alarm.file_server["disk_iops_utilization"].metric_name == "FileServerDiskIopsUtilization" &&
      aws_cloudwatch_metric_alarm.file_server["disk_iops_utilization"].threshold == 80 &&
      aws_cloudwatch_metric_alarm.file_server["disk_iops_utilization"].dimensions == tomap({ FileSystemId = "fs-0123456789abcdef0" })
    )
    error_message = "Disk IOPS alarm does not use the documented metric and dimensions."
  }
}

run "disk_throughput_utilization" {
  command = plan

  variables {
    enable_disk_throughput_utilization_alarm = true
  }

  assert {
    condition     = length(aws_cloudwatch_metric_alarm.file_server) == 1
    error_message = "Expected exactly one file-server alarm."
  }

  assert {
    condition = (
      aws_cloudwatch_metric_alarm.file_server["disk_throughput_utilization"].namespace == "AWS/FSx" &&
      aws_cloudwatch_metric_alarm.file_server["disk_throughput_utilization"].metric_name == "FileServerDiskThroughputUtilization" &&
      aws_cloudwatch_metric_alarm.file_server["disk_throughput_utilization"].threshold == 80 &&
      aws_cloudwatch_metric_alarm.file_server["disk_throughput_utilization"].dimensions == tomap({ FileSystemId = "fs-0123456789abcdef0" })
    )
    error_message = "Disk throughput alarm does not use the documented metric and dimensions."
  }
}

run "file_servers" {
  command = plan

  variables {
    enable_cpu_utilization_alarm = true
    file_server_names            = ["FsxId0123456789abcdef0-01", "FsxId0123456789abcdef0-02"]
  }

  assert {
    condition     = length(aws_cloudwatch_metric_alarm.file_server) == 2
    error_message = "Expected one CPU alarm per file server."
  }

  assert {
    condition = alltrue([
      for s in ["FsxId0123456789abcdef0-01", "FsxId0123456789abcdef0-02"] :
      aws_cloudwatch_metric_alarm.file_server["cpu_utilization/${s}"].dimensions == tomap({
        FileSystemId = "fs-0123456789abcdef0"
        FileServer   = s
      })
    ])
    error_message = "Per-file-server alarms must use FileSystemId + FileServer."
  }
}

run "volumes" {
  command = plan

  variables {
    volume_ids = ["fsvol-0123456789abcdef0", "fsvol-0123456789abcdef1"]
  }

  assert {
    condition     = length(aws_cloudwatch_metric_alarm.volume_capacity) + length(aws_cloudwatch_metric_alarm.volume_inode) == 4
    error_message = "Expected 2 alarms per volume."
  }

  assert {
    condition = alltrue([
      for v in ["fsvol-0123456789abcdef0", "fsvol-0123456789abcdef1"] : (
        aws_cloudwatch_metric_alarm.volume_capacity[v].metric_name == "StorageCapacityUtilization" &&
        aws_cloudwatch_metric_alarm.volume_capacity[v].statistic == "Average" &&
        aws_cloudwatch_metric_alarm.volume_capacity[v].dimensions == tomap({
          FileSystemId = "fs-0123456789abcdef0"
          VolumeId     = v
        })
      )
    ])
    error_message = "Volume capacity alarms must use StorageCapacityUtilization with FileSystemId + VolumeId."
  }

  assert {
    condition = alltrue([
      for v in ["fsvol-0123456789abcdef0", "fsvol-0123456789abcdef1"] :
      toset([for q in aws_cloudwatch_metric_alarm.volume_inode[v].metric_query : q.id]) == toset(["inode_pct", "files_used", "files_capacity"])
    ])
    error_message = "Inode alarms must have the inode_pct, files_used, and files_capacity queries."
  }

  assert {
    condition = alltrue(flatten([
      for v in ["fsvol-0123456789abcdef0", "fsvol-0123456789abcdef1"] : [
        for q in aws_cloudwatch_metric_alarm.volume_inode[v].metric_query : (
          q.id == "inode_pct" ? q.return_data == true :
          q.id == "files_used" ? (
            one(q.metric).metric_name == "FilesUsed" &&
            one(q.metric).stat == "Average" &&
            one(q.metric).dimensions == tomap({ FileSystemId = "fs-0123456789abcdef0", VolumeId = v })
          ) :
          (
            one(q.metric).metric_name == "FilesCapacity" &&
            one(q.metric).stat == "Maximum" &&
            one(q.metric).dimensions == tomap({ FileSystemId = "fs-0123456789abcdef0", VolumeId = v })
          )
        )
      ]
    ]))
    error_message = "Inode alarm queries must use FilesUsed/Average and FilesCapacity/Maximum with FileSystemId + VolumeId."
  }
}
