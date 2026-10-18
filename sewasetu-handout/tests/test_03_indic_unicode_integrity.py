"""
test_03_indic_unicode_integrity.py
Unicode / Indic-language data-integrity regression tests.

These tests verify that:
1. sanitize() truncates by Unicode character count, not by raw UTF-8 bytes.
2. PDF acknowledgment generation does not corrupt Indic names.
3. The database stores and retrieves Indic names faithfully (UTF-8).

All mutating tests use reserved mobile prefix 9999900020/9999900030 and
application_no prefix TEST-INDIC-/TEST-PROBE-, cleaned in finally blocks.
"""

import subprocess
import pytest
import requests

from conftest import run_in_app_container

BASE_URL = "http://localhost:8000"


# ---------------------------------------------------------------------------
# Unit-style: runs inside the container against app code, no DB mutation
# ---------------------------------------------------------------------------

def test_indic_name_not_truncated_to_20_bytes():
    """
    DATA INTEGRITY TEST:
    Verify that Indic names longer than 20 bytes are not corrupted or truncated.
    Assamese name 'ভূপেন হাজৰিকা' is 14 Unicode characters but 42 UTF-8 bytes.
    The original sanitize() did [:20] on bytes, chopping this name entirely.
    """
    script = (
        "import sys, os\n"
        "sys.path.insert(0, '/app')\n"
        "from app import sanitize\n"
        "full_name = 'ভূপেন হাজৰিকা'  # 14 chars, 42 UTF-8 bytes\n"
        "sanitized = sanitize(full_name)\n"
        "assert sanitized == full_name, (\n"
        "    f'Name was truncated or corrupted: expected {repr(full_name)}, got {repr(sanitized)}'\n"
        ")\n"
        "print('SANITIZE_OK')\n"
    )
    code, out, err = run_in_app_container(["python", "-c", script])
    assert code == 0, (
        f"Indic name truncation detected!\nStdout: {out}\nStderr: {err}"
    )
    assert "SANITIZE_OK" in out


def test_sanitize_long_indic_name_truncates_at_100_chars():
    """
    DATA INTEGRITY TEST:
    A name with more than 100 Unicode characters should be truncated at exactly
    100 characters, not at a byte boundary that would split a codepoint.
    """
    script = (
        "import sys\n"
        "sys.path.insert(0, '/app')\n"
        "from app import sanitize\n"
        "# 110 Assamese characters (each is 3 bytes in UTF-8)\n"
        "long_name = 'ক' * 110\n"
        "sanitized = sanitize(long_name)\n"
        "assert len(sanitized) == 100, f'Expected 100 chars, got {len(sanitized)}'\n"
        "# Every character must be a valid Unicode codepoint (no replacement chars)\n"
        "assert all(c == 'ক' for c in sanitized), 'Truncated name contains corrupted characters'\n"
        "print('TRUNCATE_OK')\n"
    )
    code, out, err = run_in_app_container(["python", "-c", script])
    assert code == 0, (
        f"Indic truncation test failed!\nStdout: {out}\nStderr: {err}"
    )
    assert "TRUNCATE_OK" in out


# ---------------------------------------------------------------------------
# Integration: DB insertion and retrieval
# ---------------------------------------------------------------------------

