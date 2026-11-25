# Case Ledger tool classification

93 distinct tools over 20 templates. Default rule only; overrides in use: none.

| tool | kind | object id fields | other *_id fields (not used for matching) | templates |
| --- | --- | --- | --- | --- |
| end_session | control | - | - | 20 |
| escalate_to_duty_nurse | handoff | ticket_id | - | 4 |
| escalate_to_human | handoff | ticket_id | - | 20 |
| say_to_customer | message | - | contact_id | 11 |
| send_message_to_user | message | - | user_id | 20 |
| get_current_time | read | - | - | 20 |
| get_fulfillment_status | read | order_id | - | 4 |
| get_order_details | read | order_id | - | 4 |
| get_payment_ledger | read | order_id | - | 4 |
| get_refund | read | refund_id | - | 4 |
| get_shipment | read | order_id | - | 4 |
| get_ticket | read | ticket_id | - | 20 |
| list_followups | read | ticket_id | - | 20 |
| list_refunds | read | order_id | - | 4 |
| read_application | read | application_id | - | 1 |
| read_appointment | read | - | appointment_id | 1 |
| read_assessment | read | claim_id | - | 1 |
| read_claim | read | claim_id | - | 1 |
| read_contact_preferences | read | - | customer_id | 11 |
| read_disbursement | read | - | loan_id | 1 |
| read_installment | read | subject_id | - | 1 |
| read_intake | read | - | visit_id | 1 |
| read_payout | read | claim_id | - | 1 |
| read_policy | read | - | policy_id | 1 |
| read_preauth | read | - | preauth_id | 1 |
| read_quote | read | application_id | - | 1 |
| read_referral | read | - | referral_id | 1 |
| read_renewal_quote | read | - | policy_id | 1 |
| read_repair_order | read | subject_id | - | 1 |
| read_service_order | read | - | service_order_id | 3 |
| search_tickets | read | application_id, claim_id, order_id, subject_id | appointment_id, loan_id, policy_id, preauth_id, referral_id, service_order_id, user_id, visit_id | 20 |
| add_ticket_note | write | ticket_id | - | 20 |
| ask_repair_result | write | - | contact_id | 1 |
| book_appointment | write | - | contact_id | 1 |
| book_exam | write | - | contact_id | 1 |
| book_other_institution | write | - | referral_id | 1 |
| cancel_appointment | write | - | service_order_id | 1 |
| cancel_followup | write | - | task_id | 20 |
| cancel_fulfillment | write | order_id | - | 4 |
| cancel_pending_order | write | order_id | - | 4 |
| charge_service_fee | write | - | service_order_id | 1 |
| close_ticket | write | ticket_id | - | 20 |
| confirm_new_account | write | - | contact_id | 1 |
| confirm_specialist_slot | write | - | contact_id | 1 |
| correct_document_record | write | claim_id | - | 1 |
| create_service_order | write | - | user_id | 1 |
| create_ticket | write | application_id, claim_id, subject_id | appointment_id, loan_id, policy_id, preauth_id, referral_id, service_order_id, user_id, visit_id | 20 |
| dispatch_rework | write | - | service_order_id | 1 |
| follow_up_specialist | write | - | referral_id | 1 |
| inform_assessment_revision | write | - | contact_id | 1 |
| inform_overdue_status | write | - | contact_id | 1 |
| inform_quote_change | write | - | contact_id | 1 |
| inform_visit_options | write | - | contact_id | 1 |
| issue_compensation_voucher | write | - | service_order_id | 1 |
| issue_refund | write | order_id | - | 4 |
| offer_alternative_slots | write | - | contact_id | 1 |
| offer_available_slots | write | - | contact_id | 3 |
| offer_exam_slots | write | - | contact_id | 1 |
| offer_specialist_slots | write | - | contact_id | 1 |
| pay_claim | write | claim_id | - | 1 |
| present_renewal_quote | write | - | contact_id | 1 |
| proceed_with_assessment | write | claim_id | - | 1 |
| rebook_appointment | write | - | contact_id | 1 |
| record_customer_agreement | write | - | contact_id | 1 |
| record_customer_confirmation | write | - | contact_id | 1 |
| record_customer_consent | write | - | contact_id | 1 |
| record_fault_report | write | - | service_order_id | 1 |
| record_fee_consent | write | - | contact_id | 1 |
| record_patient_choice | write | - | contact_id | 1 |
| record_quote_consent | write | - | contact_id | 1 |
| record_repayment_commitment | write | - | contact_id | 1 |
| record_self_pay_consent | write | - | contact_id | 1 |
| register_payout_account | write | - | contact_id | 1 |
| request_doctor_note | write | - | preauth_id | 1 |
| request_document_clarification | write | claim_id | - | 1 |
| request_part_reorder | write | subject_id | - | 1 |
| reschedule_appointment | write | - | contact_id | 2 |
| resubmit_disbursement | write | - | loan_id | 1 |
| resubmit_preauth | write | - | preauth_id | 1 |
| retry_payout | write | claim_id | - | 1 |
| retry_refund | write | refund_id | - | 4 |
| schedule_followup | write | ticket_id | - | 20 |
| send_document_reminder | write | - | visit_id | 1 |
| send_secure_upload_link | write | - | visit_id | 1 |
| send_supplement_reminder | write | claim_id | - | 1 |
| send_upload_link | write | - | service_order_id | 1 |
| sign_contract | write | application_id | - | 1 |
| start_outbound_contact | write | - | customer_id | 11 |
| submit_disbursement | write | - | loan_id | 1 |
| submit_renewal | write | - | contact_id | 1 |
| suspend_claim | write | claim_id | - | 1 |
| verify_customer_identity | write | - | contact_id | 6 |
| withdraw_application | write | application_id | - | 1 |

