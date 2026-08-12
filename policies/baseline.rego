package financial.access

# 비교군 정책: 역할·행위·목적을 구분하지 않고 인증된 신뢰 단말이면 5개 업무
# 어디든 광범위하게 허용한다(제안군의 role_based_microsegment_policy와 대비).

default decision := {"allow": false, "reason": "default_deny"}

decision := {"allow": true, "reason": "baseline_broad_authenticated_access"} if {
  input.user != "anonymous"
  input.device_trust == "trusted"
  input.destination in {"customer", "loan", "credit", "aml", "approval"}
}
