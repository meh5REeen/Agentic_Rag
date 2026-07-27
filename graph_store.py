import os
from dotenv import load_dotenv
from neo4j import GraphDatabase

load_dotenv()

NEO4J_URI = os.getenv("NEO4J_URI")
NEO4J_USERNAME = os.getenv("NEO4J_USERNAME")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")


def clean_metadata(metadata):
    """
    Convert metadata into Neo4j-safe values.

    Neo4j supports:
    - str
    - int
    - float
    - bool
    - None
    - lists of the above

    Everything else is converted to a JSON string.
    """
    cleaned = {}

    for key, value in metadata.items():

        if isinstance(value, (str, int, float, bool)) or value is None:
            cleaned[key] = value

        elif isinstance(value, list):
            if all(isinstance(x, (str, int, float, bool)) for x in value):
                cleaned[key] = value
            else:
                cleaned[key] = json.dumps(value)

        else:
            cleaned[key] = json.dumps(value)

    return cleaned

class Neo4jStore:
    def __init__(self):
        if not (NEO4J_URI and NEO4J_USERNAME and NEO4J_PASSWORD):
            raise RuntimeError("Neo4j environment variables are not configured")
        self.driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USERNAME, NEO4J_PASSWORD))

    def close(self):
        if getattr(self, "driver", None):
            self.driver.close()

    def ensure_constraints(self):
        with self.driver.session() as session:
            session.run("CREATE CONSTRAINT document_id_unique IF NOT EXISTS FOR (d:Document) REQUIRE d.id IS UNIQUE")

    def create_document(self, doc_id, title, filename, file_path, source_url=None, metadata=None):
        metadata = clean_metadata(metadata or {})
        with self.driver.session() as session:
            result = session.run(
                """
               MERGE (d:Document {id: $doc_id})

                SET d.title = $title,
                    d.filename = $filename,
                    d.file_path = $file_path,
                    d.source_url = $source_url

                SET d += $metadata

                RETURN d
                """,
                doc_id=doc_id,
                title=title,
                filename=filename,
                file_path=file_path,
                source_url=source_url,
                metadata=metadata,
            )
            return result.single()[0]

    def add_chunk(self, doc_id, chunk_index, page_number, text, metadata=None):
        metadata = clean_metadata(metadata or {})
        with self.driver.session() as session:
            session.run(
                """
                MATCH (d:Document {id: $doc_id})

                CREATE (c:Chunk {
                    id: $chunk_id,
                    chunk_index: $chunk_index,
                    page_number: $page_number,
                    text: $text
                })

                SET c += $metadata

                CREATE (d)-[:HAS_CHUNK]->(c)
                """,
                doc_id=doc_id,
                chunk_id=f"{doc_id}:{chunk_index}",
                chunk_index=chunk_index,
                page_number=page_number,
                text=text,
                metadata=metadata,
            )

    def add_related_document(self, doc_id, related_doc_id, relation_type="RELATED_TO"):
        with self.driver.session() as session:
            session.run(
                """
                MATCH (a:Document {id: $doc_id})
                MATCH (b:Document {id: $related_doc_id})
                MERGE (a)-[r:RELATED_TO]->(b)
                SET r.type = $relation_type
                """,
                doc_id=doc_id,
                related_doc_id=related_doc_id,
                relation_type=relation_type,
            )


def get_graph_store():
    return Neo4jStore()
