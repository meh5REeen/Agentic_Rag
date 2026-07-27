import chromadb

client = chromadb.PersistentClient(path="./chroma_db")  # Replace with your path
collections = client.list_collections()

for c in collections:
    print(c)
collection = client.get_collection("langchain")
print(collection.count())
results = collection.peek(limit=5)

print(results)