package financial.access

# 역할 기반 마이크로세그멘테이션 정책(제안군).
# data.financial.permissions에 명시된 (role, source_business, destination, action, purpose)
# 5-튜플과 정확히 일치하는 요청만 허용한다(default-deny). 업무 간 연결관계가 존재하더라도
# 요청의 역할·행위·목적이 튜플에 없으면 거부한다.

default decision := {"allow": false, "reason": "default_deny"}

valid_method if input.method in {"GET", "POST"}

valid_grade if input.data_grade in {"S", "O"}

permission_match if {
  some permission in data.financial.permissions
  permission.role == input.role
  permission.source_business == input.source_business
  permission.destination == input.destination
  permission.action == input.action
  permission.purpose == input.purpose
}

decision := {"allow": true, "reason": "role_based_microsegment_policy"} if {
  input.user != "anonymous"
  input.device_trust == "trusted"
  valid_method
  valid_grade
  permission_match
}

decision := {"allow": false, "reason": "role_purpose_action_mismatch"} if {
  input.user != "anonymous"
  input.device_trust == "trusted"
  valid_method
  valid_grade
  not permission_match
}
