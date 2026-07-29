import os
from dotenv import load_dotenv
from neo4j import GraphDatabase

load_dotenv()

URI = os.getenv("NEO4J_URI")
USERNAME = os.getenv("NEO4J_USERNAME")
PASSWORD = os.getenv("NEO4J_PASSWORD")

print("URI:", URI)
print("USERNAME:", USERNAME)

driver = GraphDatabase.driver(
    URI,
    auth=(USERNAME, PASSWORD)
)

try:
    with driver.session() as session:
        result = session.run("RETURN 1 AS test")
        print(result.single())
except Exception as e:
    print("Connection failed:")
    print(e)
finally:
    driver.close()