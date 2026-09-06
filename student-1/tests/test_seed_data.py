from database.init_database import TABLES, build, healthCheck


def test_seed_data_has_ten_records_in_every_table(tmp_path):
    connection = build(tmp_path / "patients.db")
    try:
        counts = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in TABLES
        }
    finally:
        connection.close()

    assert counts == {table: 10 for table in TABLES}


def test_seed_data_preserves_expected_patient_statuses(tmp_path):
    connection = build(tmp_path / "patients.db")
    try:
        statuses = {
            status: count
            for status, count in connection.execute(
                "SELECT patient_status, COUNT(*) FROM patients GROUP BY patient_status"
            ).fetchall()
        }
    finally:
        connection.close()

    assert statuses == {
        "Active": 6,
        "Inactive": 2,
        "Transferred": 1,
        "Deceased": 1,
    }


def test_seed_database_passes_health_check(tmp_path):
    connection = build(tmp_path / "patients.db")
    try:
        assert healthCheck(connection) is True
    finally:
        connection.close()
