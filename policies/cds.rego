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

decision := {"allow": false, "reason": "s_grade_direct_transfer_blocked"} if input.data_grade == "S"
decision := {"allow": false, "reason": "content_validation_failed"} if input.content_safe == false
