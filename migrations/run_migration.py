"""Run SQL migration against Neon PostgreSQL."""
import sys
import psycopg2

def run_migration(conn_string: str, sql_file: str):
    with open(sql_file, 'r') as f:
        sql = f.read()

    conn = psycopg2.connect(conn_string)
    conn.autocommit = True
    cur = conn.cursor()

    try:
        cur.execute(sql)
        print("Migration completed successfully.")

        # Verify: list all tables
        cur.execute("""
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = 'public' ORDER BY table_name;
        """)
        tables = [row[0] for row in cur.fetchall()]
        print(f"\nCreated {len(tables)} tables:")
        for t in tables:
            print(f"  - {t}")
    except Exception as e:
        print(f"Migration failed: {e}")
        sys.exit(1)
    finally:
        cur.close()
        conn.close()

if __name__ == "__main__":
    conn_string = sys.argv[1] if len(sys.argv) > 1 else None
    if not conn_string:
        print("Usage: python run_migration.py <connection_string>")
        sys.exit(1)
    run_migration(conn_string, "backend/migrations/001_initial_schema.sql")
