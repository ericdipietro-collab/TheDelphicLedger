import sqlite3

conn = sqlite3.connect("data/demo.db")
c = conn.cursor()

c.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = set(r[0] for r in c.fetchall())
print("Tables:", sorted(tables))

c.execute("PRAGMA table_info(unresolved_queue)")
print("\nunresolved_queue cols:", [r[1] for r in c.fetchall()])

c.execute("SELECT * FROM unresolved_queue LIMIT 5")
print("unresolved_queue rows:")
for r in c.fetchall():
    print(" ", r)

c.execute("PRAGMA table_info(mapping_decisions)")
print("\nmapping_decisions cols:", [r[1] for r in c.fetchall()])

c.execute("SELECT raw_value, accepted_by, method FROM mapping_decisions LIMIT 10")
print("mapping_decisions sample:")
for r in c.fetchall():
    print(" ", r)

c.execute("PRAGMA table_info(position_snapshots)")
print("\nposition_snapshots cols:", [r[1] for r in c.fetchall()])

c.execute("SELECT * FROM position_snapshots LIMIT 5")
print("position_snapshots rows:")
for r in c.fetchall():
    print(" ", r)

conn.close()
