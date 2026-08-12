package financial.cds_test

import data.financial.cds.decision

base_input(role, destination, data_grade, approved, purpose, content_safe, device_trust) := {
	"user": "lab-user",
	"role": role,
	"device_trust": device_trust,
	"destination": destination,
	"data_grade": data_grade,
	"approved": approved,
	"purpose": purpose,
	"content_safe": content_safe,
	"source_object_id": "OBJ-TEST-001",
}

# C01: 승인된 O 파생본의 공개 전송은 허용된다.
test_allow_public_o_grade_approved if {
	result := decision with input as base_input("publisher", "public", "O", true, "public_disclosure", true, "trusted")
	result.allow == true
}

# C02: 승인된 O 파생본의 외부 AI 활용도 허용된다.
test_allow_external_ai_o_grade_approved if {
	result := decision with input as base_input("ai_user", "external_ai", "O", true, "approved_ai_use", true, "trusted")
	result.allow == true
}

# C03: S 원본은 승인·목적·콘텐츠 검사와 무관하게 직접 전송이 금지된다.
test_deny_s_grade_direct_transfer if {
	result := decision with input as base_input("publisher", "public", "S", true, "public_disclosure", true, "trusted")
	result.allow == false
	result.reason == "s_grade_direct_transfer_blocked"
}

# C04: O 등급이라도 콘텐츠 검사에 실패하면 거부된다.
test_deny_content_validation_failed if {
	result := decision with input as base_input("ai_user", "external_ai", "O", true, "approved_ai_use", false, "trusted")
	result.allow == false
	result.reason == "content_validation_failed"
}

# C05: 승인되지 않은 전송은 거부된다.
test_deny_unapproved if {
	result := decision with input as base_input("publisher", "public", "O", false, "public_disclosure", true, "trusted")
	result.allow == false
}

# C06: 신뢰되지 않은 단말은 거부된다.
test_deny_untrusted_device if {
	result := decision with input as base_input("ai_user", "external_ai", "O", true, "approved_ai_use", true, "untrusted")
	result.allow == false
}

# 경계조건: S등급이면서 동시에 콘텐츠 검사에도 실패하는 입력이 들어와도 decision이
# 정확히 하나의 값만 만들어야 한다(겹치는 두 deny 규칙이 동시에 참이 되어 OPA가
# eval_conflict_error를 내는 것을 방지하는 회귀 테스트). S등급 판단이 우선한다.
test_deny_s_grade_and_content_unsafe_no_conflict if {
	result := decision with input as base_input("publisher", "public", "S", true, "public_disclosure", false, "trusted")
	result.allow == false
	result.reason == "s_grade_direct_transfer_blocked"
}
