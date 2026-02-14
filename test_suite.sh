#!/bin/bash

# ClinSight Backend - Automated Test Script
# This script tests all major features of the application

set -e  # Exit on error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Configuration
BASE_URL="http://localhost:8000"
API_URL="${BASE_URL}/api"

# Test results
TESTS_PASSED=0
TESTS_FAILED=0

# Helper functions
print_header() {
    echo ""
    echo "=========================================="
    echo "$1"
    echo "=========================================="
}

print_success() {
    echo -e "${GREEN}✓${NC} $1"
    ((TESTS_PASSED++))
}

print_error() {
    echo -e "${RED}✗${NC} $1"
    ((TESTS_FAILED++))
}

print_info() {
    echo -e "${YELLOW}ℹ${NC} $1"
}

# Test health endpoint
test_health() {
    print_header "Testing Health Endpoint"
    
    response=$(curl -s "${BASE_URL}/health")
    
    if echo "$response" | grep -q "healthy"; then
        print_success "Health endpoint is working"
        echo "$response" | jq '.' 2>/dev/null || echo "$response"
    else
        print_error "Health endpoint failed"
        echo "$response"
        exit 1
    fi
}

# Test authentication
test_authentication() {
    print_header "Testing Authentication"
    
    # Login as physician
    print_info "Logging in as physician..."
    response=$(curl -s -X POST "${API_URL}/auth/login" \
        -H "Content-Type: application/json" \
        -d '{"username":"physician","password":"demo"}')
    
    TOKEN=$(echo "$response" | jq -r '.access_token' 2>/dev/null)
    
    if [ -n "$TOKEN" ] && [ "$TOKEN" != "null" ]; then
        print_success "Login successful"
        print_info "Token: ${TOKEN:0:20}..."
        export AUTH_TOKEN="$TOKEN"
    else
        print_error "Login failed"
        echo "$response"
        exit 1
    fi
}

# Test patient creation
test_patient_creation() {
    print_header "Testing Patient Management"
    
    print_info "Creating new patient..."
    response=$(curl -s -X POST "${API_URL}/patients" \
        -H "Authorization: Bearer $AUTH_TOKEN" \
        -H "Content-Type: application/json" \
        -d '{
            "first_name": "Test",
            "last_name": "Patient",
            "date_of_birth": "1970-01-01",
            "gender": "male",
            "phone": "+1-555-TEST",
            "email": "test.patient@test.com",
            "chief_complaint": "Test complaint for automated testing"
        }')
    
    PATIENT_ID=$(echo "$response" | jq -r '.uuid' 2>/dev/null)
    MRN=$(echo "$response" | jq -r '.mrn' 2>/dev/null)
    
    if [ -n "$PATIENT_ID" ] && [ "$PATIENT_ID" != "null" ]; then
        print_success "Patient created successfully"
        print_info "Patient ID: $PATIENT_ID"
        print_info "MRN: $MRN"
        export TEST_PATIENT_ID="$PATIENT_ID"
    else
        print_error "Patient creation failed"
        echo "$response"
        return 1
    fi
    
    # Test patient retrieval
    print_info "Retrieving patient..."
    response=$(curl -s -X GET "${API_URL}/patients/${PATIENT_ID}" \
        -H "Authorization: Bearer $AUTH_TOKEN")
    
    retrieved_id=$(echo "$response" | jq -r '.uuid' 2>/dev/null)
    
    if [ "$retrieved_id" == "$PATIENT_ID" ]; then
        print_success "Patient retrieved successfully"
    else
        print_error "Patient retrieval failed"
        return 1
    fi
}

# Test imaging studies
test_imaging() {
    print_header "Testing Imaging Studies"
    
    if [ -z "$TEST_PATIENT_ID" ]; then
        print_error "No patient ID available for imaging test"
        return 1
    fi
    
    print_info "Creating imaging study..."
    response=$(curl -s -X POST "${API_URL}/imaging?patient_id=${TEST_PATIENT_ID}" \
        -H "Authorization: Bearer $AUTH_TOKEN" \
        -H "Content-Type: application/json" \
        -d '{
            "study_date": "2026-02-13T10:00:00",
            "modality": "xray",
            "body_part": "Chest",
            "description": "Test chest X-ray"
        }')
    
    STUDY_ID=$(echo "$response" | jq -r '.uuid' 2>/dev/null)
    
    if [ -n "$STUDY_ID" ] && [ "$STUDY_ID" != "null" ]; then
        print_success "Imaging study created"
        print_info "Study ID: $STUDY_ID"
        export TEST_STUDY_ID="$STUDY_ID"
    else
        print_error "Imaging study creation failed"
        echo "$response"
        return 1
    fi
}

