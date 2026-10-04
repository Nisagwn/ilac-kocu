"""Veritabanından bağımsız embedding sütun tipi.

PostgreSQL'de pgvector `vector(N)` (cosine `<=>` operatörü + HNSW indeksi), diğer
veritabanlarında (testlerdeki SQLite) JSON listesi olarak saklanır. Bu modül
`app.models` tarafından içe aktarıldığı için modellere/servislere bağımlılığı yoktur.
"""

from pgvector.sqlalchemy import Vector
from sqlalchemy.types import JSON, TypeDecorator

EMBEDDING_DIM = 384


class EmbeddingVector(TypeDecorator):
    impl = JSON
    cache_ok = True

    def __init__(self, dim: int = EMBEDDING_DIM):
        super().__init__()
        self.dim = dim

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(Vector(self.dim))
        # none_as_null: Python None -> SQL NULL (JSON 'null' değil); "embedding IS NULL" sorgusu çalışsın
        return dialect.type_descriptor(JSON(none_as_null=True))

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return [float(x) for x in value]

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return [float(x) for x in value]
