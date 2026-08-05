package financial.access

default decision := {"allow": false, "reason": "default_deny"}

decision := {"allow": true, "reason": "baseline_broad_authenticated_access"} if {
  input.user != "anonymous"
  input.device_trust == "trusted"
  input.destination in {"customer", "loan", "credit", "aml", "approval"}
}
