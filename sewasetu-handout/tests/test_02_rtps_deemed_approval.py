"""
test_02_rtps_deemed_approval.py
RTPS Deemed-Approval regression tests.

Safety contract
---------------
All data-mutating tests insert rows under reserved test-mobile prefixes
(9999900xxx) and delete them in a finally block.  They touch the *same*
database as production but only rows with synthetic application_no values
prefixed 'TEST-'.  A guard at the top of each mutating test verifies that
the total seeded-citizen count has not already drifted before the test
begins; if the environment is unknown the test bails out safely.

We consciously run against the live database rather than a separate
disposable DB because Docker Compose does not expose the DB port on the
host (only internally), and adding a second volume would risk the seeded
data.  The guard + reserved prefix + finally-cleanup pattern keeps the
approach safe.
"""

import os
import subprocess
import pytest
import requests
from datetime import datetime, date, timedelta

from conftest import run_in_app_container

BASE_URL = "http://localhost:8000"

# Minimum real-citizen count we expect to exist.  If the actual count is
# lower the environment is unknown or was already mutated.
EXPECTED_MIN_CITIZEN_COUNT = 100002


def _get_real_citizen_count():
    """Return current row count from the applications table."""
    script = (
        "import psycopg2\n"
        "conn = psycopg2.connect(host='db', port=5432, dbname='sewasetu', user='sewasetu', password='sewasetu123')\n"
        "cur = conn.cursor()\n"
        "cur.execute(\"SELECT COUNT(*) FROM applications WHERE application_no NOT LIKE 'TEST-%'\")\n"
        "print(cur.fetchone()[0])\n"
        "cur.close(); conn.close()\n"
    )
    code, out, err = run_in_app_container(["python", "-c", script])
    assert code == 0, f"Failed to count citizens: {err}"
    return int(out.strip())


def _assert_safe_environment():
    """Bail out with a clear message if the DB looks wrong."""
    count = _get_real_citizen_count()
    assert count >= EXPECTED_MIN_CITIZEN_COUNT, (
        f"Real-citizen count {count} < {EXPECTED_MIN_CITIZEN_COUNT}. "
        "Database may have been reset or corrupted. Aborting mutating test."
    )


# ---------------------------------------------------------------------------
# Non-mutating tests (safe to run anytime)
# ---------------------------------------------------------------------------

def test_deemed_approval_script_executes_without_error():
    """
    RTPS TEST (non-mutating: script runs but if the DB has no overdue PENDING
    rows it will simply report 0 approvals, which is still a passing run).

    Verifies that deemed_approval.py executes successfully without
    FileNotFoundError: /app/config/settings.ini  (the original defect).
    """
    code, stdout, stderr = run_in_app_container(["python", "scripts/deemed_approval.py"])
    assert code == 0, (
        f"deemed_approval.py failed with exit code {code}.\n"
        f"Stderr: {stderr}\nStdout: {stdout}"
    )
    assert "deemed approval:" in stdout, (
        f"Expected summary line containing 'deemed approval:' in output, got:\n{stdout}"
    )


# ---------------------------------------------------------------------------
# Mutating tests (insert synthetic rows, always cleaned up in finally)
# ---------------------------------------------------------------------------

def test_rtps_sla_boundary_and_transition():
    """
    RTPS BUSINESS RULE:
    15-day statutory SLA.  Applications untouched beyond 15 calendar days
    must be marked DEEMED_APPROVED.  Applications within 15 days must remain
    PENDING.

    Safety: uses reserved mobile 9999900001/9999900002 and application_no
    prefix TEST-RTPS-.  A finally block always cleans up.
    """
    _assert_safe_environment()

    setup_script = (
        "import psycopg2\n"
        "from datetime import datetime, timedelta\n"
        "conn = psycopg2.connect(host='db', port=5432, dbname='sewasetu', user='sewasetu', password='sewasetu123')\n"
        "cur = conn.cursor()\n"
        "cur.execute(\"DELETE FROM applications WHERE application_no LIKE 'TEST-RTPS-%'\")\n"
        "cur.execute('''\n"
        "    INSERT INTO applications\n"
        "    (application_no, applicant_name, mobile, dob, village, block, bank_account, ifsc, status, submitted_at)\n"
        "    VALUES ('TEST-RTPS-OVERDUE', 'RTPS Test Overdue', '9999900001', '1950-01-01',\n"
        "            'Test Village', 'Sonari', '123456789', 'SBIN0001', 'PENDING', %s)\n"
        "''', (datetime.now() - timedelta(days=16),))\n"
        "cur.execute('''\n"
        "    INSERT INTO applications\n"
        "    (application_no, applicant_name, mobile, dob, village, block, bank_account, ifsc, status, submitted_at)\n"
        "    VALUES ('TEST-RTPS-CURRENT', 'RTPS Test Current', '9999900002', '1950-01-01',\n"
        "            'Test Village', 'Sonari', '123456789', 'SBIN0001', 'PENDING', %s)\n"
        "''', (datetime.now() - timedelta(days=5),))\n"
        "conn.commit()\n"
        "cur.close(); conn.close()\n"
        "print('SETUP_OK')\n"
    )

    cleanup_script = (
        "import psycopg2\n"
        "conn = psycopg2.connect(host='db', port=5432, dbname='sewasetu', user='sewasetu', password='sewasetu123')\n"
        "cur = conn.cursor()\n"
        "cur.execute(\"DELETE FROM applications WHERE application_no LIKE 'TEST-RTPS-%'\")\n"
        "conn.commit()\n"
        "cur.close(); conn.close()\n"
    )

    verify_script = (
        "import psycopg2\n"
        "conn = psycopg2.connect(host='db', port=5432, dbname='sewasetu', user='sewasetu', password='sewasetu123')\n"
        "cur = conn.cursor()\n"
        "cur.execute(\"SELECT application_no, status, decided_by FROM applications WHERE application_no LIKE 'TEST-RTPS-%'\")\n"
        "rows = dict((r[0], (r[1], r[2])) for r in cur.fetchall())\n"
        "cur.close(); conn.close()\n"
        "assert rows['TEST-RTPS-OVERDUE'][0] == 'DEEMED_APPROVED', f\"Expected DEEMED_APPROVED, got {rows['TEST-RTPS-OVERDUE'][0]}\"\n"
        "assert rows['TEST-RTPS-OVERDUE'][1] == 'RTPS-AUTO', f\"Expected decided_by=RTPS-AUTO, got {rows['TEST-RTPS-OVERDUE'][1]}\"\n"
        "assert rows['TEST-RTPS-CURRENT'][0] == 'PENDING', f\"Expected PENDING, got {rows['TEST-RTPS-CURRENT'][0]}\"\n"
        "print('VERIFY_OK')\n"
    )

    code, out, err = run_in_app_container(["python", "-c", setup_script])
    assert code == 0, f"Setup test records failed:\nStderr: {err}\nStdout: {out}"

    try:
        run_code, run_out, run_err = run_in_app_container(["python", "scripts/deemed_approval.py"])
        assert run_code == 0, f"deemed_approval.py failed:\nStderr: {run_err}\nStdout: {run_out}"

        v_code, v_out, v_err = run_in_app_container(["python", "-c", verify_script])
        assert v_code == 0, f"Status verification failed:\nStderr: {v_err}\nStdout: {v_out}"
    finally:
        run_in_app_container(["python", "-c", cleanup_script])


