import pymysql

conn = pymysql.connect(
    host="localhost",
    user="root",
    password="",          # XAMPP初期は空が多い
    database="inventory_db",
    charset="utf8mb4"
)

with conn.cursor() as cur:
    cur.execute("SELECT category_id, category_name FROM categories ORDER BY category_id;")
    rows = cur.fetchall()
    print(rows)

conn.close()
