package financial.access

default decision := {"allow": false, "reason": "default_deny"}

allowed_workflow := {
  "customer": {"loan"},
  "loan": {"credit", "aml", "approval"},
  "credit": {"loan", "approval"},
  "aml": {"loan", "approval"},
  "approval": {"loan"}
}

valid_method if input.method in {"GET", "POST"}
valid_grade if input.data_grade in {"S", "O"}
valid_purpose if input.purpose in {"approved_workflow", "case_processing", "risk_review", "approval"}

workflow_allowed if {
  destinations := object.get(allowed_workflow, input.source_business, set())
  input.destination in destinations
}

decision := {"allow": true, "reason": "role_based_microsegment_policy"} if {
  input.user != "anonymous"
  input.device_trust == "trusted"
  input.role in {"analyst", "reviewer", "approver", "system"}
  valid_method
  valid_grade
  valid_purpose
  workflow_allowed
}

decision := {"allow": false, "reason": "unapproved_business_path"} if {
  input.device_trust == "trusted"
  not workflow_allowed
}
