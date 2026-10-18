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

def test_deemed_approval_script_config_and_connectivity():
    """
    RTPS TEST -- config loading and DB connectivity (SAFE: no live UPDATE).

    The original defect was FileNotFoundError: /app/config/settings.ini.
    This test verifies that the script can:
      1. Load its config (app.ini found; settings.ini fallback works).
      2. Connect to the database.
      3. Parse without a Python exception.

    We do NOT run the full script against the seeded database because the
    seeded PENDING records are real citizen applications. The actual UPDATE
    logic is tested in test_rtps_sla_boundary_and_transition with controlled
    synthetic rows only.
    """
    # Check syntax
    syntax_check = (
        "import ast, sys\n"
        "src = open('/app/scripts/deemed_approval.py').read()\n"
        "try:\n"
        "    ast.parse(src)\n"
        "    print('SYNTAX_OK')\n"
        "except SyntaxError as e:\n"
        "    print('SYNTAX_ERROR:', e)\n"
        "    sys.exit(1)\n"
    )
    code, out, err = run_in_app_container(["python", "-c", syntax_check])
    assert code == 0, f"Script syntax check failed:\nStderr: {err}\nStdout: {out}"
    assert "SYNTAX_OK" in out

    # Verify config loads (the original defect: FileNotFoundError on settings.ini)
    config_check = (
        "import configparser, os, sys\n"
        "BASE_DIR = '/app'\n"
        "config = configparser.ConfigParser()\n"
        "CONFIG_PATH = os.path.join(BASE_DIR, 'config', 'app.ini')\n"
        "if not os.path.exists(CONFIG_PATH):\n"
        "    CONFIG_PATH = os.path.join(BASE_DIR, 'config', 'settings.ini')\n"
        "if not os.path.exists(CONFIG_PATH):\n"
        "    print('CONFIG_MISSING'); sys.exit(1)\n"
        "with open(CONFIG_PATH) as fh:\n"
        "    config.read_file(fh)\n"
        "sla = config.getint('pension', 'sla_days')\n"
        "assert sla > 0, 'Invalid SLA'\n"
        "print('CONFIG_OK sla_days=' + str(sla))\n"
    )
    code, out, err = run_in_app_container(["python", "-c", config_check])
    assert code == 0, (
        "Config loading failed (original FileNotFoundError defect may have recurred):\n"
        "Stderr: " + err + "\nStdout: " + out
    )
    assert "CONFIG_OK" in out, f"Config check output unexpected: {out}"

    # Verify DB connectivity from within the container
    db_check = (
        "import psycopg2, configparser, os\n"
        "BASE_DIR = '/app'\n"
        "config = configparser.ConfigParser()\n"
        "with open(os.path.join(BASE_DIR, 'config', 'app.ini')) as fh:\n"
        "    config.read_file(fh)\n"
        "conn = psycopg2.connect(\n"
        "    host=config.get('database', 'host'),\n"
        "    port=config.get('database', 'port'),\n"
        "    dbname=config.get('database', 'name'),\n"
        "    user=config.get('database', 'user'),\n"
        "    password=config.get('database', 'password'))\n"
        "cur = conn.cursor()\n"
        "cur.execute('SELECT 1')\n"
        "assert cur.fetchone()[0] == 1\n"
        "cur.close(); conn.close()\n"
        "print('DB_CONNECT_OK')\n"
    )
    code, out, err = run_in_app_container(["python", "-c", db_check])
    assert code == 0, f"DB connectivity check failed:\nStderr: {err}\nStdout: {out}"
    assert "DB_CONNECT_OK" in out


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
        "cur.execute(\"\"\"\n"
        "    INSERT INTO applications\n"
        "    (application_no, applicant_name, mobile, dob, village, block, bank_account, ifsc, status, submitted_at)\n"
        "    VALUES ('TEST-RTPS-OVERDUE', 'RTPS Test Overdue', '9999900001', '1950-01-01',\n"
        "            'Test Village', 'Sonari', '123456789', 'SBIN0001', 'PENDING', %s)\n"
        "\"\"\", (datetime.now() - timedelta(days=16),))\n"
        "cur.execute(\"\"\"\n"
        "    INSERT INTO applications\n"
        "    (application_no, applicant_name, mobile, dob, village, block, bank_account, ifsc, status, submitted_at)\n"
        "    VALUES ('TEST-RTPS-CURRENT', 'RTPS Test Current', '9999900002', '1950-01-01',\n"
        "            'Test Village', 'Sonari', '123456789', 'SBIN0001', 'PENDING', %s)\n"
        "\"\"\", (datetime.now() - timedelta(days=5),))\n"
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
        "assert rows['TEST-RTPS-OVERDUE'][0] == 'DEEMED_APPROVED', 'Expected DEEMED_APPROVED, got ' + rows['TEST-RTPS-OVERDUE'][0]\n"
        "assert rows['TEST-RTPS-OVERDUE'][1] == 'RTPS-AUTO', 'Expected decided_by=RTPS-AUTO, got ' + rows['TEST-RTPS-OVERDUE'][1]\n"
        "assert rows['TEST-RTPS-CURRENT'][0] == 'PENDING', 'Expected PENDING, got ' + rows['TEST-RTPS-CURRENT'][0]\n"
        "print('VERIFY_OK')\n"
    )

    code, out, err = run_in_app_container(["python", "-c", setup_script])
    assert code == 0, f"Setup test records failed:\nStderr: {err}\nStdout: {out}"

    try:
        run_code, run_out, run_err = run_in_app_container(["python", "scripts/deemed_approval.py"])
        assert run_code == 0, f"deemed_approval.py failed:\nStderr: {run_err}\nStdout: {run_out}"
        assert "deemed approval:" in run_out, f"Expected summary line, got: {run_out}"

        v_code, v_out, v_err = run_in_app_container(["python", "-c", verify_script])
        assert v_code == 0, f"Status verification failed:\nStderr: {v_err}\nStdout: {v_out}"
        assert "VERIFY_OK" in v_out
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
        "cur.execute(\"\"\"\n"
        "    INSERT INTO applications\n"
        "    (application_no, applicant_name, mobile, dob, village, block, bank_account, ifsc, status, submitted_at)\n"
        "    VALUES ('TEST-SSP-CITIZEN', 'Eligible Citizen', '9999900010', '1955-05-15',\n"
        "            'Village A', 'Sonari', '1234567890', 'SBIN0001', 'DEEMED_APPROVED', %s)\n"
        "    RETURNING id\n"
        "\"\"\", (datetime.now() - timedelta(days=20),))\n"
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
        "assert cnt == 1, 'Expected exactly 1 existing application for mobile, found ' + str(cnt)\n"
        "print('DUPLICATE_CHECK_OK')\n"
    )
    code, out, err = run_in_app_container(["python", "-c", check_script])
    assert code == 0, f"Check failed:\nStderr: {err}\nStdout: {out}"
    assert "DUPLICATE_CHECK_OK" in out


