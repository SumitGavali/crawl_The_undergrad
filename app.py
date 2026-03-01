"""
KnowledgeOS — Complete Backend
Real crawler + embeddings + search, all triggered from the UI.
One button → reads files → extracts text → creates embeddings → saves DB → search works.
"""

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse
import json, os, pickle, time, subprocess, platform
import numpy as np
from pathlib import Path

app = FastAPI(title="KnowledgeOS API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ── Config ─────────────────────────────────────────────────────────────────────
DATA_DIR       = os.path.join(os.path.dirname(__file__), "data")
VECTOR_DB_PATH = os.path.join(DATA_DIR, "vectors.pkl")
METADATA_PATH  = os.path.join(DATA_DIR, "metadata.json")
MODEL_NAME     = "all-MiniLM-L6-v2"
TOP_K          = 5
CHUNK_SIZE     = 400      # words per chunk
CHUNK_OVERLAP  = 50       # overlap between chunks
SUPPORTED_EXT  = {".pdf", ".docx", ".doc", ".xlsx", ".xls", ".txt", ".csv"}
# ──────────────────────────────────────────────────────────────────────────────

os.makedirs(DATA_DIR, exist_ok=True)

# ── Load model ─────────────────────────────────────────────────────────────────
try:
    from sentence_transformers import SentenceTransformer
    print("Loading embedding model...")
    model = SentenceTransformer(MODEL_NAME)
    MODEL_LOADED = True
    print("✓ Model loaded")
except Exception as e:
    print(f"WARNING: Could not load SentenceTransformer: {e}")
    MODEL_LOADED = False
    model = None

vectors  = None
metadata = []

def load_db():
    global vectors, metadata
    if os.path.exists(VECTOR_DB_PATH):
        with open(VECTOR_DB_PATH, "rb") as f:
            vectors = pickle.load(f)
        if isinstance(vectors, list):
            vectors = np.array(vectors, dtype=np.float32)
        print(f"✓ Loaded {len(vectors)} vectors")
    else:
        vectors = None
    if os.path.exists(METADATA_PATH):
        with open(METADATA_PATH, "r", encoding="utf-8") as f:
            metadata = json.load(f)
        print(f"✓ Loaded {len(metadata)} metadata entries")
    else:
        metadata = []

load_db()

# ── Text extractors ─────────────────────────────────────────────────────────────

def extract_pdf(path):
    try:
        import pdfplumber
        results = []
        with pdfplumber.open(path) as pdf:
            for i, page in enumerate(pdf.pages):
                text = page.extract_text() or ""
                if text.strip():
                    results.append({"text": text.strip(), "page": i + 1})
        return results
    except Exception as e:
        return [{"text": f"PDF error: {e}", "page": 1}]

def extract_docx(path):
    try:
        from docx import Document
        doc = Document(path)
        full = "\n".join(p.text for p in doc.paragraphs if p.text.strip())
        words = full.split()
        results = []
        for i in range(0, max(1, len(words)), 500):
            chunk = " ".join(words[i:i+500])
            if chunk.strip():
                results.append({"text": chunk, "page": i // 500 + 1})
        return results
    except Exception as e:
        return [{"text": f"DOCX error: {e}", "page": 1}]

def extract_xlsx(path):
    try:
        import openpyxl
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        results = []
        for i, name in enumerate(wb.sheetnames):
            ws = wb[name]
            rows = []
            for row in ws.iter_rows(values_only=True):
                row_text = "  |  ".join(str(c) for c in row if c is not None)
                if row_text.strip():
                    rows.append(row_text)
            if rows:
                results.append({"text": f"[Sheet: {name}]\n" + "\n".join(rows), "page": i + 1})
        return results
    except Exception as e:
        return [{"text": f"XLSX error: {e}", "page": 1}]

def extract_txt(path):
    try:
        text = Path(path).read_text(encoding="utf-8", errors="ignore")
        words = text.split()
        results = []
        for i in range(0, max(1, len(words)), 500):
            chunk = " ".join(words[i:i+500])
            if chunk.strip():
                results.append({"text": chunk, "page": i // 500 + 1})
        return results
    except Exception as e:
        return [{"text": f"TXT error: {e}", "page": 1}]

def extract_text(path):
    ext = Path(path).suffix.lower()
    if ext == ".pdf":                 return extract_pdf(path)
    elif ext in (".docx", ".doc"):    return extract_docx(path)
    elif ext in (".xlsx", ".xls"):    return extract_xlsx(path)
    else:                             return extract_txt(path)

def make_chunks(pages, file_path, file_type):
    chunks = []
    for page in pages:
        words = page["text"].split()
        for i in range(0, len(words), CHUNK_SIZE - CHUNK_OVERLAP):
            w = words[i:i + CHUNK_SIZE]
            if len(w) < 8:
                continue
            chunks.append({
                "file_path":   file_path,
                "file_type":   file_type,
                "page":        page["page"],
                "chunk_index": len(chunks),
                "text":        " ".join(w),
            })
    return chunks

# ── Real Crawl SSE ──────────────────────────────────────────────────────────────

def real_crawl_generator(folder: str):
    def evt(event, data):
        return f"event: {event}\ndata: {json.dumps(data)}\n\n"

    folder = os.path.abspath(folder)
    yield evt("start", {"message": f"Starting crawl → {folder}", "folder": folder})
    time.sleep(0.15)

    # Discover
    if not os.path.exists(folder):
        yield evt("error", {"message": f"Folder not found: {folder}"})
        return

    all_files = []
    for root, _, files in os.walk(folder):
        for f in files:
            if Path(f).suffix.lower() in SUPPORTED_EXT:
                all_files.append(os.path.join(root, f))

    if not all_files:
        yield evt("error", {"message": f"No supported files found. Supported types: PDF, DOCX, XLSX, TXT, CSV"})
        return

    yield evt("info", {"message": f"Found {len(all_files)} files to process", "total": len(all_files), "demo": False})
    time.sleep(0.15)

    # Extract + chunk
    all_chunks = []
    indexed = 0

    for i, fpath in enumerate(all_files):
        ext = Path(fpath).suffix.upper().strip(".")
        yield evt("reading",    {"file": fpath, "type": ext, "index": i+1, "total": len(all_files), "status": "reading"})
        pages = extract_text(fpath)
        yield evt("extracting", {"file": fpath, "type": ext, "pages": len(pages), "index": i+1, "status": "extracting"})
        chunks = make_chunks(pages, fpath, ext)
        yield evt("indexing",   {"file": fpath, "chunks": len(chunks), "type": ext, "index": i+1, "status": "indexing"})
        all_chunks.extend(chunks)
        indexed += 1
        yield evt("file_done",  {
            "file": fpath, "chunks": len(chunks), "type": ext,
            "indexed": indexed, "total": len(all_files), "total_chunks": len(all_chunks)
        })

    if not all_chunks:
        yield evt("error", {"message": "Could not extract any text from the files."})
        return

    # Embed
    if not MODEL_LOADED:
        yield evt("error", {"message": "Embedding model not loaded. Run: pip install sentence-transformers"})
        return

    yield evt("embedding_start", {"message": f"Creating embeddings for {len(all_chunks)} chunks...", "total_chunks": len(all_chunks)})

    texts    = [c["text"] for c in all_chunks]
    all_vecs = []
    BATCH    = 64

    for b in range(0, len(texts), BATCH):
        batch = texts[b:b + BATCH]
        vecs  = model.encode(batch, show_progress_bar=False)
        all_vecs.extend(vecs)
        pct = min(100, round(((b + len(batch)) / len(texts)) * 100))
        yield evt("embedding_progress", {"done": b + len(batch), "total": len(texts), "pct": pct})

    vectors_arr = np.array(all_vecs, dtype=np.float32)

    # Save
    yield evt("saving", {"message": "Saving index to disk..."})
    with open(VECTOR_DB_PATH, "wb") as f:
        pickle.dump(vectors_arr, f)
    with open(METADATA_PATH, "w", encoding="utf-8") as f:
        json.dump(all_chunks, f, ensure_ascii=False, indent=2)

    # Hot reload
    global vectors, metadata
    vectors  = vectors_arr
    metadata = all_chunks

    yield evt("complete", {
        "total_files":  len(all_files),
        "total_chunks": len(all_chunks),
        "message": f"Index complete — {len(all_files)} files, {len(all_chunks)} chunks. Ready to search."
    })

@app.get("/api/crawl/stream")
def crawl_stream(folder: str = ""):
    return StreamingResponse(
        real_crawl_generator(folder or "./demo_docs"),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )

# ── Search ──────────────────────────────────────────────────────────────────────

def cosine_sim(qvec, matrix):
    q     = qvec / (np.linalg.norm(qvec) + 1e-9)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True) + 1e-9
    return (matrix / norms) @ q

@app.get("/api/search")
def search(q: str = Query(..., min_length=2)):
    start = time.time()

    if vectors is None or len(metadata) == 0:
        return {
            "query": q, "results": [], "total": 0,
            "elapsed_ms": 0, "mode": "no_index",
            "message": "No index found. Go to Live Crawl tab and index your folder first."
        }

    if not MODEL_LOADED:
        return {"error": "Embedding model not loaded.", "results": [], "mode": "error"}

    qvec    = model.encode([q])[0].astype(np.float32)
    scores  = cosine_sim(qvec, vectors)
    top_idx = np.argsort(scores)[::-1][:TOP_K]

    results = []
    for idx in top_idx:
        if float(scores[idx]) < 0.15:
            continue
        m = metadata[idx]
        results.append({
            "file":  m.get("file_path", ""),
            "chunk": m.get("text", ""),
            "score": float(round(scores[idx], 4)),
            "page":  m.get("page", 1),
            "type":  m.get("file_type", ""),
        })

    return {
        "query": q, "results": results, "total": len(results),
        "elapsed_ms": round((time.time() - start) * 1000), "mode": "live"
    }

# ── Stats ────────────────────────────────────────────────────────────────────────

@app.get("/api/stats")
def stats():
    if vectors is None:
        return {"total_chunks": 0, "total_files": 0, "file_types": {}, "model": MODEL_NAME, "mode": "no_index"}
    file_types, unique_files = {}, set()
    for m in metadata:
        ext = m.get("file_type", "?")
        file_types[ext] = file_types.get(ext, 0) + 1
        unique_files.add(m.get("file_path", ""))
    return {
        "total_chunks": len(vectors), "total_files": len(unique_files),
        "file_types": file_types, "model": MODEL_NAME, "mode": "live"
    }

@app.get("/api/reload")
def reload_db():
    load_db()
    return {"status": "reloaded", "vectors": len(vectors) if vectors is not None else 0}

@app.get("/api/open")
def open_file(path: str):
    try:
        p = path if os.path.isabs(path) else os.path.abspath(path)
        if platform.system() == "Windows":   subprocess.Popen(["explorer", "/select,", p])
        elif platform.system() == "Darwin":  subprocess.Popen(["open", "-R", p])
        else:                                subprocess.Popen(["xdg-open", os.path.dirname(p)])
        return {"status": "opened", "path": p}
    except Exception as e:
        return {"status": "error", "message": str(e)}

# Serve frontend
_frontend = os.path.join(os.path.dirname(__file__), "..", "frontend")
if os.path.exists(_frontend):
    app.mount("/static", StaticFiles(directory=_frontend), name="static")
    @app.get("/")
    def root():
        return FileResponse(os.path.join(_frontend, "index.html"))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=False)
