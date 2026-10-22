from database.init_database import TABLES, build, healthCheck


def test_seed_data_has_thirty_records_in_every_table(tmp_path):
    connection = build(tmp_path / "patients.db")
    try:
        counts = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in TABLES
        }
    finally:
        connection.close()

    assert counts == {table: 30 for table in TABLES}


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
        "Active": 20,
        "Inactive": 5,
        "Transferred": 3,
        "Deceased": 2,
    }


def test_seed_database_passes_health_check(tmp_path):
    connection = build(tmp_path / "patients.db")
    try:
        assert healthCheck(connection) is True
    finally:
        connection.close()