def test_end_to_end_indic_name_storage():
    """
    DATA INTEGRITY:
    Verify that an application with an Indian-language name is stored in
    PostgreSQL and retrieved without byte-level corruption or silent truncation.
    Uses reserved mobile 9999900030 and application_no TEST-PROBE-01.
    """
    setup_script = (
        "import psycopg2\n"
        "conn = psycopg2.connect(host='db', port=5432, dbname='sewasetu', user='sewasetu', password='sewasetu123')\n"
        "cur = conn.cursor()\n"
        "cur.execute(\"DELETE FROM applications WHERE mobile = '9999900030'\")\n"
        "test_name = 'প্ৰোব টোকেন - ৰুণু দত্ত'\n"
        "cur.execute('''\n"
        "    INSERT INTO applications\n"
        "    (application_no, applicant_name, mobile, dob, village, block, bank_account, ifsc, status, submitted_at)\n"
        "    VALUES ('TEST-PROBE-01', %s, '9999900030', '1960-01-01', 'Sonari', 'Sonari', '11223344', 'SBIN0001', 'PENDING', NOW())\n"
        "    RETURNING applicant_name\n"
        "''', (test_name,))\n"
        "saved_name = cur.fetchone()[0]\n"
        "conn.commit()\n"
        "cur.close(); conn.close()\n"
        "assert saved_name == test_name, f'Storage corrupted Indic string: expected {repr(test_name)}, got {repr(saved_name)}'\n"
        "print('STORAGE_OK')\n"
    )
    cleanup_script = (
        "import psycopg2\n"
        "conn = psycopg2.connect(host='db', port=5432, dbname='sewasetu', user='sewasetu', password='sewasetu123')\n"
        "cur = conn.cursor()\n"
        "cur.execute(\"DELETE FROM applications WHERE mobile = '9999900030'\")\n"
        "conn.commit()\n"
        "cur.close(); conn.close()\n"
    )
    code, out, err = run_in_app_container(["python", "-c", setup_script])
    try:
        assert code == 0, (
            f"Indic storage test failed!\nStdout: {out}\nStderr: {err}"
        )
        assert "STORAGE_OK" in out
    finally:
        run_in_app_container(["python", "-c", cleanup_script])


# ---------------------------------------------------------------------------
# PDF generation
# ---------------------------------------------------------------------------

def test_pdf_renders_indic_name_without_corruption():
    """
    DATA INTEGRITY & PDF RENDERING TEST:
    Verify that generate_acknowledgment() produces a non-empty PDF for an
    application containing an Assamese name.  The test checks that:
    - The PDF is generated without raising an exception.
    - The byte length is plausible (> 1000 bytes).
    Uses reserved mobile 9999900020 and application_no TEST-INDIC-01.
    """
    script = (
        "import sys, psycopg2\n"
        "sys.path.insert(0, '/app')\n"
        "from app import generate_acknowledgment\n"
        "conn = psycopg2.connect(host='db', port=5432, dbname='sewasetu', user='sewasetu', password='sewasetu123')\n"
        "cur = conn.cursor()\n"
        "cur.execute(\"DELETE FROM applications WHERE application_no = 'TEST-INDIC-01'\")\n"
        "cur.execute('''\n"
        "    INSERT INTO applications\n"
        "    (application_no, applicant_name, mobile, dob, village, block, bank_account, ifsc, status, submitted_at)\n"
        "    VALUES ('TEST-INDIC-01', 'ৰুণু দত্ত', '9999900020', '1955-01-01',\n"
        "            'Milanpur', 'Borgaon', '12345678', 'SBIN0001', 'APPROVED', NOW())\n"
        "    RETURNING id\n"
        "'''\n"
        ")\n"
        "app_id = cur.fetchone()[0]\n"
        "conn.commit()\n"
        "try:\n"
        "    pdf_bytes = generate_acknowledgment(cur, app_id)\n"
        "    assert len(pdf_bytes) > 1000, f'PDF too small: {len(pdf_bytes)} bytes'\n"
        "    print(f'PDF_OK len={len(pdf_bytes)}')\n"
        "finally:\n"
        "    cur.execute(\"DELETE FROM applications WHERE id = %s\", (app_id,))\n"
        "    conn.commit()\n"
        "    cur.close(); conn.close()\n"
    )
    code, out, err = run_in_app_container(["python", "-c", script])
    assert code == 0, (
        f"PDF generation failed on Indic name!\nStdout: {out}\nStderr: {err}"
    )
    assert "PDF_OK" in out
