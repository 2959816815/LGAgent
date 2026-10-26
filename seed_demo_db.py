import sqlite3
from pathlib import Path

path = Path("data/agent.db")
path.parent.mkdir(exist_ok=True)
connection = sqlite3.connect(path)
connection.execute("CREATE TABLE IF NOT EXISTS notes(id INTEGER PRIMARY KEY, title TEXT, body TEXT)")
connection.execute("INSERT OR IGNORE INTO notes(id,title,body) VALUES(1,'demo','hello mcp')")
connection.commit()
connection.close()
print(path.resolve())
