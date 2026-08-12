package financial.access_test

import data.financial.access.decision

base_input(role, source, dest, action, purpose, method) := {
	"user": "lab-user",
	"role": role,
	"device_trust": "trusted",
	"source_business": source,
	"destination": dest,
	"action": action,
	"purpose": purpose,
	"method": method,
	"data_grade": "S",
}

# --- 정상 5-튜플: 모두 allow ---

test_allow_loan_reads_customer_profile if {
	result := decision with input as base_input("loan_reviewer", "loan", "customer", "read_customer_profile", "loan_screening", "GET")
	result.allow == true
}

test_allow_loan_requests_credit_assessment if {
	result := decision with input as base_input("loan_reviewer", "loan", "credit", "request_credit_assessment", "loan_screening", "POST")
	result.allow == true
}

test_allow_loan_requests_aml_screening if {
	result := decision with input as base_input("loan_reviewer", "loan", "aml", "request_aml_screening", "loan_screening", "POST")
	result.allow == true
}

test_allow_loan_submits_for_approval if {
	result := decision with input as base_input("loan_reviewer", "loan", "approval", "submit_for_approval", "loan_approval", "POST")
	result.allow == true
}

test_allow_approval_reads_review_package if {
	result := decision with input as base_input("loan_approver", "approval", "loan", "read_review_package", "loan_approval", "GET")
	result.allow == true
}

# --- 위반 케이스: 모두 deny ---

test_deny_role_mismatch if {
	# approval 업무 담당자가 loan_reviewer 전용 행위를 시도
	result := decision with input as base_input("loan_approver", "loan", "customer", "read_customer_profile", "loan_screening", "GET")
	result.allow == false
	result.reason == "role_purpose_action_mismatch"
}

test_deny_purpose_mismatch if {
	# 업무 목적이 튜플과 다름 (loan_approval을 심사 단계 조회에 사용)
	result := decision with input as base_input("loan_reviewer", "loan", "customer", "read_customer_profile", "loan_approval", "GET")
	result.allow == false
}

test_deny_action_mismatch if {
	# 정의되지 않은 행위(DELETE 등)
	result := decision with input as base_input("loan_reviewer", "loan", "customer", "delete_customer_profile", "loan_screening", "DELETE")
	result.allow == false
}

test_deny_untrusted_device if {
	input_doc := object.union(
		base_input("loan_reviewer", "loan", "customer", "read_customer_profile", "loan_screening", "GET"),
		{"device_trust": "untrusted"},
	)
	result := decision with input as input_doc
	result.allow == false
	result.reason == "default_deny"
}

test_deny_anonymous_user if {
	input_doc := object.union(
		base_input("loan_reviewer", "loan", "customer", "read_customer_profile", "loan_screening", "GET"),
		{"user": "anonymous"},
	)
	result := decision with input as input_doc
	result.allow == false
}

test_deny_reverse_direction if {
	# 논문 정상 흐름은 loan -> customer이며 그 반대는 허용되지 않는다.
	result := decision with input as base_input("loan_reviewer", "customer", "loan", "read_customer_profile", "loan_screening", "GET")
	result.allow == false
}

test_deny_unrelated_cross_business if {
	# approval -> credit 은 어떤 튜플에도 없는 업무 간 경로
	result := decision with input as base_input("loan_approver", "approval", "credit", "read_review_package", "loan_approval", "GET")
	result.allow == false
}