## Write tools without an object id field

- ask_repair_result
- book_appointment
- book_exam
- book_other_institution
- cancel_appointment
- cancel_followup
- charge_service_fee
- confirm_new_account
- confirm_specialist_slot
- create_service_order
- dispatch_rework
- follow_up_specialist
- inform_assessment_revision
- inform_overdue_status
- inform_quote_change
- inform_visit_options
- issue_compensation_voucher
- offer_alternative_slots
- offer_available_slots
- offer_exam_slots
- offer_specialist_slots
- present_renewal_quote
- rebook_appointment
- record_customer_agreement
- record_customer_confirmation
- record_customer_consent
- record_fault_report
- record_fee_consent
- record_patient_choice
- record_quote_consent
- record_repayment_commitment
- record_self_pay_consent
- register_payout_account
- request_doctor_note
- reschedule_appointment
- resubmit_disbursement
- resubmit_preauth
- send_document_reminder
- send_secure_upload_link
- send_upload_link
- start_outbound_contact
- submit_disbursement
- submit_renewal
- verify_customer_identity

## Per template

- **credit-delinquency**: control: end_session; handoff: escalate_to_human; message: say_to_customer, send_message_to_user; read: get_current_time, get_ticket, list_followups, read_contact_preferences, read_installment, search_tickets; write: add_ticket_note, cancel_followup, close_ticket, create_ticket, inform_overdue_status, record_repayment_commitment, schedule_followup, start_outbound_contact, verify_customer_identity
- **credit-application**: control: end_session; handoff: escalate_to_human; message: send_message_to_user; read: get_current_time, get_ticket, list_followups, read_application, search_tickets; write: add_ticket_note, cancel_followup, close_ticket, create_ticket, schedule_followup
- **field-service-repair**: control: end_session; handoff: escalate_to_human; message: say_to_customer, send_message_to_user; read: get_current_time, get_ticket, list_followups, read_contact_preferences, read_repair_order, search_tickets; write: add_ticket_note, book_appointment, cancel_followup, close_ticket, create_ticket, offer_available_slots, request_part_reorder, schedule_followup, start_outbound_contact
- **insurance-renewal-followup**: control: end_session; handoff: escalate_to_human; message: say_to_customer, send_message_to_user; read: get_current_time, get_ticket, list_followups, read_contact_preferences, read_policy, read_renewal_quote, search_tickets; write: add_ticket_note, cancel_followup, close_ticket, create_ticket, present_renewal_quote, record_quote_consent, schedule_followup, start_outbound_contact, submit_renewal, verify_customer_identity
- **insurance-claim-documents**: control: end_session; handoff: escalate_to_human; message: send_message_to_user; read: get_current_time, get_ticket, list_followups, read_claim, search_tickets; write: add_ticket_note, cancel_followup, close_ticket, correct_document_record, create_ticket, request_document_clarification, schedule_followup, send_supplement_reminder, suspend_claim
- **insurance-claim-payout**: control: end_session; handoff: escalate_to_human; message: send_message_to_user; read: get_current_time, get_ticket, list_followups, read_payout, search_tickets; write: add_ticket_note, cancel_followup, close_ticket, create_ticket, pay_claim, register_payout_account, retry_payout, schedule_followup, verify_customer_identity
- **insurance-damage-revision**: control: end_session; handoff: escalate_to_human; message: say_to_customer, send_message_to_user; read: get_current_time, get_ticket, list_followups, read_assessment, read_contact_preferences, search_tickets; write: add_ticket_note, cancel_followup, close_ticket, create_ticket, inform_assessment_revision, proceed_with_assessment, record_customer_consent, schedule_followup, start_outbound_contact, verify_customer_identity
- **credit-disbursement**: control: end_session; handoff: escalate_to_human; message: send_message_to_user; read: get_current_time, get_ticket, list_followups, read_disbursement, search_tickets; write: add_ticket_note, cancel_followup, close_ticket, confirm_new_account, create_ticket, resubmit_disbursement, schedule_followup, submit_disbursement, verify_customer_identity
- **credit-quote-change**: control: end_session; handoff: escalate_to_human; message: say_to_customer, send_message_to_user; read: get_current_time, get_ticket, list_followups, read_contact_preferences, read_quote, search_tickets; write: add_ticket_note, cancel_followup, close_ticket, create_ticket, inform_quote_change, record_customer_agreement, schedule_followup, sign_contract, start_outbound_contact, verify_customer_identity, withdraw_application
- **field-service-noshow**: control: end_session; handoff: escalate_to_human; message: say_to_customer, send_message_to_user; read: get_current_time, get_ticket, list_followups, read_contact_preferences, read_service_order, search_tickets; write: add_ticket_note, cancel_followup, close_ticket, create_ticket, issue_compensation_voucher, offer_available_slots, reschedule_appointment, schedule_followup, start_outbound_contact
- **field-service-rework**: control: end_session; handoff: escalate_to_human; message: say_to_customer, send_message_to_user; read: get_current_time, get_ticket, list_followups, read_contact_preferences, read_service_order, search_tickets; write: add_ticket_note, ask_repair_result, cancel_followup, charge_service_fee, close_ticket, create_service_order, create_ticket, dispatch_rework, record_customer_confirmation, record_fault_report, schedule_followup, send_upload_link, start_outbound_contact
- **field-service-reschedule**: control: end_session; handoff: escalate_to_human; message: send_message_to_user; read: get_current_time, get_ticket, list_followups, read_service_order, search_tickets; write: add_ticket_note, cancel_appointment, cancel_followup, close_ticket, create_ticket, offer_available_slots, record_fee_consent, reschedule_appointment, schedule_followup
- **medical-referral**: control: end_session; handoff: escalate_to_duty_nurse, escalate_to_human; message: say_to_customer, send_message_to_user; read: get_current_time, get_ticket, list_followups, read_contact_preferences, read_referral, search_tickets; write: add_ticket_note, book_other_institution, cancel_followup, close_ticket, confirm_specialist_slot, create_ticket, follow_up_specialist, offer_specialist_slots, schedule_followup, start_outbound_contact
- **medical-preauth**: control: end_session; handoff: escalate_to_duty_nurse, escalate_to_human; message: say_to_customer, send_message_to_user; read: get_current_time, get_ticket, list_followups, read_contact_preferences, read_preauth, search_tickets; write: add_ticket_note, book_exam, cancel_followup, close_ticket, create_ticket, offer_exam_slots, record_self_pay_consent, request_doctor_note, resubmit_preauth, schedule_followup, start_outbound_contact
- **medical-clinic-suspended**: control: end_session; handoff: escalate_to_duty_nurse, escalate_to_human; message: say_to_customer, send_message_to_user; read: get_current_time, get_ticket, list_followups, read_appointment, read_contact_preferences, search_tickets; write: add_ticket_note, cancel_followup, close_ticket, create_ticket, offer_alternative_slots, rebook_appointment, schedule_followup, start_outbound_contact
- **medical-intake-documents**: control: end_session; handoff: escalate_to_duty_nurse, escalate_to_human; message: say_to_customer, send_message_to_user; read: get_current_time, get_ticket, list_followups, read_contact_preferences, read_intake, search_tickets; write: add_ticket_note, cancel_followup, close_ticket, create_ticket, inform_visit_options, record_patient_choice, schedule_followup, send_document_reminder, send_secure_upload_link, start_outbound_contact
- **refund-silent-failure**: control: end_session; handoff: escalate_to_human; message: send_message_to_user; read: get_current_time, get_fulfillment_status, get_order_details, get_payment_ledger, get_refund, get_shipment, get_ticket, list_followups, list_refunds, search_tickets; write: add_ticket_note, cancel_followup, cancel_fulfillment, cancel_pending_order, close_ticket, create_ticket, issue_refund, retry_refund, schedule_followup
- **refund-timeout-committed**: control: end_session; handoff: escalate_to_human; message: send_message_to_user; read: get_current_time, get_fulfillment_status, get_order_details, get_payment_ledger, get_refund, get_shipment, get_ticket, list_followups, list_refunds, search_tickets; write: add_ticket_note, cancel_followup, cancel_fulfillment, cancel_pending_order, close_ticket, create_ticket, issue_refund, retry_refund, schedule_followup
- **cross-channel-revisit**: control: end_session; handoff: escalate_to_human; message: send_message_to_user; read: get_current_time, get_fulfillment_status, get_order_details, get_payment_ledger, get_refund, get_shipment, get_ticket, list_followups, list_refunds, search_tickets; write: add_ticket_note, cancel_followup, cancel_fulfillment, cancel_pending_order, close_ticket, create_ticket, issue_refund, retry_refund, schedule_followup
- **cancel-partial-success**: control: end_session; handoff: escalate_to_human; message: send_message_to_user; read: get_current_time, get_fulfillment_status, get_order_details, get_payment_ledger, get_refund, get_shipment, get_ticket, list_followups, list_refunds, search_tickets; write: add_ticket_note, cancel_followup, cancel_fulfillment, cancel_pending_order, close_ticket, create_ticket, issue_refund, retry_refund, schedule_followup
