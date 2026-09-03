# Student 3 Database Service

The database container stores SQLite at `/data/pharmacy.db`. The image starts
by running `python init_db.py`; it creates the schema and seeds the database
only when the file has no medicine records. Mount `/data` as a Docker volume
to preserve data across container replacement.
