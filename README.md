# KnowledgeOS — Enterprise File Search Prototype

> **One button. Any folder. Ask anything. Get answers from your files.**

Local semantic search engine for company documents — PDFs, Word docs, Excel sheets.  
Built with Python + HuggingFace (free, offline, no OpenAI key needed).

---

## What This Does

```
You type:   "what is the oil change interval for GA 37 compressor"
It finds:   Engineering/GA37_Maintenance_Manual.pdf — Page 47 — 94% match
            "Oil change interval is every 4000 operating hours or 12 months..."
```

Instead of ctrl+F across 300 PDFs — one search box finds answers by **meaning**, not just keywords.

---

## Project Structure

```
knowledge_search/
│
├── backend/
│   ├── app.py               ← FastAPI server (crawler + embeddings + search)
│   ├── requirements.txt     ← Python dependencies
│   └── data/                ← Auto-created when you index
│       ├── vectors.pkl      ← Embeddings saved here
│       └── metadata.json    ← File paths + text chunks saved here
│
├── frontend/
│   └── index.html           ← Full dashboard (open in browser)
│
├── demo_docs/               ← Sample company documents (optional)
│   ├── Engineering/
│   ├── Procedures/
│   ├── Training/
│   ├── HR/
│   ├── Reports/
│   └── Compliance/
│
└── create_demo_docs.py      ← Script to regenerate demo documents
```

---

## Setup — First Time (5 minutes)

### Step 1 — Install Python dependencies

```bash
cd backend
pip install -r requirements.txt
```

> First run will also download the HuggingFace embedding model (~90MB).  
> This happens automatically and only once. No API key needed.

### Step 2 — Start the backend server

```bash
cd backend
python app.py
```

You should see:
```
Loading embedding model...
✓ Model loaded
INFO: Uvicorn running on http://0.0.0.0:8000
```

### Step 3 — Open the dashboard

Open your browser and go to:
```
http://localhost:8000
```

Or open `frontend/index.html` directly by double-clicking it.

---

## How to Use

### Indexing your folder (Live Crawl tab)

1. Click **Live Crawl** tab in the top nav
2. Enter the full path to your folder, for example:
   - Windows: `C:\Users\Sumit\Documents\CompanyFiles`
   - Mac/Linux: `/Users/sumit/Documents/CompanyFiles`
   - Or use the demo folder: `./demo_docs`
3. Click **Start Indexing**
4. Watch the terminal — it shows every file being read, extracted, and embedded in real time
5. When you see **"Index complete — Ready to search"**, switch to the Search tab

> The indexing creates `backend/data/vectors.pkl` and `backend/data/metadata.json`.  
> These persist on disk — you only need to re-index when files change.

### Searching (Search tab)

1. Type any question in plain English
2. Hit Enter or click Search
3. Results show:
   - Which file the answer came from (full path)
   - The exact excerpt
   - Relevance % score
4. Click **"View source proof"** under any result to see:
   - Full file path
   - Page number
   - **"Open Original File"** button — opens the file in your OS

---

## Supported File Types

| Type | Extension | Notes |
|------|-----------|-------|
| PDF | `.pdf` | Text-based PDFs (not scanned images) |
| Word | `.docx`, `.doc` | All Word documents |
| Excel | `.xlsx`, `.xls` | All sheets extracted |
| Text | `.txt`, `.csv` | Plain text and CSV files |

---

## Good Demo Queries (for demo_docs folder)

| Query | Expected Source File |
|-------|---------------------|
| `what is the oil change interval for GA 37` | Engineering/GA37_Maintenance_Manual.pdf |
| `steps to follow before starting maintenance` | Procedures/Lockout_Tagout_SOP.docx |
| `what PPE is needed for oil change` | Procedures/PPE_Requirements.pdf |
| `how many days annual leave do employees get` | HR/Employee_Handbook_v3.pdf |
| `spare parts for GA37 oil filter part number` | Engineering/Spare_Parts_Catalog.xlsx |
| `what non-conformances were found in the audit` | Compliance/ISO_9001_Internal_Audit_2023.pdf |

---

## How It Works (Technical)

```
INDEXING (one time):
Folder → walk all files
       → extract text (pdfplumber / python-docx / openpyxl)
       → split into 400-word overlapping chunks
       → encode each chunk → vector (HuggingFace all-MiniLM-L6-v2)
       → save vectors.pkl + metadata.json to disk

SEARCHING (every query):
Question → encode → question vector
         → cosine similarity vs all chunk vectors
         → return top 5 closest matches
         → show file, excerpt, page, score
```

The key: same model encodes both documents and questions, so meaning is compared — not just keywords.

---

## Re-indexing

Run a new crawl any time your files change:
- Go to Live Crawl tab
- Enter the folder path again
- Click Start Indexing
- Old index is automatically replaced

---

## Troubleshooting

**Backend won't start:**
```bash
pip install -r requirements.txt
# Make sure you're in the backend/ folder
python app.py
```

**"No index found" when searching:**
- Go to Live Crawl tab first and index a folder
- Search only works after indexing completes

**Files not being found during crawl:**
- Make sure the path exists and is accessible
- Windows paths use backslash: `C:\Users\Sumit\Downloads\myfiles`
- Check that files are PDF, DOCX, XLSX, or TXT format

**PDF text not extracting:**
- Scanned PDFs (images of text) are not supported without OCR
- Text-based PDFs work fine

**Model download is slow on first run:**
- The ~90MB model downloads once from HuggingFace
- Needs internet connection only this first time
- Subsequent runs load from local cache instantly

---

## Requirements

```
fastapi==0.110.0
uvicorn==0.29.0
sentence-transformers==2.7.0
numpy==1.26.4
python-multipart==0.0.9
pdfplumber          ← PDF reading
python-docx         ← Word reading
openpyxl            ← Excel reading
```

Python 3.9+ required.

---

## Roadmap (Next Steps as a Product)

| Feature | What it adds |
|---------|-------------|
| LLM answer synthesis | Generates a paragraph answer from top chunks, not just excerpts |
| SharePoint connector | Crawls SharePoint via Microsoft Graph API |
| Network drive support | Crawls `\\SERVER\Share` paths on Windows |
| Multi-folder index | Index multiple folders into one searchable index |
| Scheduled re-indexing | Auto re-index nightly via cron/Task Scheduler |
| Azure OpenAI upgrade | Replace local model with Azure for enterprise compliance |
| Microsoft Copilot plugin | Register as a Copilot connector for Teams integration |
| User authentication | Azure AD login for enterprise deployment |

---

## Demo Folder Setup

To regenerate the demo documents:

```bash
pip install python-docx openpyxl reportlab
python create_demo_docs.py
```