# Test labs
test_labs() {
    print_header "Testing Laboratory Results"
    
    if [ -z "$TEST_PATIENT_ID" ]; then
        print_error "No patient ID available for lab test"
        return 1
    fi
    
    print_info "Adding lab result..."
    response=$(curl -s -X POST "${API_URL}/labs/labs?patient_id=${TEST_PATIENT_ID}" \
        -H "Authorization: Bearer $AUTH_TOKEN" \
        -H "Content-Type: application/json" \
        -d '{
            "test_name": "WBC",
            "test_value": 10.5,
            "unit": "10^9/L",
            "reference_range_low": 4.0,
            "reference_range_high": 11.0,
            "test_date": "2026-02-13T08:00:00"
        }')
    
    LAB_ID=$(echo "$response" | jq -r '.uuid' 2>/dev/null)
    IS_ABNORMAL=$(echo "$response" | jq -r '.is_abnormal' 2>/dev/null)
    
    if [ -n "$LAB_ID" ] && [ "$LAB_ID" != "null" ]; then
        print_success "Lab result added"
        print_info "Lab ID: $LAB_ID"
        print_info "Abnormal flag: $IS_ABNORMAL"
    else
        print_error "Lab result creation failed"
        echo "$response"
        return 1
    fi
}

# Test vitals
test_vitals() {
    print_header "Testing Vital Signs"
    
    if [ -z "$TEST_PATIENT_ID" ]; then
        print_error "No patient ID available for vitals test"
        return 1
    fi
    
    print_info "Adding vital signs..."
    response=$(curl -s -X POST "${API_URL}/labs/vitals?patient_id=${TEST_PATIENT_ID}" \
        -H "Authorization: Bearer $AUTH_TOKEN" \
        -H "Content-Type: application/json" \
        -d '{
            "temperature": 37.2,
            "temperature_unit": "C",
            "systolic_bp": 120,
            "diastolic_bp": 80,
            "heart_rate": 75,
            "respiratory_rate": 16,
            "oxygen_saturation": 98,
            "weight": 70.0,
            "height": 170,
            "measurement_date": "2026-02-13T09:00:00"
        }')
    
    VITALS_ID=$(echo "$response" | jq -r '.uuid' 2>/dev/null)
    BMI=$(echo "$response" | jq -r '.bmi' 2>/dev/null)
    
    if [ -n "$VITALS_ID" ] && [ "$VITALS_ID" != "null" ]; then
        print_success "Vital signs added"
        print_info "Vitals ID: $VITALS_ID"
        print_info "Calculated BMI: $BMI"
    else
        print_error "Vital signs creation failed"
        echo "$response"
        return 1
    fi
}

# Test clinical notes
test_clinical_notes() {
    print_header "Testing Clinical Notes"
    
    if [ -z "$TEST_PATIENT_ID" ]; then
        print_error "No patient ID available for notes test"
        return 1
    fi
    
    print_info "Creating clinical note..."
    response=$(curl -s -X POST "${API_URL}/notes?patient_id=${TEST_PATIENT_ID}" \
        -H "Authorization: Bearer $AUTH_TOKEN" \
        -H "Content-Type: application/json" \
        -d '{
            "title": "Test Progress Note",
            "content": "This is a test clinical note for automated testing. Patient is doing well with no acute complaints. Plan to continue current management.",
            "note_type": "progress_note",
            "note_date": "2026-02-13T11:00:00"
        }')
    
    NOTE_ID=$(echo "$response" | jq -r '.uuid' 2>/dev/null)
    
    if [ -n "$NOTE_ID" ] && [ "$NOTE_ID" != "null" ]; then
        print_success "Clinical note created"
        print_info "Note ID: $NOTE_ID"
        export TEST_NOTE_ID="$NOTE_ID"
    else
        print_error "Clinical note creation failed"
        echo "$response"
        return 1
    fi
}

