# Creates and populates the Patient & Admissions Management Database

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_DB = Path(__file__).resolve().parent / "patients.db"
SCHEMA_FILE = HERE / "schema.sql"
SEED_FILE = HERE / "seed_data.sql"

