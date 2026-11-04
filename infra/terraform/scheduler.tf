# EventBridge Scheduler runs the same image as one-off Fargate tasks: no always-on worker to pay for.
data "aws_iam_policy_document" "scheduler_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }
  }
}
resource "aws_iam_role" "scheduler" {
  name               = "${local.name}-scheduler"
  assume_role_policy = data.aws_iam_policy_document.scheduler_assume.json
}
resource "aws_iam_role_policy" "scheduler" {
  role = aws_iam_role.scheduler.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["ecs:RunTask"], Resource = "arn:aws:ecs:${var.region}:${data.aws_caller_identity.me.account_id}:task-definition/${local.name}:*" },
  { Effect = "Allow", Action = ["iam:PassRole"], Resource = [aws_iam_role.execution.arn, aws_iam_role.job.arn] }] })
}
locals {
  jobs = {
    import      = { schedule = var.import_schedule, command = ["python", "-m", "api.app.jobs.run", "import", "--who=scheduler"] }
    maintenance = { schedule = var.maintenance_schedule, command = ["python", "-m", "api.app.jobs.run", "maintenance", "--limit=${var.maintenance_batch}", "--who=scheduler"] }
  }
}
resource "aws_scheduler_schedule" "job" {
  for_each                     = local.jobs
  name                         = "${local.name}-${each.key}"
  schedule_expression          = each.value.schedule
  schedule_expression_timezone = "UTC"
  flexible_time_window { mode = "OFF" }
  target {
    arn      = aws_ecs_cluster.this.arn
    role_arn = aws_iam_role.scheduler.arn
    input    = jsonencode({ containerOverrides = [{ name = "app", command = each.value.command }] })
    ecs_parameters {
      task_definition_arn = aws_ecs_task_definition.job.arn
      launch_type         = "FARGATE"
      network_configuration {
        subnets          = aws_subnet.public[*].id
        security_groups  = [aws_security_group.app.id]
        assign_public_ip = true
      }
    }
  }
}