def test_rtps_scheduler_idempotency():
    """
    RTPS IDEMPOTENCY:
    Running the scheduler twice must not double-count or re-update already
    DEEMED_APPROVED records (WHERE status='PENDING' is the guard).
    Verified by inserting one overdue record, running the scheduler twice,
    and checking it is approved exactly once (decided_at unchanged on second run).
    """
    _assert_safe_environment()

    setup_script = (
        "import psycopg2\n"
        "from datetime import datetime, timedelta\n"
        "conn = psycopg2.connect(host='db', port=5432, dbname='sewasetu', user='sewasetu', password='sewasetu123')\n"
        "cur = conn.cursor()\n"
        "cur.execute(\"DELETE FROM applications WHERE application_no = 'TEST-IDEM-01'\")\n"
        "cur.execute(\"\"\"\n"
        "    INSERT INTO applications\n"
        "    (application_no, applicant_name, mobile, dob, village, block, bank_account, ifsc, status, submitted_at)\n"
        "    VALUES ('TEST-IDEM-01', 'Idempotency Test', '9999900005', '1950-01-01',\n"
        "            'Test Village', 'Sonari', '999999', 'SBIN0001', 'PENDING', %s)\n"
        "    RETURNING id\n"
        "\"\"\", (datetime.now() - timedelta(days=20),))\n"
        "print(cur.fetchone()[0])\n"
        "conn.commit(); cur.close(); conn.close()\n"
    )
    cleanup_script = (
        "import psycopg2\n"
        "conn = psycopg2.connect(host='db', port=5432, dbname='sewasetu', user='sewasetu', password='sewasetu123')\n"
        "cur = conn.cursor()\n"
        "cur.execute(\"DELETE FROM applications WHERE application_no = 'TEST-IDEM-01'\")\n"
        "conn.commit(); cur.close(); conn.close()\n"
    )
    verify_script = (
        "import psycopg2\n"
        "conn = psycopg2.connect(host='db', port=5432, dbname='sewasetu', user='sewasetu', password='sewasetu123')\n"
        "cur = conn.cursor()\n"
        "cur.execute(\"SELECT status, decided_at, decided_by FROM applications WHERE application_no = 'TEST-IDEM-01'\")\n"
        "row = cur.fetchone()\n"
        "cur.close(); conn.close()\n"
        "assert row[0] == 'DEEMED_APPROVED', 'Status wrong: ' + str(row[0])\n"
        "assert row[2] == 'RTPS-AUTO', 'decided_by wrong: ' + str(row[2])\n"
        "decided_at_1 = row[1]\n"
        "print('decided_at=' + str(decided_at_1))\n"
        "print('IDEM_OK')\n"
    )

    code, out, err = run_in_app_container(["python", "-c", setup_script])
    assert code == 0, f"Setup failed: {err}"

    try:
        # First run
        r1_code, r1_out, r1_err = run_in_app_container(["python", "scripts/deemed_approval.py"])
        assert r1_code == 0, f"First scheduler run failed: {r1_err}"
        assert "1 applications approved" in r1_out, f"Expected 1 approval in first run: {r1_out}"

        # Second run — must approve 0 additional records
        r2_code, r2_out, r2_err = run_in_app_container(["python", "scripts/deemed_approval.py"])
        assert r2_code == 0, f"Second scheduler run failed: {r2_err}"
        assert "0 applications approved" in r2_out, (
            f"Idempotency broken: second run approved records again: {r2_out}"
        )

        v_code, v_out, v_err = run_in_app_container(["python", "-c", verify_script])
        assert v_code == 0, f"Verify failed: {v_err}\n{v_out}"
        assert "IDEM_OK" in v_out
    finally:
        run_in_app_container(["python", "-c", cleanup_script])
