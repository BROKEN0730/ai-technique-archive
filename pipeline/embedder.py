"""sentence-transformers 封裝。本地、$0、輸出 384 維對應 vector(384)。"""
from sentence_transformers import SentenceTransformer

_model = None

def get_model():
    global _model
    if _model is None:
        _model = SentenceTransformer('all-MiniLM-L6-v2')
    return _model

def generate_embedding(text):
    return get_model().encode((text or "")[:2000], convert_to_tensor=False).tolist()

def to_pgvector(vec):
    """list[float] -> pgvector 文字字面值 '[0.1,0.2,...]'"""
    return "[" + ",".join(str(x) for x in vec) + "]" if vec else ""