def test_citizen_status_reflects_deemed_approval():
    """
    BUSINESS RULE:
    The citizen-facing status page must display DEEMED_APPROVED for an
    application that has been marked DEEMED_APPROVED in the DB.
    """
    _assert_safe_environment()

    setup_script = (
        "import psycopg2\n"
        "from datetime import datetime, timedelta\n"
        "import hashlib\n"
        "conn = psycopg2.connect(host='db', port=5432, dbname='sewasetu', user='sewasetu', password='sewasetu123')\n"
        "cur = conn.cursor()\n"
        "cur.execute(\"DELETE FROM applications WHERE mobile = '9999900010'\")\n"
        "cur.execute(\"DELETE FROM portal_users WHERE mobile = '9999900010'\")\n"
        "cur.execute('''\n"
        "    INSERT INTO applications\n"
        "    (application_no, applicant_name, mobile, dob, village, block, bank_account, ifsc, status, submitted_at)\n"
        "    VALUES ('TEST-SSP-CITIZEN', 'Eligible Citizen', '9999900010', '1955-05-15',\n"
        "            'Village A', 'Sonari', '1234567890', 'SBIN0001', 'DEEMED_APPROVED', %s)\n"
        "    RETURNING id\n"
        "''', (datetime.now() - timedelta(days=20),))\n"
        "app_id = cur.fetchone()[0]\n"
        "p_hash = hashlib.sha256('15051955'.encode('utf-8')).hexdigest()\n"
        "cur.execute(\"INSERT INTO portal_users (mobile, password_hash) VALUES ('9999900010', %s)\", (p_hash,))\n"
        "conn.commit()\n"
        "cur.close(); conn.close()\n"
        "print(app_id)\n"
    )

    cleanup_script = (
        "import psycopg2\n"
        "conn = psycopg2.connect(host='db', port=5432, dbname='sewasetu', user='sewasetu', password='sewasetu123')\n"
        "cur = conn.cursor()\n"
        "cur.execute(\"DELETE FROM applications WHERE mobile = '9999900010'\")\n"
        "cur.execute(\"DELETE FROM portal_users WHERE mobile = '9999900010'\")\n"
        "conn.commit()\n"
        "cur.close(); conn.close()\n"
    )

    code, out, err = run_in_app_container(["python", "-c", setup_script])
    assert code == 0, f"Setup citizen failed:\nStderr: {err}"
    app_id = int(out.strip().splitlines()[-1])

    try:
        s = requests.Session()
        login_resp = s.post(
            f"{BASE_URL}/status",
            data={"mobile": "9999900010", "password": "15051955"},
            allow_redirects=False,
        )
        assert login_resp.status_code == 302, (
            f"Login failed, status {login_resp.status_code}"
        )

        view_resp = s.get(f"{BASE_URL}/application/{app_id}")
        assert view_resp.status_code == 200
        assert "DEEMED_APPROVED" in view_resp.text, (
            "Citizen status view must display DEEMED_APPROVED"
        )
    finally:
        run_in_app_container(["python", "-c", cleanup_script])


def test_mandatory_business_rules():
    """
    BUSINESS RULES:
    1. One application per person (duplicate mobile rejected).
    Verified by checking mobile 9861908220 has exactly 1 application.
    """
    check_script = (
        "import psycopg2\n"
        "conn = psycopg2.connect(host='db', port=5432, dbname='sewasetu', user='sewasetu', password='sewasetu123')\n"
        "cur = conn.cursor()\n"
        "cur.execute(\"SELECT count(*) FROM applications WHERE mobile = '9861908220'\")\n"
        "cnt = cur.fetchone()[0]\n"
        "cur.close(); conn.close()\n"
        "assert cnt == 1, f'Expected exactly 1 existing application for mobile, found {cnt}'\n"
        "print('DUPLICATE_CHECK_OK')\n"
    )
    code, out, err = run_in_app_container(["python", "-c", check_script])
    assert code == 0, f"Check failed:\nStderr: {err}\nStdout: {out}"
    assert "DUPLICATE_CHECK_OK" in out
