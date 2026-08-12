package financial.cds

default decision := {"allow": false, "reason": "default_deny"}

valid_destination if input.destination in {"public", "external_ai"}
valid_role if input.role in {"publisher", "ai_user", "approver"}

# S 원본의 직접 외부 전송은 금지하고, 승인된 O 파생본만 허용한다.
decision := {"allow": true, "reason": "approved_open_derivative"} if {
  input.user != "anonymous"
  input.device_trust == "trusted"
  valid_destination
  valid_role
  input.data_grade == "O"
  input.approved == true
  input.content_safe == true
  input.purpose in {"public_disclosure", "approved_ai_use"}
}


# 두 거부 규칙은 상호배타적으로 작성한다. data_grade=="S"와 content_safe==false가
# 동시에 참인 입력이 들어오면(예: S등급이면서 콘텐츠 검사에도 실패) 겹치는 조건의
# complete rule이 서로 다른 두 값을 만들어 OPA가 eval_conflict_error를 낸다.
# S등급 여부를 우선 판단하고, O등급에서만 콘텐츠 검사 실패를 별도로 판단한다.
decision := {"allow": false, "reason": "s_grade_direct_transfer_blocked"} if input.data_grade == "S"
decision := {"allow": false, "reason": "content_validation_failed"} if {
  input.data_grade != "S"
  input.content_safe == false
}
