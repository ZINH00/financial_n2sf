package financial.cds

default decision := {"allow": false, "reason": "default_deny"}

# 비교군: 사용자·단말 인증과 승인 여부만 확인하는 단순 경계 통제.
decision := {"allow": true, "reason": "baseline_approval_only"} if {
  input.user != "anonymous"
  input.device_trust == "trusted"
  input.approved == true
  input.destination in {"public", "external_ai"}
}
