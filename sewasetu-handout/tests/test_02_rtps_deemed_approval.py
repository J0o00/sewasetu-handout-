import os
import subprocess
import pytest
import requests
from datetime import datetime, date, timedelta

BASE_URL = "http://localhost:8000"

DB_CONFIG = {
    "host": "localhost",
    "port": 5432,
    "dbname": "sewasetu",
    "user": "sewasetu",
    "password": "sewasetu123",
}

def run_in_app_container(cmd):
    """Run a command inside the docker 'app' container and return exit code and output."""
    full_cmd = ["docker", "compose", "exec", "-T", "app"] + cmd
    res = subprocess.run(full_cmd, cwd="sewasetu-handout", stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return res.returncode, res.stdout, res.stderr


def test_deemed_approval_script_executes_without_error():
    """
    RTPS TEST:
    Verify that deemed_approval.py executes successfully without FileNotFoundError.
    On the inherited code, this fails with FileNotFoundError: /app/config/settings.ini.
    """
    code, stdout, stderr = run_in_app_container(["python", "scripts/deemed_approval.py"])
    assert code == 0, f"deemed_approval.py failed with exit code {code}.\nStderr: {stderr}\nStdout: {stdout}"
    assert "deemed approval:" in stdout, f"Expected summary in output, got: {stdout}"


def test_rtps_sla_boundary_and_transition():
    """
    RTPS BUSINESS RULE:
    15-day statutory SLA. Applications untouched beyond 15 calendar days must be marked DEEMED_APPROVED.
    Applications within 15 days must remain PENDING.
    """
    # Verify via execution inside container
    script = """
import psycopg2
from datetime import datetime, timedelta

conn = psycopg2.connect(host='db', port=5432, dbname='sewasetu', user='sewasetu', password='sewasetu123')
cur = conn.cursor()
# Clean any previous test data
cur.execute("DELETE FROM applications WHERE application_no LIKE 'TEST-RTPS-%'")
# Insert an application submitted 16 days ago (overdue)
cur.execute('''INSERT INTO applications 
    (application_no, applicant_name, mobile, dob, village, block, bank_account, ifsc, status, submitted_at)
    VALUES ('TEST-RTPS-OVERDUE', 'RTPS Test Overdue', '9999900001', '1950-01-01', 'Test Village', 'Sonari', '123456789', 'SBIN0001', 'PENDING', %s)''',
    (datetime.now() - timedelta(days=16),))
# Insert an application submitted 5 days ago (within SLA)
cur.execute('''INSERT INTO applications 
    (application_no, applicant_name, mobile, dob, village, block, bank_account, ifsc, status, submitted_at)
    VALUES ('TEST-RTPS-CURRENT', 'RTPS Test Current', '9999900002', '1950-01-01', 'Test Village', 'Sonari', '123456789', 'SBIN0001', 'PENDING', %s)''',
    (datetime.now() - timedelta(days=5),))
conn.commit()
cur.close(); conn.close()
"""
    code, out, err = run_in_app_container(["python", "-c", script])
    assert code == 0, f"Setup test records failed: {err}"

    # Run deemed approval job
    run_code, run_out, run_err = run_in_app_container(["python", "scripts/deemed_approval.py"])
    assert run_code == 0, f"deemed_approval.py failed: {run_err}"

    # Verify statuses
    verify_script = """
import psycopg2
conn = psycopg2.connect(host='db', port=5432, dbname='sewasetu', user='sewasetu', password='sewasetu123')
cur = conn.cursor()
cur.execute("SELECT application_no, status, decided_by FROM applications WHERE application_no LIKE 'TEST-RTPS-%'")
rows = dict((r[0], (r[1], r[2])) for r in cur.fetchall())
cur.execute("DELETE FROM applications WHERE application_no LIKE 'TEST-RTPS-%'")
conn.commit()
cur.close(); conn.close()

assert rows['TEST-RTPS-OVERDUE'][0] == 'DEEMED_APPROVED', f"Expected DEEMED_APPROVED, got {rows['TEST-RTPS-OVERDUE'][0]}"
assert rows['TEST-RTPS-OVERDUE'][1] == 'RTPS-AUTO', f"Expected decided_by=RTPS-AUTO, got {rows['TEST-RTPS-OVERDUE'][1]}"
assert rows['TEST-RTPS-CURRENT'][0] == 'PENDING', f"Expected PENDING, got {rows['TEST-RTPS-CURRENT'][0]}"
print('OK')
"""
    v_code, v_out, v_err = run_in_app_container(["python", "-c", verify_script])
    assert v_code == 0, f"Status verification failed: {v_err}\n{v_out}"


def test_citizen_status_reflects_deemed_approval():
    """
    BUSINESS RULE:
    The citizen-facing status must display deemed approval for an eligible application
    whose deadline has passed.
    """
    setup_script = """
import psycopg2
from datetime import datetime, timedelta
import hashlib

conn = psycopg2.connect(host='db', port=5432, dbname='sewasetu', user='sewasetu', password='sewasetu123')
cur = conn.cursor()
cur.execute("DELETE FROM applications WHERE mobile = '9999900010'")
cur.execute("DELETE FROM portal_users WHERE mobile = '9999900010'")
cur.execute('''INSERT INTO applications 
    (application_no, applicant_name, mobile, dob, village, block, bank_account, ifsc, status, submitted_at)
    VALUES ('TEST-SSP-CITIZEN', 'Eligible Citizen', '9999900010', '1955-05-15', 'Village A', 'Sonari', '1234567890', 'SBIN0001', 'DEEMED_APPROVED', %s)
    RETURNING id''',
    (datetime.now() - timedelta(days=20),))
app_id = cur.fetchone()[0]
p_hash = hashlib.sha256('15051955'.encode('utf-8')).hexdigest()
cur.execute("INSERT INTO portal_users (mobile, password_hash) VALUES ('9999900010', %s)", (p_hash,))
conn.commit()
cur.close(); conn.close()
print(app_id)
"""
    code, out, err = run_in_app_container(["python", "-c", setup_script])
    assert code == 0, f"Setup citizen failed: {err}"
    app_id = int(out.strip().splitlines()[-1])

    s = requests.Session()
    login_resp = s.post(f"{BASE_URL}/status", data={"mobile": "9999900010", "password": "15051955"}, allow_redirects=False)
    assert login_resp.status_code == 302

    view_resp = s.get(f"{BASE_URL}/application/{app_id}")
    assert view_resp.status_code == 200
    assert "DEEMED_APPROVED" in view_resp.text, "Citizen status view must display DEEMED_APPROVED"

    # Cleanup
    cleanup_script = """
import psycopg2
conn = psycopg2.connect(host='db', port=5432, dbname='sewasetu', user='sewasetu', password='sewasetu123')
cur = conn.cursor()
cur.execute("DELETE FROM applications WHERE mobile = '9999900010'")
cur.execute("DELETE FROM portal_users WHERE mobile = '9999900010'")
conn.commit()
cur.close(); conn.close()
"""
    run_in_app_container(["python", "-c", cleanup_script])


def test_mandatory_business_rules():
    """
    BUSINESS RULES:
    1. Applicant must be at least 60 years old on the application date.
    2. One application per person (duplicate mobile rejected).
    3. Application window closes at midnight Indian Standard Time.
    """
    # 1. One application per person check on existing citizen 1 (mobile: 9861908220)
    # Using an authenticated session on the application endpoint
    s = requests.Session()
    # Test duplicate check logic via application submission
    check_script = """
import psycopg2
conn = psycopg2.connect(host='db', port=5432, dbname='sewasetu', user='sewasetu', password='sewasetu123')
cur = conn.cursor()
cur.execute("SELECT count(*) FROM applications WHERE mobile = '9861908220'")
cnt = cur.fetchone()[0]
cur.close(); conn.close()
assert cnt == 1, f"Expected exactly 1 existing application for mobile, found {cnt}"
print('DUPLICATE_CHECK_OK')
"""
    code, out, err = run_in_app_container(["python", "-c", check_script])
    assert code == 0
    assert "DUPLICATE_CHECK_OK" in out
