output "function_application_id" {
  value = oci_functions_application.tier0_app.id
}

output "function_id" {
  value = oci_functions_function.tier0_deploy.id
}

output "event_rule_id" {
  value = oci_events_rule.instance_launch.id
}

output "dynamic_group_id" {
  value = oci_identity_dynamic_group.tier0_functions.id
}