# Test audit logs
test_audit_logs() {
    print_header "Testing Audit Logs"
    
    # Need admin/compliance token
    print_info "Logging in as compliance officer..."
    response=$(curl -s -X POST "${API_URL}/auth/login" \
        -H "Content-Type: application/json" \
        -d '{"username":"compliance","password":"demo"}')
    
    COMP_TOKEN=$(echo "$response" | jq -r '.access_token' 2>/dev/null)
    
    if [ -z "$COMP_TOKEN" ] || [ "$COMP_TOKEN" == "null" ]; then
        print_error "Compliance login failed"
        return 1
    fi
    
    print_info "Retrieving audit logs..."
    response=$(curl -s -X GET "${API_URL}/audit?page=1&page_size=10" \
        -H "Authorization: Bearer $COMP_TOKEN")
    
    TOTAL=$(echo "$response" | jq -r '.total' 2>/dev/null)
    
    if [ -n "$TOTAL" ] && [ "$TOTAL" != "null" ]; then
        print_success "Audit logs retrieved"
        print_info "Total audit entries: $TOTAL"
    else
        print_error "Audit logs retrieval failed"
        echo "$response"
        return 1
    fi
}

# Test AI diagnostic (if API keys configured)
test_ai_diagnostic() {
    print_header "Testing AI Diagnostic Features"
    
    if [ -z "$TEST_PATIENT_ID" ]; then
        print_error "No patient ID available for AI test"
        return 1
    fi
    
    # Test entity extraction
    print_info "Testing entity extraction..."
    response=$(curl -s -X POST "${API_URL}/diagnostic/extract-entities" \
        -H "Authorization: Bearer $AUTH_TOKEN" \
        -H "Content-Type: application/json" \
        -d '{
            "text": "Patient presents with fever, cough, and dyspnea. Started on Azithromycin."
        }')
    
    if echo "$response" | jq -e '.entities' > /dev/null 2>&1; then
        print_success "Entity extraction working"
        SYMPTOMS=$(echo "$response" | jq -r '.entities.symptoms[]' 2>/dev/null | tr '\n' ', ')
        MEDS=$(echo "$response" | jq -r '.entities.medications[]' 2>/dev/null | tr '\n' ', ')
        print_info "Extracted symptoms: $SYMPTOMS"
        print_info "Extracted medications: $MEDS"
    else
        print_error "Entity extraction failed (API key may be missing)"
        print_info "This is optional - basic features still work"
    fi
    
    # Test capabilities endpoint
    print_info "Checking AI capabilities..."
    response=$(curl -s -X GET "${API_URL}/diagnostic/capabilities" \
        -H "Authorization: Bearer $AUTH_TOKEN")
    
    if echo "$response" | jq -e '.available_models' > /dev/null 2>&1; then
        print_success "AI capabilities endpoint working"
    else
        print_error "AI capabilities endpoint failed"
        return 1
    fi
}

# Print summary
print_summary() {
    print_header "Test Summary"
    
    TOTAL=$((TESTS_PASSED + TESTS_FAILED))
    
    echo "Total Tests: $TOTAL"
    echo -e "${GREEN}Passed: $TESTS_PASSED${NC}"
    echo -e "${RED}Failed: $TESTS_FAILED${NC}"
    
    if [ $TESTS_FAILED -eq 0 ]; then
        echo ""
        echo -e "${GREEN}All tests passed! ✓${NC}"
        echo ""
        echo "Your ClinSight backend is working perfectly!"
        return 0
    else
        echo ""
        echo -e "${RED}Some tests failed. Please check the errors above.${NC}"
        return 1
    fi
}

# Main test execution
main() {
    clear
    echo "╔════════════════════════════════════════╗"
    echo "║   ClinSight Backend - Test Suite      ║"
    echo "╚════════════════════════════════════════╝"
    echo ""
    print_info "Starting automated tests..."
    print_info "API URL: $API_URL"
    echo ""
    
    # Run tests
    test_health
    test_authentication
    test_patient_creation || true
    test_imaging || true
    test_labs || true
    test_vitals || true
    test_clinical_notes || true
    test_audit_logs || true
    test_ai_diagnostic || true
    
    # Print summary
    print_summary
}

# Run main function
main
